"""bhap backup create | restore | verify | list."""
import getpass
import os
import sys

from .. import ui
from ..core import backup, library
from ..errors import EXIT_OK, UsageError
from ..i18n import t, tr
from ..storage import sqlite
from .common import Context, emit_json


def _passphrase(confirm=False):
    env = os.environ.get("BHAP_BACKUP_PASSPHRASE")
    if env:
        return env
    if not sys.stdin.isatty():
        raise UsageError(tr("Passphrase dibutuhkan: set BHAP_BACKUP_PASSPHRASE atau jalankan di terminal",
                            "Passphrase required: set BHAP_BACKUP_PASSPHRASE or run in a terminal"))
    first = getpass.getpass(t("backup.passphrase"))
    if confirm:
        if len(first) < 8:
            raise UsageError(tr("Passphrase minimal 8 karakter", "Passphrase must be at least 8 characters"))
        if getpass.getpass(t("backup.passphrase_again")) != first:
            raise UsageError(tr("Passphrase tidak sama", "Passphrases do not match"))
    return first


def cmd_backup(args):
    if args.action == "list":
        files = backup.list_backups()
        if args.json:
            emit_json([{"file": str(f), "size": f.stat().st_size, "encrypted": backup.is_encrypted(f)} for f in files])
            return EXIT_OK
        if not files:
            ui.info(tr("Belum ada backup di direktori data.", "No backups in the data directory yet."))
            return EXIT_OK
        ui.table([(f.name, ui.human_size(f.stat().st_size), "🔒" if backup.is_encrypted(f) else "") for f in files],
                 ["FILE", tr("UKURAN", "SIZE"), ""])
        return EXIT_OK
    if args.action == "create":
        if args.encrypt:
            backup._crypto()  # fail early if the optional dependency is missing
        passphrase = _passphrase(confirm=True) if args.encrypt else None
        with sqlite.db() as conn:
            if args.all:
                names = [row["name"] for row in library.workspaces(conn)]
            else:
                names = [Context(conn, args).workspace]
            with ui.Spinner(tr("Membuat backup…", "Creating backup…"), enabled=not args.json):
                path, manifest, docs = backup.create(conn, names, output=args.output,
                                                     include_originals=not args.no_originals, passphrase=passphrase)
        if args.json:
            emit_json({"file": str(path), "manifest": manifest})
            return EXIT_OK
        ui.ok(t("backup.created", file=path, size=ui.human_size(path.stat().st_size), docs=docs,
                enc=tr(", terenkripsi", ", encrypted") if passphrase else ""))
        if not passphrase:
            ui.warn(t("backup.pii"))
        return EXIT_OK
    if not args.file:
        raise UsageError(f"bhap backup {args.action} <file.bhap>")
    passphrase = _passphrase() if backup.is_encrypted(args.file) else None
    if args.action == "verify":
        manifest, problems = backup.verify(args.file, passphrase)
        if args.json:
            emit_json({"ok": not problems, "problems": problems, "manifest": manifest})
            return EXIT_OK if not problems else 1
        for ws in manifest["workspaces"]:
            ui.out(f"  {ws['name']}: {ws['documents']} {tr('dokumen', 'documents')}, {ws['chunks']} chunk")
        ui.out(f"  Bhap {manifest['app_version']} · schema v{manifest['schema_version']} · "
               f"embedding: {', '.join(manifest.get('embedding_models', {})) or '-'} · "
               f"originals: {manifest.get('includes_originals')}")
        if problems:
            for problem in problems:
                ui.error(problem)
            return 1
        ui.ok(tr("Backup utuh (checksum cocok).", "Backup is intact (checksums match)."))
        return EXIT_OK
    with sqlite.db() as conn:
        restored, manifest, notes = backup.restore(conn, args.file, passphrase, rename=args.as_name)
    if args.json:
        emit_json({"restored": restored, "notes": notes})
        return EXIT_OK
    for ws in restored:
        ui.ok(t("backup.restored", name=ws["name"], docs=ws["documents"]))
    for note in notes:
        ui.warn(note)
    return EXIT_OK
