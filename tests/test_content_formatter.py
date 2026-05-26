"""Tests for content_formatter module."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.content_formatter import (
    format_time,
    get_date_key,
    get_month_key,
    build_h1,
    build_h2,
    build_text,
    build_divider,
    build_date_header_block,
)


def test_format_time_iso():
    result = format_time("2026-05-26T14:30:25.000+0800")
    assert "2026-05-26 14:30" == result


def test_format_time_invalid():
    result = format_time("bad-time")
    assert result == "bad-time"


def test_get_date_key():
    assert get_date_key("2026-05-26T14:30:25.000+0800") == "2026-05-26"


def test_get_date_key_invalid():
    assert get_date_key("bad") == "unknown"


def test_get_month_key():
    year, month = get_month_key("2026-05-26T14:30:25.000+0800")
    assert year == 2026
    assert month == 5


def test_build_h1():
    block = build_h1("2026-05-26")
    assert block["block_type"] == 3
    assert block["heading1"]["elements"][0]["text_run"]["content"] == "2026-05-26"


def test_build_h2():
    block = build_h2("Test Title")
    assert block["block_type"] == 4
    assert block["heading2"]["elements"][0]["text_run"]["content"] == "Test Title"


def test_build_text():
    block = build_text("Hello World")
    assert block["block_type"] == 2
    assert block["text"]["elements"][0]["text_run"]["content"] == "Hello World"


def test_build_divider():
    block = build_divider()
    assert block["block_type"] == 22


def test_build_date_header_block():
    blocks = build_date_header_block("2026-05-26")
    assert len(blocks) == 1
    assert blocks[0]["block_type"] == 3
