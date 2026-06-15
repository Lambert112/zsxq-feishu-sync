#!/usr/bin/env python3
"""将 Markdown 文件转换为飞书 Docx 文档。

用法：
    python md_to_feishu.py 徐少年和七个娃-拍摄企划方案.md

输出：飞书文档链接
"""

import re
import sys
import os

# Import src.feishu_client — src must be importable as a package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.feishu_client import FeishuClient, FeishuError


# ------------------------------------------------------------------
# Markdown → Feishu inline elements (text runs)
# ------------------------------------------------------------------

def parse_inline(text: str) -> list[dict]:
    """Parse Markdown inline formatting into Feishu text_run elements.

    Handles: **bold**, *italic*, `inline code`, ~~strikethrough~~, [links](url)
    """
    elements = []
    # Pattern order matters: longer/more specific first
    pattern = re.compile(
        r'(\*\*\*(.+?)\*\*\*|'         # ***bold italic***
         r'\*\*(.+?)\*\*|'              # **bold**
         r'\*(.+?)\*|'                  # *italic* (must be after bold)
         r'`([^`]+)`|'                  # `inline code`
         r'~~(.+?)~~|'                  # ~~strikethrough~~
         r'\[([^\]]+)\]\(([^)]+)\))'    # [text](url)
    )

    pos = 0
    for m in pattern.finditer(text):
        # Plain text before this match
        if m.start() > pos:
            plain = text[pos:m.start()]
            if plain:
                elements.append(text_run(plain))

        g = m.groups()
        # g[0] = outer alternation (always non-None), actual captures start at g[1]
        if g[1] is not None:  # ***bold italic***
            elements.append(text_run(g[1], bold=True, italic=True))
        elif g[2] is not None:  # **bold**
            elements.append(text_run(g[2], bold=True))
        elif g[3] is not None:  # *italic*
            elements.append(text_run(g[3], italic=True))
        elif g[4] is not None:  # `code`
            elements.append(text_run(g[4], inline_code=True))
        elif g[5] is not None:  # ~~strikethrough~~
            elements.append(text_run(g[5], strikethrough=True))
        elif g[6] is not None:  # [text](url)
            elements.append(text_run(g[6], link_url=g[7]))

        pos = m.end()

    # Remaining plain text
    if pos < len(text):
        plain = text[pos:]
        if plain:
            elements.append(text_run(plain))

    # If nothing matched, treat whole text as plain
    if not elements:
        elements.append(text_run(text))

    return elements


def text_run(content: str, bold: bool = False, italic: bool = False,
             strikethrough: bool = False, inline_code: bool = False,
             link_url: str = "", background_color: int = 0) -> dict:
    """Build a single text_run element."""
    style = {}
    if bold:
        style["bold"] = True
    if italic:
        style["italic"] = True
    if strikethrough:
        style["strikethrough"] = True
    if inline_code:
        style["inline_code"] = True
    if link_url:
        style["link"] = {"url": link_url}
    if background_color:
        style["background_color"] = background_color

    result = {"text_run": {"content": content[:5000]}}
    if style:
        result["text_run"]["text_element_style"] = style
    return result


# ------------------------------------------------------------------
# Block builders
# ------------------------------------------------------------------

def make_text_block(elements: list[dict]) -> dict:
    return {"block_type": 2, "text": {"elements": elements}}


def make_heading(level: int, elements: list[dict]) -> dict:
    """level 1-6 → block_type 3-8. Feishu supports up to heading9 (block_type 11)."""
    if level < 1:
        level = 1
    if level > 9:
        level = 9
    block_type = 2 + level  # h1=3, h2=4, ..., h9=11
    key = f"heading{level}"
    return {"block_type": block_type, key: {"elements": elements}}


def make_bullet(elements: list[dict]) -> dict:
    return {"block_type": 12, "bullet": {"elements": elements}}


def make_ordered(elements: list[dict]) -> dict:
    return {"block_type": 13, "ordered": {"elements": elements}}


def make_code(text: str, language: str = "") -> dict:
    return {
        "block_type": 14,
        "code": {"elements": [text_run(text, inline_code=True)],
                 "style": {"language": 1}},  # 1=plaintext
    }


def make_quote(elements: list[dict]) -> dict:
    return {"block_type": 15, "quote": {"elements": elements}}


def make_divider() -> dict:
    return {"block_type": 22, "divider": {}}


def make_callout(elements: list[dict], color: int = 14) -> dict:
    """color: 14=yellow (💡 tip), 6=blue (info), 2=red (warning)"""
    return {
        "block_type": 34,
        "callout": {"color": color, "elements": elements},
    }


# ------------------------------------------------------------------
# Markdown → Feishu blocks (main parser)
# ------------------------------------------------------------------

def md_to_blocks(md_text: str) -> list[dict]:
    """Convert Markdown text to a list of Feishu block dicts."""
    lines = md_text.split("\n")
    blocks = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # --- Empty line: skip ---
        if not line.strip():
            i += 1
            continue

        # --- Table detection (look ahead for |---|---| pattern) ---
        if "|" in line and i + 1 < len(lines) and re.match(r'^\|?[\s\-:|]+\|?$', lines[i + 1]):
            table_lines = [line]
            i += 2  # skip header + separator
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            blocks.extend(_parse_table(table_lines))
            continue

        # --- Code block (```) ---
        if line.strip().startswith("```"):
            code_lines = []
            lang = line.strip()[3:].strip()
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            code_text = "\n".join(code_lines)
            if len(code_text) > 5000:
                code_text = code_text[:5000]
            blocks.append(make_code(code_text, lang))
            continue

        # --- Divider (--- or *** or ___ on its own) ---
        if re.match(r'^[\\-\\*_]{3,}$', line.strip()):
            blocks.append(make_divider())
            i += 1
            continue

        # --- Headings (# ## ### etc.) ---
        heading_match = re.match(r'^(#{1,6})\s+(.+?)(?:\s+#+)?$', line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            blocks.append(make_heading(level, parse_inline(text)))
            i += 1
            continue

        # --- Unordered list (- * +) — strip links to avoid schema issues ---
        ul_match = re.match(r'^(\s*)[-\\*+]\s+(.+)$', line)
        if ul_match:
            indent = len(ul_match.group(1))
            text = ul_match.group(2).strip()
            text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
            blocks.append(make_bullet(parse_inline(text)))
            i += 1
            continue

        # --- Ordered list (1. 2) etc.) — strip links to avoid schema issues ---
        ol_match = re.match(r'^(\s*)\d+[.)]\s+(.+)$', line)
        if ol_match:
            text = ol_match.group(2).strip()
            # Remove markdown links: [text](url) → text
            text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
            blocks.append(make_ordered(parse_inline(text)))
            i += 1
            continue

        # --- Blockquote (> text) → plain text with prefix ---
        bq_match = re.match(r'^>\s?(.+)$', line)
        if bq_match:
            text = bq_match.group(1).strip()
            blocks.append(make_text_block(parse_inline(text)))
            i += 1
            continue

        # --- Callout (💡 / ⚠️ / ✅ / 📌 prefixed lines) ---
        callout_match = re.match(r'^(>?\s*[💡⚠️✅📌🔴🟡🟢🔵💎🔥🎯]\s*)(.+)$', line)
        if callout_match:
            prefix = callout_match.group(1)
            text = callout_match.group(2).strip()
            color = 14  # default yellow
            if "⚠️" in prefix or "🔴" in prefix:
                color = 2  # red
            elif "✅" in prefix or "🟢" in prefix:
                color = 4  # green
            elif "📌" in prefix or "🔵" in prefix:
                color = 6  # blue
            blocks.append(make_callout(parse_inline(text), color))
            i += 1
            continue

        # --- Regular paragraph ---
        text = line.strip()
        # Merge consecutive non-empty lines into one paragraph (until blank line)
        para_lines = [text]
        j = i + 1
        while j < len(lines) and lines[j].strip() and not _is_block_start(lines[j]):
            para_lines.append(lines[j].strip())
            j += 1

        merged = " ".join(para_lines)
        blocks.append(make_text_block(parse_inline(merged)))
        i = j

    return blocks


def _is_block_start(line: str) -> bool:
    """Check if a line starts a new block element."""
    s = line.strip()
    return bool(
        re.match(r'^#{1,6}\s', s) or
        re.match(r'^[-\\*+]\s', s) or
        re.match(r'^\d+[.)]\s', s) or
        re.match(r'^>\s?', s) or
        re.match(r'^```', s) or
        re.match(r'^[\\-\\*_]{3,}$', s) or
        "|" in s and re.match(r'^\|?[\s\-:|]+\|?$', s)  # likely table
    )


def _parse_table(lines: list[str]) -> list[dict]:
    """Convert Markdown table lines to formatted text blocks.

    Note: Feishu children API doesn't support creating nested table cells in one request.
    Table blocks require two-phase creation (table block first, then cell children).
    For simplicity, we render tables as bold-header text rows.
    """
    if len(lines) < 2:
        return [make_text_block(parse_inline(" ".join(lines)))]

    headers = _split_cells(lines[0])
    rows = [_split_cells(line) for line in lines[2:]]

    result = []
    for row_idx, row in enumerate([headers] + rows):
        cell_texts = [c.strip() for c in row]
        if row_idx == 0:
            line = " | ".join(f"**{c}**" for c in cell_texts)
        else:
            line = " | ".join(cell_texts)
        result.append(make_text_block(parse_inline(line)))

    return result


def _split_cells(line: str) -> list[str]:
    """Split a Markdown table row into cells."""
    # Remove leading/trailing pipes
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("用法: python md_to_feishu.py <markdown文件路径> [飞书文档标题]")
        sys.exit(1)

    md_path = sys.argv[1]
    if not os.path.exists(md_path):
        print(f"[ERROR] 文件不存在: {md_path}")
        sys.exit(1)

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Custom title from CLI arg, or extract from first H1, or use filename
    if len(sys.argv) >= 3:
        doc_title = sys.argv[2]
    else:
        title_match = re.search(r'^#\s+(.+)$', md_text, re.MULTILINE)
        doc_title = title_match.group(1).strip() if title_match else os.path.basename(md_path).replace(".md", "")

    print(f"📄 读取文件: {md_path}")
    print(f"📝 文档标题: {doc_title}")

    # Convert
    blocks = md_to_blocks(md_text)
    print(f"🧱 生成 {len(blocks)} 个飞书内容块")

    # Create document via Feishu API
    feishu = FeishuClient()
    try:
        print("🔗 正在连接飞书...")
        doc = feishu.create_document(doc_title)
        doc_id = doc["document"]["document_id"]
        doc_url = doc["document"].get("url") or f"https://bytedance.feishu.cn/docx/{doc_id}"
        print(f"📃 文档已创建: {doc_url}")

        print("✍️  正在写入内容...")
        created = feishu.append_blocks(doc_id, blocks)
        print(f"✅ 成功写入 {len(created)} 个内容块")

        print(f"\n🎉 完成！飞书文档链接:")
        print(f"   {doc_url}")

    except FeishuError as e:
        print(f"[ERROR] 飞书API错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] 未知错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
