# CLAUDE.md — General-purpose Anki card pipeline

This project collects raw study material on **any subject** and turns it into
reviewable Anki flashcards.

`raw/` is an inbox — drop any kind of material in (notes, `.url` shortcuts,
links, images…).
`clean/` is the curated, categorized result — the source of truth for cards.
`tools/md_to_anki.py` turns `clean/` into Anki decks.

**Everything subject-specific lives in `config.json`** (deck name, model name,
card field labels, content language, which folders skip card generation). The
workflow below is the same regardless of subject — only `config.json` and the
folders you create under `clean/` change.

---

## Configuration (`config.json`)

```json
{
  "deck_root": "My Cards",          // top-level Anki deck name
  "advanced_deck_root": null,        // null → "<deck_root>-Advanced"
  "model_name": null,                // null → "<deck_root>-Basic"
  "content_language": "zh-TW",       // language you write card CONTENT in
  "no_card_folders": ["notes"],      // clean/ folders that are prose, not cards
  "advanced_tag": "advanced",        // tag that moves a card to the advanced deck
  "field_labels": {                  // the bold labels you use in each card block
    "question": "題目",
    "key_points": "重點",
    "answer": "解答",
    "source": "出處",
    "tags": "標籤"
  }
}
```

- **Categories are auto-discovered**: every subfolder of `clean/` is a category
  (and an Anki sub-deck), *except* folders starting with `_` (assets/archives)
  and folders listed in `no_card_folders`. To add a category, just make a folder.
- **Field labels** are matched with either an ASCII or full-width colon, so both
  `**Question:**` and `**題目：**` parse. Write card content in
  `content_language`; keep code, API names, and proper nouns in their original
  form.
- **This file and card content can be in different languages.** These
  instructions are language-neutral; the cards follow `content_language`.

---

## When to run

**Only process `raw/` when the user explicitly asks.** Do not scan or tidy on
your own.

Trigger phrases: "process raw", "integrate raw into clean", "make cards from
the new material", or anything clearly pointing at "move `raw/` content into
`clean/`". If the user only vaguely mentions they added something, confirm
before acting.

---

## Pipeline (run after a trigger)

### 1. Scan `raw/` root

Look only at the root — **do not recurse into `_processed/` or `_fetched/`**.

| File type | Action |
|-----------|--------|
| `.md` (plain notes) | parse for cards |
| `.md` (contains a URL) | fetch a snapshot (step 2), then parse |
| `.url` (Windows shortcut) | read the URL, fetch a snapshot |
| `.png` / `.jpg` / `.gif` | move to `clean/_asset/`, reference with a relative path |
| anything else | ask the user |

### 2. Fetch web content

For each URL to fetch:

1. Use `WebFetch`.
2. Save the raw fetched content to `raw/_fetched/<source-name-or-url-slug>.md`
   — the snapshot guards against the page changing or disappearing later.
3. Parse the snapshot as a card source.

**On failure**: leave the file in `raw/` (don't move it), list
`fetch failed: <file> — <reason>` in the final report, and continue with the
rest. Never abort the whole run.

### 3. Classify and split

First decide each raw file's (or snapshot's) **type**:

- **Q&A type** — built around "question + answer" (exam dumps, fact lists,
  problem sets). One file may span several topics; **split it card by card**
  into the right `clean/<category>/` folder. Use the §4 card format.
- **Prose type** — narrative / experience / guide (write-ups, summaries,
  reading material). **Do not chop into cards.** Put the whole piece into a
  `no_card_folders` folder (e.g. `clean/notes/<topic>.md`) using the §4b format.
  - If the prose **lists concrete reviewable items** (e.g. "common questions:
    X, Y, Z"), also write each as a card stub in the matching category folder,
    with its **source** pointing back to the prose file.

When unsure, treat it as prose, file it under a `no_card_folders` folder, then
extract any cards from it.

**Choosing a category folder**: pick the most specific existing folder under
`clean/`. If none fits, create a new folder (it auto-becomes a category) — but
if the boundary is genuinely unclear, ask the user first.

### 4. Card format (Q&A type)

Every card in a category folder uses this format. The bold labels come from
`config.json` `field_labels` (shown here with the default zh-TW labels):

```markdown
## <short card title>

**題目：**

<original question text, code block, or image>

**重點：**

- <key concept 1>
- <key concept 2>

**解答：**

<full answer, code, further explanation>

**出處：** `raw/<source>.md #<n>` or `https://<URL>`

**標籤：** #tag1 #tag2 #tag3
```

Notes:
- **Never reveal the answer in the card front.** The front is the `## title`
  + the **question** section (§7a) — neither may give away the answer. Keep the
  title neutral/topical (name the problem, don't state its result), and don't
  let the question text, hints, or examples leak the solution. Key points and
  the answer live on the **back** only.
- **Question**: keep the original wording; don't rewrite needlessly.
- For **library-function reimplementation** questions (hand-rolling something
  like `strcpy`/`atoi`), include the standard **prototype** in a code block so
  the signature is unambiguous, and prefix your implementation (e.g. `my_`) to
  avoid clashing with the real symbol. Plain data-structure/algorithm problems
  don't need this.
- **Key points**: 3–6 bullets for a fast pre-review skim.
- **Answer**: a complete explanation. If the source lacks one, write it and
  mark `<!-- answer added by Claude -->` at the end.
- **Source**: comma-separate multiple origins:
  `raw/a.md #3, raw/b.md #10`.
- **Tags**: kebab-case, `#`-prefixed, so they're greppable
  (`grep "#mutex" clean/`).
- **Advanced marking**: add the `advanced_tag` (default `#advanced`) to a card's
  tag line to move it to the separate advanced deck — keeps hard cards out of
  daily review. The file doesn't move; only its deck changes. See §7.
- If a card came from a prose file, point **source** back at it:
  `clean/notes/<file>.md §<section>`.

### 4b. Prose format (`no_card_folders` folders)

```markdown
## <title or topic>

**Source:** `raw/<file>` or `https://<URL>`
**Author background:** <a line or two, if relevant>
**TL;DR:**

- 3–5 key takeaway bullets

**Body:**

<keep the original structure; preserve the author's voice and key passages;
cut filler / repetition / off-topic chatter>
<keep original lists, tables, quotes>
<if it's short (< ~1500 words) keep it whole; if long, summarize + keep the key
passages as blockquotes>

**Referenced cards:** (if the piece lists concrete reviewable items)

- <item> → `clean/<category>/<file>.md` §<section>
- ...

**Tags:** #tag1 #tag2 ...
```

Notes:
- **Don't** chop prose into Q&A cards — reading a whole write-up beats 50 bullets.
- **Referenced cards** is a two-way cross-reference: the prose lists items, and
  each card's **source** points back to the prose.
- If a passage is itself a complete Q&A, extract it into the matching category
  folder (§4 format) and list it under **Referenced cards**.

### 5. Deduplicate

When the same card appears in multiple sources:
- **Merge** into one entry.
- List all origins in **Source**.
- Keep the most complete/clear version as the base; fold any extra key points
  from the others in.
- Same core question = same card, even if the wording differs.

### 6. Archive processed sources

Once a file's cards are written into `clean/`:
- move the original from `raw/` root to `raw/_processed/<file>`.
- web snapshots stay in `raw/_fetched/` (don't touch them again).

**Never delete any source file.**

### 7. Generate / import Anki cards

After updating `clean/`, run:

```bash
python3 tools/md_to_anki.py
```

The script:

**(a) Writes TSV** — scans every auto-discovered category folder under `clean/`
and writes `anki/<category>/<topic>.txt` (importable manually via File → Import):

- **Front** = `## title` + the question section (code blocks → `<pre><code>`)
  — **must not reveal the answer** (see §4); keep the title neutral and the
  question free of solution hints.
- **Back** = key-points + answer + source
- **Tags** = the tags line + `<category>` + `<topic>` +
  `guid::<category>::<topic>::<slug>` (used for dedup)
- **Deck** = `<deck_root>::<category>::<topic>` (auto-created)
  - **Advanced cards**: a tag line containing `advanced_tag` lands the card in
    `<advanced_deck_root>::<category>::<topic>` instead. The file doesn't move and
    the guid is unchanged, so an already-imported card is moved in place
    (`changeDeck`) — review history is preserved.

**(b) Auto-imports to Anki Desktop (AnkiConnect)** — POSTs to
`http://localhost:8765`:

- matches existing cards by `guid::...` tag → `updateNote`, else `addNote`
- no duplicates; idempotent (re-runnable any number of times)
- **if Anki is closed / AnkiConnect is missing it doesn't fail** — it just skips
  the auto-import and tells you to import the TSV manually later

**(c) Auto-syncs to AnkiWeb** — after a local import that actually changed
something, it triggers Anki → AnkiWeb sync so mobile (AnkiDroid / AnkiMobile)
picks up the latest. On failure it prints the error (usually: not logged in /
network / schema conflict) and exits non-zero.

**One-time setup**:
1. Install Anki Desktop.
2. Tools → Add-ons → Get Add-ons → code `2055492159` (AnkiConnect) → restart.
   (Or run `bash scripts/install-ankiconnect.sh` with Anki closed.)
3. Tools → Preferences → Sync → log into AnkiWeb (needed for (c)).

**Flags**:
- `--no-sync` — only write TSV, skip AnkiConnect entirely
- `--no-cloud-sync` — write to local Anki but don't trigger AnkiWeb sync
- `--sync-url URL` — custom AnkiConnect endpoint
- `--reset` — delete all `guid::*` cards, then re-import (note-type migration)
- `--prune` — delete cards in Anki tagged `guid::*` that no longer exist in
  `clean/` (after deleting cards from `.md`; default only warns)

**Rules**:
- ✅ Re-run the script after any `clean/` change (idempotent).
- ✅ `no_card_folders` folders produce no cards (prose is read whole, not carded).
- ✅ A card missing its **question** or **answer** section is skipped.
- ❌ Don't hand-edit files under `anki/` (the script overwrites them).
- ❌ Don't remove `guid::...` tags (dedup relies on them).
- ℹ After deleting cards from a `.md`, run with `--prune` to clear the orphan
  cards in Anki; without `--prune` it only warns.

### 8. Git commit

Commit the whole batch as **one commit**:

```
process raw: <main file names, comma-separated, max 5 then ...>

- new cards: N
- merged into existing: M
- web snapshots fetched: K
- Anki: wrote X TSV cards; AnkiConnect: added Y / updated Z (or skipped: Anki not running)
- fetch failed: <files, omit if none>
```

### 9. Report

Finish with a markdown table:

| Source | Result | Written to |
|--------|--------|-----------|
| `raw/a.md` | 10 cards | `clean/<cat>/x.md` (8), `clean/<cat>/y.md` (2) |
| `raw/foo.url` | fetch failed | — |

Plus an Anki summary: `Anki: X TSV cards → anki/; AnkiConnect: added Y / updated
Z / failed W (or: Anki not running, auto-import skipped)`.

---

## Rules & limits

### Content
- **Language**: write `clean/` card content in `config.json`'s
  `content_language`; keep code, API names, and proper nouns in their original
  form.
- **File names**: lowercase + hyphens for category `.md` files
  (`function-pointer.md`). Non-Latin names are fine when the subject calls for it.
- **Images**: all images go in `clean/_asset/`; reference them with a relative
  path: `![alt](../_asset/foo.png)`.

### Behavior
- ✅ Only process `raw/` when explicitly asked.
- ✅ One commit per processing run.
- ✅ Move processed sources to `_processed/`, **never delete**.
- ✅ Keep web snapshots in `_fetched/` as backups.
- ✅ Always run `python3 tools/md_to_anki.py` before committing.
- ❌ Don't scan `raw/` proactively.
- ❌ Don't delete `raw/` sources.
- ❌ Don't write the same card twice in `clean/` (merge via the **source** field).
- ❌ Don't put `_fetched/` snapshots directly into `clean/` (they're raw material).
- ❌ Don't modify archived files under `_processed/`.
- ❌ Don't hand-edit `.txt` files under `anki/` (script-generated, overwritten).

### When unsure
- Category boundary unclear → ask the user.
- Card meaning ambiguous → keep the original text, note
  `<!-- original wording unclear, needs confirmation -->` in the answer.
- No suitable existing `.md` → create one with the most specific name.
