"""Convert ZSXQ topics to Feishu document blocks."""

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
    return [{"text_run": {"content": text}}]


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
    return {"block_type": 22}


def build_image(file_token: str) -> dict:
    return {"block_type": 27, "image": {"token": file_token}}


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
) -> list[dict]:
    """Convert a single ZSXQ topic to Feishu document blocks."""
    blocks = []

    # Divider
    blocks.append(build_divider())

    # Title — strip newlines (headings are single-line)
    title = topic.get("title", "") or _extract_title_from_talk(topic)
    title = title.replace("\n", " ").replace("\r", " ").strip()
    if not title:
        title = "无标题"
    blocks.append(build_h2(title))

    # Publish time
    create_time = topic.get("create_time", "")
    blocks.append(build_text(f"发布时间：{format_time(create_time)}"))

    # Body text
    text = topic.get("talk", {}).get("text", "")
    if text:
        blocks.append(build_text(text))

    # Dedup marker
    topic_id = topic.get("topic_id", "")
    if topic_id:
        blocks.append(build_text(f"[zsxq_topic_id: {topic_id}]"))

    temp_dir = config.TEMP_DIR

    # Images
    for img in zsxq.extract_images(topic)[:10]:
        local_path = _download(img["url"], temp_dir)
        if local_path:
            file_token = feishu.upload_media(
                local_path, img["filename"],
                parent_type="docx_image",
                parent_node=doc_id,
            )
            if file_token:
                blocks.append(build_image(file_token))
            _safe_remove(local_path)

    # Files (PDF etc.)
    for f_info in zsxq.extract_files(topic)[:5]:
        local_path = _download(f_info["url"], temp_dir)
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
        resp = http.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return filepath
    except Exception:
        logger.warning("下载失败: %s", url, exc_info=True)
        return None


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
