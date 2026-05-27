"""Convert ZSXQ topics to Feishu document blocks."""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests as http

from . import config
from . import zsxq_client as zsxq
from .feishu_client import FeishuClient

logger = logging.getLogger(__name__)

# Beijing timezone
CST = timezone(timedelta(hours=8))


def format_time(create_time_str: str) -> str:
    """Convert ZSXQ create_time to readable Beijing time."""
    try:
        if "T" in create_time_str:
            dt = datetime.fromisoformat(create_time_str)
        else:
            dt = datetime.strptime(create_time_str, "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo:
            dt = dt.astimezone(CST)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return create_time_str


def get_date_key(create_time_str: str) -> str:
    """Extract date key (YYYY-MM-DD) from ZSXQ create_time."""
    try:
        if "T" in create_time_str:
            dt = datetime.fromisoformat(create_time_str)
        else:
            dt = datetime.strptime(create_time_str, "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo:
            dt = dt.astimezone(CST)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "unknown"


def get_month_key(create_time_str: str) -> tuple[int, int]:
    """Extract (year, month) from ZSXQ create_time."""
    try:
        if "T" in create_time_str:
            dt = datetime.fromisoformat(create_time_str)
        else:
            dt = datetime.strptime(create_time_str, "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo:
            dt = dt.astimezone(CST)
        return dt.year, dt.month
    except (ValueError, TypeError):
        now = datetime.now(CST)
        return now.year, now.month


# ------------------------------------------------------------------
# Block builders
# ------------------------------------------------------------------

def build_text_elements(text: str) -> list[dict]:
    text = text.replace("<br>", "\n").replace("<br/>", "\n")
    text = text.replace("&nbsp;", " ")
    # Feishu text_run content limit is 5000 chars
    if len(text) > 5000:
        text = text[:5000]
    return [{"text_run": {"content": text, "text_element_style": {}}}]


def build_h1(text: str) -> dict:
    return {
        "block_type": 3,
        "heading1": {"elements": build_text_elements(text)},
    }


def build_h2(text: str) -> dict:
    return {
        "block_type": 4,
        "heading2": {"elements": build_text_elements(text)},
    }


def build_text(text: str) -> dict:
    return {
        "block_type": 2,
        "text": {"elements": build_text_elements(text)},
    }


def build_divider() -> dict:
    return {"block_type": 22, "divider": {}}


def build_image(file_token: str) -> dict:
    return {
        "block_type": 27,
        "image": {
            "token": file_token,
            "width": 400,
            "height": 300,
        },
    }


def build_file(file_token: str, name: str) -> dict:
    return {
        "block_type": 23,
        "file": {"token": file_token, "name": name},
    }


def build_date_header_block(date_str: str) -> list[dict]:
    """Build date header block (H1)."""
    return [build_h1(date_str)]


# ------------------------------------------------------------------
# Main formatter
# ------------------------------------------------------------------

def format_topic_to_blocks(
    topic: dict,
    feishu: FeishuClient,
    doc_id: str,
    zsxq_client=None,
) -> list[dict]:
    """Convert a single ZSXQ topic to Feishu document blocks."""
    blocks = []

    # Divider
    blocks.append(build_divider())

    # Time as plain text with yellow highlight (e.g. "14:30")
    create_time = topic.get("create_time", "")
    time_str = format_time(create_time)
    # Extract just the time part if format is "YYYY-MM-DD HH:MM"
    if " " in time_str:
        time_str = time_str.split(" ")[1]
    blocks.append({
        "block_type": 2,
        "text": {
            "elements": [{
                "text_run": {
                    "content": time_str,
                    "text_element_style": {"background_color": 3},
                }
            }]
        },
    })

    # Body text — ZSXQ stores it in talk.text or question.text
    text = (
        topic.get("talk", {}).get("text", "")
        or topic.get("question", {}).get("text", "")
        or topic.get("content", "")
    )
    if text:
        blocks.append(build_text(text))


    temp_dir = config.TEMP_DIR

    # Images — temporarily disabled to isolate 1770001 error
    images = zsxq.extract_images(topic)
    if images:
        logger.info("发现 %d 张图片 (topic_id=%s) — 暂时跳过以调试", len(images), topic.get("topic_id", "?"))
    # for img in images[:10]:
    #     local_path = _download(img["url"], temp_dir)
    #     if local_path:
    #         clean_name = _sanitize_filename(img.get("filename", "image"))
    #         file_token = feishu.upload_media(
    #             local_path, clean_name,
    #             parent_type="docx_image",
    #             parent_node=doc_id,
    #         )
    #         if file_token:
    #             logger.info("图片上传成功: token=%s", file_token)
    #             blocks.append(build_image(file_token))
    #         else:
    #             logger.warning("图片上传失败: %s", clean_name)
    #         _safe_remove(local_path)

    # Files (PDF etc.)
    files = zsxq.extract_files(topic)
    if files:
        logger.info("发现 %d 个文件 (topic_id=%s)", len(files), topic.get("topic_id", "?"))
    for f_info in files[:5]:
        local_path = _download_zsxq_file(f_info, temp_dir, zsxq_client)
        if local_path:
            file_token = feishu.upload_media(
                local_path, f_info["filename"],
                parent_type="docx_file",
                parent_node=doc_id,
            )
            if file_token:
                blocks.append(build_file(file_token, f_info["filename"]))
            _safe_remove(local_path)

    return blocks


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _extract_title_from_talk(topic: dict) -> str:
    text = topic.get("talk", {}).get("text", "")
    if not text:
        text = topic.get("question", {}).get("text", "")
    if not text:
        return "无标题"
    first_line = text.split("\n")[0].strip()
    if len(first_line) > 50:
        return first_line[:50] + "..."
    return first_line


def _download(url: str, dest_dir: str) -> Optional[str]:
    """Download a file from URL to dest_dir. Returns local path or None."""
    os.makedirs(dest_dir, exist_ok=True)
    filename = url.split("/")[-1].split("?")[0] or "attachment"
    filepath = os.path.join(dest_dir, filename)
    try:
        headers = {"User-Agent": "zsxq-sync/1.0"}
        resp = http.get(url, timeout=60, stream=True, headers=headers)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" in content_type or "application/json" in content_type:
            snippet = resp.text[:200] if len(resp.text) < 500 else resp.text[:200] + "..."
            logger.warning("下载返回非二进制内容 (%s): %s", content_type, snippet)
            if len(resp.content) < 1024:
                return None
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        logger.info("下载成功: %s -> %s (%d bytes)", filename, filepath, os.path.getsize(filepath))
        return filepath
    except Exception:
        logger.warning("下载失败: %s", url, exc_info=True)
        return None


def _download_zsxq_file(f_info: dict, dest_dir: str, zsxq_client=None) -> Optional[str]:
    """Download a ZSXQ file (regular URL or zsxq://file/{id}). Returns local path."""
    os.makedirs(dest_dir, exist_ok=True)
    url = f_info.get("url", "")
    filename = f_info.get("filename", "file")
    filepath = os.path.join(dest_dir, filename)

    # ZSXQ internal file — use call_zsxq_api to download
    if url.startswith("zsxq://file/") and zsxq_client:
        file_id = f_info.get("file_id", url.split("/")[-1])
        logger.info("Downloading ZSXQ file %s via MCP API...", file_id)
        if zsxq_client.download_file(file_id, filepath):
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                logger.info("ZSXQ file downloaded: %s (%d bytes)", filename, os.path.getsize(filepath))
                return filepath
            else:
                logger.warning("ZSXQ file download produced empty/missing file")
                _safe_remove(filepath)
                return None
        logger.warning("ZSXQ file download not supported for %s — skipping", file_id)
        return None

    # Regular HTTP download (only for http/https URLs)
    if url.startswith("http"):
        return _download(url, dest_dir)
    return None


def _sanitize_filename(name: str) -> str:
    """Clean up a filename, removing URL query params and special chars."""
    import re
    # Take only the part before any '?' or '&' (URL query params)
    name = name.split("?")[0].split("&")[0]
    # Remove or replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip()
    # Ensure we have a valid extension
    if '.' not in name[-10:]:
        name += '.jpg'
    # Max 100 chars
    if len(name) > 100:
        name = name[:50] + '...' + name[-47:]
    return name or 'image.jpg'


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
