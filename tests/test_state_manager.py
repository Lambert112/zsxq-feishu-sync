"""Tests for state_manager module."""

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.state_manager import load_state, save_state, update_last_topic


def test_load_state_default():
    state = load_state()
    assert state["last_topic_id"] is None
    assert state["last_sync_time"] is None


def test_save_and_load_state(tmp_path, monkeypatch):
    # Use a temp file instead of the real sync_state.json
    state_file = str(tmp_path / "sync_state.json")
    import src.state_manager as sm
    monkeypatch.setattr(sm, "STATE_FILE", state_file)

    test_state = {
        "last_topic_id": "test-123",
        "last_sync_time": 1234567890,
    }
    save_state(test_state)

    loaded = load_state()
    assert loaded["last_topic_id"] == "test-123"


def test_update_last_topic(tmp_path, monkeypatch):
    state_file = str(tmp_path / "sync_state.json")
    import src.state_manager as sm
    monkeypatch.setattr(sm, "STATE_FILE", state_file)

    state = update_last_topic("topic-456")
    assert state["last_topic_id"] == "topic-456"
    assert state["last_sync_time"] is not None
