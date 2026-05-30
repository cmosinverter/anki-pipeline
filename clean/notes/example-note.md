# Example prose note

This is a seed file showing the **prose** format. Files under `notes/` (a
`no_card_folders` folder in `config.json`) are read-through material and produce
**no Anki cards**. Delete this once you understand the format.

## How to use this pipeline

**Source:** seed example
**TL;DR:**

- Drop raw material into `raw/`, then ask Claude to "process raw".
- Q&A material becomes cards under a category folder; prose goes under `notes/`.
- Run `python3 tools/md_to_anki.py` to push cards into Anki.

**Body:**

Categories are just folders under `clean/`. Make a folder named after your
subject (e.g. `spanish`, `history`, `system-design`) and put `.md` files of
cards in it — each `## heading` with a question/answer block becomes one card.

Edit `config.json` to set your deck name and, if you want, switch the card
field labels to your language. That's the only file that knows about your
subject.

**Tags:** #example #meta
