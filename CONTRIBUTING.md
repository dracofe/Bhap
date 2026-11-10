# Contributing to Bhap

Thanks for helping. Bhap's priorities, in order: **offline correctness, citation integrity, privacy, terminal UX**. Features that compromise the first three will not be merged.

## Setup

```sh
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m unittest discover -s tests
ruff check src tests
```

The tests use `tests/fake_ollama.py`, a deterministic local stand-in for the Ollama API, so no model is needed. Real-model checks are manual: `bhap doctor --offline --verify`.

## Layout

```
src/bhap/
  cli.py            argparse command tree, error → exit-code mapping, network guard install
  repl.py           interactive session (routing, navigation, slash commands)
  commands/         one module per command group (rendering only; logic lives in core/)
  core/             ingest, chunking, embedder, retrieval, generation, citations, study, history, backup, graph
  parsers/          pdf, ooxml (docx/pptx), text, ocr, isolated worker
  adapters/         Ollama and llama.cpp runtimes (loopback only)
  storage/          SQLite schema + migrations, vector blobs
  config.py i18n.py ui.py netguard.py hardware.py logs.py errors.py
tests/unit · tests/integration · tests/offline
```

## Rules of thumb

- Never add a network call outside `adapters/`, and only to loopback. The network guard will fail your tests otherwise.
- Never let generated text decide a locator. Citations must come from stored chunk metadata.
- Every new user-facing string goes through `i18n.t()` / `tr()` (Bahasa Indonesia + English).
- Results go to stdout, progress/warnings to stderr; `--json` must stay ANSI-free and stable.
- Schema changes need a migration in `storage/sqlite.py` and a test.
- Regenerate the CLI reference after changing commands: `python scripts/gen_cli_reference.py`.
- Never commit user documents, databases, models or `.env` files.
