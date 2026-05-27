"""Main orchestrator — ties ZSXQ + Feishu together with state management."""

import logging
import os
import shutil
import sys
import time
from collections import defaultdict

from . import config
from . import state_manager as state_mgr
from .content_formatter import (
    format_topic_to_blocks,
    build_date_header_block,
    get_date_key,
    get_month_key,
)
from .feishu_client import FeishuClient, FeishuError
from .notifier import send_auth_error, send_error, send_sync_summary
from .zsxq_client import ZsxqClient, ZsxqError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def run() -> None:
    """Main sync entry point."""
    config._validate()
    logger.info("=== ZSXQ → Feishu 同步开始 ===")
    start_time = time.time()

    # ---- Load state ----
    state = state_mgr.load_state()
    last_sync_time = None if config.FORCE_FULL_SYNC else state.get("last_sync_time")

    # ---- Init clients ----
    zsxq_client = ZsxqClient()
    feishu_client = FeishuClient()

    # ---- Check ZSXQ auth ----
    if not zsxq_client.check_auth():
        logger.error("ZSXQ MCP API 认证失败")
        send_auth_error()
        sys.exit(1)

    # ---- Fetch new topics ----
    limit = config.INITIAL_SYNC_LIMIT if last_sync_time is None else None
    try:
        topics = zsxq_client.fetch_new_topics(last_sync_time, limit=limit)
    except ZsxqError as e:
        logger.error("获取 ZSXQ 帖子失败: %s", e)
        send_error(f"获取帖子失败: {e}")
        sys.exit(1)

    if not topics:
        logger.info("没有新增帖子，同步结束")
        return

    logger.info("发现 %d 条新帖子", len(topics))

    # ---- Group topics by month and date ----
    # Structure: { (2026, 5): { "2026-05-26": [topic, ...], ... } }
    grouped = defaultdict(lambda: defaultdict(list))
    for topic in topics:
        month_key = get_month_key(topic.get("create_time", ""))
        date_key = get_date_key(topic.get("create_time", ""))
        grouped[month_key][date_key].append(topic)

    # ---- Sync each month group ----
    total_synced = 0
    last_date_str = ""
    for (year, month), date_groups in sorted(grouped.items(), reverse=True):
        month_key = f"{year}-{month}"
        try:
            if (state.get("current_doc_month") == month_key
                    and state.get("current_doc_id")):
                doc_id = state["current_doc_id"]
                logger.info("使用已缓存的月度文档: %s", doc_id)
            else:
                doc_id = feishu_client.create_monthly_doc(year, month)
                state["current_doc_id"] = doc_id
                state["current_doc_month"] = month_key

            # Try to set the user as document manager
            if config.FEISHU_USER_ID and not state.get("manager_added"):
                if feishu_client.add_document_manager(doc_id, config.FEISHU_USER_ID):
                    state["manager_added"] = True
        except FeishuError as e:
            logger.error("创建月度文档失败: %s", e)
            send_error(f"飞书文档操作失败: {e}")
            sys.exit(1)

        for date_str in sorted(date_groups.keys(), reverse=True):
            day_topics = date_groups[date_str]
            blocks = []

            # Date header (H1)
            blocks.extend(build_date_header_block(date_str))

            # Each topic
            for topic in day_topics:
                try:
                    topic_blocks = format_topic_to_blocks(topic, feishu_client, doc_id, zsxq_client)
                    blocks.extend(topic_blocks)
                except Exception as e:
                    logger.warning("格式化帖子失败 (topic_id=%s): %s",
                                   topic.get("topic_id", "?"), e)
                    continue

            if config.DRY_RUN:
                logger.info("[DRY-RUN] 将向文档 %s 添加 %d 个块 (日期: %s, 帖子: %d)",
                            doc_id, len(blocks), date_str, len(day_topics))
            else:
                try:
                    feishu_client.append_blocks(doc_id, blocks)
                    logger.info("已同步: %s, %d 条帖子, %d 个块",
                                date_str, len(day_topics), len(blocks))
                except FeishuError as e:
                    logger.error("追加文档块失败 (日期=%s): %s", date_str, e)
                    # Save partial progress and continue with next date
                    if total_synced > 0:
                        _save_progress(state, topics, total_synced)
                    send_error(f"追加文档块失败 (日期={date_str}, 已同步={total_synced}条): {e}")
                    continue

            total_synced += len(day_topics)
            last_date_str = date_str
            time.sleep(1)  # Rate limit: pause between dates

    # ---- Save final state ----
    if not config.DRY_RUN:
        state_mgr.update_sync_time(state)

    elapsed = time.time() - start_time
    logger.info("=== 同步完成: %d 条帖子, 耗时 %.1f 秒 ===", total_synced, elapsed)

    # ---- Notify ----
    send_sync_summary(total_synced, last_date_str)

    # ---- Cleanup temp files ----
    if os.path.exists(config.TEMP_DIR):
        shutil.rmtree(config.TEMP_DIR)


def _save_progress(state: dict, topics: list, count: int) -> None:
    """Save partial progress so we don't re-sync already-processed topics."""
    if count > 0:
        state_mgr.update_sync_time(state)


if __name__ == "__main__":
    run()
