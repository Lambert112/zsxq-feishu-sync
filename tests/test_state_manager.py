"""Tests for state_manager module."""

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.state_manager import load_state, save_state, update_sync_time


def test_load_state_default():
    state = load_state()
    assert state["last_sync_time"] is None
    assert state["current_doc_id"] is None
    assert state["date_headers"] == {}
    assert state["file_summary_count"] == 0
    assert state["file_summary"] == {}
    assert state["file_tokens"] == {}


def test_save_and_load_state(tmp_path, monkeypatch):
    # Use a temp file instead of the real sync_state.json
    state_file = str(tmp_path / "sync_state.json")
    import src.state_manager as sm
    monkeypatch.setattr(sm, "STATE_FILE", state_file)

    test_state = {
        "last_sync_time": 1234567890,
        "current_doc_id": "doc-abc",
        "current_doc_month": "2026-06",
        "date_headers": {},
        "file_summary_count": 0,
        "file_summary": {},
        "file_tokens": {},
    }
    save_state(test_state)

    loaded = load_state()
    assert loaded["current_doc_id"] == "doc-abc"
    assert loaded["current_doc_month"] == "2026-06"


def test_update_sync_time(tmp_path, monkeypatch):
    state_file = str(tmp_path / "sync_state.json")
    import src.state_manager as sm
    monkeypatch.setattr(sm, "STATE_FILE", state_file)

    state = update_sync_time()
    assert state["last_sync_time"] is not None
    assert isinstance(state["last_sync_time"], int)
