# Changelog

All notable changes follow [Semantic Versioning](https://semver.org/).

## 1.1.0 — 2026-11-09

### Added
- **Full-screen terminal UI** for bare `bhap` (prompt_toolkit): mascot header with model/readiness/workspace, conversation area, input box pinned to the bottom between rules, mode badge, status bar, live `/` command picker with descriptions, path completion, Alt+Enter for newlines. Falls back to the plain prompt with `BHAP_PLAIN=1`, `NO_COLOR` or when prompt_toolkit is missing.
- `/upload <path>`: upload and index a file or folder; paths with spaces need no quotes.
- `/remove <id|name>` and `/reindex` in the session.
- **Runtime autostart**: if Ollama is installed but not running, Bhap starts `ollama serve` on the configured loopback address for commands that need a model and stops it when Bhap exits (log: `BHAP_HOME/logs/ollama.log`). Disable with `bhap config set auto_start_runtime false`.
- Unexpected errors inside the session are logged and shown instead of ending the session.
- PDF text is extracted in layout mode: table rows stay together (`PC | 98 | 1000 | 98000`) instead of one cell per line, which made models swap columns. Reindex PDFs to benefit (`bhap docs reindex --all`).
- Supporting excerpts are widened around the best-matching line (no more one-word fragments); table-of-contents chunks are ranked down.

### Verified
- End to end with Ollama 0.40.15 + `qwen2.5:7b` + `bge-m3` on an RTX 5070 inside a network namespace that has only loopback (no Wi-Fi/Ethernet): setup, doctor `--verify`, upload, cited answers and abstention all work.

### Changed
- `prompt_toolkit==3.0.52` is now a required (pure-Python) dependency.

## 1.0.0 — 2026-11-06

Complete terminal product built from PRD v1.2 (MVP + v1.1 + selected v2 features).

### Added
- **Interactive session** (bare `bhap`): virtual cwd with `cd/pwd/ls`, paste-a-path import (quoted paths, absolute/relative/`~`/Windows), directory preview with confirmation and excludes, `@file` scoped questions, ambiguity prompt, streaming answers, Ctrl+C cancel, Tab completion, persistent input history, multi-line input, 30 slash commands, opt-in confirmed `!shell`.
- **Grounded answers**: modes `strict` / `general` / `brainstorm`, document/folder scope, multi-turn follow-ups, citation markers validated against retrieved chunks, evidence map, abstention with knowledge-gap detection, prompt-injection isolation.
- **Structure-aware parsing**: PDF pages + outline sections + visual/scan detection + local OCR with confidence; DOCX heading hierarchy, tables, approximate pages, footnotes; PPTX true slide order, titles, tables, speaker notes, hidden slides; Markdown heading paths; text line locators. Parsing runs in a resource-limited worker; ZIP bombs, DTDs and extension spoofing are refused.
- **Hybrid retrieval**: FTS5/BM25 + vectors, reciprocal-rank fusion, rare-term anchor boost, per-document diversity, context budgeting; optional numpy acceleration.
- **Reliability**: persistent restart-safe job queue (`bhap jobs`), atomic indexing, content-hash dedupe, document versions, embedding cache, stale detection on embedding-model change, reference mode, change detection (`docs check`, `reindex --changed`), folder watcher (`bhap watch`), disk-space guard.
- **Study suite**: tiered summaries with map-reduce, teach-from-zero lessons, Socratic tutor, MCQ/essay quizzes with AI grading, flashcards with SM-2 spaced repetition, exam sprint, concept memory, Markdown/JSON export.
- **Synthesis**: `bhap compare` (similarities, differences, contradiction radar), `bhap graph` knowledge constellation (text/Mermaid/DOT/JSON).
- **Notes & history**: source-linked notes, searchable/exportable conversation history.
- **Portability**: `bhap backup create/restore/verify` packs with manifest, checksums, embedding compatibility checks and optional encryption.
- **Operations**: `doctor` (offline readiness + `--verify`), `status`, `setup` wizard with hardware-aware suggestions and consent-gated downloads, `model`, `config`, structured local logs with redacted export, llama.cpp adapter, bilingual UI (id/en), stable `--json`, documented exit codes.
- **Security**: process-wide network guard (loopback only), loopback-only runtime URLs, proxy bypass, owner-only data permissions.

### Changed
- Data layout moved to `config/`, `db/`, `workspaces/<id>/`. 0.1 databases migrate automatically (a `bhap-0.1-backup.sqlite` copy is kept); migrated documents are marked stale for reindexing.

## 0.1.0
- Initial MVP prototype.
