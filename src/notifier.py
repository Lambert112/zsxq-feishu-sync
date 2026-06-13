"""Feishu bot notifications for errors and status updates."""

import json
import logging
import os

import requests

from . import config

logger = logging.getLogger(__name__)


def send_auth_error(detail: str = "") -> None:
    """Notify user that ZSXQ auth (MCP API key) has failed."""
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
    _post_all(body)


def send_error(message: str, doc_id: str = "") -> None:
    """Send a generic error notification."""
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

    _post_all({
        "msg_type": "text",
        "content": {"text": text},
    })


def send_sync_summary(new_count: int, doc_id: str,
                      topic_summaries: list[dict] | None = None) -> None:
    """Send sync summary (first) then per-topic cards to all webhooks."""
    if new_count == 0:
        return

    doc_url = f"https://larkcommunity.feishu.cn/docx/{doc_id}" if doc_id else ""

    # 1. Summary — text message for @all support
    text = f"知识星球同步完成\n新增 {new_count} 条帖子"
    if doc_url:
        text += f"\n\n📄 查看文档: {doc_url}"
    text += "\n\n<at id=all></at>"
    _post_all({
        "msg_type": "text",
        "content": {"text": text},
    })

    # 2. Per-topic full content cards
    if topic_summaries:
        import time as _time
        for i, ts in enumerate(topic_summaries):
            if i > 0:
                _time.sleep(0.5)

            time_str = ts.get("time", "")
            date_str = ts.get("date", "")
            body_text = ts.get("body", "")

            content = f"**{time_str}** {date_str}\n\n{body_text}"
            if len(content) > 10000:
                content = content[:10000] + "\n\n...[内容过长已截断]"

            _post_all({
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": f"{date_str} {time_str}"},
                        "template": "blue",
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": {"tag": "lark_md", "content": content},
                        },
                        {
                            "tag": "hr",
                        },
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": f"[📄 查看完整内容]({doc_url})",
                            },
                        },
                    ],
                },
            })


def _get_webhooks() -> list[str]:
    """Return all configured webhook URLs."""
    hooks = list(config.FEISHU_BOT_WEBHOOKS)
    if config.FEISHU_BOT_WEBHOOK and config.FEISHU_BOT_WEBHOOK not in hooks:
        hooks.insert(0, config.FEISHU_BOT_WEBHOOK)
    return hooks


def _post_all(body: dict) -> None:
    """Send to all configured webhooks."""
    hooks = _get_webhooks()
    if not hooks:
        logger.warning("未配置任何 FEISHU_BOT_WEBHOOK，跳过通知")
        return
    for webhook in hooks:
        try:
            resp = requests.post(webhook, json=body, timeout=15)
            resp.raise_for_status()
        except Exception:
            logger.warning("发送飞书通知失败 (webhook=%s...)", webhook[:50], exc_info=True)


def _post(webhook: str, body: dict) -> None:
    try:
        resp = requests.post(webhook, json=body, timeout=15)
        resp.raise_for_status()
    except Exception:
        logger.warning("发送飞书通知失败", exc_info=True)
