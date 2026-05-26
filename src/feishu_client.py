"""Feishu (飞书) API client — document management, media upload."""

import logging
import time
from typing import Optional

import requests

from . import config

logger = logging.getLogger(__name__)


class FeishuClient:
    """Client for Feishu Open API (Docx + Drive)."""

    def __init__(self):
        self.app_id = config.FEISHU_APP_ID
        self.app_secret = config.FEISHU_APP_SECRET
        self.folder_token = config.FEISHU_FOLDER_TOKEN
        self._token: Optional[str] = None
        self._token_expiry: float = 0

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expiry - 60:
            return self._token

        resp = requests.post(
            f"{config.FEISHU_BASE_URL}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["tenant_access_token"]
        self._token_expiry = now + data.get("expire", 7200)
        return self._token

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str,
                 body: Optional[dict] = None,
                 params: Optional[dict] = None) -> dict:
        token = self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        url = f"{config.FEISHU_BASE_URL}{path}"
        resp = requests.request(
            method, url, json=body, params=params,
            headers=headers, timeout=30,
        )
        if not resp.ok:
            logger.error(
                "Feishu HTTP %s on %s %s: %s",
                resp.status_code, method, path, resp.text[:500],
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", -1) != 0:
            raise FeishuError(
                data.get("code", -1),
                data.get("msg", "未知错误"),
            )
        return data.get("data", {})

    # ------------------------------------------------------------------
    # Document operations
    # ------------------------------------------------------------------

    def create_document(self, title: str) -> dict:
        """Create a new Docx document."""
        return self._request("POST", "/docx/v1/documents", body={
            "title": title,
        })

    def get_document(self, document_id: str) -> dict:
        """Get document info by ID."""
        return self._request("GET", f"/docx/v1/documents/{document_id}")

    def get_page_block_id(self, document_id: str) -> str:
        """Get the page (root) block ID of a document."""
        data = self._request(
            "GET",
            f"/docx/v1/documents/{document_id}/blocks",
            params={"page_size": 1},
        )
        items = data.get("items", [])
        if items:
            return items[0]["block_id"]
        # Fallback: document_id itself can be used as the page block reference
        return document_id

    def create_monthly_doc(self, year: int, month: int) -> str:
        """Create a new monthly document. Returns document_id."""
        title = config.DOC_TITLE_FORMAT.format(year=year, month=month)
        logger.info("创建月度文档: %s", title)
        doc = self.create_document(title)
        return doc["document"]["document_id"]

    # ------------------------------------------------------------------
    # Block operations
    # ------------------------------------------------------------------

    def append_blocks(self, document_id: str, blocks: list[dict],
                      parent_block_id: Optional[str] = None) -> list[dict]:
        """Append blocks as children of parent_block_id (default: page root)."""
        if parent_block_id is None:
            parent_block_id = self.get_page_block_id(document_id)

        # Feishu API has a limit on block children per request (typically 50)
        # But document blocks have a more restrictive limit for descendants
        # We send all blocks in one call for simplicity
        MAX_PER_BATCH = 20
        created = []

        for i in range(0, len(blocks), MAX_PER_BATCH):
            batch = blocks[i:i + MAX_PER_BATCH]
            data = self._request(
                "POST",
                f"/docx/v1/documents/{document_id}/blocks/{parent_block_id}/children",
                body={"children": batch},
            )
            created.extend(data.get("children", []))
            if i + MAX_PER_BATCH < len(blocks):
                time.sleep(0.5)

        return created

    def get_block_children(self, document_id: str,
                           block_id: str) -> list[dict]:
        """Get direct children of a block."""
        children = []
        page_token = ""
        while True:
            params = {"page_size": 50}
            if page_token:
                params["page_token"] = page_token
            path = f"/docx/v1/documents/{document_id}/blocks/{block_id}/children"
            data = self._request("GET", path, params=params)
            children.extend(data.get("items", []))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
        return children

    # ------------------------------------------------------------------
    # Media upload
    # ------------------------------------------------------------------

    def upload_media(self, file_path: str, filename: str,
                     parent_type: str = "docx_image",
                     parent_node: str = "") -> Optional[str]:
        """Upload a file to Feishu Drive. Returns file_token on success."""
        import os
        file_size = os.path.getsize(file_path)

        token = self._get_token()
        url = f"{config.FEISHU_BASE_URL}/drive/v1/medias/upload_all"

        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                data={
                    "file_name": filename,
                    "parent_type": parent_type,
                    "parent_node": parent_node or self.folder_token,
                    "size": str(file_size),
                },
                files={"file": (filename, f, "application/octet-stream")},
                timeout=120,
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", -1) != 0:
            logger.error("上传媒体失败: %s", data.get("msg", ""))
            return None
        return data["data"]["file_token"]


class FeishuError(Exception):
    """Feishu API error."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
