# Bhap

**Your knowledge. Your machine. Your intelligence.**

Bhap is a private, offline, terminal-first knowledge assistant. Add your PDF, DOCX, PPTX, Markdown and text files; Bhap indexes them locally and a local LLM (Ollama or llama.cpp) answers questions with **citations that point to the real page, slide or section** — even with Wi-Fi off.

```text
$ cd ~/Kuliah/Semester-3
$ bhap

  ▄▀▄   ▄▀▄   Bhap v1.1.0
  █ ◉ ▄ ◉ █   qwen2.5:7b · bge-m3 · ● offline siap
  █  ▀▀▀  █   ~/Kuliah/Semester-3
   ▀▀▄ ▄▀▀    Workspace: semester-3 · 24 dokumen

❯ /upload ~/Kuliah/basis-data/pbd2.pdf
✓ pbd2.pdf · 38 halaman · 112 chunk · terindeks lokal  #7 · 4.1s
❯ Apa beda ERD konseptual dan model relasional menurut materi ini?
ERD konseptual menggambarkan entitas, atribut, dan relasi [1], sedangkan model relasional
menyimpan data dalam tabel dengan primary key dan foreign key [2].

  Sumber:
  [1] pbd2.pdf — halaman 11 · § Bab 3 > ERD
  [2] basis-data.pptx — slide 7 “Model Relasional”
```

## Why Bhap

- **Offline by construction.** After setup, ingest, search, chat, study tools and backups never need the internet. The `bhap` process installs a network guard that refuses every non-loopback connection; the only peer it can reach is your local model runtime on `127.0.0.1`.
- **Citations you can trust.** The model may only cite numbered evidence that Bhap retrieved. Every marker is validated; page/slide/section locators come from the parser, never from generated text. Fabricated citations are dropped and reported.
- **Says "not found".** In the default *source-only* mode, questions without evidence get `Tidak ditemukan dalam materi yang dipilih.` plus a knowledge-gap hint instead of a confident guess.
- **Terminal-native.** Bare `bhap` opens an interactive session: `cd`, `ls`, paste a file path to import it, ask in Bahasa Indonesia or English. Every feature is also a scriptable subcommand with stable `--json` output and consistent exit codes.
- **Learn, not just search.** Tiered summaries, teach-from-zero lessons, a Socratic tutor, quizzes with graded answers, flashcards with spaced repetition, and an exam sprint over your weak spots — all grounded in your material.
- **Light.** One required dependency (`pypdf`). Optional extras: `numpy` (faster vectors) and `cryptography` (encrypted backups).

## Install

Prerequisites: Git, Python 3.10+, and a local model runtime ([Ollama](https://ollama.com/download) recommended).

```sh
git clone https://github.com/<owner>/bhap.git && cd bhap
python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[all]"    # or just `-e .` for the minimal install
bhap --help
```

Arch / CachyOS: `sudo pacman -S python ollama` (optional OCR: `tesseract tesseract-data-ind tesseract-data-eng poppler`).
Ubuntu: `sudo apt install python3-venv` + Ollama installer (optional OCR: `tesseract-ocr tesseract-ocr-ind poppler-utils`).
More detail, including air-gapped installs: [docs/installation.md](docs/installation.md).

## First run (online once, then offline forever)

```sh
bhap setup                            # shows hardware profile, suggests models, asks before any download
bhap doctor --offline                 # Offline Readiness Check
# turn Wi-Fi off, then:
bhap doctor --offline --verify        # end-to-end embedding + chat test without internet
```

You do not need to keep `ollama serve` running yourself: when Ollama is installed but not running, Bhap starts it on `127.0.0.1` for the commands that need a model and stops it again when Bhap exits (`bhap config set auto_start_runtime false` to opt out).

Non-interactive setup: `bhap setup --download --llm qwen2.5:7b --embedding bge-m3`. Downloads are performed by the local Ollama daemon and happen **only** when you pass `--download` or confirm a prompt. See [docs/model-support.md](docs/model-support.md) for model choices per hardware profile.

## Quickstart

```sh
bhap workspace create kuliah --use
bhap add ./materi --recursive                 # previews the folder and asks before importing
bhap docs list                                # pages/slides, chunks, parsing warnings
bhap search "normalisasi database"
bhap ask "Apa itu normalisasi database?" --sources
echo "Apa itu ERD?" | bhap ask --stdin --json
bhap summarize pbd1.pdf --level short
bhap compare pbd1.pdf pbd2.pdf --topic normalisasi
bhap study teach "Dijkstra"
bhap study quiz "Dijkstra" --count 5
bhap                                          # interactive session
```

### Inside the interactive session

| Input | What happens |
|---|---|
| `cd ../Semester-4`, `pwd`, `ls` | Moves the *session* cwd only; never reads or indexes files |
| `/upload ~/Downloads/ERD final.pdf` | Uploads and indexes a file or folder (spaces need no quotes) |
| `./slides/week2.pptx`, `"/home/me/ERD final.pdf"` | Pasting a path also imports it |
| `./materi/` | Shows a summary and asks before a recursive import (with exclude patterns) |
| `Jelaskan isi @./materi/graph.pdf` | Imports if needed, then answers from that file only |
| `/sources`, `/open 2`, `/open 2 --app` | Sources of the last answer; preview with highlight; open original |
| `/mode general` | `strict` (source only) · `general` (+ labelled general knowledge) · `brainstorm` |
| `/scope pbd1.pdf bab2/` · `/scope clear` | Restrict retrieval to documents/folders |
| `/teach`, `/tutor`, `/quiz`, `/flashcards`, `/review` | Study tools |
| `/save`, `/notes`, `/history`, `/new` | Notes with source links, saved conversations |
| `Ctrl+C` / `Ctrl+D` / `/exit` | Cancel the current answer / quit (your shell is unchanged) |

Type `/` to open the command picker or `/help` for everything. Set `BHAP_PLAIN=1` for a minimal prompt (e.g. screen readers). Full reference: [docs/cli-reference.md](docs/cli-reference.md).

## Privacy, data and limits

- Everything lives under one data directory (Linux `~/.local/share/bhap`, macOS `~/Library/Application Support/Bhap`, Windows `%LOCALAPPDATA%\Bhap`; override with `BHAP_HOME`). It is created with owner-only permissions. No telemetry, no cloud backend.
- Originals are copied into the data directory by default (`bhap config set store_originals false` for reference mode). `bhap docs remove` deletes the copy, chunks, vectors, cached vectors, full-text index entries and citations. Notes you explicitly saved stay yours.
- Documents are untrusted input: parsing runs in a resource-limited worker process, ZIP bombs/DTDs/extension spoofing are refused, embedded links and macros are never followed, and document text is isolated from instructions in prompts.
- Backups (`bhap backup create`) contain your documents and history. Use `--encrypt` (scrypt + Fernet) for anything sensitive.
- PDFs are text-extracted; image-only pages are OCR'd locally when Tesseract and Poppler are installed (with a confidence figure), otherwise flagged. Diagrams, complex tables and handwriting may not be interpreted — Bhap marks such pages instead of pretending.
- Answers are produced by the local model you choose. Bhap guarantees that citations point to retrieved passages; it cannot guarantee the model's claim is fully entailed by them. Verify important facts with `/open`.

## Support matrix

| Platform | Status |
|---|---|
| Linux (Arch/CachyOS, Ubuntu) | Primary target; developed and tested here (Python 3.10 and 3.14) |
| macOS, Windows | CI runs the full test suite; interactive terminal features depend on the platform's readline (Windows: install `pyreadline3` for history/completion) |

## Development

```sh
python -m pip install -e ".[dev]"
python -m unittest discover -s tests       # 70+ tests; uses a fake local runtime, no model needed
ruff check src tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and [CHANGELOG.md](CHANGELOG.md). The product requirements are in `Bhap_Offline_AI_PRD.pdf`.

License: MIT.
