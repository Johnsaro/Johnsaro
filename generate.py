"""
generate.py - build the SVG images for the GitHub profile README.

Reads a "ledger" (a JSON file listing your languages and every reviewed
project) and draws three pictures:

    assets/skills.svg   one card per language: %, progress bar, what to try next
    assets/growth.svg   step chart of how each language's % climbed over time
    assets/log.svg      table of the most recent projects (newest first)

Usage:
    python generate.py                                   # ledger.json -> assets/
    python generate.py --ledger ledger.sample.json --out assets/sample

Adding a project: append an entry to "projects" in ledger.json, then run
`python generate.py` again and commit the updated SVGs.

Only the Python standard library is used. GitHub READMEs can't run CSS or
JavaScript, so everything is drawn as plain SVG and embedded with <img>.
"""

import argparse
import json
import math
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

# ---------------------------------------------------------------------------
# Design tokens (copied from the web dashboard)
# ---------------------------------------------------------------------------
WIDTH = 840          # every image uses viewBox width 840
MARGIN = 24          # outer padding around the image
BG = "#10161D"
SURFACE = "#18212B"
INK = "#E4EAF1"
MUTED = "#93A1B1"
LINE = "#2A3644"
CHIP = "#223040"
GOOD = "#4CC28A"
WARN = "#F08A4B"

DISPLAY_FONT = '"Segoe UI Black", "Segoe UI", "Helvetica Neue", Arial, sans-serif'
MONO_FONT = '"JetBrains Mono", "Cascadia Code", Consolas, "SFMono-Regular", Menlo, monospace'
BODY_FONT = '"Segoe UI", "Helvetica Neue", Arial, sans-serif'

# Rough average character width as a fraction of font size. We can't measure
# text inside an SVG file, so we estimate to keep things from overflowing.
CHAR_WIDTH = {"mono": 0.6, "display": 0.62, "body": 0.5}

GATES = (40, 70, 90)  # the % thresholds marked on every progress bar

# ---------------------------------------------------------------------------
# Fundamentals checklist per language, split into three tiers.
# Languages not listed here still work; they just skip the tier counts.
# ---------------------------------------------------------------------------
FUNDAMENTALS = {
    "cpp": [
        ["Variables & types", "Conditionals", "Loops", "Functions",
         "Arrays & strings", "Console I/O", "Scope & const"],
        ["Structs & classes", "References & pointers", "STL containers",
         "File I/O", "Exceptions & validation", "Multi-file / headers", "Overloading"],
        ["Templates", "RAII & destructors", "Inheritance & virtual",
         "Smart pointers", "Operator overloading", "Move semantics", "Lambdas + algorithms"],
    ],
    "py": [
        ["Variables & types", "if/elif/else", "Loops", "Functions",
         "Lists & dicts", "Strings & f-strings", "input/print"],
        ["Classes & objects", "File I/O", "try/except", "Modules & imports",
         "Comprehensions", "Standard library", "pip + 3rd-party lib"],
        ["Decorators", "Generators", "Context managers", "Inheritance & dunders",
         "Type hints + tests", "async/await", "Closures"],
    ],
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def esc(text):
    """Escape text so it is safe inside SVG (XML) text and attributes."""
    return escape(str(text), {'"': "&quot;"})


def text_width(text, size, kind="mono"):
    """Estimate how many pixels wide a piece of text will be."""
    return len(text) * size * CHAR_WIDTH[kind]


def truncate(text, max_px, size, kind="mono"):
    """Shorten text with '…' so it fits in max_px (estimated)."""
    if text_width(text, size, kind) <= max_px:
        return text
    while text and text_width(text + "…", size, kind) > max_px:
        text = text[:-1]
    return text.rstrip() + "…"


def fmt_num(value):
    """5.0 -> '5', 15.5 -> '15.5'."""
    return f"{value:g}"


def fmt_date(d):
    """date(2026, 10, 11) -> 'Oct 11'."""
    return f"{d:%b} {d.day}"


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def text_el(x, y, content, size, fill, kind="mono", anchor="start", extra=""):
    """One <text> element. `content` is escaped here."""
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{kind}" font-size="{size}" '
            f'fill="{fill}" text-anchor="{anchor}"{extra}>{esc(content)}</text>')


def svg_document(height, title, body):
    """Wrap drawing code in an <svg> with the shared fonts, background and grid."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img">
<title>{esc(title)}</title>
<style><![CDATA[
  .display {{ font-family: {DISPLAY_FONT}; font-weight: 800; }}
  .mono {{ font-family: {MONO_FONT}; }}
  .body {{ font-family: {BODY_FONT}; }}
]]></style>
<defs>
  <pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse">
    <path d="M24 0H0V24" fill="none" stroke="rgb(140,170,220)" stroke-opacity="0.05" stroke-width="1"/>
  </pattern>
</defs>
<rect width="{WIDTH}" height="{height}" fill="{BG}"/>
<rect width="{WIDTH}" height="{height}" fill="url(#grid)"/>
{body}
</svg>
"""


def section_header(eyebrow, heading, accent="#7FA2FF"):
    """Eyebrow as a rounded accent tag, then a big heading.
    Content starts at y=CONTENT_TOP below it."""
    label = eyebrow.upper()
    pw = text_width(label, 11) + len(label) * 2 + 24   # text + letter-spacing + padding
    return "\n".join([
        f'<rect x="{MARGIN}" y="22" width="{pw:.1f}" height="24" rx="12" '
        f'fill="{CHIP}" stroke="{accent}" stroke-opacity="0.5" stroke-width="1"/>',
        text_el(MARGIN + 12, 38, label, 11, accent, extra=' letter-spacing="2" font-weight="600"'),
        text_el(MARGIN, 82, heading, 31, INK, kind="display"),
    ])


CONTENT_TOP = 104


def card_rect(x, y, w, h):
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="6" '
            f'fill="{SURFACE}" stroke="{LINE}" stroke-width="1"/>')


def layout_chips(labels, max_width, font_size, height, pad_x=8, gap=6):
    """Place chips left-to-right, wrapping to a new line when they'd overflow.

    Returns (chips, total_height) where each chip is (x, y, width, label),
    with x/y relative to the top-left of the chip area.
    """
    chips = []
    x = y = 0
    for label in labels:
        label = truncate(label, max_width - 2 * pad_x, font_size)
        w = text_width(label, font_size) + 2 * pad_x
        if x > 0 and x + w > max_width:   # doesn't fit: start a new line
            x = 0
            y += height + gap
        chips.append((x, y, w, label))
        x += w + gap
    total_height = y + height if chips else 0
    return chips, total_height


# ---------------------------------------------------------------------------
# Reading the ledger
# ---------------------------------------------------------------------------
def load_ledger(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    languages = data.get("languages", {})
    projects = []
    for i, p in enumerate(data.get("projects", [])):
        p = dict(p)
        p["date"] = date.fromisoformat(p["date"])
        p["order"] = i  # keeps same-day projects in the order they were logged
        projects.append(p)
    projects.sort(key=lambda p: (p["date"], p["order"]))  # oldest first
    return languages, projects


def lang_info(languages, key):
    info = languages.get(key, {})
    return info.get("name", key), info.get("color", MUTED)


def projects_for(projects, key):
    return [p for p in projects if p["lang"] == key]


def current_percent(lang_projects):
    """A language's % is the running total of its most recent project."""
    return lang_projects[-1]["total"] if lang_projects else 0


# ---------------------------------------------------------------------------
# skills.svg
# ---------------------------------------------------------------------------
CARD_PAD = 20
CARD_GAP = 16


def next_gate_text(t2, t3, t3_total):
    if t2 < 3:
        return f"Next gate 40%: needs {3 - t2} more Tier 2"
    if t3 < 2:
        return "Next gate 70%: needs Tier 3"
    if t3 < t3_total:
        return f"Next gate 90%: needs {t3_total - t3} more Tier 3"
    return "Next gate 90%: every fundamental covered"


def suggestions(tiers, learned, has_projects):
    """Up to 3 fundamentals to try next."""
    if not has_projects:
        return tiers[0][:3]
    unused_t2 = [f for f in tiers[1] if f not in learned]
    unused_t3 = [f for f in tiers[2] if f not in learned]
    return (unused_t2 + unused_t3)[:3]


def skill_card(key, languages, projects, w):
    """Draw one language card. Returns (svg_parts, content_height)."""
    name, color = lang_info(languages, key)
    mine = projects_for(projects, key)
    pct = current_percent(mine)
    parts = []

    # Name (left) and big % (right)
    pct_text = fmt_num(pct) + "%"
    pct_w = text_width(pct_text, 40, "display")
    name = truncate(name, w - 2 * CARD_PAD - pct_w - 12, 18, "display")
    parts.append(text_el(CARD_PAD, 38, name, 18, INK, kind="display"))
    parts.append(text_el(w - CARD_PAD, 52, pct_text, 40, color, kind="display", anchor="end"))

    # Progress bar with gate ticks
    bar_y, bar_w = 70, w - 2 * CARD_PAD
    parts.append(f'<rect x="{CARD_PAD}" y="{bar_y}" width="{bar_w}" height="10" rx="5" fill="{CHIP}"/>')
    fill_w = bar_w * max(0, min(pct, 100)) / 100
    if fill_w > 0:
        parts.append(f'<rect x="{CARD_PAD}" y="{bar_y}" width="{fill_w:.1f}" height="10" rx="5" fill="{color}"/>')
    for gate in GATES:
        gx = CARD_PAD + bar_w * gate / 100
        parts.append(f'<line x1="{gx:.1f}" y1="{bar_y - 4}" x2="{gx:.1f}" y2="{bar_y + 14}" '
                     f'stroke="{MUTED}" stroke-width="2" stroke-opacity="0.55"/>')
        parts.append(text_el(gx, bar_y + 28, f"{gate}%", 10, MUTED, anchor="middle"))

    # Tier counts + next gate
    y = bar_y + 54
    tiers = FUNDAMENTALS.get(key)
    learned = {f for p in mine for f in p.get("new_fundamentals", [])}
    if tiers:
        counts = [sum(f in learned for f in tier) for tier in tiers]
        stats = " · ".join(f"T{i + 1} {c}/{len(t)}" for i, (c, t) in enumerate(zip(counts, tiers)))
        stats += " · " + plural(len(mine), "project")
    else:
        stats = plural(len(mine), "project")
    parts.append(text_el(CARD_PAD, y, truncate(stats, bar_w, 13), 13, MUTED))

    chip_labels = []
    if tiers:
        y += 22
        gate = "First project pending" if not mine else next_gate_text(counts[1], counts[2], len(tiers[2]))
        gate_color = MUTED if not mine else WARN
        parts.append(text_el(CARD_PAD, y, truncate(gate, bar_w, 13), 13, gate_color))
        chip_labels = ["try: " + f for f in suggestions(tiers, learned, bool(mine))]

    # "try:" chips with a dashed border
    y += 16
    chips, chips_h = layout_chips(chip_labels, bar_w, 12, 24)
    for cx, cy, cw, label in chips:
        x0, y0 = CARD_PAD + cx, y + cy
        parts.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{cw:.1f}" height="24" rx="4" fill="{CHIP}" '
                     f'fill-opacity="0.4" stroke="{MUTED}" stroke-opacity="0.6" stroke-dasharray="4 3"/>')
        prefix, _, rest = label.partition(": ")
        parts.append(f'<text x="{x0 + 8:.1f}" y="{y0 + 16:.1f}" class="mono" font-size="12" fill="{INK}">'
                     f'<tspan fill="{MUTED}">{esc(prefix)}: </tspan>{esc(rest)}</text>')

    height = y + chips_h + CARD_PAD
    return parts, height


def render_skills(languages, projects):
    keys = list(languages)
    card_w = (WIDTH - 2 * MARGIN - CARD_GAP) / 2
    body = [section_header("SKILL BY LANGUAGE", "Every language, one gate at a time")]
    y = CONTENT_TOP

    if not keys:
        body.append(text_el(MARGIN, y + 20, "No languages in the ledger yet.", 13, MUTED))
        return svg_document(y + 44, "Skill by language", "\n".join(body))

    for row_start in range(0, len(keys), 2):
        row = [skill_card(k, languages, projects, card_w) for k in keys[row_start:row_start + 2]]
        row_h = max(h for _, h in row)
        for col, (parts, _) in enumerate(row):
            x = MARGIN + col * (card_w + CARD_GAP)
            body.append(f'<g transform="translate({x:.1f},{y:.1f})">')
            body.append(card_rect(0, 0, card_w, row_h))
            body.extend(parts)
            body.append("</g>")
        y += row_h + CARD_GAP

    height = math.ceil(y - CARD_GAP + MARGIN)
    return svg_document(height, "Skill by language", "\n".join(body))


# ---------------------------------------------------------------------------
# growth.svg
# ---------------------------------------------------------------------------
def render_growth(languages, projects):
    card_x, card_y = MARGIN, CONTENT_TOP
    card_w = WIDTH - 2 * MARGIN
    left, right = card_x + 56, card_x + card_w - 72   # plot area (x)
    top, bottom = card_y + 28, card_y + 248           # plot area (y)
    card_h = 324
    height = card_y + card_h + MARGIN

    body = [section_header("HOW THE % CLIMBS", "Growth over time"), card_rect(card_x, card_y, card_w, card_h)]

    max_total = max((p["total"] for p in projects), default=0)
    y_max = max(20, math.ceil((max_total + 5) / 5) * 5)

    def ys(value):
        return bottom - (bottom - top) * value / y_max

    # Horizontal gridlines + % labels every 5
    step = 5 if y_max <= 50 else 10  # avoid crowded labels on tall charts
    for v in range(0, y_max + 1, step):
        body.append(f'<line x1="{left}" y1="{ys(v):.1f}" x2="{right}" y2="{ys(v):.1f}" stroke="{LINE}" stroke-width="1"/>')
        body.append(text_el(left - 10, ys(v) + 4, f"{v}%", 11, MUTED, anchor="end"))

    if not projects:
        body.append(text_el((left + right) / 2, ys(y_max * 0.625) + 4, "No projects yet — first one lands here",
                            13, MUTED, anchor="middle"))
        legend_y = bottom + 50
        draw_legend(body, languages, projects, left, legend_y)
        return svg_document(height, "How the % climbs", "\n".join(body))

    start = projects[0]["date"] - timedelta(days=1)
    end = projects[-1]["date"] + timedelta(days=2)
    span = (end - start).days

    def xs(d):
        return left + (right - left) * (d - start).days / span

    # Date labels (~4 evenly spaced)
    ticks = sorted({round(i * span / 3) for i in range(4)})
    for t in ticks:
        d = start + timedelta(days=t)
        body.append(text_el(xs(d), bottom + 22, fmt_date(d), 11, MUTED, anchor="middle"))

    areas, strokes, dots, labels = [], [], [], []
    for key in languages:
        mine = projects_for(projects, key)
        if not mine:
            continue
        _, color = lang_info(languages, key)
        path = f"M{left:.1f} {ys(0):.1f}"
        for p in mine:
            path += f" H{xs(p['date']):.1f} V{ys(p['total']):.1f}"
        path += f" H{right:.1f}"
        areas.append(f'<path d="{path} V{ys(0):.1f} Z" fill="{color}" fill-opacity="0.08"/>')
        strokes.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round"/>')
        for i, p in enumerate(mine):
            cx, cy = xs(p["date"]), ys(p["total"])
            if i == len(mine) - 1:
                dots.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" fill="{color}"/>')
                labels.append([cy - 12, cx + 4, fmt_num(p["total"]) + "%", color])  # above the dot
            else:
                dots.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{SURFACE}" stroke="{color}" stroke-width="2"/>')

    # Nudge end labels apart so they never overlap each other
    labels.sort()
    for i in range(1, len(labels)):
        labels[i][0] = max(labels[i][0], labels[i - 1][0] + 16)

    body += areas + strokes + dots
    for ly, lx, text, color in labels:
        body.append(text_el(lx, ly, text, 12, color, extra=' font-weight="700"'))

    draw_legend(body, languages, projects, left, bottom + 50)
    return svg_document(height, "How the % climbs", "\n".join(body))


def draw_legend(body, languages, projects, x, y):
    for key in languages:
        name, color = lang_info(languages, key)
        label = f"{name} {fmt_num(current_percent(projects_for(projects, key)))}%"
        body.append(f'<rect x="{x}" y="{y - 9}" width="14" height="3" rx="1.5" fill="{color}"/>')
        body.append(text_el(x + 20, y - 3, label, 12, INK))
        x += 20 + text_width(label, 12) + 24


# ---------------------------------------------------------------------------
# log.svg
# ---------------------------------------------------------------------------
MAX_LOG_ROWS = 8
COLUMNS = [  # (header, width) - widths add up to 792 (840 minus margins)
    ("DATE", 76), ("PROJECT", 214), ("LINES", 56), ("NEW FUNDAMENTALS", 246),
    ("QUALITY", 66), ("GAIN", 60), ("TOTAL", 74),
]
CELL_PAD = 12
HEADER_H = 32
ROW_MIN_H = 44


def render_log(languages, projects):
    table_x, table_y = MARGIN, CONTENT_TOP
    table_w = sum(w for _, w in COLUMNS)
    col_x = []
    x = table_x
    for _, w in COLUMNS:
        col_x.append(x)
        x += w

    newest_first = list(reversed(projects))
    shown = newest_first[:MAX_LOG_ROWS]
    hidden = len(newest_first) - len(shown)

    rows = []       # svg parts for the rows
    y = table_y + HEADER_H
    if not shown:
        rows.append(text_el(table_x + CELL_PAD, y + 27, "No projects logged yet.", 13, MUTED))
        y += ROW_MIN_H
    for p in shown:
        name, color = lang_info(languages, p["lang"])
        base = y + 27  # text baseline, lined up with the first line of chips

        # Fundamentals as wrapping chips (these decide the row height)
        fund_w = COLUMNS[3][1] - 2 * CELL_PAD
        chips, chips_h = layout_chips(p.get("new_fundamentals", []), fund_w, 11, 20)
        row_h = max(ROW_MIN_H, chips_h + 24)

        rows.append(text_el(col_x[0] + CELL_PAD, base, fmt_date(p["date"]), 12, INK))

        rows.append(f'<circle cx="{col_x[1] + CELL_PAD + 4}" cy="{base - 4}" r="4" fill="{color}"/>')
        project_label = truncate(f"{name} · {p['name']}", COLUMNS[1][1] - CELL_PAD * 2 - 14, 13, "body")
        rows.append(text_el(col_x[1] + CELL_PAD + 14, base, project_label, 13, INK, kind="body"))

        rows.append(text_el(col_x[2] + CELL_PAD, base, str(p.get("lines", "")), 12, INK))

        if chips:
            for cx, cy, cw, label in chips:
                x0, y0 = col_x[3] + CELL_PAD + cx, y + 12 + cy
                rows.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{cw:.1f}" height="20" rx="4" fill="{CHIP}"/>')
                rows.append(text_el(x0 + 8, y0 + 14, label, 11, INK))
        else:
            rows.append(text_el(col_x[3] + CELL_PAD, base, "—", 12, MUTED))

        rows.append(text_el(col_x[4] + CELL_PAD, base, f"×{p.get('quality', 1):.1f}", 12, INK))
        rows.append(text_el(col_x[5] + CELL_PAD, base, f"+{fmt_num(p['gain'])}%", 12, GOOD, extra=' font-weight="700"'))
        rows.append(text_el(col_x[6] + CELL_PAD, base, f"{fmt_num(p['total'])}%", 12, INK, extra=' font-weight="700"'))

        y += row_h
        rows.append(f'<line x1="{table_x}" y1="{y:.1f}" x2="{table_x + table_w}" y2="{y:.1f}" stroke="{LINE}"/>')
    if shown:
        rows.pop()  # no divider under the last row (the card border is there)

    table_h = y - table_y
    body = [
        section_header("EVERY PROJECT, ON RECORD", "Project log"),
        '<defs><clipPath id="table"><rect x="{0}" y="{1}" width="{2}" height="{3}" rx="6"/></clipPath></defs>'
        .format(table_x, table_y, table_w, table_h),
        card_rect(table_x, table_y, table_w, table_h),
        f'<rect x="{table_x}" y="{table_y}" width="{table_w}" height="{HEADER_H}" fill="{CHIP}" clip-path="url(#table)"/>',
    ]
    for (header, _), cx in zip(COLUMNS, col_x):
        body.append(text_el(cx + CELL_PAD, table_y + 20, header, 11, MUTED, extra=' letter-spacing="1"'))
    body += rows

    height = y + MARGIN
    if hidden:
        body.append(text_el(table_x + table_w, y + 22, f"+{hidden} more in LOG.md", 12, MUTED, anchor="end"))
        height += 24
    return svg_document(math.ceil(height), "Every project, on record", "\n".join(body))


# ---------------------------------------------------------------------------
# Text panels (about / journey / currently) from profile.json
# ---------------------------------------------------------------------------
SOFT = "#B8C4D1"        # secondary text: brighter than MUTED so it stays readable
TEXT_SIZE = 15
TEXT_LINE = 24


def wrap(text, max_px, size, kind="body"):
    """Split text into lines that fit max_px (word wrap, estimated widths)."""
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and text_width(candidate, size, kind) > max_px:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def text_color(text, normal=INK):
    """Placeholders like [your goal] show in orange so they're easy to spot."""
    return WARN if text.strip().startswith("[") else normal


def draw_lines(lines, x, y, size, fill, kind="body"):
    """Draw wrapped lines; y is the first baseline. Returns the list of elements."""
    return [text_el(x, y + i * TEXT_LINE, line, size, fill, kind=kind) for i, line in enumerate(lines)]


def block_text(block, y, w):
    pad = CARD_PAD
    lines = wrap(block["text"], w - 2 * pad, TEXT_SIZE)
    h = pad * 2 + len(lines) * TEXT_LINE - 6
    body = [card_rect(MARGIN, y, w, h)]
    body += draw_lines(lines, MARGIN + pad, y + pad + 15, TEXT_SIZE, text_color(block["text"]))
    return body, h


def block_cards(block, y, w):
    body, top = [], y
    for item in block["items"]:
        pad = CARD_PAD
        lines = wrap(item["text"], w - 2 * pad, TEXT_SIZE)
        link = item.get("link_text")   # shown as "text →"; README wraps the image in the real link
        h = pad + 18 + 30 + len(lines) * TEXT_LINE + pad - 6 + (32 if link else 0)
        body.append(card_rect(MARGIN, y, w, h))
        if item.get("featured"):   # highlight: border + left accent in the card's color
            color = item.get("color", "#7FA2FF")
            body.append(f'<rect x="{MARGIN:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="6" '
                        f'fill="none" stroke="{color}" stroke-opacity="0.55" stroke-width="1"/>')
            body.append(f'<rect x="{MARGIN:.1f}" y="{y + 6:.1f}" width="3" height="{h - 12:.1f}" rx="1.5" fill="{color}"/>')
        if link:
            ly = y + pad + 66 + len(lines) * TEXT_LINE + 10
            body.append(text_el(MARGIN + pad, ly, f"{link} →", 13, "#7FA2FF", extra=' font-weight="600"'))
        body.append(text_el(MARGIN + pad, y + pad + 8, item["label"].upper(), 11,
                            item.get("color", MUTED), extra=' letter-spacing="2"'))
        body.append(text_el(MARGIN + pad, y + pad + 38, item["title"], 19, INK, kind="display"))
        body += draw_lines(lines, MARGIN + pad, y + pad + 66, TEXT_SIZE, text_color(item["text"], SOFT))
        y += h + 16
    return body, y - 16 - top


def block_steps(block, y, w):
    pad = CARD_PAD
    rows = block["items"]
    h = pad * 2 + 30 + len(rows) * 30 - 8
    body = [card_rect(MARGIN, y, w, h),
            text_el(MARGIN + pad, y + pad + 14, block["title"], 16, INK, kind="display")]
    for i, (title, desc) in enumerate(rows):
        ry = y + pad + 50 + i * 30
        body.append(text_el(MARGIN + pad, ry, f"{i + 1:02d}", 13, "#7FA2FF"))
        body.append(f'<text x="{MARGIN + pad + 40}" y="{ry}" class="body" font-size="{TEXT_SIZE}" fill="{INK}">'
                    f'<tspan font-weight="700">{esc(title)}</tspan>'
                    f'<tspan fill="{SOFT}"> · {esc(desc)}</tspan></text>')
    return body, h


def block_rows(block, y, w):
    pad, key_w = CARD_PAD, 150
    body, top, row_y = [], y, y
    parts = []
    for i, (key, value) in enumerate(block["items"]):
        lines = wrap(value, w - 2 * pad - key_w, TEXT_SIZE)
        rh = 16 + len(lines) * TEXT_LINE
        if i:
            parts.append(f'<line x1="{MARGIN + pad}" x2="{MARGIN + w - pad}" y1="{row_y:.1f}" y2="{row_y:.1f}" stroke="{LINE}"/>')
        parts.append(text_el(MARGIN + pad, row_y + 25, key.upper(), 11, MUTED, extra=' letter-spacing="1.5"'))
        parts += draw_lines(lines, MARGIN + pad + key_w, row_y + 26, TEXT_SIZE, text_color(value))
        row_y += rh
    h = row_y - top
    body.append(card_rect(MARGIN, y, w, h))
    body += parts
    return body, h


def block_subhead(block, y, w):
    """A labeled part divider: extra space, a rule, an uppercase label, and an
    optional one-line note. Signals 'new part of this section' so stacked cards
    don't blur together."""
    lead = 18                       # breathing room above the divider
    color = block.get("color", "#7FA2FF")
    label = block["label"].upper()
    ry = y + lead
    body = [f'<line x1="{MARGIN}" x2="{MARGIN + w}" y1="{ry:.1f}" y2="{ry:.1f}" stroke="{LINE}"/>']
    lw = text_width(label, 12) + len(label) + 10
    body.append(f'<rect x="{MARGIN}" y="{ry:.1f}" width="{lw:.1f}" height="2" fill="{color}"/>')
    body.append(text_el(MARGIN, ry + 24, label, 12, color, extra=' letter-spacing="2" font-weight="700"'))
    h = lead + 32
    note = block.get("note")
    if note:
        lines = wrap(note, w, TEXT_SIZE)
        for i, line in enumerate(lines):
            body.append(text_el(MARGIN, ry + 24 + 22 + i * TEXT_LINE, line, TEXT_SIZE, SOFT))
        h += 22 + len(lines) * TEXT_LINE - 6
    return body, h


def block_stats(block, y, w):
    """A card of number tiles: each shows a big colored figure over a small label.
    items are [number, label] or {"num","label","color"}. Optional block title;
    `featured` draws the accent border + left stripe like a highlighted card."""
    pad, gap, cols = CARD_PAD, 14, 3
    title = block.get("title")
    items = [i if isinstance(i, dict) else {"num": i[0], "label": i[1]} for i in block["items"]]
    rows = (len(items) + cols - 1) // cols
    tile_w = (w - 2 * pad - (cols - 1) * gap) / cols
    tile_h, label_lines_max = 40, 2
    top_off = pad + (28 if title else 0)
    row_h = tile_h + 16 + label_lines_max * 16 + gap
    h = top_off + rows * row_h - gap + pad - 8
    body = [card_rect(MARGIN, y, w, h)]
    color = block.get("color", "#E3B24F")
    if block.get("featured"):
        body.append(f'<rect x="{MARGIN:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="6" '
                    f'fill="none" stroke="{color}" stroke-opacity="0.55" stroke-width="1"/>')
        body.append(f'<rect x="{MARGIN:.1f}" y="{y + 6:.1f}" width="3" height="{h - 12:.1f}" rx="1.5" fill="{color}"/>')
    if title:
        body.append(text_el(MARGIN + pad, y + pad + 10, title.upper(), 11, MUTED, extra=' letter-spacing="2"'))
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        tx = MARGIN + pad + c * (tile_w + gap)
        ty = y + top_off + r * row_h
        tcol = it.get("color", color)
        body.append(f'<rect x="{tx:.1f}" y="{ty:.1f}" width="{tile_w:.1f}" height="{tile_h + 16 + label_lines_max * 16:.1f}" '
                    f'rx="5" fill="{CHIP}" stroke="{LINE}" stroke-width="1"/>')
        body.append(text_el(tx + 14, ty + 38, str(it["num"]), 34, tcol, kind="display"))
        for j, line in enumerate(wrap(it["label"], tile_w - 28, 12)[:label_lines_max]):
            body.append(text_el(tx + 14, ty + tile_h + 24 + j * 16, line, 12, SOFT))
    return body, h


def chip(x, y, label, fill, color, anchor_right=False):
    """Small rounded tag. With anchor_right, x is the chip's right edge."""
    w = text_width(label, 10) + len(label) + 16   # + letter-spacing
    if anchor_right:
        x -= w
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="20" rx="3" fill="{fill}"/>'
            + text_el(x + 8, y + 14, label, 10, color, extra=' letter-spacing="1"')), w


def render_shipped_card(item):
    """One project card as its own image, so the README can wrap it in a link.
    Cards stack flush: each image ends with the 12px gap before the next one."""
    w, pad, y = WIDTH - 2 * MARGIN, CARD_PAD, 0
    body = []
    if item.get("group_label"):
        body.append(text_el(MARGIN, 22, item["group_label"].upper(), 11, item.get("group_color", MUTED),
                            extra=' letter-spacing="2"'))
        y = 38
    lines = wrap(item["text"], w - 2 * pad, TEXT_SIZE)
    h = pad + 26 + 26 + len(lines) * TEXT_LINE + pad - 4
    featured = item.get("featured")
    body.append(f'<rect x="{MARGIN}" y="{y}" width="{w}" height="{h}" rx="6" fill="{SURFACE}" '
                f'stroke="{"#7FA2FF" if featured else LINE}" stroke-opacity="{0.55 if featured else 1}" stroke-width="1"/>')
    body.append(text_el(MARGIN + pad, y + pad + 18, item["name"], 21 if featured else 19, INK, kind="display"))

    # status chips, right-aligned on the title row
    right = MARGIN + w - pad
    status = item.get("status", "private")
    label, fill, color = {
        "public": (f"{item.get('repo', 'repo')} →", "#1E2D4A", "#7FA2FF"),
        "private": ("PRIVATE REPO", CHIP, MUTED),
        "local": ("LOCAL ONLY", CHIP, MUTED),
    }[status]
    el, cw = chip(right, y + pad + 3, label, fill, color, anchor_right=True)
    body.append(el)
    if featured:
        el, _ = chip(right - cw - 8, y + pad + 3, "MOST GRIND", "#3A2E14", "#E3B24F", anchor_right=True)
        body.append(el)

    body.append(text_el(MARGIN + pad, y + pad + 44, item["meta"], 12, MUTED))
    body += draw_lines(lines, MARGIN + pad, y + pad + 72, TEXT_SIZE, SOFT)
    return svg_document(y + h + 12, item["name"], "\n".join(body))


def render_shipped(section):
    """Heading image + one image per project card. Returns {filename: svg}."""
    head = [section_header(section["eyebrow"], section["heading"], section.get("accent", "#7FA2FF"))]
    for i, line in enumerate(wrap(section["intro"], WIDTH - 2 * MARGIN, TEXT_SIZE)):
        head.append(text_el(MARGIN, CONTENT_TOP + 10 + i * TEXT_LINE, line, TEXT_SIZE, SOFT, kind="body"))
    head_h = CONTENT_TOP + 10 + len(wrap(section["intro"], WIDTH - 2 * MARGIN, TEXT_SIZE)) * TEXT_LINE + 8

    # skills these projects built (not coding) as green chips under the intro
    skills = section.get("skills", [])
    if skills:
        chips, chips_h = layout_chips(skills, WIDTH - 2 * MARGIN, 13, 30, pad_x=12, gap=8)
        for cx, cy, cw, label in chips:
            x, y = MARGIN + cx, head_h - 6 + cy
            head.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw:.1f}" height="30" rx="4" '
                        f'fill="#15302A" stroke="{GOOD}" stroke-opacity="0.45"/>')
            head.append(text_el(x + 12, y + 20, label, 13, GOOD))
        head_h += chips_h + 14
    files = {"shipped.svg": svg_document(head_h, section["heading"], "\n".join(head))}
    for item in section["items"]:
        files[f"shipped-{item['slug']}.svg"] = render_shipped_card(item)
    return files


BLOCKS = {"text": block_text, "cards": block_cards, "steps": block_steps,
          "rows": block_rows, "stats": block_stats, "subhead": block_subhead}


def render_panel(section):
    """One SVG panel: eyebrow + heading, then each block stacked with a gap."""
    # A panel can be split into flush-stacked images (so part of it can be a link):
    # "continues": no bottom margin; "continued": no header, no top margin.
    w = WIDTH - 2 * MARGIN
    body = []
    if section.get("continued"):
        y = 0
    else:
        body.append(section_header(section["eyebrow"], section["heading"],
                                   section.get("accent", "#7FA2FF")))
        y = CONTENT_TOP
    for i, block in enumerate(section["blocks"]):
        # extra air before a new part divider, so categories read as separate
        if i and block["type"] == "subhead":
            y += 16
        parts, h = BLOCKS[block["type"]](block, y, w)
        body += parts
        y += h + 18
    bottom = 0 if section.get("continues") else MARGIN - 12
    return svg_document(math.ceil(y + bottom), section.get("heading", "The journey"), "\n".join(body))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Generate README SVGs from the skill ledger.")
    parser.add_argument("--ledger", default=here / "ledger.json", type=Path, help="ledger JSON file")
    parser.add_argument("--out", default=here / "assets", type=Path, help="output folder for the SVGs")
    args = parser.parse_args()

    languages, projects = load_ledger(args.ledger)
    args.out.mkdir(parents=True, exist_ok=True)

    outputs = {
        "skills.svg": render_skills(languages, projects),
        "growth.svg": render_growth(languages, projects),
        "log.svg": render_log(languages, projects),
    }
    profile_path = here / "profile.json"
    if args.ledger == here / "ledger.json" and profile_path.exists():
        with open(profile_path, encoding="utf-8") as f:
            profile = json.load(f)
        for name, section in profile.items():
            if name == "shipped":
                outputs.update(render_shipped(section))
            elif not name.startswith("_"):
                outputs[f"{name}.svg"] = render_panel(section)

    for filename, svg in outputs.items():
        path = args.out / filename
        path.write_text(svg, encoding="utf-8", newline="\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
