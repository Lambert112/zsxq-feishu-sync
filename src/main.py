"""Main orchestrator — H3 headers are parent blocks, posts are children."""

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
    config._validate()
    logger.info("=== ZSXQ → Feishu 同步开始 ===")
    start_time = time.time()

    state = state_mgr.load_state()
    last_sync_time = None if config.FORCE_FULL_SYNC else state.get("last_sync_time")

    zsxq_client = ZsxqClient()
    feishu_client = FeishuClient()

    if not zsxq_client.check_auth():
        logger.error("ZSXQ MCP API 认证失败")
        send_auth_error()
        sys.exit(1)

    # ── Fetch new topics ──────────────────────────
    limit = config.INITIAL_SYNC_LIMIT if last_sync_time is None else None
    try:
        topics = zsxq_client.fetch_new_topics(last_sync_time, limit=limit)
    except ZsxqError as e:
        logger.error("获取 ZSXQ 帖子失败: %s", e)
        send_error(f"获取帖子失败: {e}")
        sys.exit(1)

    if not topics:
        logger.info("没有新增帖子")
        _ensure_doc_permission(feishu_client, state)
        return

    logger.info("发现 %d 条新帖子", len(topics))

    # ── Build topic summaries for notifications ────
    topic_summaries = []
    for t in topics:
        create_time = t.get("create_time", "")
        date_str = create_time[:10] if "T" in create_time else create_time.split(" ")[0]
        time_str = create_time[11:16] if "T" in create_time else create_time.split(" ")[-1][:5]
        body = (
            t.get("talk", {}).get("text", "")
            or t.get("question", {}).get("text", "")
            or t.get("content", "")
            or ""
        )
        topic_summaries.append({
            "time": time_str,
            "date": date_str,
            "body": body,
        })

    # ── Group by (month, date) ────────────────────
    grouped = defaultdict(lambda: defaultdict(list))
    for t in topics:
        mk = get_month_key(t.get("create_time", ""))
        dk = get_date_key(t.get("create_time", ""))
        grouped[mk][dk].append(t)

    # ── Process each month ────────────────────────
    total_synced = 0
    date_headers = state.get("date_headers", {})

    for (year, month), date_groups in sorted(grouped.items()):
        month_key = f"{year}-{month}"

        # ── Get or create month document ──
        try:
            if config.FORCE_FULL_SYNC and state.get("current_doc_id"):
                doc_id = feishu_client.create_monthly_doc(year, month)
                state["current_doc_id"] = doc_id
                state["current_doc_month"] = month_key
                date_headers = {}
                logger.info("全量同步：新建月文档 %s", doc_id)
            elif (state.get("current_doc_month") == month_key
                    and state.get("current_doc_id")):
                doc_id = state["current_doc_id"]
                logger.info("使用月文档: %s", doc_id)
            else:
                doc_id = feishu_client.create_monthly_doc(year, month)
                state["current_doc_id"] = doc_id
                state["current_doc_month"] = month_key
                date_headers = {}
                logger.info("新建月文档: %s", doc_id)

            _ensure_doc_permission(feishu_client, state)
        except FeishuError as e:
            logger.error("创建月度文档失败: %s", e)
            send_error(f"飞书文档操作失败: {e}")
            sys.exit(1)

        # ── Process each date ──
        # Sort oldest-first: each batch inserts at index=0, so later
        # batches (newer) push earlier ones down → newest on top.
        for date_str in sorted(date_groups.keys()):
            day_topics = sorted(
                date_groups[date_str],
                key=lambda t: t.get("create_time", ""),
            )

            # ── Step 1: Ensure H3 header exists ──
            if date_str in date_headers:
                h3_id = date_headers[date_str]
                logger.info("H3 已存在: %s -> %s", date_str, h3_id)
            else:
                # Create H3 block at document root
                h3_blocks = build_date_header_block(date_str)
                created_h3 = feishu_client.append_blocks(doc_id, h3_blocks)
                # Find the H3 in response
                h3_id = None
                for b in created_h3:
                    if b.get("block_type") == 5:
                        h3_id = b["block_id"]
                        break
                if not h3_id:
                    logger.error("创建 H3 失败，未返回 block_id")
                    continue
                date_headers[date_str] = h3_id
                state["date_headers"] = date_headers
                logger.info("新建 H3: %s -> %s", date_str, h3_id)

            # ── Step 2: Append each topic individually (oldest first) ──
            # Per-topic append guarantees correct sort: oldest inserted
            # first at index=0, then pushed down by each newer topic.
            synced_count = 0
            for topic in day_topics:
                try:
                    tb, ti, tf = format_topic_to_blocks(
                        topic, feishu_client, doc_id, zsxq_client,
                    )
                except Exception as e:
                    logger.warning("格式化失败 (topic_id=%s): %s",
                                   topic.get("topic_id", "?"), e)
                    continue

                if not tb:
                    continue

                if config.DRY_RUN:
                    logger.info("[DRY-RUN] %s → H3 %s: topic=%s, %d个块",
                                date_str, h3_id[:12],
                                topic.get("topic_id",""), len(tb))
                    synced_count += 1
                    continue

                try:
                    created = feishu_client.append_blocks(
                        doc_id, tb, parent_block_id=h3_id,
                    )

                    if ti:
                        _refill_images(feishu_client, doc_id, created,
                                       ti, config.TEMP_DIR)
                    if tf:
                        _refill_files(feishu_client, doc_id, created,
                                      tf, config.TEMP_DIR, zsxq_client)
                    synced_count += 1
                except FeishuError as e:
                    logger.error("追加失败 (topic=%s): %s",
                                 topic.get("topic_id", "?"), e)
                    continue

                time.sleep(0.5)

            if synced_count > 0:
                logger.info("已同步: %s, %d 条帖子", date_str, synced_count)
                total_synced += synced_count

    # ── Save state ────────────────────────────────
    if not config.DRY_RUN:
        state["last_sync_time"] = int(time.time())
        state_mgr.save_state(state)

    elapsed = time.time() - start_time
    logger.info("=== 同步完成: %d 条帖子, 耗时 %.1f 秒 ===", total_synced, elapsed)
    send_sync_summary(total_synced, state.get("current_doc_id", ""), topic_summaries)

    if os.path.exists(config.TEMP_DIR):
        shutil.rmtree(config.TEMP_DIR)


# ── Helpers ──────────────────────────────────────────

def _refill_images(feishu_client, doc_id, created_blocks, image_refs, temp_dir):
    from .content_formatter import _download, _safe_remove
    img_ids = [b["block_id"] for b in created_blocks if b.get("block_type") == 27]
    if not img_ids:
        return
    logger.info("Refilling %d image blocks...", min(len(img_ids), len(image_refs)))
    for i, ref in enumerate(image_refs):
        if i >= len(img_ids):
            break
        bid = img_ids[i]
        local = _download(ref["url"], temp_dir)
        if not local:
            continue
        ft = feishu_client.upload_media(local, ref["filename"],
                                        parent_type="docx_image", parent_node=bid)
        _safe_remove(local)
        if ft:
            feishu_client.replace_image(doc_id, bid, ft)


def _refill_files(feishu_client, doc_id, created_blocks, file_refs, temp_dir, zsxq_client):
    from .content_formatter import _download_zsxq_file, _safe_remove
    view_blocks = [b for b in created_blocks if b.get("block_type") == 33]
    if not view_blocks:
        return
    file_ids = []
    for vb in view_blocks:
        try:
            for c in feishu_client.get_block_children(doc_id, vb["block_id"]):
                if c.get("block_type") == 23:
                    file_ids.append(c["block_id"])
                    break
        except FeishuError:
            pass
    if not file_ids:
        return
    logger.info("Refilling %d file blocks...", min(len(file_ids), len(file_refs)))
    for i, ref in enumerate(file_refs):
        if i >= len(file_ids):
            break
        bid = file_ids[i]
        local = _download_zsxq_file(ref, temp_dir, zsxq_client)
        if not local:
            continue
        ft = feishu_client.upload_media(local, ref["filename"],
                                        parent_type="docx_file", parent_node=bid)
        _safe_remove(local)
        if ft:
            feishu_client.replace_file(doc_id, bid, ft)


def _ensure_doc_permission(feishu_client, state):
    if not config.FEISHU_USER_ID:
        return
    doc_id = state.get("current_doc_id")
    if not doc_id:
        return
    try:
        feishu_client.transfer_ownership(doc_id, config.FEISHU_USER_ID)
    except Exception as e:
        logger.warning("转移文档所有权失败: %s", e)


if __name__ == "__main__":
    run()
