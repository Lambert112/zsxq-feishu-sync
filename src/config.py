"""Configuration from environment variables — lazy validation."""

import os
import sys
from typing import Optional


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _validate() -> None:
    """Check all required env vars are set. Call once at startup."""
    required = [
        "ZSXQ_COOKIE",
        "ZSXQ_GROUP_ID",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_FOLDER_TOKEN",
    ]
    missing = [k for k in required if not _env(k)]
    if missing:
        print(f"[ERROR] 缺少必要的环境变量: {', '.join(missing)}")
        sys.exit(1)


# ZSXQ
ZSXQ_COOKIE: str = _env("ZSXQ_COOKIE")
ZSXQ_GROUP_ID: str = _env("ZSXQ_GROUP_ID")
ZSXQ_BASE_URL = "https://api.zsxq.com/v2"
ZSXQ_SECRET = "zsxqapi2020"
ZSXQ_APP_VERSION = "2.57.0"
ZSXQ_PLATFORM = "web"
ZSXQ_REQUEST_DELAY = 2

# Feishu
FEISHU_APP_ID: str = _env("FEISHU_APP_ID")
FEISHU_APP_SECRET: str = _env("FEISHU_APP_SECRET")
FEISHU_FOLDER_TOKEN: str = _env("FEISHU_FOLDER_TOKEN")
FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"
FEISHU_BOT_WEBHOOK: str = _env("FEISHU_BOT_WEBHOOK")

# Sync
FORCE_FULL_SYNC: bool = _env("FORCE_FULL_SYNC", "false").lower() == "true"
DRY_RUN: bool = _env("DRY_RUN", "false").lower() == "true"
INITIAL_SYNC_LIMIT = 20

# Document
DOC_TITLE_FORMAT = "知识星球同步 - {year}年{month}月"

# Paths
STATE_FILE = "sync_state.json"
TEMP_DIR = "temp"
