# Troubleshooting

Start with `bhap doctor` (add `--offline --verify` for an end-to-end test). Every failing item prints the fix.

| Symptom | Cause | Fix |
|---|---|---|
| `Ollama is not reachable at http://127.0.0.1:11434` (exit 3) | runtime not installed, autostart disabled, or it failed to start | install Ollama; check `BHAP_HOME/logs/ollama.log`; or run `ollama serve` yourself |
| `No chat model configured` / `not installed` | setup not finished | `bhap setup`; while online: `bhap model pull <name>` |
| `N document(s) need reindexing` | embedding model changed, or migrated from 0.1 | `bhap docs reindex --stale` |
| `no readable text on page(s) …` | scanned/image-only PDF | install Tesseract + Poppler ([installation](installation.md#3-optional-ocr-for-scanned-pdfs)), then `bhap docs reindex <id>` |
| `legacy .doc/.ppt is not supported` | old binary Office format | `libreoffice --headless --convert-to docx file.doc` |
| `extension … does not match its content` | renamed/spoofed file | open and re-save the file in its real format |
| `possible zip bomb` / `declares a DTD` | malformed or hostile Office file | Bhap refuses it on purpose |
| `parsing exceeded …s` / `resource limits` | huge or pathological file | raise `parse_timeout` / `parse_memory_mb`, or split the file |
| `exceeds the … MB limit` | size limit | `bhap config set max_file_mb 300` |
| `Not enough free disk space` | low disk | free space, or set `BHAP_HOME` to another disk |
| Answers say "Tidak ditemukan dalam materi yang dipilih." | no supporting evidence in scope | check `/scope`, try other keywords, try `/mode general`, or import more material (Bhap lists the terms it could not find) |
| `Invalid citations removed: [9]` | the model cited a source it was not given | handled automatically; consider a stronger model |
| Interactive session refuses to start | stdin/stdout is not a terminal | use `bhap ask "…"` or `bhap ask --stdin --json` in scripts |
| `/open 1 --app` does nothing over SSH | no desktop session | use the text preview (`/open 1`) |
| PDF opens at page 1 | the system opener cannot jump to a page | `bhap config set pdf_viewer "zathura -P {page} {file}"` (or `okular -p {page} {file}`, `evince -i {page} {file}`) |
| No ↑/↓ history or Tab completion on Windows | no readline | `pip install pyreadline3` |
| `Database … is unreadable or corrupt` (exit 5) | damaged DB | `bhap backup restore <file>`; the DB is `BHAP_HOME/db/bhap.sqlite` |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success (abstention is a valid outcome, not an error) |
| 1 | generic failure |
| 2 | invalid usage / not found |
| 3 | model or runtime not ready |
| 4 | a document failed to parse |
| 5 | database/storage problem |
| 6 | network access blocked by the offline guard |
| 130 | cancelled with Ctrl+C |

## Logs

`bhap logs` shows recent structured events. `bhap logs export --redact` writes a shareable copy with paths and texts removed. Logs never leave your machine.
