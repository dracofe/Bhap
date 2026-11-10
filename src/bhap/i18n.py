"""Tiny bilingual message catalog (Bahasa Indonesia default, English)."""
from .config import load

_lang = {"value": None}

ABSTAIN = {"id": "Tidak ditemukan dalam materi yang dipilih.",
           "en": "Not found in the selected material."}

M = {
    # locators
    "loc.page": ("halaman {n}", "page {n}"),
    "loc.page_approx": ("± halaman {n}", "≈ page {n}"),
    "loc.slide": ("slide {n}", "slide {n}"),
    "loc.line": ("baris {n}", "line {n}"),
    "loc.notes": ("catatan pembicara", "speaker notes"),
    "loc.footnotes": ("catatan kaki", "footnotes"),
    "loc.table": ("tabel", "table"),
    "loc.ocr": ("OCR {conf}%", "OCR {conf}%"),
    "loc.document": ("dokumen", "document"),
    # general
    "yes": ("ya", "yes"),
    "no": ("tidak", "no"),
    "none": ("tidak ada", "none"),
    "ready": ("Offline siap", "Offline ready"),
    "setup_incomplete": ("Setup belum lengkap", "Setup incomplete"),
    "model_unavailable": ("Model tidak tersedia", "Model unavailable"),
    "cancelled": ("Dibatalkan.", "Cancelled."),
    "interrupted": ("Dihentikan oleh pengguna.", "Interrupted by user."),
    "done_in": ("selesai dalam {t}", "done in {t}"),
    "not_tty": ("Sesi interaktif membutuhkan terminal (TTY). Untuk skrip gunakan `bhap ask \"...\"` "
                "atau `bhap ask --stdin`.",
                "The interactive session needs a terminal (TTY). For scripts use `bhap ask \"...\"` "
                "or `bhap ask --stdin`."),
    # workspace
    "ws.created": ("Workspace dibuat: {name}", "Workspace created: {name}"),
    "ws.active": ("Workspace aktif: {name}", "Active workspace: {name}"),
    "ws.deleted": ("Workspace dihapus beserta dokumen, indeks, chat, dan catatan: {name}",
                   "Workspace deleted with its documents, index, chats and notes: {name}"),
    "ws.renamed": ("Workspace diganti nama: {old} → {new}", "Workspace renamed: {old} → {new}"),
    "ws.missing": ("Workspace '{name}' tidak ada.", "Workspace '{name}' does not exist."),
    "ws.missing_hint": ("bhap workspace create {name}", "bhap workspace create {name}"),
    "ws.exists": ("Workspace '{name}' sudah ada.", "Workspace '{name}' already exists."),
    "ws.invalid": ("Nama workspace hanya boleh huruf, angka, '-', '_', '.', spasi (maks. 64).",
                   "Workspace names may contain letters, digits, '-', '_', '.', spaces (max 64)."),
    "ws.confirm_delete": ("Hapus workspace '{name}' ({docs} dokumen) secara permanen?",
                          "Permanently delete workspace '{name}' ({docs} documents)?"),
    "ws.linked": ("Folder {folder} ditautkan ke workspace '{name}' ({file})",
                  "Folder {folder} linked to workspace '{name}' ({file})"),
    # ingest
    "ingest.stage.queued": ("antre", "queued"),
    "ingest.stage.extracting": ("mengekstrak", "extracting"),
    "ingest.stage.chunking": ("memecah", "chunking"),
    "ingest.stage.embedding": ("embedding", "embedding"),
    "ingest.stage.indexed": ("terindeks", "indexed"),
    "ingest.stage.failed": ("gagal", "failed"),
    "ingest.indexed": ("{file} · {units} · {chunks} chunk · terindeks lokal",
                       "{file} · {units} · {chunks} chunks · indexed locally"),
    "ingest.unchanged": ("{file} tidak berubah (sudah terindeks, #{id})", "{file} unchanged (already indexed, #{id})"),
    "ingest.duplicate": ("{file} identik dengan dokumen #{id} ({other}); dilewati. Pakai --force untuk tetap mengimpor.",
                         "{file} is identical to document #{id} ({other}); skipped. Use --force to import anyway."),
    "ingest.changed": ("{file} berubah sejak diindeks; reindex?", "{file} changed since it was indexed; reindex?"),
    "ingest.failed": ("{file}: {error}", "{file}: {error}"),
    "ingest.summary": ("{ok} terindeks, {skipped} dilewati, {failed} gagal", "{ok} indexed, {skipped} skipped, {failed} failed"),
    "ingest.no_text": ("Tidak ada teks yang dapat diekstrak (PDF scan? pasang tesseract + poppler untuk OCR lokal).",
                       "No extractable text (scanned PDF? install tesseract + poppler for local OCR)."),
    "ingest.too_large": ("{file} melebihi batas {limit} MB (bhap config set max_file_mb <n>)",
                         "{file} exceeds the {limit} MB limit (bhap config set max_file_mb <n>)"),
    "ingest.dir_preview": ("{path}: {count} file didukung ({size}) — {breakdown}",
                           "{path}: {count} supported files ({size}) — {breakdown}"),
    "ingest.dir_skipped": ("dilewati: {details}", "skipped: {details}"),
    "ingest.dir_confirm": ("Impor {count} file secara rekursif?", "Import {count} files recursively?"),
    "ingest.dir_exclude": ("Pola exclude (glob, pisahkan koma; Enter untuk tidak ada): ",
                           "Exclude patterns (globs, comma separated; Enter for none): "),
    "ingest.dir_needs_flag": ("{path} adalah direktori; tambahkan --recursive (atau --yes untuk melewati konfirmasi).",
                              "{path} is a directory; add --recursive (or --yes to skip confirmation)."),
    "ingest.warning": ("peringatan: {text}", "warning: {text}"),
    "ingest.cancelled": ("Impor dibatalkan; dokumen yang belum selesai tidak disimpan.",
                         "Import cancelled; unfinished documents were not saved."),
    # docs
    "docs.empty": ("Belum ada dokumen di workspace '{name}'. Tambahkan dengan `bhap add <file>`.",
                   "No documents in workspace '{name}' yet. Add some with `bhap add <file>`."),
    "docs.removed": ("Dokumen #{id} ({file}) beserta chunk, embedding, sitasi, dan salinan aslinya dihapus.",
                     "Document #{id} ({file}) and its chunks, embeddings, citations and stored copy were deleted."),
    "docs.confirm_remove": ("Hapus {count} dokumen secara permanen?", "Permanently delete {count} document(s)?"),
    "docs.missing": ("Dokumen #{id} tidak ada di workspace ini.", "Document #{id} is not in this workspace."),
    "docs.stale": ("{count} dokumen perlu di-reindex (model embedding/format berubah): bhap docs reindex --stale",
                   "{count} document(s) need reindexing (embedding model/format changed): bhap docs reindex --stale"),
    "docs.moved": ("file asli dipindah/hilang", "original moved/missing"),
    "docs.modified": ("file asli berubah", "original modified"),
    "docs.ok": ("sesuai", "in sync"),
    # retrieval / answers
    "ask.no_docs": ("Workspace '{name}' belum memiliki dokumen terindeks. Tambahkan dengan `bhap add <file>`.",
                    "Workspace '{name}' has no indexed documents yet. Add some with `bhap add <file>`."),
    "ask.sources": ("Sumber", "Sources"),
    "ask.unsupported": ("Jawaban model tidak memuat sitasi yang valid; dalam mode ketat diperlakukan sebagai tidak ditemukan.",
                        "The model's answer had no valid citations; strict mode treats it as not found."),
    "ask.invalid_markers": ("Sitasi tidak valid dibuang: {markers}", "Invalid citations removed: {markers}"),
    "ask.gap": ("Istilah yang tidak ada di koleksi: {terms}. Pertimbangkan mengimpor materi tentang topik ini.",
                "Terms not present in your collection: {terms}. Consider importing material on this topic."),
    "ask.gap_generic": ("Materi terkait tidak cukup kuat di koleksi ini. Coba kata kunci lain, ubah scope, atau impor materi tambahan.",
                        "Related material is weak in this collection. Try other keywords, widen the scope, or import more material."),
    "ask.evidence_map": ("Peta bukti", "Evidence map"),
    "ask.inference": ("inferensi / tanpa sitasi", "inference / uncited"),
    "ask.generated": ("dibuat {model} · {mode} · {n} sumber", "generated by {model} · {mode} · {n} sources"),
    "ask.cancelled": ("Generasi dibatalkan (Ctrl+C). Riwayat dan indeks tidak berubah.",
                      "Generation cancelled (Ctrl+C). History and index unchanged."),
    "ask.conflict_note": ("Sumber dari beberapa dokumen digunakan; periksa bila ada perbedaan.",
                          "Sources from several documents were used; check for disagreements."),
    "search.none": ("Tidak ada hasil.", "No results."),
    "mode.strict": ("Hanya sumber", "Source only"),
    "mode.general": ("Sumber + pengetahuan umum", "Source + general knowledge"),
    "mode.brainstorm": ("Brainstorm", "Brainstorm"),
    # doctor / status
    "doctor.title": ("Pemeriksaan kesiapan Bhap", "Bhap readiness check"),
    "doctor.offline_title": ("Offline Readiness Check", "Offline Readiness Check"),
    "doctor.all_ok": ("Semua komponen inti siap dipakai tanpa internet.", "All core components are ready to run without internet."),
    "doctor.not_ok": ("Belum siap offline. Perbaiki item bertanda ✗ di atas.", "Not offline-ready yet. Fix the ✗ items above."),
    "setup.title": ("Setup model lokal Bhap", "Bhap local model setup"),
    "setup.no_runtime": ("Ollama tidak berjalan di {url}.", "Ollama is not running at {url}."),
    "setup.install_hint": ("Pasang Ollama (https://ollama.com/download; Arch/CachyOS: `sudo pacman -S ollama`), "
                           "jalankan `ollama serve` atau `systemctl start ollama`, lalu ulangi `bhap setup`. "
                           "Bhap tidak memasang apa pun secara diam-diam.",
                           "Install Ollama (https://ollama.com/download; Arch/CachyOS: `sudo pacman -S ollama`), "
                           "run `ollama serve` or `systemctl start ollama`, then rerun `bhap setup`. "
                           "Bhap never installs anything silently."),
    "setup.saved": ("Konfigurasi tersimpan: chat={llm}, embedding={emb}", "Configuration saved: chat={llm}, embedding={emb}"),
    "setup.pull_confirm": ("Unduh {model} melalui Ollama sekarang? (membutuhkan internet; ini satu-satunya langkah online)",
                           "Download {model} through Ollama now? (needs internet; this is the only online step)"),
    "setup.embedding_changed": ("Model embedding berubah; {count} dokumen perlu reindex: bhap docs reindex --stale",
                                "Embedding model changed; {count} document(s) need reindexing: bhap docs reindex --stale"),
    # repl
    "repl.help_hint": ("Ketik pertanyaan langsung, /upload <file> untuk menambah materi, /help untuk perintah, /exit untuk keluar.",
                       "Just type a question, /upload <file> to add material, /help for commands, /exit to quit."),
    "repl.ctrl_c": ("(Ctrl+D atau /exit untuk keluar)", "(Ctrl+D or /exit to quit)"),
    "repl.cwd": ("Direktori kerja: {path}", "Working directory: {path}"),
    "repl.unknown_cmd": ("Perintah tidak dikenal: {cmd}. Ketik /help.", "Unknown command: {cmd}. Type /help."),
    "repl.path_missing": ("Path tidak ditemukan: {path}. Periksa ejaan atau gunakan tanda kutip untuk path berspasi.",
                          "Path not found: {path}. Check the spelling or quote paths that contain spaces."),
    "repl.unsupported_file": ("{file} bukan format yang didukung ({formats}).", "{file} is not a supported format ({formats})."),
    "repl.ambiguous": ("'{text}' cocok dengan file di folder ini. Impor sebagai dokumen? (n = kirim sebagai pertanyaan)",
                       "'{text}' matches a file here. Import it as a document? (n = send it as a question)"),
    "repl.shell_disabled": ("Perintah shell dinonaktifkan. Aktifkan dengan `bhap config set allow_shell true` (dikonfirmasi setiap kali).",
                            "Shell commands are disabled. Enable with `bhap config set allow_shell true` (confirmed every time)."),
    "repl.shell_confirm": ("Jalankan perintah shell ini di {cwd}?\n  $ {cmd}\n", "Run this shell command in {cwd}?\n  $ {cmd}\n"),
    "repl.no_last": ("Belum ada jawaban dengan sumber di sesi ini.", "No answer with sources in this session yet."),
    "repl.note_saved": ("Catatan #{id} disimpan dengan {n} rujukan sumber.", "Note #{id} saved with {n} source references."),
    "repl.new_conversation": ("Percakapan baru dimulai.", "Started a new conversation."),
    "repl.cleared": ("Konteks sesi dibersihkan (riwayat tersimpan tidak dihapus).",
                     "Session context cleared (saved history is kept)."),
    "repl.scope": ("Scope: {scope}", "Scope: {scope}"),
    "repl.scope_all": ("seluruh workspace", "whole workspace"),
    "repl.mode": ("Mode jawaban: {mode}", "Answer mode: {mode}"),
    "repl.at_import": ("{file} belum terindeks. Impor sekarang agar bisa dipakai sebagai konteks?",
                       "{file} is not indexed yet. Import it now so it can be used as context?"),
    "repl.bye": ("Sampai jumpa. Shell Anda tidak berubah.", "Bye. Your shell is unchanged."),
    "repl.auto_switch": ("Folder tertaut → workspace '{name}'", "Linked folder → workspace '{name}'"),
    # study
    "study.correct": ("Benar!", "Correct!"),
    "study.wrong": ("Kurang tepat. Jawaban: {answer}", "Not quite. Answer: {answer}"),
    "study.score": ("Skor: {score}/{total}", "Score: {score}/{total}"),
    "study.your_answer": ("Jawaban Anda", "Your answer"),
    "study.none_due": ("Tidak ada kartu/soal yang jatuh tempo. Buat dengan `bhap study flashcards <topik>` atau `bhap study quiz <topik>`.",
                       "Nothing is due. Create items with `bhap study flashcards <topic>` or `bhap study quiz <topic>`."),
    "study.generated_label": ("Soal dibuat AI dari materi Anda — periksa rujukannya.",
                              "AI-generated from your material — check the references."),
    "study.reveal": ("Enter untuk melihat jawaban…", "Press Enter to reveal…"),
    "study.rate": ("Seberapa ingat? [1] lupa  [2] sulit  [3] baik  [4] mudah: ",
                   "How well did you recall? [1] again  [2] hard  [3] good  [4] easy: "),
    "study.progress_empty": ("Belum ada riwayat belajar.", "No study history yet."),
    "study.reset": ("Progres belajar workspace '{name}' dihapus.", "Study progress for workspace '{name}' deleted."),
    "study.tutor_intro": ("Mode tutor Socratic untuk '{topic}'. Jawab pertanyaan tutor; ketik /hint, /answer, atau /exit.",
                          "Socratic tutor for '{topic}'. Answer the tutor's questions; type /hint, /answer or /exit."),
    # backup
    "backup.created": ("Backup dibuat: {file} ({size}, {docs} dokumen{enc})", "Backup created: {file} ({size}, {docs} documents{enc})"),
    "backup.restored": ("Backup dipulihkan ke workspace '{name}' ({docs} dokumen).", "Backup restored into workspace '{name}' ({docs} documents)."),
    "backup.passphrase": ("Passphrase backup: ", "Backup passphrase: "),
    "backup.passphrase_again": ("Ulangi passphrase: ", "Repeat passphrase: "),
    "backup.pii": ("Backup berisi dokumen dan riwayat Anda. Simpan di lokasi aman; pertimbangkan --encrypt.",
                   "The backup contains your documents and history. Store it safely; consider --encrypt."),
}


def language():
    if _lang["value"] is None:
        try:
            _lang["value"] = load()["language"]
        except Exception:  # noqa: BLE001 - never fail on i18n
            _lang["value"] = "id"
    return _lang["value"]


def set_language(value):
    _lang["value"] = value if value in ("id", "en") else "id"


def t(key, **kwargs):
    pair = M.get(key)
    if pair is None:
        return key.format(**kwargs) if kwargs else key
    text = pair[0] if language() == "id" else pair[1]
    return text.format(**kwargs) if kwargs else text


def abstain(lang=None):
    return ABSTAIN[lang or language()]


def tr(id_text, en_text):
    """Inline bilingual text for one-off strings (help texts, prompts)."""
    return id_text if language() == "id" else en_text
