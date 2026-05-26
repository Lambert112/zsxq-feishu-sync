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

        # Parse SSE: collect data lines from the last event
        text = resp.text
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

    def fetch_new_topics(self, last_topic_id: Optional[str] = None,
                         limit: Optional[int] = None) -> list[dict]:
        """Fetch recent topics via MCP, returning chronological (oldest first)."""
        self._init()

        limit = limit or 20
        # Use get_group_topics if available, fall back to search
        if "get_group_topics" in self._tools:
            return self._fetch_via_group_topics(last_topic_id, limit)
        if "search_topics" in self._tools:
            return self._fetch_via_search(last_topic_id, limit)
        # Generic tool call attempt
        return self._fetch_via_any_tool(last_topic_id, limit)

    def _fetch_via_group_topics(self, last_topic_id: Optional[str],
                                 limit: int) -> list[dict]:
        """Fetch using the get_group_topics MCP tool."""
        params: dict = {"group_id": self.group_id, "count": min(limit, 30)}
        result = self._rpc("tools/call", {
            "name": "get_group_topics",
            "arguments": params,
        })
        content = result.get("content", [])
        topics = []
        for item in content:
            if item.get("type") == "text":
                try:
                    data = json.loads(item["text"])
                    if isinstance(data, dict) and "topics" in data:
                        topics.extend(data["topics"])
                    elif isinstance(data, list):
                        topics.extend(data)
                except json.JSONDecodeError:
                    pass
        return self._filter_and_sort(topics, last_topic_id, limit)

    def _fetch_via_search(self, last_topic_id: Optional[str],
                           limit: int) -> list[dict]:
        """Fetch using search_topics MCP tool."""
        topics = []
        # Search with common keywords to get recent content
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
        return self._filter_and_sort(topics, last_topic_id, limit)

    def _fetch_via_any_tool(self, last_topic_id: Optional[str],
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
        return self._filter_and_sort(topics, last_topic_id, limit)

    def _filter_and_sort(self, topics: list[dict],
                         last_topic_id: Optional[str],
                         limit: int) -> list[dict]:
        """Filter out already-synced topics, dedupe, sort chronologically."""
        seen = set()
        filtered = []
        for t in topics:
            tid = t.get("topic_id", "")
            if tid == last_topic_id:
                break
            if tid and tid not in seen:
                seen.add(tid)
                filtered.append(t)

        # Sort by create_time ascending (oldest first)
        filtered.sort(key=lambda t: t.get("create_time", ""))
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

def extract_images(topic: dict) -> list[dict]:
    images = []
    talk = topic.get("talk", {}) or {}
    for img in talk.get("images", []) or []:
        if isinstance(img, dict):
            url = img.get("large_url") or img.get("original_url") or img.get("url", "")
            name = img.get("name", "") or url.split("/")[-1].split("?")[0]
            if url:
                images.append({"url": url, "filename": name})
    return images


def extract_files(topic: dict) -> list[dict]:
    files = []
    for container in [topic.get("files", []), topic.get("talk", {}).get("files", [])]:
        for f in (container or []):
            if isinstance(f, dict):
                url = f.get("download_url") or f.get("url", "")
                name = f.get("name", "") or url.split("/")[-1].split("?")[0]
                if url:
                    files.append({"url": url, "filename": name})
    return files
