# History preservation audit — v3

## Findings addressed

The supplied v2 project kept the latest raw match object, but a later response
could replace that local file without retaining a previous copy outside Git
history. Its atomic replacement used one fixed temporary filename and did not
fsync. The workflow skipped raw-data persistence after an unexpected build error,
and a normal push failure had no recovery artifact. Git text normalization also
needed attention once byte-exact integrity checking was introduced.

This update addresses these specific paths. It is not a claim that all possible
failures have been eliminated or that a live deployment has been verified.

## Storage protocol

1. Obtain one non-blocking OS lock for the repository's CLI command.
2. Recover any pending journaled transaction and verify the existing inventory.
3. On the first upgrade only, validate legacy match envelopes and establish a
   checksum baseline. Copy their exact original bytes into the revision store.
4. Capture a received HTTP body before JSON parsing (15 MiB safety limit, explicit
   truncated flag for over-limit errors). Preserve an entire decoded response
   before validating that it can be admitted to the match archive.
5. Prepare a new latest-match envelope and retain both previous and new accepted
   versions under SHA-256-derived names. Keep every original field, not merely
   the fields consumed by the UI.
6. Stage and fsync every changed file. Persist a journal describing the old/new
   checksums. Replace targets, update the integrity inventory atomically, then
   remove only the staging files for that completed transaction.
7. Read back and verify the protected archive. Build/export from the latest
   accepted records; these derived outputs never replace the raw history.

`matches/` is the convenient latest view; `revisions/`, `snapshots/`, and
`responses/` retain earlier information. A later sparse reply can change the
latest display, but earlier richer data still exists in those stores. We do NOT
silently combine contradictory versions into invented match statistics.

A crash before journal creation cannot replace the latest match target. Staging
files without a journal may remain after a hard exit; they are not automatically
deleted. A full decoded response is also written before the match transaction,
so it can be replayed using `import-json` after the original failure is resolved.
A journaled interrupted transaction is finished on the next command. Unexpected
changes or unavailable staged bytes stop recovery instead of guessing.

## Checks and their limits

Protected inventory: current matches, retained revisions, decoded response
snapshots and captured HTTP response envelopes. A deletion is detected relative
to the inventory; a valid-JSON edit is also detected by its byte checksum. Ordinary
collector pause state is kept separately; ZIP backups cover it as part of `data/`.

SHA-256 protects against accidental corruption relative to a stored baseline.
It is not a signed audit ledger. Deliberately editing both data and inventory is
not prevented, and no baseline can detect damage that predated its creation.

File fsync and rename are used on all supported platforms; directory fsync is
used on POSIX. Windows lacks the same portable directory-fsync primitive. This
session exercised Linux; native Windows locking/filesystem behavior was not run.
Windows-style CRLF normalization was tested using Git's `core.autocrlf=true` on
Linux, including a remote clone and checksum verification.

## Git persistence

The supplied attribute rule prevents Git from rewriting protected data bytes:

```gitattributes
/data/** -text -filter -ident -working-tree-encoding
```

After staging, the persistence helper independently compares the index's Git
blob IDs to hashes of actual working-tree bytes, refusing to commit a transformed
archive. It supports Git SHA-1 and SHA-256 repository formats. Existing source
code/config changes are not swept into the bot commit.

The helper creates a recovery ZIP before its Git operations, verifies history,
refuses deletions, and uses only normal pushes. A non-conflicting concurrent
change can be rebased; a conflicting archive is aborted with no force push.
The workflow's `always()` persistence condition handles a preceding viewer-build
failure. A failed persistence/build step causes an attempted recovery upload.

There is still a vulnerable interval while data exists only on a hosted runner.
A hard runner loss, cancellation, infrastructure outage, missing permissions,
or exceeded artifact quota may prevent both push and backup upload. No code here
can make that interval impossible. Do not interpret a locally created recovery
ZIP as safely uploaded until the GitHub step actually succeeds.

## Recovery guidance

- If `verify` reports a mismatch/deletion, stop collection. Do not delete or reset
  `integrity.json` to hide the warning. Keep the whole directory and restore the
  affected exact files from a verified ZIP or Git commit.
- Verify a backup with `python scripts/tracker.py verify-backup FILE.zip`.
  Restore into a NEW empty folder first, inspect its `data/`, and run `verify`
  using the scripts/config there before replacing a working installation.
- Failed-run recovery ZIPs may contain the evidence of corruption that caused
  the failure. Their ZIP/SHA checks prove the copied bytes match the ZIP manifest,
  not that every record was good before copying.
- An archived `snapshots/<feed>/<hash>.json` is a full decoded EA response and can
  be replayed with `import-json FILE --match-type leagueMatch` (use the actual feed).
- Keep an independent off-device ZIP. Actions artifacts are temporary, not a
  permanent second copy, and a local backup folder is not protection against
  losing the whole disk.

## Primary references checked

Community event mapping (not an official schema):
https://www.reddit.com/r/fifaclubs/comments/1ws2mv0/pro_clubs_api_decoding_match_event_aggregate/

Git attributes and object format:
https://git-scm.com/docs/gitattributes
https://git-scm.com/book/en/v2/Git-Internals-Git-Objects

GitHub schedules and artifact behavior:
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
https://github.com/actions/upload-artifact

PCT's description of the recent-match availability window:
https://proclubstracker.com/pct-plus
