# anki-pipeline

A general-purpose pipeline that turns raw study notes on **any subject** into
[Anki](https://apps.ankiweb.net/) flashcards. Drop material into `raw/`, curate
it into categorized markdown under `clean/`, and a script pushes the cards
straight into Anki Desktop (and on to AnkiWeb / your phone).

```
raw/      inbox — drop notes, links, images here
  _processed/   archived originals after processing
  _fetched/     saved web snapshots
clean/    curated cards (source of truth)
  <category>/   any subfolder = a category = an Anki sub-deck
  notes/        prose / read-through material (no cards) — see config
  _asset/       shared images
anki/     generated TSV mirror of clean/
tools/    md_to_anki.py — the generator
config.json   ALL subject-specific settings live here
CLAUDE.md     instructions for Claude Code to run the workflow
```

## Quick start

1. **Edit `config.json`** — set `deck_root` to your deck name. Optionally set
   `content_language` and switch `field_labels` to your language.

   ```json
   {
     "deck_root": "My Cards",
     "content_language": "zh-TW",
     "no_card_folders": ["notes"],
     "field_labels": { "question": "題目", "key_points": "重點",
                       "answer": "解答", "source": "出處", "tags": "標籤" }
   }
   ```

2. **Add cards** — make a folder under `clean/` (e.g. `clean/spanish/`) and write
   `.md` files. Each `## heading` with a question/answer block is one card:

   ```markdown
   ## What does "hola" mean?

   **題目：**

   Translate: hola

   **解答：**

   Hello.

   **標籤：** #greetings
   ```

   New folders auto-become categories — no config edit needed. Folders starting
   with `_` and those listed in `no_card_folders` produce no cards.

3. **Generate** —

   ```bash
   python3 tools/md_to_anki.py
   ```

   Writes `anki/<category>/<topic>.txt`, and if Anki Desktop is running with
   AnkiConnect, imports the cards directly (and triggers AnkiWeb sync).

## One-time Anki setup

1. Install [Anki Desktop](https://apps.ankiweb.net/).
2. Install AnkiConnect: Tools → Add-ons → Get Add-ons → code `2055492159` →
   restart. (Or, with Anki **closed**, run `bash scripts/install-ankiconnect.sh`.)
3. Log into AnkiWeb (Tools → Preferences → Sync) for phone sync.

Without Anki / AnkiConnect the script still works — it just writes the TSV files
for manual import (File → Import).

## Useful flags

| Flag | Effect |
|------|--------|
| `--no-sync` | only write TSV, skip AnkiConnect |
| `--no-cloud-sync` | local import but don't trigger AnkiWeb sync |
| `--sync-url URL` | custom AnkiConnect endpoint |
| `--reset` | delete all `guid::*` cards, then re-import (note-type migration) |
| `--prune` | delete Anki cards that no longer exist in `clean/` |

Optional: `pip install pygments` for syntax-highlighted code blocks on cards.

## Using with Claude Code

`CLAUDE.md` tells Claude Code how to process `raw/` into `clean/` automatically —
just say "process raw" once you've dropped material in. See `CLAUDE.md` for the
full workflow.
