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
        """Download a ZSXQ file. Tries call_zsxq_api then direct API call."""
        group_id = config.ZSXQ_GROUP_ID

        # Approach 1: call_zsxq_api with various paths
        for args in [
            {"method": "POST", "path": "/v2/files/download", "body": {"file_id": file_id}},
            {"method": "GET", "path": f"/v2/files/{file_id}/download"},
            {"method": "GET", "path": f"/v2/groups/{group_id}/files/{file_id}/download"},
        ]:
            try:
                result = self._rpc("tools/call", {
                    "name": "call_zsxq_api",
                    "arguments": json.dumps(args),
                })
                content = result.get("content", [])
                for item in content:
                    if item.get("type") == "text":
                        data = json.loads(item["text"])
                        if isinstance(data, dict):
                            download_url = data.get("download_url") or data.get("url", "")
                            if download_url:
                                resp = requests.get(download_url, timeout=120)
                                resp.raise_for_status()
                                with open(dest_path, "wb") as f:
                                    f.write(resp.content)
                                return True
                            b64 = data.get("content") or data.get("data", "")
                            if b64:
                                import base64
                                with open(dest_path, "wb") as f:
                                    f.write(base64.b64decode(b64))
                                return True
                logger.info("call_zsxq_api with args %s returned no downloadable content",
                            json.dumps(args)[:80])
            except ZsxqError as e:
                logger.warning("call_zsxq_api args=%s failed: %s",
                               json.dumps(args)[:80], e)
            except Exception as e:
                logger.warning("Download processing failed for args=%s: %s",
                               json.dumps(args)[:80], e)

        # Approach 2: Direct HTTP call to ZSXQ API with MCP API key
        try:
            api_url = f"https://api.zsxq.com/v2/files/{file_id}/download"
            headers = {
                "User-Agent": "zsxq-sync/1.0",
                "X-API-Key": config.ZSXQ_MCP_API_KEY,
                "Cookie": f"mcp_api_key={config.ZSXQ_MCP_API_KEY}",
            }
            resp = requests.get(api_url, headers=headers, timeout=120, stream=True)
            if resp.ok and len(resp.content) > 0:
                content_type = resp.headers.get("Content-Type", "")
                if "json" in content_type:
                    data = resp.json()
                    dl_url = data.get("download_url") or data.get("url", "")
                    if dl_url:
                        resp2 = requests.get(dl_url, timeout=120)
                        resp2.raise_for_status()
                        with open(dest_path, "wb") as f:
                            f.write(resp2.content)
                        return True
                else:
                    with open(dest_path, "wb") as f:
                        f.write(resp.content)
                    return True
            logger.warning("Direct API download failed: HTTP %s", resp.status_code)
        except Exception as e:
            logger.warning("Direct API download exception: %s", e)

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

    import ast
    import re

    def _parse_url(val):
        """Parse a URL from a dict, string-dict, or string value."""
        # Direct dict: {'url': 'https://...'}
        if isinstance(val, dict):
            return val.get("url", "") or val.get("large_url", "") or val.get("original_url", "")
        # String representation: "{'url': 'https://...'}"
        if isinstance(val, str) and val.strip().startswith("{"):
            try:
                d = ast.literal_eval(val)
                if isinstance(d, dict):
                    return d.get("url", "")
            except Exception as e:
                logger.debug("ast.literal_eval failed for %s...: %s", val[:100], e)
            # Fallback: regex extract https?:// URL from the string
            match = re.search(r"https?://[^\s'\"]+", val)
            if match:
                return match.group(0)
        # Plain URL string
        if isinstance(val, str) and val.startswith("http"):
            return val
        return ""

    _collect(obj)
    for img in candidates:
        if not isinstance(img, dict):
            continue
        # Try direct URL fields first, then thumbnail/large/original (which may be string-dicts)
        url = (
            img.get("large_url") or img.get("original_url") or img.get("url")
            or img.get("image_url") or img.get("source_url") or img.get("link") or ""
        )
        if not url:
            for size_key in ("large", "original", "thumbnail"):
                val = img.get(size_key, "")
                url = _parse_url(val)
                if url:
                    break
        name = (
            img.get("name") or img.get("file_name") or img.get("filename")
            or img.get("title") or ""
        ) or url.split("/")[-1].split("?")[0] if url else "image"
        if url and url not in {i["url"] for i in images}:
            logger.info("Extracted image: url=%s, name=%s", url[:80], name)
            images.append({"url": url, "filename": name or "image"})
    if candidates and not images:
        logger.warning("Image candidates found but no valid URL — candidate keys: %s",
                       [{k: str(v)[:80] for k, v in c.items()} for c in candidates[:3]])
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
