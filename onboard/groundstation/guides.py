"""The guides (GUIDES.md) as pages for the Ground Station's Guides tab.

GUIDES.md is the one source: readable on GitHub and in the software folder,
and shown in the app. Each "## Title {#id}" starts a guide. Only the plain
Markdown the guides use is converted: headings, paragraphs, lists (also
nested), tables, **bold**, *italic* and `code`. Everything is HTML-escaped
first, so the file can't inject markup.
"""

from __future__ import annotations

import html
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
GUIDES_MD = os.path.join(os.path.dirname(HERE), "GUIDES.md")
HEAD = re.compile(r"^## (.+?)\s*\{#([a-z0-9-]+)\}\s*$")
LIST = re.compile(r"^(\s*)(?:([-*])|(\d+)\.)\s+(.*)$")


def inline(text: str) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    return t


def _join_items(lines: list[str]) -> list[str]:
    """A list item's wrapped lines onto the item's own line (so **bold** can wrap)."""
    out: list[str] = []
    for ln in lines:
        if (out and ln.startswith(" ") and ln.strip() and not LIST.match(ln)
                and LIST.match(out[-1])):
            out[-1] += " " + ln.strip()
        else:
            out.append(ln)
    return out


def to_html(md: str) -> str:
    lines = _join_items(md.split("\n"))
    out: list[str] = []
    para: list[str] = []
    stack: list[tuple[int, str]] = []        # open lists: (indent, "ul"|"ol")
    i = 0

    def flush_para():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    def close_lists(to_indent=-1):
        while stack and stack[-1][0] > to_indent:
            out.append(f"</li></{stack.pop()[1]}>")

    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            flush_para()
            i += 1
            continue
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            flush_para()
            close_lists()
            cells = lambda row: [c.strip() for c in row.strip().strip("|").split("|")]  # noqa: E731
            head = cells(ln)
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            out.append('<div class="gd-table"><table><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r)
                                                          + "</tr>" for r in rows) + "</tbody></table></div>")
            continue
        m = re.match(r"^(#{3,4}) (.+)$", ln)
        if m:
            flush_para()
            close_lists()
            out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>")
            i += 1
            continue
        m = LIST.match(ln)
        if m:
            flush_para()
            indent, kind = len(m.group(1)), "ul" if m.group(2) else "ol"
            close_lists(indent)                            # back out of deeper lists
            if stack and stack[-1][0] == indent and stack[-1][1] != kind:
                close_lists(indent - 1)                    # same level, other kind of list
            if stack and stack[-1][0] == indent:
                out.append("</li><li>")                    # next item
            else:
                out.append(f"<{kind}><li>")                # a new (possibly nested) list
                stack.append((indent, kind))
            out.append(inline(m.group(4)))
            i += 1
            continue
        close_lists()
        para.append(ln.strip())
        i += 1
    flush_para()
    close_lists()
    return "\n".join(out)


def load(path: str = GUIDES_MD) -> dict:
    """{"intro": html, "guides": [{"id", "title", "html"}]}"""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    parts = re.split(r"^(?=## )", text, flags=re.M)
    intro_md = re.sub(r"^# .*\n", "", parts[0], count=1)
    guides = []
    for p in parts[1:]:
        first, _, body = p.partition("\n")
        m = HEAD.match(first)
        if not m:
            raise ValueError(f"GUIDES.md: a guide heading needs an id: {first!r}")
        guides.append({"id": m.group(2), "title": m.group(1).strip(), "html": to_html(body)})
    return {"intro": to_html(intro_md), "guides": guides}
