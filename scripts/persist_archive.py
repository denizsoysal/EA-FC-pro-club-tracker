#!/usr/bin/env python3
"""CI: keep a recovery ZIP, verify history, commit data, push without force.

Called even if the viewer build failed. A failed normal push is retried after a
non-conflicting rebase. Conflicts are aborted, never auto-resolved by discarding
one archive. A full recovery ZIP exists before any Git operations.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import time

from archive_store import ArchiveError, create_backup, repository_lock
from tracker import load_config, archive_report, archive_failures

ROOT = Path(__file__).resolve().parents[1]
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if check and result.returncode:
        raise ArchiveError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result



def verify_git_index(root: Path, paths: list[str]) -> None:
    """Reject any line-ending/filter transformation between disk and staged blobs."""
    algorithm = git(root, "rev-parse", "--show-object-format").stdout.strip()
    if algorithm not in ("sha1", "sha256"):
        raise ArchiveError("Unknown Git object hash format.")
    for record in git(root, "ls-files", "--stage", "-z", "--", *paths).stdout.split("\0"):
        if not record:
            continue
        meta, relative = record.split("\t", 1)
        mode, object_id, stage = meta.split()
        if stage != "0" or mode not in ("100644", "100755"):
            raise ArchiveError("Unmerged or non-regular file in archive Git index: " + relative)
        body = (root / relative).read_bytes()
        header = b"blob " + str(len(body)).encode("ascii") + b"\0"
        actual = hashlib.new(algorithm, header + body).hexdigest()
        if actual != object_id:
            raise ArchiveError("Git would change archived bytes (line endings/filter): " + relative
                               + ". Install the supplied .gitattributes; no archive commit was made.")


def committable(root: Path, *, protect: bool = False) -> tuple[list[str], str]:
    """Paths safe to commit, and a description of any archive that is not.

    With every club healthy this is all of data/. Otherwise only the verified
    club folders: one club's damage must not strand another club's new matches
    on a runner, and the damaged folder is left exactly as found.
    """
    report = archive_report(root, load_config(root), protect=protect)
    if report["status"] == "ok":
        return ["data/"], ""
    paths = [entry["path"] + "/" for entry in report["archives"] if entry["status"] == "ok"]
    if paths and (root / "data" / "collector_state.json").is_file():
        paths.append("data/collector_state.json")
    return paths, archive_failures(report)


def prune_recovery(folder: Path, keep: int, current: Path) -> list[str]:
    """Delete all but the newest `keep` recovery ZIPs, never the current one.

    Called only after a successful push, when every pruned ZIP's data is also
    in the remote repository. Names sort by their creation timestamp.
    """
    zips = sorted(folder.glob("dubsfc-*.zip"))
    removed = []
    for path in zips[:max(len(zips) - keep, 0)]:
        if path.resolve() != current.resolve():
            path.unlink()
            removed.append(path.name)
    return removed


def persist(root: Path, branch: str, *, pause=time.sleep, git_name: str = BOT_NAME,
            git_email: str = BOT_EMAIL, keep_recovery: int | None = None) -> dict:
    git(root, "check-ref-format", "--branch", branch)
    # This ZIP intentionally copies all bytes even when integrity verification
    # subsequently fails. It is a recovery/forensic copy, not a health certificate.
    from datetime import datetime, timezone
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    backup = create_backup(root, root / ".runtime" / "recovery" / f"dubsfc-{name}.zip")
    paths, failures = committable(root, protect=True)
    if not paths:
        raise ArchiveError("Archive check failed for " + failures)
    # Refuse accidental raw-history deletions instead of committing them.
    deleted = git(root, "diff", "HEAD", "--name-only", "--diff-filter=D", "--", *paths).stdout.strip()
    if deleted:
        raise ArchiveError("Refusing to commit deleted archive files: " + deleted)
    staged = git(root, "diff", "--cached", "--name-only").stdout.splitlines()
    if any(not p.startswith("data/") for p in staged):
        raise ArchiveError("Unexpected staged code/config changes. Only data/ is committed by the collector.")
    git(root, "add", "--", *paths)
    verify_git_index(root, paths)
    changed = bool(git(root, "diff", "--cached", "--name-only", "--", "data/").stdout.strip())
    if changed:
        git(root, "-c", f"user.name={git_name}", "-c", f"user.email={git_email}",
            "commit", "-m", "Archive DubsFC match data and retained revisions")
    # Rebase also needs an identity on a fresh hosted runner (no global config).
    git(root, "config", "user.name", git_name)
    git(root, "config", "user.email", git_email)
    # An unchanged workspace can still include a commit from a previous failed
    # push when this helper is retried locally, so always try a normal push.
    for attempt in range(3):
        push = git(root, "push", "origin", f"HEAD:refs/heads/{branch}", check=False)
        if not push.returncode:
            if failures:
                # Reported only now, after every verified club is safely pushed.
                raise ArchiveError("Verified club archives were committed and pushed. NOT committed, "
                                   "left untouched for recovery: " + failures)
            result = {"status": "pushed", "new_commit": changed, "recovery_zip": backup["backup"]}
            if keep_recovery is not None:
                result["recovery_zips_removed"] = prune_recovery(
                    Path(backup["backup"]).parent, keep_recovery, Path(backup["backup"]))
            return result
        if attempt == 2:
            raise ArchiveError("Git push failed; raw data remains in the recovery ZIP. " + push.stderr.strip())
        git(root, "fetch", "origin", f"refs/heads/{branch}")
        rebase = git(root, "rebase", "FETCH_HEAD", check=False)
        if rebase.returncode:
            git(root, "rebase", "--abort", check=False)
            raise ArchiveError("Concurrent archive edits could not be merged safely. No force-push used. Download the recovery ZIP.")
        # A nonconflicting remote code/README change is safe. Validate the merged
        # archive before it can be pushed or used by the published viewer.
        merged, problems = committable(root)
        if any(path not in merged for path in paths):
            raise ArchiveError("The merged archive failed verification; nothing pushed. " + problems)
        verify_git_index(root, paths)
        pause(2 * (attempt + 1))
    raise ArchiveError("Unreachable persistence state")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--git-name", default=BOT_NAME, help="Commit author name")
    parser.add_argument("--git-email", default=BOT_EMAIL, help="Commit author email")
    parser.add_argument("--keep-recovery", type=int, metavar="N",
                        help="After a successful push, keep only the newest N recovery ZIPs (default: keep all)")
    args = parser.parse_args()
    if args.keep_recovery is not None and args.keep_recovery < 1:
        parser.error("--keep-recovery must be at least 1")
    try:
        with repository_lock(ROOT):
            print(persist(ROOT, args.branch, git_name=args.git_name, git_email=args.git_email,
                          keep_recovery=args.keep_recovery))
        return 0
    except (ArchiveError, OSError) as exc:
        print(f"Archive persistence error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
