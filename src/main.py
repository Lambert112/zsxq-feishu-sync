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
    grouped = defaultdict(lambda: defaultdict(list))
    for topic in topics:
        month_key = get_month_key(topic.get("create_time", ""))
        date_key = get_date_key(topic.get("create_time", ""))
        grouped[month_key][date_key].append(topic)

    # ---- Sync each month group ----
    total_synced = 0
    last_date_str = ""
    for (year, month), date_groups in sorted(grouped.items()):
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
                # New month doc → reset H3 tracking
            if not (state.get("current_doc_month") == month_key
                    and state.get("current_doc_id")):
                state["synced_dates"] = []

            # Always ensure user has manage permission (idempotent)
            if config.FEISHU_USER_ID:
                feishu_client.add_document_manager(doc_id, config.FEISHU_USER_ID)
        except FeishuError as e:
            logger.error("创建月度文档失败: %s", e)
            send_error(f"飞书文档操作失败: {e}")
            sys.exit(1)

        for date_str in sorted(date_groups.keys()):
            day_topics = date_groups[date_str]
            blocks = []
            image_refs = []
            file_refs = []

            # Date header (H3) — only if this date hasn't been synced before
            synced_dates = state.get("synced_dates", [])
            if date_str not in synced_dates:
                blocks.extend(build_date_header_block(date_str))
                synced_dates.append(date_str)
                state["synced_dates"] = synced_dates

            # Each topic
            for topic in day_topics:
                try:
                    topic_blocks, topic_images, topic_files = format_topic_to_blocks(
                        topic, feishu_client, doc_id, zsxq_client,
                    )
                    blocks.extend(topic_blocks)
                    image_refs.extend(topic_images)
                    file_refs.extend(topic_files)
                except Exception as e:
                    logger.warning("格式化帖子失败 (topic_id=%s): %s",
                                   topic.get("topic_id", "?"), e)
                    continue

            if config.DRY_RUN:
                logger.info("[DRY-RUN] 将向文档 %s 添加 %d 个块 (日期: %s, 帖子: %d)",
                            doc_id, len(blocks), date_str, len(day_topics))
            else:
                try:
                    created = feishu_client.append_blocks(doc_id, blocks)
                    logger.info("已同步: %s, %d 条帖子, %d 个块",
                                date_str, len(day_topics), len(blocks))

                    if image_refs:
                        _refill_images(
                            feishu_client, doc_id, created, image_refs, config.TEMP_DIR,
                        )

                    if file_refs:
                        _refill_files(
                            feishu_client, doc_id, created, file_refs,
                            config.TEMP_DIR, zsxq_client,
                        )
                except FeishuError as e:
                    logger.error("追加文档块失败 (日期=%s): %s", date_str, e)
                    if total_synced > 0:
                        _save_progress(state, topics, total_synced)
                    send_error(f"追加文档块失败 (日期={date_str}, 已同步={total_synced}条): {e}")
                    continue

            total_synced += len(day_topics)
            last_date_str = date_str
            time.sleep(1)

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


def _refill_images(feishu_client: FeishuClient, doc_id: str,
                   created_blocks: list[dict], image_refs: list[dict],
                   temp_dir: str) -> None:
    """Upload images and refill placeholder image blocks."""
    from .content_formatter import _download, _safe_remove

    img_block_ids = [
        b["block_id"] for b in created_blocks
        if b.get("block_type") == 27
    ]

    if not img_block_ids:
        logger.warning("No image blocks found in created blocks, skipping refill")
        return

    if len(img_block_ids) != len(image_refs):
        logger.warning(
            "Image block count mismatch: %d blocks vs %d refs",
            len(img_block_ids), len(image_refs),
        )

    logger.info("Refilling %d image blocks...", min(len(img_block_ids), len(image_refs)))

    for i, img_ref in enumerate(image_refs):
        if i >= len(img_block_ids):
            break
        block_id = img_block_ids[i]
        url = img_ref["url"]
        filename = img_ref["filename"]

        local_path = _download(url, temp_dir)
        if not local_path:
            logger.warning("Image download failed, skipping: %s", url[:80])
            continue

        file_token = feishu_client.upload_media(
            local_path, filename,
            parent_type="docx_image",
            parent_node=block_id,
        )
        _safe_remove(local_path)

        if file_token:
            feishu_client.replace_image(doc_id, block_id, file_token)
        else:
            logger.warning("Image upload failed for block %s", block_id)


def _refill_files(feishu_client: FeishuClient, doc_id: str,
                  created_blocks: list[dict], file_refs: list[dict],
                  temp_dir: str, zsxq_client=None) -> None:
    """Upload files and refill placeholder file blocks."""
    from .content_formatter import _download_zsxq_file, _safe_remove

    view_blocks = [
        b for b in created_blocks if b.get("block_type") == 33
    ]

    if not view_blocks:
        block_types = set(b.get("block_type") for b in created_blocks)
        logger.warning("No view/file blocks found in created blocks (types: %s), skipping refill",
                       sorted(block_types))
        return

    file_block_ids = []
    for vb in view_blocks:
        try:
            children = feishu_client.get_block_children(doc_id, vb["block_id"])
            for child in children:
                if child.get("block_type") == 23:
                    file_block_ids.append(child["block_id"])
                    break
        except FeishuError:
            logger.warning("Failed to get children of view block %s", vb["block_id"])

    if not file_block_ids:
        logger.warning("No file blocks found inside view blocks, skipping refill")
        return

    logger.info("Refilling %d file blocks...", min(len(file_block_ids), len(file_refs)))

    for i, file_ref in enumerate(file_refs):
        if i >= len(file_block_ids):
            break
        block_id = file_block_ids[i]
        filename = file_ref["filename"]

        local_path = _download_zsxq_file(file_ref, temp_dir, zsxq_client)
        if not local_path:
            logger.warning("File download failed, skipping: %s", filename)
            continue

        file_token = feishu_client.upload_media(
            local_path, filename,
            parent_type="docx_file",
            parent_node=block_id,
        )
        _safe_remove(local_path)

        if file_token:
            feishu_client.replace_file(doc_id, block_id, file_token)
        else:
            logger.warning("File upload failed for block %s", block_id)


def _save_progress(state: dict, topics: list, count: int) -> None:
    """Save partial progress so we don't re-sync already-processed topics."""
    if count > 0:
        state_mgr.update_sync_time(state)


if __name__ == "__main__":
    run()
