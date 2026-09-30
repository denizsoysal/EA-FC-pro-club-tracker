"""Durable local archive primitives. Standard library only (Python 3.10+).

All writers must hold repository_lock. Checksums detect accidental changes; they
are not authentication and cannot prove a pre-existing v1 file was never edited.
Network outages, total disk loss and lost hosted runners still require backups.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import uuid
import zipfile


class ArchiveError(ValueError):
    """Stop rather than silently replace, skip, or bless damaged history."""


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def metadata(data: bytes) -> dict:
    return {"sha256": digest(data), "bytes": len(data)}


def load(path: Path):
    return json.loads(path.read_bytes().decode("utf-8-sig"))


def fsync_directory(path: Path) -> None:
    # POSIX directory fsync persists renames. Windows has no equivalent through
    # this portable API; regular file fsync + os.replace are still used there.
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_bytes(path: Path, data: bytes) -> bool:
    """Unique same-directory temporary, fsync, atomic replace, read-back check."""
    if path.is_symlink():
        raise ArchiveError(f"Refusing to write through a symlink: {path}")
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if temp.read_bytes() != data:
            raise ArchiveError(f"Read-back failed before replacing {path}")
        os.replace(temp, path)
        fsync_directory(path.parent)
        if path.read_bytes() != data:
            raise ArchiveError(f"Read-back failed after replacing {path}")
        return True
    finally:
        # An interrupted process may leave a temp file. Never delete the target.
        if temp.exists():
            temp.unlink()


@contextmanager
def repository_lock(root: Path):
    """Non-blocking OS lock, automatically released even after process death."""
    path = root / ".runtime" / "archive.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ArchiveError("Another tracker command is using this archive. Wait for it to finish.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ArchiveStore:
    GROUPS = ("matches", "revisions", "snapshots", "responses")

    def __init__(self, folder: Path, identity: dict):
        self.folder = folder
        self.identity = {k: str(identity[k]) for k in ("edition", "platform", "club_id")}
        self.inventory = folder / "integrity.json"
        self.journal = folder / "pending_transaction.json"

    def target(self, relative: str) -> Path:
        p = PurePosixPath(relative)
        if (p.is_absolute() or ".." in p.parts or "\\" in relative
                or not p.parts or p.parts[0] not in self.GROUPS or p.suffix != ".json"):
            raise ArchiveError(f"Unsafe archive path: {relative!r}")
        target = self.folder.joinpath(*p.parts)
        if not target.resolve().is_relative_to(self.folder.resolve()):
            raise ArchiveError(f"Archive path escapes its folder: {relative}")
        if any(parent.is_symlink() for parent in [target, *target.parents] if parent != self.folder.parent):
            raise ArchiveError(f"Symlink in archive path: {relative}")
        return target

    def files(self) -> dict[str, Path]:
        return {p.relative_to(self.folder).as_posix(): p
                for group in self.GROUPS for p in (self.folder / group).rglob("*.json")}

    def manifest(self) -> dict:
        value = load(self.inventory)
        if (not isinstance(value, dict) or value.get("version") != 1
                or value.get("identity") != self.identity or not isinstance(value.get("files"), dict)):
            raise ArchiveError(f"Invalid archive inventory: {self.inventory}")
        return value

    def check_file(self, relative: str, body: bytes) -> None:
        """Validate path/hash/identity without dropping any unknown fields."""
        self.target(relative)
        try:
            value = json.loads(body.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ArchiveError(f"Invalid JSON in {relative}") from exc
        group = relative.split("/")[0]
        if group != "matches" and Path(relative).stem != digest(body):
            raise ArchiveError(f"Content-addressed filename mismatch: {relative}")
        if group in ("matches", "revisions"):
            if not isinstance(value, dict) or not isinstance(value.get("raw"), dict):
                raise ArchiveError(f"Invalid match envelope in {relative}")
            mid = str(value.get("match_id", ""))
            if not re.fullmatch(r"[0-9]+", mid):
                raise ArchiveError(f"Invalid match ID in {relative}")
            for k, v in self.identity.items():
                if str(value.get(k)) != v:
                    raise ArchiveError(f"Wrong {k} in {relative}")
            if str(value["raw"].get("matchId")) != mid:
                raise ArchiveError(f"Raw match ID disagrees in {relative}")
            if group == "matches" and Path(relative).stem != mid:
                raise ArchiveError(f"Match filename disagrees in {relative}")
            if group == "revisions" and PurePosixPath(relative).parts[1] != mid:
                raise ArchiveError(f"Revision folder disagrees in {relative}")
            clubs = value["raw"].get("clubs")
            if not isinstance(clubs, dict) or self.identity["club_id"] not in clubs:
                raise ArchiveError(f"Configured club absent from {relative}")
            if not isinstance(value.get("match_types"), list):
                raise ArchiveError(f"Missing match types in {relative}")

    def recover(self) -> bool:
        """Finish a journaled transaction; never overwrite unrelated file edits."""
        if not self.journal.exists():
            return False
        journal = load(self.journal)
        if (not isinstance(journal, dict) or journal.get("identity") != self.identity
                or not re.fullmatch(r"[a-f0-9]{32}", str(journal.get("id", "")))
                or not isinstance(journal.get("changes"), list)):
            raise ArchiveError("Invalid pending transaction; restore from a verified backup.")
        manifest = self.manifest()
        for n, change in enumerate(journal["changes"]):
            relative, new, old = change["path"], change["new"], change["old"]
            path = self.target(relative)
            current = metadata(path.read_bytes()) if path.exists() else None
            recorded = manifest["files"].get(relative)
            if recorded not in (old, new):
                raise ArchiveError(f"Inventory conflict during transaction recovery: {relative}")
            if current != new:
                if current != old:
                    raise ArchiveError(f"Unexpected file change during transaction recovery: {relative}")
                stage = self.folder / "_transactions" / journal["id"] / f"{n}.json"
                if not stage.is_file() or metadata(stage.read_bytes()) != new:
                    raise ArchiveError(f"Missing or damaged staged data for {relative}. Keep all files and restore a backup.")
                self.check_file(relative, stage.read_bytes())
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage, path)
                fsync_directory(path.parent)
            if metadata(path.read_bytes()) != new:
                raise ArchiveError(f"Post-write checksum mismatch: {relative}")
            manifest["files"][relative] = new
        atomic_bytes(self.inventory, encoded(manifest))
        self.journal.unlink()
        fsync_directory(self.folder)
        stage_folder = self.folder / "_transactions" / journal["id"]
        # Remove ONLY staging files for this successfully completed transaction.
        if stage_folder.exists():
            for p in stage_folder.iterdir():
                p.unlink()
            stage_folder.rmdir()
        return True

    def verify(self) -> dict:
        self.recover()
        if not self.inventory.exists():
            raise ArchiveError("History has no checksum baseline yet. Run: python scripts/tracker.py backup")
        manifest = self.manifest()
        found = self.files()
        missing = set(manifest["files"]) - set(found)
        extra = set(found) - set(manifest["files"])
        if missing:
            raise ArchiveError("Missing archived file(s): " + ", ".join(sorted(missing)[:5]))
        if extra:
            raise ArchiveError("Untracked archive file(s); use import-json, not manual edits: " + ", ".join(sorted(extra)[:5]))
        total_bytes = 0
        for relative, expected in manifest["files"].items():
            body = self.target(relative).read_bytes()
            if metadata(body) != expected:
                raise ArchiveError(f"Checksum mismatch: {relative}. Nothing has been overwritten.")
            self.check_file(relative, body)
            total_bytes += len(body)
        return {"status": "ok", "club_id": self.identity["club_id"],
                "matches": sum(p.startswith("matches/") for p in found),
                "revisions": sum(p.startswith("revisions/") for p in found),
                "snapshots": sum(p.startswith("snapshots/") for p in found),
                "http_responses": sum(p.startswith("responses/") for p in found),
                "files_checked": len(found), "bytes_checked": total_bytes}

    def prepare(self) -> dict:
        """Protect legacy files without rewriting them; then verify everything."""
        self.recover()
        if not self.inventory.exists():
            present = self.files()
            if any(not key.startswith("matches/") for key in present):
                raise ArchiveError("The integrity inventory is missing from a versioned archive. Restore it from Git/backup; do not reset the baseline.")
            entries = {}
            for relative, path in present.items():
                body = path.read_bytes()
                self.check_file(relative, body)
                entries[relative] = metadata(body)
            self.folder.mkdir(parents=True, exist_ok=True)
            atomic_bytes(self.inventory, encoded({"version": 1, "identity": self.identity, "files": entries}))
        self.verify()
        # Every current version, including a v1 pre-upgrade file, gets an immutable copy.
        manifest = self.manifest()
        additions = {}
        for relative, path in self.files().items():
            if not relative.startswith("matches/"):
                continue
            body = path.read_bytes()
            name = f"revisions/{path.stem}/{digest(body)}.json"
            if name not in manifest["files"]:
                additions[name] = body
        if additions:
            self.write_many(additions)
        return self.verify()

    def write_many(self, values: dict[str, bytes]) -> int:
        """Journal -> staged bytes -> targets -> inventory. No file deletions."""
        self.verify()
        manifest = self.manifest()
        changes, bodies = [], []
        for relative, body in values.items():
            self.check_file(relative, body)
            new, old = metadata(body), manifest["files"].get(relative)
            if old == new:
                continue
            if old is not None and not relative.startswith("matches/"):
                raise ArchiveError(f"Immutable archive object would change: {relative}")
            changes.append({"path": relative, "old": old, "new": new})
            bodies.append(body)
        if not changes:
            return 0
        transaction = uuid.uuid4().hex
        stage = self.folder / "_transactions" / transaction
        for n, body in enumerate(bodies):
            atomic_bytes(stage / f"{n}.json", body)
        atomic_bytes(self.journal, encoded({"version": 1, "id": transaction,
                                          "identity": self.identity, "changes": changes}))
        self.recover()
        self.verify()
        return len(changes)

    def snapshot(self, feed: str, data) -> str:
        body = encoded(data)
        relative = f"snapshots/{feed}/{digest(body)}.json"
        self.write_many({relative: body})
        return relative

    def http_response(self, feed: str, status: int, body: bytes, content_type: str,
                      truncated: bool = False) -> None:
        # Only a non-secret header is retained. Never save cookies/auth headers.
        envelope = {"status": status, "content_type": content_type,
                    "truncated": truncated, "body_base64": base64.b64encode(body).decode("ascii")}
        content = encoded(envelope)
        self.write_many({f"responses/{feed}/{digest(content)}.json": content})


def verify_backup(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise ArchiveError("Backup ZIP integrity check failed.")
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ArchiveError("Backup contains duplicate filenames.")
        manifest = json.loads(z.read("BACKUP_MANIFEST.json"))
        expected = manifest["files"]
        if set(names) != set(expected) | {"BACKUP_MANIFEST.json"}:
            raise ArchiveError("Backup inventory does not match its files.")
        for relative, info in expected.items():
            p = PurePosixPath(relative)
            if p.is_absolute() or ".." in p.parts or "\\" in relative:
                raise ArchiveError("Unsafe backup path.")
            if metadata(z.read(relative)) != info:
                raise ArchiveError(f"Backup checksum mismatch: {relative}")
    # Counted from the ZIP's own checked file list, one entry per club archive.
    clubs = {}
    for relative in sorted(expected):
        parts = PurePosixPath(relative).parts
        if len(parts) >= 5 and parts[0] == "data":
            club = clubs.setdefault(parts[1:4], {"edition": parts[1], "platform": parts[2],
                                                 "club_id": parts[3], "files": 0, "matches": 0})
            club["files"] += 1
            club["matches"] += parts[4] == "matches"
    return {"status": "ok", "backup": str(path), "files_checked": len(expected),
            "clubs": list(clubs.values())}


def create_backup(root: Path, destination: Path | None = None) -> dict:
    """Whole data/ + config, CRC/SHA checked before publishing the new ZIP."""
    when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    destination = destination or root / "backups" / f"dubsfc-{when}.zip"
    if destination.exists():
        raise ArchiveError("Backup already exists; choose a new filename. Backups are never overwritten.")
    if destination.resolve().is_relative_to((root / "data").resolve()):
        raise ArchiveError("Put backups outside data/.")
    files = [p for p in (root / "data").rglob("*") if p.is_file()]
    if (root / "config.json").is_file():
        files.append(root / "config.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=".zip.tmp", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        entries = {}
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for path in sorted(files):
                if path.is_symlink():
                    raise ArchiveError(f"Refusing symlink in backup: {path}")
                relative, body = path.relative_to(root).as_posix(), path.read_bytes()
                entries[relative] = metadata(body)
                z.writestr(relative, body)
            z.writestr("BACKUP_MANIFEST.json", encoded({"version": 1, "created_at": when, "files": entries}))
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        verify_backup(temporary)
        os.replace(temporary, destination)
        fsync_directory(destination.parent)
        result = verify_backup(destination)
        result["sha256"] = digest(destination.read_bytes())
        return result
    finally:
        if temporary.exists():
            temporary.unlink()
