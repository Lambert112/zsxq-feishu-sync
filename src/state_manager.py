"""Sync state persistence — read/write last-synced cursor."""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "sync_state.json"


def load_state() -> dict:
    """Load sync state from disk. Returns defaults if no state exists."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
            logger.info("加载同步状态: last_sync_time=%s", state.get("last_sync_time"))
            return state
        except (json.JSONDecodeError, IOError):
            logger.warning("同步状态文件损坏，使用默认状态")
    return _default_state()


def save_state(state: dict) -> None:
    """Persist sync state to disk."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    logger.info("保存同步状态: last_sync_time=%s", state.get("last_sync_time"))


def update_sync_time(state: dict | None = None) -> dict:
    """Update state with the current time as last_sync_time.

    If state is provided, it is updated in-place and saved.
    Otherwise, state is loaded from disk first.
    """
    import time
    if state is None:
        state = load_state()
    state["last_sync_time"] = int(time.time())
    save_state(state)
    return state


def _default_state() -> dict:
    return {
        "last_sync_time": None,
        "current_doc_id": None,
        "current_doc_month": None,
        "date_headers": {},       # { "2026-06-11": "h3_block_id", ... }
        "file_summary_count": 0,  # total blocks in summary section (to batch-delete)
    }
