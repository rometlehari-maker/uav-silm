"""The guides (GUIDES.md) and their pages in the Ground Station."""

import os
import re
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
ONBOARD = os.path.join(HERE, "..")
sys.path.insert(0, ONBOARD)

from groundstation import guides  # noqa: E402
from groundstation import project as P  # noqa: E402

STATIC = os.path.join(ONBOARD, "groundstation", "static")


class Balanced(HTMLParser):
    VOID = {"br", "img", "input", "hr"}

    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1] == tag, (tag, self.stack[-4:])
        self.stack.pop()


def test_every_guide_converts_to_balanced_html():
    g = guides.load()
    ids = [x["id"] for x in g["guides"]]
    assert len(ids) == len(set(ids)) >= 12
    for x in g["guides"] + [{"id": "intro", "html": g["intro"]}]:
        p = Balanced()
        p.feed("<div>" + x["html"] + "</div>")
        assert not p.stack, x["id"]
        text = re.sub(r"<code>.*?</code>", "", x["html"])
        assert "**" not in text and "`" not in text, x["id"]          # no Markdown left over


def test_every_link_in_the_app_points_at_a_guide():
    ids = {x["id"] for x in guides.load()["guides"]}
    used = set()
    for f in os.listdir(STATIC):
        if f.endswith((".html", ".js")):
            used |= set(re.findall(r'data-guide="([a-z0-9-]+)"', open(os.path.join(STATIC, f)).read()))
    used |= {q["guide"] for q in P.QUESTIONS if q.get("guide")}
    assert used and used <= ids, used - ids
    assert {"parts", "wiring", "firmware-first", "sd-card", "python", "simulator", "camera",
            "servos-esc", "cg", "airspeed", "legal", "usb-stick"} <= ids


def test_markdown_subset():
    h = guides.to_html("Intro **bold** and `a<b>` and *it*.\n\n1. one\n   wrapped **bo\n   ld**\n   - sub\n2. two\n\n"
                       "| A | B |\n|---|---|\n| x | <script> |\n\n### Sub\ntext")
    assert "<b>bold</b>" in h and "<code>a&lt;b&gt;</code>" in h and "<i>it</i>" in h
    assert "<ol><li>one wrapped <b>bo ld</b><ul><li>sub</li></ul></li><li>two</li></ol>" in h.replace("\n", "")
    assert "<td>&lt;script&gt;</td>" in h and "<h3>Sub</h3>" in h and "<script>" not in h


def test_a_guide_heading_needs_an_id(tmp_path):
    p = tmp_path / "g.md"
    p.write_text("# T\n\n## No id here\ntext\n")
    try:
        guides.load(str(p))
    except ValueError as e:
        assert "needs an id" in str(e)
    else:
        raise AssertionError("accepted a guide without an id")
