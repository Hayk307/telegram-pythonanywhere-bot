"""Slide-deck generation for the /slides command.

The user gives a topic (and optionally how many slides); the AI returns a
structured spec (title, subtitle, and a list of slides with a heading + bullet
points); this module turns that spec into an attractive, consistently-themed
.pptx.

python-pptx is imported **lazily inside** build_deck() — never at module
import time — so the bot still boots and every other command keeps working
even when the library isn't installed on the host yet. A /slides attempt then
just fails with a clear "not available" message instead of taking the whole
worker down. This mirrors bot/fileconvert.py.

parse_deck_spec() and parse_slides_request() are pure stdlib, so the AI-output
parsing and the command parsing are testable without python-pptx installed.
"""

from __future__ import annotations

import json
import re

# Upper safety ceiling. The user can ask for any number of slides up to this;
# beyond it a single AI call (bounded by Telegram's ~60s webhook window and the
# model's max output) can't reliably produce complete JSON, so we clamp and the
# handler tells the user. Bullets stay modest so slides read cleanly.
MAX_SLIDES = 50
MAX_BULLETS = 6
MAX_HEADING_LEN = 120
MAX_BULLET_LEN = 300

# 16:9 widescreen, measured in EMU (English Metric Units; 914400 EMU = 1 inch).
_SLIDE_W = 12192000  # 13.333 in
_SLIDE_H = 6858000   # 7.5 in
_IN = 914400         # 1 inch in EMU

# A single, cohesive, modern theme so every deck looks intentional.
_PRIMARY = (0x14, 0x33, 0x55)      # deep navy — title bg + header bands
_ACCENT = (0x2F, 0x86, 0xC9)       # azure — bullet dots, rules
_HIGHLIGHT = (0xF3, 0x9C, 0x12)    # amber — top strip, title underline
_TEXT_DARK = (0x21, 0x2B, 0x36)    # body text
_TEXT_LIGHT = (0xFF, 0xFF, 0xFF)   # text on the navy background
_SUBTLE = (0xC7, 0xD3, 0xE8)       # subtitle on navy
_MUTED = (0x9A, 0x9A, 0x9A)        # footer / slide numbers
_FONT = "Calibri"


class SlideError(Exception):
    """Raised with a user-facing message when a deck can't be built."""


def _clean(value) -> str:
    """Coerce a spec value to a trimmed single-spaced string."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_slides_request(text: str):
    """Split the /slides argument into (requested_count | None, topic).

    Accepts a leading count ("20 climate change") or a "N slides" phrase
    anywhere ("climate change, 20 slides"). Returns (None, topic) when no
    count is given, letting the AI choose a sensible length. A bare number
    with no topic yields (count, "").
    """
    text = _clean(text)
    if not text:
        return None, ""
    m = re.match(r"^(\d{1,3})\b[\s,.:;-]*(.*)$", text)
    if m:
        return int(m.group(1)), m.group(2).strip()
    m = re.search(r"(\d{1,3})\s*slides?\b", text, re.IGNORECASE)
    if m:
        topic = (text[: m.start()] + text[m.end() :]).strip(" ,.:;-")
        return int(m.group(1)), topic
    return None, text


def parse_deck_spec(raw: str):
    """Parse the AI's response into a validated (title, subtitle, slides) tuple.

    `slides` is a list of {"heading": str, "bullets": [str, ...]}. The AI is
    asked for strict JSON, but models sometimes wrap it in ```json fences or
    add a sentence of preamble, so we extract the outermost {...} block before
    decoding. Raises SlideError (user-facing) on anything unusable.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise SlideError("The AI didn't return slide data. Try rephrasing the topic.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        raise SlideError(
            "The AI's slide data was incomplete — try again, or ask for fewer slides."
        )

    title = _clean(data.get("title")) or "Presentation"
    subtitle = _clean(data.get("subtitle"))

    slides = []
    for item in (data.get("slides") or [])[:MAX_SLIDES]:
        if not isinstance(item, dict):
            continue
        heading = _clean(item.get("heading") or item.get("title"))[:MAX_HEADING_LEN]
        bullets = []
        for bullet in (item.get("bullets") or [])[:MAX_BULLETS]:
            bullet = _clean(bullet)[:MAX_BULLET_LEN]
            if bullet:
                bullets.append(bullet)
        if heading or bullets:
            slides.append({"heading": heading or title, "bullets": bullets})

    if not slides:
        raise SlideError("The AI returned no usable slides. Try a clearer topic.")
    return title, subtitle, slides


def build_deck(title: str, subtitle: str, slides, output_path: str) -> str:
    """Render the spec into an attractive, themed .pptx at output_path.

    Raises SlideError if python-pptx isn't installed.
    """
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Emu, Pt
    except ImportError:
        raise SlideError("Slide generation isn't available (missing python-pptx).")

    def rgb(triple):
        return RGBColor(*triple)

    def rect(slide, left, top, width, height, color):
        shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Emu(left), Emu(top), Emu(width), Emu(height)
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb(color)
        shape.line.fill.background()
        shape.shadow.inherit = False
        return shape

    def add_run(paragraph, text, *, size, color, bold=False):
        run = paragraph.add_run()
        run.text = text
        run.font.name = _FONT
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = rgb(color)
        return run

    prs = Presentation()
    prs.slide_width = Emu(_SLIDE_W)
    prs.slide_height = Emu(_SLIDE_H)
    blank = prs.slide_layouts[6]  # fully blank — we place every element

    total = len(slides)
    footer_title = (title[:57] + "…") if len(title) > 58 else title

    # ── Title slide ─────────────────────────────────────────────────────────
    slide = prs.slides.add_slide(blank)
    rect(slide, 0, 0, _SLIDE_W, _SLIDE_H, _PRIMARY)          # full navy field
    rect(slide, 0, 0, _SLIDE_W, int(_IN * 0.18), _HIGHLIGHT)  # amber top strip
    rect(slide, 0, _SLIDE_H - int(_IN * 0.18), _SLIDE_W, int(_IN * 0.18), _ACCENT)

    box = slide.shapes.add_textbox(
        Emu(_IN), Emu(int(_SLIDE_H * 0.33)), Emu(_SLIDE_W - 2 * _IN), Emu(int(_IN * 2.6))
    )
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tp = tf.paragraphs[0]
    tp.alignment = PP_ALIGN.CENTER
    add_run(tp, title, size=42, color=_TEXT_LIGHT, bold=True)
    if subtitle:
        sp = tf.add_paragraph()
        sp.alignment = PP_ALIGN.CENTER
        sp.space_before = Pt(16)
        add_run(sp, subtitle, size=22, color=_SUBTLE)
    # Short amber underline centered beneath the title block.
    rect(
        slide,
        int(_SLIDE_W / 2 - _IN),
        int(_SLIDE_H * 0.33) + int(_IN * 2.6) + int(_IN * 0.1),
        2 * _IN,
        int(_IN * 0.06),
        _HIGHLIGHT,
    )

    # ── Content slides ────────────────────────────────────────────────────
    margin = int(_IN * 0.8)
    content_w = _SLIDE_W - 2 * margin
    band_h = int(_IN * 1.25)
    for index, spec in enumerate(slides, start=1):
        slide = prs.slides.add_slide(blank)

        # Header band with a thin amber accent strip on top.
        rect(slide, 0, 0, _SLIDE_W, band_h, _PRIMARY)
        rect(slide, 0, 0, _SLIDE_W, int(_IN * 0.12), _HIGHLIGHT)

        # Heading inside the band (left), vertically centered.
        head_box = slide.shapes.add_textbox(
            Emu(margin), Emu(0), Emu(content_w - int(_IN * 1.1)), Emu(band_h)
        )
        htf = head_box.text_frame
        htf.word_wrap = True
        htf.vertical_anchor = MSO_ANCHOR.MIDDLE
        add_run(htf.paragraphs[0], spec["heading"], size=28, color=_TEXT_LIGHT, bold=True)

        # Big faint slide number on the right of the band.
        num_box = slide.shapes.add_textbox(
            Emu(_SLIDE_W - int(_IN * 1.6)), Emu(0), Emu(int(_IN * 1.2)), Emu(band_h)
        )
        ntf = num_box.text_frame
        ntf.vertical_anchor = MSO_ANCHOR.MIDDLE
        np = ntf.paragraphs[0]
        np.alignment = PP_ALIGN.RIGHT
        add_run(np, str(index), size=30, color=_ACCENT, bold=True)

        # Bullets — a colored dot run + dark text run per point. Font eases
        # down a touch on dense slides so they still fit cleanly.
        bullets = spec["bullets"]
        size = 20 if len(bullets) <= 5 else 18
        body_box = slide.shapes.add_textbox(
            Emu(margin),
            Emu(band_h + int(_IN * 0.35)),
            Emu(content_w),
            Emu(_SLIDE_H - band_h - int(_IN * 0.9)),
        )
        btf = body_box.text_frame
        btf.word_wrap = True
        for i, bullet in enumerate(bullets):
            para = btf.paragraphs[0] if i == 0 else btf.add_paragraph()
            para.space_after = Pt(14)
            add_run(para, "●  ", size=size, color=_ACCENT, bold=True)
            add_run(para, bullet, size=size, color=_TEXT_DARK)

        # Footer: thin accent line, deck title (left), n / total (right).
        rect(
            slide,
            margin,
            _SLIDE_H - int(_IN * 0.55),
            content_w,
            int(_IN * 0.015),
            _ACCENT,
        )
        foot = slide.shapes.add_textbox(
            Emu(margin), Emu(_SLIDE_H - int(_IN * 0.5)), Emu(content_w), Emu(int(_IN * 0.4))
        )
        fp = foot.text_frame.paragraphs[0]
        add_run(fp, footer_title, size=10, color=_MUTED)
        fnum = slide.shapes.add_textbox(
            Emu(margin), Emu(_SLIDE_H - int(_IN * 0.5)), Emu(content_w), Emu(int(_IN * 0.4))
        )
        fnp = fnum.text_frame.paragraphs[0]
        fnp.alignment = PP_ALIGN.RIGHT
        add_run(fnp, f"{index} / {total}", size=10, color=_MUTED)

    prs.save(output_path)
    return output_path
