"""ZSXQ client via official MCP endpoint — no cookies, no scraping."""

import json
import logging
import os
import time
from typing import Optional

import requests

from . import config

logger = logging.getLogger(__name__)


class ZsxqClient:
    """Client for ZSXQ via MCP (JSON-RPC over HTTP/SSE)."""

    def __init__(self):
        self.mcp_url = (
            f"https://mcp.zsxq.com/topic/mcp?api_key={config.ZSXQ_MCP_API_KEY}"
        )
        self.group_id = config.ZSXQ_GROUP_ID
        self._req_id = 0
        self._tools: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # MCP protocol (SSE transport)
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        """Send a JSON-RPC request and parse the SSE response."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {},
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        resp = requests.post(
            self.mcp_url, json=payload, headers=headers, timeout=30,
        )
        resp.raise_for_status()

        # Parse SSE: collect data lines. Use resp.content and decode
        # explicitly as UTF-8 because the SSE Content-Type may not
        # declare charset, and resp.text defaults to ISO-8859-1.
        text = resp.content.decode("utf-8")
        data_payload = ""
        for block in text.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            for line in block.split("\n"):
                if line.startswith("data: "):
                    data_payload += line[6:]
        if not data_payload:
            raise ZsxqError(-1, f"Empty SSE response for {method}")
        result = json.loads(data_payload)
        if "error" in result:
            err = result["error"]
            raise ZsxqError(err.get("code", -1), err.get("message", "MCP error"))
        return result.get("result", {})

    def _init(self) -> None:
        """Initialize MCP session and discover tools."""
        if self._tools:
            return  # already initialized

        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "zsxq-sync", "version": "1.0.0"},
        })

        result = self._rpc("tools/list")
        for tool in result.get("tools", []):
            self._tools[tool["name"]] = tool
        logger.info("MCP tools discovered: %s", list(self._tools.keys()))

    # ------------------------------------------------------------------
    # High-level API
    # ------------------------------------------------------------------

    def check_auth(self) -> bool:
        """Test whether the API key is valid."""
        try:
            self._init()
            return len(self._tools) > 0
        except Exception as e:
            logger.warning("MCP auth check failed: %s", e)
            return False

    def fetch_new_topics(self, last_sync_time: Optional[int] = None,
                         limit: Optional[int] = None) -> list[dict]:
        """Fetch topics newer than last_sync_time (Unix timestamp)."""
        self._init()

        limit = limit or 20
        if "get_group_topics" in self._tools:
            return self._fetch_via_group_topics(last_sync_time, limit)
        if "search_topics" in self._tools:
            return self._fetch_via_search(last_sync_time, limit)
        return self._fetch_via_any_tool(last_sync_time, limit)

    def _fetch_via_group_topics(self, last_sync_time: Optional[int],
                                 limit: int) -> list[dict]:
        """Fetch using get_group_topics, then enrich with get_topic_info."""
        result = self._rpc("tools/call", {
            "name": "get_group_topics",
            "arguments": {"group_id": self.group_id, "count": min(limit, 30)},
        })
        content = result.get("content", [])
        brief_topics = []
        for item in content:
            if item.get("type") == "text":
                try:
                    data = json.loads(item["text"])
                    if isinstance(data, dict):
                        if "topics_brief" in data:
                            brief_topics.extend(data["topics_brief"])
                        elif "topics" in data:
                            brief_topics.extend(data["topics"])
                except json.JSONDecodeError:
                    pass

        # Enrich with full topic details
        topics = []
        for bt in brief_topics:
            tid = bt.get("topic_id", "")
            detail = self._get_topic_detail(tid)
            if detail:
                topics.append(detail)
            else:
                # Fall back to brief info
                topics.append(bt)
            time.sleep(0.3)  # gentle rate limit

        logger.info("Parsed %d topics from get_group_topics", len(topics))
        if topics:
            sample = topics[0]
            logger.info("Sample topic keys: %s", list(sample.keys()))
            # Log any topic that has non-empty images/files to understand structure
            for t in topics:
                imgs = t.get("images") or []
                fls = t.get("files") or []
                if imgs or fls:
                    logger.info("Topic %s has images=%s, files=%s",
                                t.get("topic_id"),
                                json.dumps(imgs[:1], ensure_ascii=False)[:300] if imgs else "[]",
                                json.dumps(fls[:1], ensure_ascii=False)[:300] if fls else "[]")
                    break
            else:
                logger.info("No topics have images or files in raw fields — checking nested locations")
            img_count = len(extract_images(sample))
            file_count = len(extract_files(sample))
            logger.info("Sample topic extract: %d images, %d files", img_count, file_count)
        return self._filter_new(topics, last_sync_time, limit)

    def _get_topic_detail(self, topic_id: str) -> Optional[dict]:
        """Fetch full topic detail via get_topic_info MCP tool."""
        try:
            result = self._rpc("tools/call", {
                "name": "get_topic_info",
                "arguments": {"topic_id": topic_id},
            })
            content = result.get("content", [])
            for item in content:
                if item.get("type") == "text":
                    data = json.loads(item["text"])
                    if isinstance(data, dict):
                        if "topic" in data:
                            return data["topic"]
                        return data
        except Exception:
            logger.debug("Failed to get detail for topic %s", topic_id)
        return None

    def download_file(self, file_id: str, dest_path: str) -> bool:
        """Download a ZSXQ file via call_zsxq_api. Returns True on success."""
        try:
            result = self._rpc("tools/call", {
                "name": "call_zsxq_api",
                "arguments": json.dumps({
                    "method": "GET",
                    "path": f"/files/{file_id}/download",
                }),
            })
            content = result.get("content", [])
            for item in content:
                if item.get("type") == "text":
                    data = json.loads(item["text"])
                    if isinstance(data, dict):
                        # Response may contain download_url or base64 content
                        download_url = data.get("download_url") or data.get("url", "")
                        if download_url:
                            # Fetch the actual file from the download URL
                            resp = requests.get(download_url, timeout=120)
                            resp.raise_for_status()
                            with open(dest_path, "wb") as f:
                                f.write(resp.content)
                            return True
                        # Maybe base64 encoded content
                        b64 = data.get("content") or data.get("data", "")
                        if b64:
                            import base64
                            with open(dest_path, "wb") as f:
                                f.write(base64.b64decode(b64))
                            return True
            logger.warning("call_zsxq_api download returned unexpected format for file %s", file_id)
        except Exception as e:
            logger.warning("Failed to download file %s via call_zsxq_api: %s", file_id, e)
        return False

    def _fetch_via_search(self, last_sync_time: Optional[int],
                           limit: int) -> list[dict]:
        """Fetch using search_topics MCP tool."""
        topics = []
        for keyword in ["", " ", "."]:
            result = self._rpc("tools/call", {
                "name": "search_topics",
                "arguments": {"group_id": self.group_id, "query": keyword},
            })
            content = result.get("content", [])
            for item in content:
                if item.get("type") == "text":
                    try:
                        data = json.loads(item["text"])
                        if isinstance(data, dict) and "topics" in data:
                            topics.extend(data["topics"])
                    except json.JSONDecodeError:
                        pass
            if len(topics) >= limit:
                break
        return self._filter_new(topics, last_sync_time, limit)

    def _fetch_via_any_tool(self, last_sync_time: Optional[int],
                             limit: int) -> list[dict]:
        """Try any available tool that returns topics."""
        topics = []
        for name in self._tools:
            if name in ("get_topic", "get_topic_comments"):
                continue
            try:
                result = self._rpc("tools/call", {
                    "name": name,
                    "arguments": {"group_id": self.group_id},
                })
                content = result.get("content", [])
                for item in content:
                    if item.get("type") == "text":
                        try:
                            data = json.loads(item["text"])
                            if isinstance(data, dict):
                                if "topics" in data:
                                    topics.extend(data["topics"])
                                elif "topic_id" in data:
                                    topics.append(data)
                        except json.JSONDecodeError:
                            pass
                if topics:
                    break
            except ZsxqError:
                continue
        return self._filter_new(topics, last_sync_time, limit)

    def _filter_new(self, topics: list[dict],
                    last_sync_time: Optional[int],
                    limit: int) -> list[dict]:
        """Keep only topics newer than last_sync_time, dedupe, sort newest-first."""
        seen = set()
        filtered = []
        for t in topics:
            tid = t.get("topic_id", "")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            create_time_str = t.get("create_time", "")
            create_ts = _parse_time_to_ts(create_time_str)
            if last_sync_time is not None and create_ts <= last_sync_time:
                continue
            filtered.append(t)

        filtered.sort(key=lambda t: t.get("create_time", ""), reverse=True)
        return filtered[:limit]


class ZsxqError(Exception):
    def __init__(self, code: int, message: str, http_status: int = 0):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"[{code}] {message}")


# ------------------------------------------------------------------
# Media extraction helpers (same as before)
# ------------------------------------------------------------------

def _parse_time_to_ts(create_time_str: str) -> int:
    """Parse ZSXQ create_time string to Unix timestamp."""
    from datetime import datetime, timezone, timedelta
    if not create_time_str:
        return 0
    try:
        if "T" in create_time_str:
            dt = datetime.fromisoformat(create_time_str)
        else:
            dt = datetime.strptime(create_time_str, "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo:
            return int(dt.timestamp())
        # Assume Beijing time if no tz
        CST = timezone(timedelta(hours=8))
        return int(dt.replace(tzinfo=CST).timestamp())
    except (ValueError, TypeError):
        return 0


def _lookup_images(obj: dict) -> list[dict]:
    """Recursively collect image dicts from known ZSXQ nesting patterns."""
    images = []
    candidates = []

    def _collect(d, depth=0):
        if depth > 3 or not isinstance(d, dict):
            return
        for key in ("images", "image_list", "pictures", "attachments"):
            vals = d.get(key)
            if isinstance(vals, list):
                candidates.extend(vals)
        for sub in ("talk", "question", "answer", "author", "owner"):
            if isinstance(d.get(sub), dict):
                _collect(d[sub], depth + 1)

    _collect(obj)
    for img in candidates:
        if not isinstance(img, dict):
            continue
        url = (
            img.get("large_url") or img.get("original_url") or img.get("url")
            or img.get("image_url") or img.get("source_url") or img.get("link") or ""
        )
        name = (
            img.get("name") or img.get("file_name") or img.get("filename")
            or img.get("title") or ""
        ) or url.split("/")[-1].split("?")[0]
        if url and url not in {i["url"] for i in images}:
            logger.debug("Extracted image: url=%s, name=%s", url[:80], name)
            images.append({"url": url, "filename": name or "image"})
    if candidates and not images:
        logger.warning("Image candidates found but no valid URL — candidate keys: %s",
                       [{k: str(v)[:60] for k, v in c.items()} for c in candidates[:3]])
    return images


def extract_images(topic: dict) -> list[dict]:
    return _lookup_images(topic)


def extract_files(topic: dict) -> list[dict]:
    """Extract file attachments from topic, checking nested locations."""
    files = []
    candidates = []

    def _collect(d, depth=0):
        if depth > 3 or not isinstance(d, dict):
            return
        for key in ("files", "file_list", "attachments"):
            vals = d.get(key)
            if isinstance(vals, list):
                candidates.extend(vals)
        for sub in ("talk", "question", "answer"):
            if isinstance(d.get(sub), dict):
                _collect(d[sub], depth + 1)

    _collect(topic)
    for f in candidates:
        if not isinstance(f, dict):
            continue
        # Try all known URL field names, then construct from file_id
        url = (
            f.get("download_url") or f.get("url") or f.get("file_url")
            or f.get("source_url") or f.get("link") or ""
        )
        file_id = f.get("file_id", "")
        if not url and file_id:
            # Construct ZSXQ file download URL via MCP
            url = f"zsxq://file/{file_id}"
        name = (
            f.get("name") or f.get("file_name") or f.get("filename")
            or f.get("title") or ""
        ) or url.split("/")[-1].split("?")[0]
        if url and url not in {fl["url"] for fl in files}:
            logger.info("Extracted file: name=%s, url=%s", name, url[:80])
            files.append({"url": url, "filename": name or "file", "file_id": file_id})
    if candidates and not files:
        logger.warning("File candidates found but no valid identifier: %s",
                       [{k: str(v)[:60] for k, v in c.items()} for c in candidates[:3]])
    return files
