"""ZSXQ (知识星球) API client — fetch topics, download attachments."""

import hashlib
import logging
import os
import time
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import requests

from . import config

logger = logging.getLogger(__name__)


class ZsxqClient:
    """Client for ZSXQ internal API."""

    def __init__(self):
        self.cookie = config.ZSXQ_COOKIE
        self.group_id = config.ZSXQ_GROUP_ID
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Cookie": self.cookie,
        })

    # ------------------------------------------------------------------
    # Signature
    # ------------------------------------------------------------------

    def _sign(self, path: str, params: dict) -> tuple[str, str]:
        """Generate X-Signature and X-Timestamp for a request."""
        ts_ms = str(int(time.time() * 1000))
        all_params = {**params, "timestamp": ts_ms}
        sorted_str = "&".join(
            f"{k}={v}" for k, v in sorted(all_params.items())
        )
        sign_str = f"{path}&{sorted_str}&{config.ZSXQ_SECRET}&{ts_ms}"
        signature = hashlib.md5(sign_str.encode()).hexdigest()
        return signature, ts_ms

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        params = params or {}
        params.setdefault("app_version", config.ZSXQ_APP_VERSION)
        params.setdefault("platform", config.ZSXQ_PLATFORM)

        sig, ts = self._sign(path, params)

        headers = {
            "X-Signature": sig,
            "X-Timestamp": ts,
            "X-Version": config.ZSXQ_APP_VERSION,
        }

        url = f"{config.ZSXQ_BASE_URL}{path}"
        resp = self.session.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("succeeded"):
            code = data.get("code", -1)
            msg = data.get("msg", "未知错误")
            raise ZsxqError(code, msg, resp.status_code)

        return data

    # ------------------------------------------------------------------
    # Topics
    # ------------------------------------------------------------------

    def fetch_new_topics(self, last_topic_id: Optional[str] = None,
                         limit: Optional[int] = None) -> list[dict]:
        """Fetch topics newer than `last_topic_id`, newest first.

        Returns topics in chronological order (oldest first) for
        convenient appending to documents.
        """
        all_topics = []
        end_time = ""
        max_pages = 50  # safety limit

        for _ in range(max_pages):
            params = {
                "scope": "all",
                "count": "20",
            }
            if end_time:
                params["end_time"] = end_time

            data = self._get(
                f"/groups/{self.group_id}/topics",
                params,
            )
            resp_data = data.get("resp_data", {})
            topics = resp_data.get("topics", [])
            if not topics:
                break

            for topic in topics:
                topic_id = topic.get("topic_id", "")
                if topic_id == last_topic_id:
                    # Reached already-synced content; return chronological
                    return list(reversed(all_topics))

                all_topics.append(topic)

                if limit and len(all_topics) >= limit:
                    return list(reversed(all_topics))

            # Paginate: use the last topic's create_time
            end_time = topics[-1].get("create_time", "")
            if not end_time:
                break

            time.sleep(config.ZSXQ_REQUEST_DELAY)

        return list(reversed(all_topics))

    def check_auth(self) -> bool:
        """Test whether the cookie is still valid."""
        try:
            self._get(f"/groups/{self.group_id}/topics", {"scope": "all", "count": "1"})
            return True
        except ZsxqError as e:
            if e.code in (401, 403, 1000, 1001):
                return False
            raise

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def download_attachment(self, url: str, dest_dir: str) -> Optional[str]:
        """Download a file from ZSXQ CDN. Returns the local file path."""
        os.makedirs(dest_dir, exist_ok=True)
        filename = url.split("/")[-1].split("?")[0] or "attachment"
        filepath = os.path.join(dest_dir, filename)

        try:
            resp = self.session.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            return filepath
        except Exception:
            logger.warning("下载附件失败: %s", url, exc_info=True)
            return None


class ZsxqError(Exception):
    """ZSXQ API error."""

    def __init__(self, code: int, message: str, http_status: int = 0):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"[{code}] {message}")


# ------------------------------------------------------------------
# Convenience: extract media URLs from a topic dict
# ------------------------------------------------------------------

def extract_images(topic: dict) -> list[dict]:
    """Return list of {url, filename} dicts for images in a topic."""
    images = []
    talk = topic.get("talk", {}) or {}
    # Images can be in talk.images (array of image objects)
    for img in talk.get("images", []) or []:
        if isinstance(img, dict):
            url = img.get("large_url") or img.get("original_url") or img.get("url", "")
            name = img.get("name", "") or url.split("/")[-1].split("?")[0]
            if url:
                images.append({"url": url, "filename": name})
    return images


def extract_files(topic: dict) -> list[dict]:
    """Return list of {url, filename, content_type} for file attachments."""
    files = []
    # Files can be in topic.files or topic.talk.files
    for container in [topic.get("files", []), topic.get("talk", {}).get("files", [])]:
        for f in (container or []):
            if isinstance(f, dict):
                url = f.get("download_url") or f.get("url", "")
                name = f.get("name", "") or url.split("/")[-1].split("?")[0]
                if url:
                    files.append({"url": url, "filename": name})
    return files
