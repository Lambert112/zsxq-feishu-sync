"""Feishu bot notifications for errors and status updates."""

import json
import logging
import os

import requests

from . import config

logger = logging.getLogger(__name__)


def send_auth_error(detail: str = "") -> None:
    """Notify user that ZSXQ auth (MCP API key) has failed."""
    webhook = config.FEISHU_BOT_WEBHOOK
    if not webhook:
        logger.warning("未配置 FEISHU_BOT_WEBHOOK，跳过通知")
        return

    body = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "ZSXQ API 认证失败"},
                "template": "red",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            "知识星球 MCP API Key 认证失败，请检查 "
                            "GitHub Secret `ZSXQ_MCP_API_KEY` 是否正确。\n"
                            f"详情: {detail}" if detail else
                            "知识星球 MCP API Key 认证失败，请检查 GitHub Secret `ZSXQ_MCP_API_KEY`。"
                        ),
                    },
                }
            ],
        },
    }
    _post(webhook, body)


def send_error(message: str, doc_id: str = "") -> None:
    """Send a generic error notification."""
    webhook = config.FEISHU_BOT_WEBHOOK
    if not webhook:
        logger.warning("未配置 FEISHU_BOT_WEBHOOK，跳过通知")
        return

    run_url = ""
    github_server = os.environ.get("GITHUB_SERVER_URL", "")
    github_repo = os.environ.get("GITHUB_REPOSITORY", "")
    github_run_id = os.environ.get("GITHUB_RUN_ID", "")
    if github_server and github_repo and github_run_id:
        run_url = f"{github_server}/{github_repo}/actions/runs/{github_run_id}"

    text = f"ZSXQ 同步失败\n\n{message}"
    if doc_id:
        text += f"\n\n📄 [查看文档](https://larkcommunity.feishu.cn/docx/{doc_id})"
    if run_url:
        text += f"\n\n🔧 [查看日志]({run_url})"

    _post(webhook, {
        "msg_type": "text",
        "content": {"text": text},
    })


def send_sync_summary(new_count: int, doc_id: str) -> None:
    """Send a success summary with document link."""
    webhook = config.FEISHU_BOT_WEBHOOK
    if not webhook:
        return
    if new_count == 0:
        return

    doc_url = f"https://larkcommunity.feishu.cn/docx/{doc_id}" if doc_id else ""
    text = f"知识星球同步完成\n新增帖子：{new_count} 条"
    if doc_url:
        text += f"\n\n📄 [查看文档]({doc_url})"

    _post(webhook, {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "知识星球同步完成"},
                "template": "green",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"新增 **{new_count}** 条帖子\n\n[📄 查看文档]({doc_url})" if doc_url else f"新增 **{new_count}** 条帖子",
                    },
                },
            ],
        },
    })


def _post(webhook: str, body: dict) -> None:
    try:
        resp = requests.post(webhook, json=body, timeout=15)
        resp.raise_for_status()
    except Exception:
        logger.warning("发送飞书通知失败", exc_info=True)
