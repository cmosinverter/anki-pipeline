#!/usr/bin/env python3
"""
Turn the markdown in clean/ into Anki cards and (by default) auto-import them
into a running Anki Desktop via AnkiConnect.

For every clean/<category>/<topic>.md (categories are auto-discovered — any
subfolder of clean/ is a category, except `_`-prefixed folders and the folders
listed in config's `no_card_folders`), this writes anki/<category>/<topic>.txt
(an Anki TSV you can also import manually via File → Import).

If AnkiConnect is reachable (http://localhost:8765 by default) the cards are
pushed straight into Anki — existing cards are matched by their `guid::...` tag
and updated, new ones are added. If Anki is closed / AnkiConnect is missing it
just writes the TSV without erroring, so you can import later.

  Front = `## title` + the question section
  Back  = key-points + answer + source
  Tags  = the `**tags:**` line + category + topic + `guid::<category>::<topic>::<slug(title)>`
  Deck  = <deck_root>::<category>::<topic>
  Model = <model_name> (auto-created, with left-aligned CSS + code coloring)

All subject-specific values (deck name, model name, field labels, language,
which folders skip card generation) come from config.json at the project root.

Flags:
  --no-sync         only write TSV, don't try AnkiConnect
  --sync-url URL    AnkiConnect endpoint (default http://localhost:8765)
  --reset           delete every card with a guid::* tag, then re-import
                    (use once when switching note type versions)
  --prune           delete cards in Anki that have a guid::* tag but no longer
                    correspond to anything in clean/ (use after deleting cards)
  --no-cloud-sync   after local import, don't trigger Anki → AnkiWeb sync
                    (default triggers sync so mobile / other devices update)

Dependencies:
  Python 3.8+ (standard library only)
  Pygments (optional) — if installed, code blocks get syntax highlighting;
                        without it they fall back to plain monospace
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (_stream.encoding or "").lower() not in (
        "utf-8",
        "utf8",
    ):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

try:
    from pygments import highlight as _pyg_highlight
    from pygments.formatters import HtmlFormatter as _PygHtmlFormatter
    from pygments.lexers import get_lexer_by_name as _pyg_get_lexer
    from pygments.util import ClassNotFound as _PygClassNotFound

    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "clean"
ANKI_OUT = ROOT / "anki"
CONFIG_PATH = ROOT / "config.json"
DEFAULT_ANKI_CONNECT = "http://localhost:8765"
GUID_TAG_PREFIX = "guid::"
DEFAULT_CODE_LANG = "text"  # fallback lexer when a fence has no language

# ──────────────────────────── configuration ────────────────────────────
# Everything subject-specific lives in config.json. These are the fallbacks
# used when a key is absent (or config.json doesn't exist at all), so the tool
# still runs out of the box. apply_config() populates the module globals below.

DEFAULT_CONFIG = {
    "deck_root": "My Cards",
    # If null, derived as "<deck_root>-Advanced" / "<deck_root>-Basic".
    "advanced_deck_root": None,
    "model_name": None,
    "content_language": "en",
    # Folders under clean/ that hold prose / read-through notes — no cards.
    "no_card_folders": ["notes"],
    # A card whose tags line contains this tag (with or without a leading #)
    # lands under advanced_deck_root instead of deck_root, keeping hard cards
    # out of the normal review pile. Default fallback lexer for code: see above.
    "advanced_tag": "advanced",
    # The bold labels used in each .md card block. Keys are fixed roles;
    # values are whatever text you write in your notes. Defaults below are
    # zh-TW; override per project for English / other languages.
    "field_labels": {
        "question": "題目",
        "key_points": "重點",
        "answer": "解答",
        "source": "出處",
        "tags": "標籤",
    },
}

# Populated by apply_config().
DECK_ROOT = DEFAULT_CONFIG["deck_root"]
ADVANCED_DECK_ROOT = "My Cards-Advanced"
MODEL_NAME = "My Cards-Basic"
ADVANCED_TAG = "advanced"
CONTENT_LANGUAGE = "en"
NO_CARD_FOLDERS: set[str] = set(DEFAULT_CONFIG["no_card_folders"])
FIELD_LABELS = dict(DEFAULT_CONFIG["field_labels"])
DEFAULT_MODEL = (MODEL_NAME, "Front", "Back")
SECTION_RE = re.compile(r"^$")  # rebuilt in apply_config()


def load_config(path: Path) -> dict:
    """Read config.json merged over DEFAULT_CONFIG. Missing file → all defaults."""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠ Could not read {path.name} ({e}); using defaults.", file=sys.stderr)
            user = {}
        for k, v in user.items():
            if k == "field_labels" and isinstance(v, dict):
                cfg["field_labels"].update(v)
            else:
                cfg[k] = v
    return cfg


def apply_config(cfg: dict) -> None:
    """Populate module globals from a config dict and rebuild derived values."""
    global DECK_ROOT, ADVANCED_DECK_ROOT, MODEL_NAME, ADVANCED_TAG
    global CONTENT_LANGUAGE, NO_CARD_FOLDERS, FIELD_LABELS, DEFAULT_MODEL, SECTION_RE

    DECK_ROOT = cfg["deck_root"]
    ADVANCED_DECK_ROOT = cfg.get("advanced_deck_root") or f"{DECK_ROOT}-Advanced"
    MODEL_NAME = cfg.get("model_name") or f"{DECK_ROOT}-Basic"
    # Accept the advanced tag with or without a leading '#'.
    ADVANCED_TAG = str(cfg.get("advanced_tag", "advanced")).lstrip("#")
    CONTENT_LANGUAGE = cfg.get("content_language", "en")
    NO_CARD_FOLDERS = set(cfg.get("no_card_folders") or [])
    FIELD_LABELS = cfg["field_labels"]
    DEFAULT_MODEL = (MODEL_NAME, "Front", "Back")

    # Section labels keyed by role; the regex matches any of them followed by an
    # ASCII or full-width colon, so both `**Question:**` and `**題目：**` parse.
    # Group 2 captures inline content on the same line (e.g. `**tags:** #a #b`).
    labels = [re.escape(v) for v in FIELD_LABELS.values()]
    SECTION_RE = re.compile(r"^\*\*(" + "|".join(labels) + r")[:：]\*\*[ \t]*(.*)$")


HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# ──────────────────────────── Note type definition ────────────────────────────
# Anki's default "Basic" note type is center-aligned and ships zero code styling.
# We register our own note type with proper typography + One Dark-style code
# coloring. Pygments tokens use these class names; CSS below covers the rest.
#
# Card template uses {{FrontSide}} on the back so the question stays visible
# when reviewing the answer (standard Anki convention).

_PYG_FORMATTER = (
    _PygHtmlFormatter(cssclass="hl", nowrap=False) if HAS_PYGMENTS else None
)
_PYG_TOKEN_CSS = _PYG_FORMATTER.get_style_defs(".card .hl") if HAS_PYGMENTS else ""

MODEL_CSS = (
    r"""
.card {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans CJK TC",
               "Source Han Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif;
  font-size: 15px;
  line-height: 1.7;
  text-align: left;
  color: #2d3748;
  background: #ffffff;
  max-width: 760px;
  margin: 0 auto;
  padding: 16px 20px;
}
.nightMode.card { color: #e2e8f0; background: #1a202c; }

.card h3 {
  font-size: 18px;
  margin: 0 0 12px;
  color: #2c5282;
  border-bottom: 2px solid #bee3f8;
  padding-bottom: 6px;
}
.nightMode.card h3 { color: #63b3ed; border-bottom-color: #2c5282; }

.card h4 {
  font-size: 13px;
  margin: 18px 0 6px;
  color: #4a5568;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.nightMode.card h4 { color: #a0aec0; }

.card hr { border: none; border-top: 1px solid #e2e8f0; margin: 14px 0; }
.nightMode.card hr { border-color: #2d3748; }

.card p { margin: 8px 0; }
.card ul, .card ol { padding-left: 22px; margin: 8px 0; }
.card li { margin: 3px 0; }
.card strong { color: #1a365d; }
.nightMode.card strong { color: #90cdf4; }

/* Inline `code` */
.card code {
  font-family: "JetBrains Mono", "Fira Code", "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.88em;
  background: #edf2f7;
  color: #c53030;
  padding: 1px 5px;
  border-radius: 3px;
}
.nightMode.card code { background: #2d3748; color: #fc8181; }

/* Code blocks (both plain <pre><code> and Pygments .hl wrapper) */
.card pre,
.card div.hl,
.card div.hl pre {
  background: #282c34;
  color: #abb2bf;
  padding: 12px 16px;
  border-radius: 6px;
  overflow-x: auto;
  font-family: "JetBrains Mono", "Fira Code", "SF Mono", Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.5;
  text-align: left;
  margin: 8px 0;
}
.card div.hl { padding: 0; }                    /* outer wrapper has no padding */
.card div.hl pre { margin: 0; border-radius: 6px; }
.card pre code,
.card div.hl code {
  background: transparent;
  color: inherit;
  padding: 0;
  font-size: inherit;
  border-radius: 0;
}

/* Tables */
.card table {
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 14px;
  width: 100%;
}
.card th, .card td {
  border: 1px solid #cbd5e0;
  padding: 6px 10px;
  text-align: left;
  vertical-align: top;
}
.card th {
  background: #edf2f7;
  color: #1a365d;
  font-weight: 600;
}
.card tbody tr:nth-child(even) td { background: #f7fafc; }
.nightMode.card th, .nightMode.card td { border-color: #4a5568; }
.nightMode.card th { background: #2d3748; color: #90cdf4; }
.nightMode.card tbody tr:nth-child(even) td { background: #2a3441; }
"""
    + _PYG_TOKEN_CSS
)

# Override Pygments' default colors with One Dark-style palette so it matches
# the rest of the card (Pygments' defaults assume a white background).
MODEL_CSS += r"""
.card .hl .k, .card .hl .kd, .card .hl .kn, .card .hl .kp, .card .hl .kr,
.card .hl .kt { color: #c678dd; font-weight: normal; }
.card .hl .s, .card .hl .s1, .card .hl .s2, .card .hl .sb, .card .hl .sc,
.card .hl .sd, .card .hl .se, .card .hl .sh, .card .hl .si, .card .hl .sx,
.card .hl .sr, .card .hl .ss { color: #98c379; }
.card .hl .c, .card .hl .c1, .card .hl .cm, .card .hl .cs, .card .hl .ch,
.card .hl .cpf { color: #5c6370; font-style: italic; }
.card .hl .m, .card .hl .mi, .card .hl .mf, .card .hl .mh, .card .hl .mo,
.card .hl .mb, .card .hl .il { color: #d19a66; }
.card .hl .n, .card .hl .nl { color: #abb2bf; font-weight: normal; }
.card .hl .nf, .card .hl .nb { color: #61afef; }
.card .hl .nc, .card .hl .nn, .card .hl .nt, .card .hl .no { color: #e5c07b; font-weight: normal; }
.card .hl .o, .card .hl .ow { color: #56b6c2; }
.card .hl .cp { color: #c678dd; }
.card .hl .err { color: #e06c75; background: transparent; }
"""

MODEL_TEMPLATES = [
    {
        "Name": "Card 1",
        "Front": "{{Front}}",
        "Back": '{{FrontSide}}<hr id="answer">{{Back}}',
    }
]


# ──────────────────────────── markdown → html ────────────────────────────


def slugify(text: str) -> str:
    text = text.strip()
    text = re.sub(
        r"[\s/\\:*?\"<>|()\[\]{}!@#$%^&+=`~,.;'\"：，。；「」『』（）【】、？！]+",
        "-",
        text,
    )
    text = text.strip("-")
    return text or "untitled"


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline_md(s: str) -> str:
    placeholders: list[str] = []

    def stash(m: re.Match) -> str:
        placeholders.append(f"<code>{html_escape(m.group(1))}</code>")
        return f"\x00{len(placeholders) - 1}\x00"

    s = re.sub(r"`([^`]+)`", stash, s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: placeholders[int(m.group(1))], s)
    return s


_TABLE_SEP_CELL_RE = re.compile(r"^\s*:?-+:?\s*$")


def _is_table_separator(line: str) -> bool:
    s = line.strip()
    if "|" not in s or "-" not in s:
        return False
    cells = [c for c in s.strip("|").split("|")]
    return bool(cells) and all(_TABLE_SEP_CELL_RE.match(c) for c in cells)


def _parse_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [cell.strip() for cell in s.split("|")]


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    head_html = "".join(f"<th>{inline_md(h)}</th>" for h in headers)
    body_parts: list[str] = []
    width = len(headers)
    for row in rows:
        cells = (row + [""] * width)[:width]
        body_parts.append(
            "<tr>" + "".join(f"<td>{inline_md(c)}</td>" for c in cells) + "</tr>"
        )
    return f"<table><thead><tr>{head_html}</tr></thead><tbody>{''.join(body_parts)}</tbody></table>"


def md_to_html(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    in_code = False
    code_lang = ""
    code_buf: list[str] = []
    in_list = False

    def flush_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            if not in_code:
                flush_list()
                in_code = True
                code_lang = line[3:].strip()
                code_buf = []
            else:
                in_code = False
                out.append(render_code_block("\n".join(code_buf), code_lang))
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # Table: a row of pipes followed by a separator line.
        if (
            line.lstrip().startswith("|")
            and i + 1 < len(lines)
            and _is_table_separator(lines[i + 1])
        ):
            flush_list()
            headers = _parse_table_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_parse_table_row(lines[i]))
                i += 1
            out.append(render_table(headers, rows))
            continue

        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline_md(m.group(1))}</li>")
            i += 1
            continue

        flush_list()
        if line.strip() == "":
            i += 1
            continue
        out.append(f"<p>{inline_md(line)}</p>")
        i += 1

    flush_list()
    if in_code:
        out.append(render_code_block("\n".join(code_buf), code_lang))
    return "".join(out)


def render_code_block(code: str, lang: str) -> str:
    """Render a fenced code block. Uses Pygments when available; plain otherwise."""
    if HAS_PYGMENTS and _PYG_FORMATTER is not None:
        try:
            lexer = _pyg_get_lexer(lang or DEFAULT_CODE_LANG, stripnl=False)
        except _PygClassNotFound:
            try:
                lexer = _pyg_get_lexer("text", stripnl=False)
            except _PygClassNotFound:
                return f"<pre><code>{html_escape(code)}</code></pre>"
        # Pygments returns a complete <div class="hl"><pre>...</pre></div>
        return _pyg_highlight(code, lexer, _PYG_FORMATTER).strip()
    lang_attr = f' class="language-{lang}"' if lang else ""
    return f"<pre><code{lang_attr}>{html_escape(code)}</code></pre>"


# ──────────────────────────── clean/.md parsing ────────────────────────────


def parse_md(path: Path):
    text = path.read_text(encoding="utf-8")
    parts = HEADING_RE.split(text)
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections: dict[str, str] = {}
        current: str | None = None
        buf: list[str] = []
        for line in body.split("\n"):
            m = SECTION_RE.match(line)
            if m:
                if current is not None:
                    sections[current] = "\n".join(buf).strip()
                current = m.group(1)
                inline = m.group(2)
                buf = [inline] if inline.strip() else []
                continue
            if line.strip() == "---":
                continue
            if current is not None:
                buf.append(line)
        if current is not None:
            sections[current] = "\n".join(buf).strip()
        yield title, sections


def parse_tags(tag_line: str) -> list[str]:
    return re.findall(r"#([\w\-一-鿿]+)", tag_line)


def build_card(sub: str, topic: str, title: str, secs: dict[str, str]) -> dict | None:
    q_label = FIELD_LABELS["question"]
    kp_label = FIELD_LABELS["key_points"]
    ans_label = FIELD_LABELS["answer"]
    src_label = FIELD_LABELS["source"]
    tags_label = FIELD_LABELS["tags"]

    question = secs.get(q_label, "").strip()
    key_points = secs.get(kp_label, "").strip()
    answer = secs.get(ans_label, "").strip()
    source = secs.get(src_label, "").strip()
    tag_line = secs.get(tags_label, "").strip()

    if not question or not answer:
        return None

    front = f"<h3>{html_escape(title)}</h3>" + md_to_html(question)
    back_parts = []
    if key_points:
        back_parts.append(f"<h4>{html_escape(kp_label)}</h4>" + md_to_html(key_points))
    back_parts.append(f"<h4>{html_escape(ans_label)}</h4>" + md_to_html(answer))
    if source:
        back_parts.append(
            '<hr><p style="color:#888;font-size:0.85em">'
            + md_to_html(source).replace("<p>", "").replace("</p>", "")
            + "</p>"
        )
    back = "".join(back_parts)

    tags = parse_tags(tag_line)
    is_advanced = ADVANCED_TAG in tags
    tags.extend([sub, topic])
    guid = f"{sub}::{topic}::{slugify(title)}"
    tags.append(f"{GUID_TAG_PREFIX}{guid}")
    seen: set[str] = set()
    tags = [t for t in tags if not (t in seen or seen.add(t))]

    root = ADVANCED_DECK_ROOT if is_advanced else DECK_ROOT
    deck = f"{root}::{sub}::{topic}"
    return {
        "guid": guid,
        "deck": deck,
        "front": front,
        "back": back,
        "tags": tags,
    }


def discover_categories() -> list[str]:
    """Every subfolder of clean/ is a category, except `_`-prefixed folders
    (assets / archives) and the no-card folders (prose / read-through notes)."""
    if not CLEAN.is_dir():
        return []
    cats = []
    for child in sorted(CLEAN.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("_") or child.name.startswith("."):
            continue
        if child.name in NO_CARD_FOLDERS:
            continue
        cats.append(child.name)
    return cats


# ──────────────────────────── TSV output ────────────────────────────


def tsv_escape(s: str) -> str:
    # \n encoded as &#10; — preserved by Anki and rendered as line break inside <pre>.
    return s.replace("\t", "    ").replace("\r", "").replace("\n", "&#10;")


def write_tsv(
    sub: str, topic: str, cards: list[dict], model: tuple[str, str, str]
) -> Path | None:
    out_path = ANKI_OUT / sub / f"{topic}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not cards:
        if out_path.exists():
            out_path.unlink()
        return None

    model_name, front_field, back_field = model
    deck = cards[0]["deck"]
    header = [
        "#separator:tab",
        "#html:true",
        f"#notetype:{model_name}",
        f"#deck:{deck}",
        f"#columns:guid\t{front_field}\t{back_field}\tTags",
        "#guid column:1",
        "#tags column:4",
    ]
    rows = [
        "\t".join(
            [
                tsv_escape(c["guid"]),
                tsv_escape(c["front"]),
                tsv_escape(c["back"]),
                tsv_escape(" ".join(c["tags"])),
            ]
        )
        for c in cards
    ]
    out_path.write_text("\n".join(header + rows) + "\n", encoding="utf-8")
    return out_path


# ──────────────────────────── AnkiConnect sync ────────────────────────────


class AnkiConnectUnavailable(Exception):
    pass


class AnkiConnectError(Exception):
    pass


def ensure_model(url: str) -> tuple[str, str, str]:
    """Create our custom note type if it doesn't exist; always refresh its CSS.

    Returns (model_name, front_field, back_field). The custom type lets us own
    the styling (left-align, code blocks, syntax highlighting) without touching
    the user's other Anki note types.
    """
    models = anki_invoke(url, "modelNames") or []
    if MODEL_NAME not in models:
        anki_invoke(
            url,
            "createModel",
            modelName=MODEL_NAME,
            inOrderFields=["Front", "Back"],
            css=MODEL_CSS,
            cardTemplates=MODEL_TEMPLATES,
        )
    else:
        # Refresh CSS so any CSS tweaks in this file propagate without manual edits in Anki.
        try:
            anki_invoke(
                url, "updateModelStyling", model={"name": MODEL_NAME, "css": MODEL_CSS}
            )
        except AnkiConnectError as e:
            print(
                f"  ⚠ Failed to update model styling (continuing): {e}", file=sys.stderr
            )
    return MODEL_NAME, "Front", "Back"


def reset_notes(url: str) -> int:
    """Delete all notes with a `guid::*` tag. Returns count deleted.

    Used when migrating to a new note type: old notes can't change their type
    in-place via AnkiConnect, so we wipe + re-add.
    """
    ids = anki_invoke(url, "findNotes", query="tag:guid::*") or []
    if ids:
        anki_invoke(url, "deleteNotes", notes=ids)
    return len(ids)


def anki_invoke(url: str, action: str, **params):
    body = json.dumps({"action": action, "version": 6, "params": params}).encode(
        "utf-8"
    )
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        raise AnkiConnectUnavailable(str(e)) from e
    if payload.get("error"):
        raise AnkiConnectError(payload["error"])
    return payload.get("result")


def sync_to_anki(
    url: str, cards: list[dict], model: tuple[str, str, str], prune: bool = False
) -> dict:
    """Push cards to AnkiConnect, skipping no-op updates.

    Strategy:
      1. deckNames once; createDeck only for missing decks (batched via multi).
      2. findNotes once with tag:guid::* to get all tracked note IDs.
      3. notesInfo once → build {guid: noteInfo} with current fields & tags.
      4. Classify each card add / update / unchanged via content diff.
         Skipping unchanged avoids bumping mod timestamps and dirtying AnkiWeb.
      5. addNotes (batch) for new cards.
      6. multi(updateNote × N) for changed cards only.
      7. If prune=True, deleteNotes for tracked notes whose guid is no longer
         in clean/ — keeps Anki in sync with the source of truth.

    Returns {"added": N, "updated": M, "unchanged": U, "moved": Mv, "failed": K,
             "orphans": O, "deleted": D, "decks": [...]}
    """
    model_name, front_field, back_field = model
    decks = sorted({c["deck"] for c in cards})

    # 1. Only create decks that don't exist yet. Saves up to len(decks)-1 RTTs.
    try:
        existing_decks = set(anki_invoke(url, "deckNames") or [])
    except AnkiConnectError:
        existing_decks = set()
    missing_decks = [d for d in decks if d not in existing_decks]
    if missing_decks:
        anki_invoke(
            url,
            "multi",
            actions=[
                {"action": "createDeck", "params": {"deck": d}} for d in missing_decks
            ],
        )

    # 2+3. Build {guid: noteInfo} for every previously synced note.
    guid_to_info: dict[str, dict] = {}
    try:
        existing_ids = (
            anki_invoke(url, "findNotes", query=f"tag:{GUID_TAG_PREFIX}*") or []
        )
    except AnkiConnectError:
        existing_ids = []
    if existing_ids:
        infos = anki_invoke(url, "notesInfo", notes=existing_ids) or []
        for info in infos:
            for tag in info.get("tags", []) or []:
                if tag.startswith(GUID_TAG_PREFIX):
                    guid_to_info[tag[len(GUID_TAG_PREFIX) :]] = info
                    break

    # 4. Diff-before-update. Anki bumps mod timestamp on every updateNote,
    # which dirties AnkiWeb sync even when content is identical — skip no-ops.
    to_add: list[dict] = []
    to_update: list[tuple[int, dict]] = []
    unchanged = 0
    for c in cards:
        info = guid_to_info.get(c["guid"])
        if info is None:
            to_add.append(c)
            continue
        cur_front = info["fields"].get(front_field, {}).get("value", "")
        cur_back = info["fields"].get(back_field, {}).get("value", "")
        cur_tags = set(info.get("tags") or [])
        if (
            cur_front == c["front"]
            and cur_back == c["back"]
            and cur_tags == set(c["tags"])
        ):
            unchanged += 1
            continue
        to_update.append((info["noteId"], c))

    added = updated = failed = 0

    # 3. Batch add new cards.
    if to_add:
        payload = [
            {
                "deckName": c["deck"],
                "modelName": model_name,
                "fields": {front_field: c["front"], back_field: c["back"]},
                "tags": c["tags"],
                "options": {"allowDuplicate": True},
            }
            for c in to_add
        ]
        try:
            results = anki_invoke(url, "addNotes", notes=payload) or []
        except AnkiConnectError as e:
            print(f"  ! addNotes failed: {e}", file=sys.stderr)
            results = [None] * len(to_add)
        for r in results:
            if r:
                added += 1
            else:
                failed += 1

    # 4. Batch update existing cards via multi action (one HTTP call).
    if to_update:
        actions = [
            {
                "action": "updateNote",
                "params": {
                    "note": {
                        "id": nid,
                        "fields": {front_field: c["front"], back_field: c["back"]},
                        "tags": c["tags"],
                    }
                },
            }
            for nid, c in to_update
        ]
        try:
            results = anki_invoke(url, "multi", actions=actions) or []
        except AnkiConnectError as e:
            print(f"  ! multi(updateNote) failed: {e}", file=sys.stderr)
            results = [{"error": str(e)}] * len(to_update)
        for r in results:
            # multi returns either raw results or {result, error} dicts.
            if isinstance(r, dict) and r.get("error"):
                failed += 1
            else:
                updated += 1

    # 4b. Move already-synced cards whose deck no longer matches the source —
    # e.g. a card tagged with the advanced tag after it was first imported needs
    # to migrate from <deck_root>::… to <advanced_deck_root>::…. changeDeck moves
    # the card in place, so its review history survives (deleting + re-adding
    # would reset scheduling). New cards are skipped: addNotes already placed
    # them in the right deck.
    moved = 0
    cardid_to_target: dict[int, str] = {}
    for c in cards:
        info = guid_to_info.get(c["guid"])
        if info is None:
            continue
        for cid in info.get("cards", []) or []:
            cardid_to_target[cid] = c["deck"]
    if cardid_to_target:
        try:
            cinfos = anki_invoke(url, "cardsInfo", cards=list(cardid_to_target)) or []
        except AnkiConnectError as e:
            print(f"  ! cardsInfo failed (skipping deck moves): {e}", file=sys.stderr)
            cinfos = []
        by_deck: dict[str, list[int]] = {}
        for ci in cinfos:
            cid = ci.get("cardId")
            target = cardid_to_target.get(cid)
            if target and ci.get("deckName") and ci["deckName"] != target:
                by_deck.setdefault(target, []).append(cid)
        for target, cids in by_deck.items():
            try:
                anki_invoke(url, "changeDeck", cards=cids, deck=target)
                moved += len(cids)
            except AnkiConnectError as e:
                print(f"  ! changeDeck → {target} failed: {e}", file=sys.stderr)

    # 5. Orphan detection + optional pruning. Tracked notes whose guid no
    # longer appears in clean/ are stale; with --prune we delete them.
    current_guids = {c["guid"] for c in cards}
    orphan_pairs = [
        (guid, info)
        for guid, info in guid_to_info.items()
        if guid not in current_guids
    ]
    deleted = 0
    if orphan_pairs and prune:
        orphan_ids = [info["noteId"] for _, info in orphan_pairs]
        try:
            anki_invoke(url, "deleteNotes", notes=orphan_ids)
            deleted = len(orphan_ids)
        except AnkiConnectError as e:
            print(f"  ! deleteNotes(prune) failed: {e}", file=sys.stderr)

    return {
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "moved": moved,
        "failed": failed,
        "orphans": len(orphan_pairs),
        "deleted": deleted,
        "orphan_guids": [g for g, _ in orphan_pairs],
        "decks": decks,
    }


# ──────────────────────────── main ────────────────────────────


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--no-sync", action="store_true", help="only write TSV, skip AnkiConnect"
    )
    ap.add_argument(
        "--sync-url",
        default=DEFAULT_ANKI_CONNECT,
        help=f"AnkiConnect endpoint (default {DEFAULT_ANKI_CONNECT})",
    )
    ap.add_argument(
        "--reset",
        action="store_true",
        help="delete every guid::* card, then re-import (use when changing note type)",
    )
    ap.add_argument(
        "--prune",
        action="store_true",
        help="delete cards in Anki tagged guid::* that no longer exist in clean/",
    )
    ap.add_argument(
        "--no-cloud-sync",
        action="store_true",
        help="after local import, don't trigger Anki → AnkiWeb sync",
    )
    args = ap.parse_args(argv)

    apply_config(load_config(CONFIG_PATH))
    print(f"Config: deck root '{DECK_ROOT}', model '{MODEL_NAME}', language '{CONTENT_LANGUAGE}'")
    print(
        f"Pygments syntax highlighting: {'on' if HAS_PYGMENTS else 'off (pip install pygments)'}"
    )

    # Decide model upfront so TSV and AnkiConnect agree on field names.
    model: tuple[str, str, str] = DEFAULT_MODEL
    anki_reachable = False
    if not args.no_sync:
        try:
            anki_invoke(args.sync_url, "version")
            anki_reachable = True
            try:
                model = ensure_model(args.sync_url)
                print(f"Note type ready: {model[0]} (fields: {model[1]} / {model[2]})")
            except AnkiConnectError as e:
                print(f"  ⚠ Could not ensure note type {MODEL_NAME}: {e}")
                anki_reachable = False
        except AnkiConnectUnavailable as e:
            print(f"⚠ Anki Desktop / AnkiConnect not reachable: {e}")
            print(
                "  Continuing with TSV-only output; start Anki and re-run to auto-import."
            )

    if args.reset:
        if not anki_reachable:
            print("✘ --reset requires AnkiConnect to be reachable.", file=sys.stderr)
            return 2
        n = reset_notes(args.sync_url)
        print(f"Reset: deleted {n} old notes (tag:guid::*)")

    total_cards = 0
    files_written = 0
    all_cards: list[dict] = []

    categories = discover_categories()
    if not categories:
        print(
            f"\nNo card categories found under {CLEAN.relative_to(ROOT)}/. "
            "Create a subfolder with .md files to get started.",
            file=sys.stderr,
        )
    print(f"Categories: {', '.join(categories) if categories else '(none)'}")

    for sub in categories:
        subdir = CLEAN / sub
        for md in sorted(subdir.glob("*.md")):
            topic = md.stem
            cards: list[dict] = []
            for title, secs in parse_md(md):
                card = build_card(sub, topic, title, secs)
                if card:
                    cards.append(card)
            out = write_tsv(sub, topic, cards, model)
            status = f"{len(cards)} cards" if cards else "skipped (no Q&A blocks)"
            tail = f"  → anki/{sub}/{topic}.txt" if out else ""
            print(f"  clean/{sub}/{md.name}: {status}{tail}")
            total_cards += len(cards)
            if cards:
                files_written += 1
            all_cards.extend(cards)

    print(
        f"\nTSV: {total_cards} cards across {files_written} files → {ANKI_OUT.relative_to(ROOT)}/"
    )

    if not anki_reachable:
        return 0

    print(f"\nAnkiConnect → {args.sync_url}")
    result = sync_to_anki(args.sync_url, all_cards, model, prune=args.prune)
    print(f"  Decks ensured: {len(result['decks'])}")
    print(
        f"  Added: {result['added']}  Updated: {result['updated']}  Unchanged: {result['unchanged']}  Moved: {result['moved']}  Failed: {result['failed']}"
    )
    if result["orphans"]:
        if args.prune:
            print(f"  Pruned: {result['deleted']} orphan cards deleted")
        else:
            print(
                f"  Orphans: {result['orphans']} cards in Anki but no longer in clean/ — add --prune to delete them"
            )
            sample = result["orphan_guids"][:5]
            for g in sample:
                print(f"    - {g}")
            if len(result["orphan_guids"]) > len(sample):
                print(f"    ... and {len(result['orphan_guids']) - len(sample)} more")

    exit_code = 0 if result["failed"] == 0 else 1

    # AnkiWeb cloud sync — push local changes up so mobile / other devices see them.
    something_changed = (
        result["added"] + result["updated"] + result["moved"] + result["deleted"]
    ) > 0 or args.reset
    if args.no_cloud_sync:
        print("\nAnkiWeb sync skipped (--no-cloud-sync).")
    elif not something_changed:
        print("\nAnkiWeb sync skipped (nothing changed).")
    else:
        print("\nAnkiWeb sync → triggering...")
        try:
            anki_invoke(args.sync_url, "sync")
            print("  ✓ Sync triggered. Check Anki's sync icon to confirm it finished.")
        except AnkiConnectError as e:
            print(f"  ✘ AnkiWeb sync failed: {e}", file=sys.stderr)
            print("    Common causes:", file=sys.stderr)
            print(
                "    - Anki not logged into AnkiWeb (Tools → Preferences → Sync)",
                file=sys.stderr,
            )
            print("    - No network / AnkiWeb service issue", file=sys.stderr)
            print(
                "    - schema conflict (first run: in Anki, Sync once and choose Upload to AnkiWeb)",
                file=sys.stderr,
            )
            print(
                "    Local cards are saved; click Anki's Sync button later to retry.",
                file=sys.stderr,
            )
            exit_code = max(exit_code, 1)
        except AnkiConnectUnavailable as e:
            print(f"  ✘ AnkiConnect disconnected: {e}", file=sys.stderr)
            exit_code = max(exit_code, 1)

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
