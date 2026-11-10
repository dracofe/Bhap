# Security policy

## Reporting

Please report vulnerabilities privately (GitHub security advisories on this repository) rather than in public issues. Include a minimal reproducing file if a parser is involved. Do not send real personal documents.

## Threat model and defenses

| Threat | Defense |
|---|---|
| Silent data exfiltration | Process-wide network guard blocks non-loopback DNS/sockets; runtime URLs must be loopback; env proxies ignored; no telemetry |
| Malicious documents (zip bombs, XXE/billion laughs, extension spoofing, pathological PDFs) | Content sniffing, ZIP size/ratio/member limits, DTD rejection, parsing in a separate process with memory/CPU/time limits, guard active in the worker |
| Prompt injection inside documents | Sources are delimited and sanitized, the system prompt declares them untrusted data, citations are validated against retrieved IDs, and Bhap has no tools the model could trigger |
| Path tricks | Directory scans do not follow symlinks, skip hidden/tooling folders and Bhap's own data dir; stdin document names are sanitized |
| Code execution from documents | Macros, embedded objects and external relationships are never executed or followed |
| Shell access | `!command` is off by default (`allow_shell`) and every run needs confirmation |
| Data at rest | Data dir and config are owner-only (POSIX); backups can be encrypted (scrypt + Fernet); hard delete removes chunks, vectors, cache, FTS entries, citations and stored copies |

Known limits: data at rest is not encrypted by default, so use OS disk encryption. The network guard covers the `bhap` process, not the model runtime daemon. Locally generated answers can still be wrong; citations prove provenance, not truth.
