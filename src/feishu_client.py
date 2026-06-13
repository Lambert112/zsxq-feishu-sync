"""Feishu (飞书) API client — document management, media upload."""

import json
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
    # IM messaging (for @all support, webhooks can't do it)
    # ------------------------------------------------------------------

    def send_text_with_at_all(self, text: str) -> bool:
        """Send a text message with @all to all configured chat IDs."""
        if not config.FEISHU_CHAT_IDS:
            return False
        import json as _json
        content = _json.dumps({"text": f"<at user_id=\"all\">所有人</at> {text}"}, ensure_ascii=False)
        ok = False
        for cid in config.FEISHU_CHAT_IDS:
            try:
                self._request(
                    "POST",
                    f"/im/v1/messages?receive_id_type=chat_id",
                    body={"receive_id": cid, "msg_type": "text", "content": content},
                )
                logger.info("Sent @all message to chat %s", cid)
                ok = True
            except FeishuError as e:
                logger.warning("IM @all failed (chat=%s): %s", cid, e)
        return ok

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
            try:
                body = resp.json()
                feishu_code = body.get("code", resp.status_code)
                feishu_msg = body.get("msg", resp.text[:200])
            except Exception:
                feishu_code = resp.status_code
                feishu_msg = resp.text[:200]
            logger.error(
                "Feishu HTTP %s on %s %s: code=%s msg=%s",
                resp.status_code, method, path, feishu_code, feishu_msg,
            )
            raise FeishuError(
                feishu_code,
                f"{method} {path} failed: [{feishu_code}] {feishu_msg}",
                resp.status_code,
            )
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

    def transfer_ownership(self, document_id: str, user_id: str) -> bool:
        """Transfer document ownership to the user (requires drive:drive permission)."""
        if not user_id:
            return False
        member_type = "openid" if user_id.startswith("ou_") else "userid"
        try:
            self._request(
                "POST",
                f"/drive/v1/permissions/{document_id}/members/transfer_owner?type=docx",
                body={
                    "member_type": member_type,
                    "member_id": user_id,
                },
            )
            logger.info("Transferred ownership of %s to %s", document_id, user_id)
            return True
        except FeishuError as e:
            logger.warning("Failed to transfer ownership of %s: %s", document_id, e)
            # Fallback: try adding as full_access manager
            try:
                self._request(
                    "POST",
                    f"/drive/v1/permissions/{document_id}/members?type=docx",
                    body={
                        "member_type": member_type,
                        "member_id": user_id,
                        "perm": "full_access",
                    },
                )
                logger.info("Added %s as document manager (fallback): %s", user_id, document_id)
                return True
            except FeishuError as e2:
                logger.warning("Fallback add manager also failed: %s", e2)
                return False

    def add_document_manager(self, document_id: str, user_id: str) -> bool:
        """Add a user as full_access manager of a document."""
        if not user_id:
            return False
        # Detect if it's an open_id or user_id
        member_type = "openid" if user_id.startswith("ou_") else "userid"
        try:
            self._request(
                "POST",
                f"/drive/v1/permissions/{document_id}/members?type=docx",
                body={
                    "member_type": member_type,
                    "member_id": user_id,
                    "perm": "full_access",
                },
            )
            logger.info("Added %s as document manager: %s", user_id, document_id)
            return True
        except FeishuError:
            logger.warning("Failed to add manager (may need drive:drive permission)")
            return False

    def get_document(self, document_id: str) -> dict:
        """Get document info by ID."""
        return self._request("GET", f"/docx/v1/documents/{document_id}")

    def get_page_block_id(self, document_id: str) -> str:
        """Get the page (root) block ID of a document."""
        data = self._request(
            "GET",
            f"/docx/v1/documents/{document_id}/blocks",
            params={"page_size": 50},
        )
        items = data.get("items", [])
        for item in items:
            if item.get("block_type") == 1:  # page block
                logger.info("Found page block: %s", item["block_id"])
                return item["block_id"]
        # If no page block found, try document_id as parent
        logger.warning("No page block found, using document_id as parent")
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
                      parent_block_id: Optional[str] = None,
                      exclude_rich: bool = False) -> list[dict]:
        """Append blocks via children API. Rich blocks (image/file) must be placeholders."""
        if parent_block_id is None:
            parent_block_id = self.get_page_block_id(document_id)

        MAX_PER_BATCH = 20
        MAX_RETRIES = 3
        created = []

        for i in range(0, len(blocks), MAX_PER_BATCH):
            batch = blocks[i:i + MAX_PER_BATCH]
            logger.info(
                "Appending batch %d/%d (%d blocks) to document %s, parent %s",
                i // MAX_PER_BATCH + 1,
                (len(blocks) + MAX_PER_BATCH - 1) // MAX_PER_BATCH,
                len(batch),
                document_id,
                parent_block_id,
            )
            logger.info("First block sample: %s",
                        json.dumps(batch[0], ensure_ascii=False)[:500])

            for attempt in range(MAX_RETRIES):
                try:
                    data = self._request(
                        "POST",
                        f"/docx/v1/documents/{document_id}/blocks/{parent_block_id}/children"
                        f"?document_revision_id=-1",
                        body={"children": batch, "index": 0},
                    )
                    created.extend(data.get("children", []))
                    break
                except FeishuError as e:
                    if e.http_status == 400 and attempt < MAX_RETRIES - 1:
                        wait = 2 ** attempt
                        logger.warning(
                            "Batch append failed (attempt %d/%d), retrying in %ds...",
                            attempt + 1, MAX_RETRIES, wait,
                        )
                        time.sleep(wait)
                    else:
                        if attempt == MAX_RETRIES - 1:
                            logger.error(
                                "Failed batch content (all %d blocks): %s",
                                len(batch),
                                json.dumps(batch, ensure_ascii=False)[:3000],
                            )
                        raise

            if i + MAX_PER_BATCH < len(blocks):
                time.sleep(1)

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

    def clear_document(self, document_id: str) -> None:
        """Delete all children blocks from the page (keeps document alive)."""
        page_id = self.get_page_block_id(document_id)
        children = self.get_block_children(document_id, page_id)
        if not children:
            return
        BATCH = 50
        total = len(children)
        logger.info("Clearing %d blocks from %s", total, document_id)
        while total > 0:
            start = max(0, total - BATCH)
            self._request(
                "DELETE",
                f"/docx/v1/documents/{document_id}/blocks/{page_id}/children/batch_delete",
                body={"start_index": start, "end_index": total},
            )
            if start > 0:
                time.sleep(1)
            total = start

    def delete_document(self, document_id: str) -> bool:
        """Delete a document entirely via Drive API."""
        try:
            self._request(
                "DELETE",
                f"/drive/v1/files/{document_id}?type=docx",
            )
            logger.info("Deleted document: %s", document_id)
            return True
        except FeishuError as e:
            logger.warning("Failed to delete document %s: %s", document_id, e)
            return False

    # ------------------------------------------------------------------
    # Block update
    # ------------------------------------------------------------------

    def replace_image(self, document_id: str, block_id: str,
                      file_token: str) -> bool:
        """Replace an image block's token (refill after initial creation)."""
        try:
            self._request(
                "PATCH",
                f"/docx/v1/documents/{document_id}/blocks/{block_id}"
                f"?document_revision_id=-1",
                body={
                    "replace_image": {"token": file_token},
                },
            )
            logger.info("Image block %s refilled with token %s", block_id, file_token)
            return True
        except FeishuError as e:
            logger.error("Replace image failed for block %s: %s", block_id, e)
            return False

    def replace_file(self, document_id: str, block_id: str,
                     file_token: str, file_name: str = "") -> bool:
        """Replace a file block's token (refill after initial creation)."""
        try:
            self._request(
                "PATCH",
                f"/docx/v1/documents/{document_id}/blocks/{block_id}"
                f"?document_revision_id=-1",
                body={
                    "replace_file": {"token": file_token},
                },
            )
            logger.info("File block %s refilled with token %s", block_id, file_token)
            return True
        except FeishuError as e:
            logger.error("Replace file failed for block %s: %s", block_id, e)
            return False

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

        form_data = {
            "file_name": filename,
            "size": str(file_size),
        }
        if parent_type:
            form_data["parent_type"] = parent_type
        if parent_node:
            form_data["parent_node"] = parent_node

        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                data=form_data,
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

    def __init__(self, code: int, message: str, http_status: int = 0):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"[{code}] {message}")
