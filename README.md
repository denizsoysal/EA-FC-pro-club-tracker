# DubsFC Tracker — complete v3 package

A static team viewer with a Python collector and a versioned, Git-friendly raw
archive. Python 3.10+; the collector has no third-party Python dependencies.

## Complete installation — no previous ZIP required

This is the **complete source project**, not an update patch. It includes the
latest viewer, collector, history-integrity and backup tools, tests, and GitHub
workflow. **`config.json` is deliberately not included**; supply your own in the
project root, alongside this README and the `scripts` folder.

Extract the `dubsfc-tracker` folder to a new location. Keep any old project,
backups and Git history until you have confirmed what they contain. Do not
transfer an old integrity inventory without its matching data files.

```text
dubsfc-tracker/
├── config.json                 <-- add your own file here
├── .github/workflows/archive-and-publish.yml
├── .gitattributes
├── .gitignore
├── scripts/
├── site/
├── data/                       <-- empty of matches initially
├── tests/
└── README.md
```

Open a terminal in that folder. With your configuration in place:

```powershell
# Fetch the available matches; initialize history and build the viewer.
python scripts/tracker.py sync

# After a successful sync, preserve a verified independent backup file.
python scripts/tracker.py backup
python scripts/tracker.py verify

# View locally; leave this window running.
python -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open `http://localhost:8000`. Stop the server with Ctrl+C. A successful `sync`
already builds `site/data/`; no separate build command is required. To rebuild
later without fetching, run `python scripts/tracker.py build`.

The collector uses only Python's standard library (Python 3.10+). No `pip`
installation is needed for normal use. `sync` is the correct command; there is
no `collect` command. A manual sync works with `collection_enabled: false`.

A fresh package contains **no previous matches or checksum baseline**. `sync`
initializes the baseline before fetching. Running `verify` before initialization
will request a baseline; `backup` can initialize it without fetching, but a
successful backup/verify of an empty archive does not mean matches were saved.
Check the `matches` count in the verification output. Copy the backup ZIP to
another drive or independent storage.

Deleted records are not recovered from this source package. A sync can fetch only
what the endpoint still returns. Retain all future `data/` files, including the
integrity inventory and revisions, together. If upgrading an existing nonempty
installation instead, read [UPGRADE.md](UPGRADE.md) first.

See [START_HERE.md](START_HERE.md) for the short setup sequence, and
[PACKAGE_VERIFICATION.md](PACKAGE_VERIFICATION.md) for checks executed on this
complete distribution.

## Viewer

Brand and browser title: **DubsFC Tracker**. No hero slogan, explanatory-card
section, event-data summary card, or event-coverage columns. A local build does
not show “not requested” or the old API-health boilerplate. Actual collector
failures, access-denial pauses, and possible missed-match warnings still display.

| View | Columns, in addition to player and appearances |
| --- | --- |
| Overview | Goals, assists, second assists, successful dribbles, tackles won, average rating |
| Shooting | Goals, shots, shots on target |
| Passing | Assists, passes made, pass %, successful through balls |
| Dribbling | Successful dribbles, non-skill opponents beaten, skill-move beats |
| Defending | Interceptions, tackles won, standing won, sliding won, ball recovered (dispossessions) |

Forward/long-pass percentages and event-coverage columns are omitted from the
visible views and their exports. All those original API fields are still kept
in raw storage. Undefined statistics remain unavailable rather than false zeros.
The per-match switch is per available appearance; percentages use paired totals.

“Successful dribbles” means the **reported completed-dribble counter (174)**,
not total attempts and not a verified count of one-on-one take-ons. “Ball recovered
(dispossessions)” uses **158: dispossessed opponent**, not a new all-recoveries
statistic. Hover the headings for these distinctions. These meanings remain
community-reported; seeing an event ID in your data does not validate its meaning.

See [stat definitions](docs/STAT_DEFINITIONS.md).

## Collection commands

```powershell
# Search only; no match collection.
python scripts/tracker.py search "Dubs VzeDoux"

# Your current numeric club ID belongs in config.json: "club_id": "696276"
# One manual fetch + durable archive write + viewer rebuild.
python scripts/tracker.py sync

# Read and checksum the protected archive; no EA request.
python scripts/tracker.py verify

# Whole data/ + config backup, ZIP CRC and per-file SHA-256 checked.
python scripts/tracker.py backup

# A NEW file on another disk is preferable. Existing backups are never replaced.
python scripts/tracker.py backup --output "E:\DubsFC\backup-2026-09-28.zip"
python scripts/tracker.py verify-backup "E:\DubsFC\backup-2026-09-28.zip"
```

`sync` is the command name. There is no `collect` command or `--dry-run` option.
`collection_enabled` controls scheduled runs, not deliberate manual syncs.

## Raw history: what is saved

For the configured club, platform and edition label:

```text
data/fc27/common-gen5/696276/
├── matches/<match_id>.json                 latest accepted match record
├── revisions/<match_id>/<sha256>.json      every distinct accepted version
├── snapshots/<match_type>/<sha256>.json    entire decoded endpoint responses
├── responses/<match_type>/<sha256>.json    captured HTTP response bodies
├── integrity.json                         protected-file SHA-256 inventory
└── collector_state.json                   pause / cooldown state, when needed
```

All returned match/player/club fields are preserved, **including both teams,
hidden stats, unrecognized keys, and all event buckets**. No field is discarded
because it is hidden from the viewer. Identical data is deduplicated, not counted
as another match. Empty feeds never remove earlier files. Later revisions never
erase the earlier raw version from the revision history.

HTTP bodies are stored as base64 in JSON with response status and Content-Type;
no authorization, Cookie or Set-Cookie headers are saved. Full decoded responses
are stored before match-shape validation. A rejected response therefore remains
available for inspection, without being treated as a valid match.

Scope: this archives the **configured match endpoints**, not every possible EA
endpoint, the full game world, event positions, video, or information EA does not
return. `edition` is a local archive label, not an API game selector. The current
safety cap is 15 MiB per HTTP response: an oversized response fails visibly, saves
a flagged prefix, and is not accepted as a complete match feed. No silent truncation.

## Durability, recovery and integrity

Unique temporary files are flushed and fsynced before replacement. A persisted
transaction journal plus staged files allows the next command to finish a write
that was interrupted midway. The same OS-level lock prevents overlapping local
CLI writers and is released when a process exits. Never run a different program
that directly edits these raw files while the collector is active.

The verifier checks protected-file existence, JSON, club/match identities,
content-addressed filenames, and SHA-256 hashes. It stops on corruption instead
of resetting the baseline or silently skipping a broken match. Immutable response
and revision objects cannot be changed through the storage API. Latest files can
be updated only through the journaled transaction, with the old version retained.

The supplied `.gitattributes` disables line-ending/filter conversion for `data/**`.
This matters for pre-existing Windows CRLF files: checksums must survive a Git
round trip unchanged. The CI persistence helper also compares **staged Git blob
hashes against working-tree bytes** before allowing an archive commit.

See [the storage audit](docs/HISTORY_SAFETY.md) and [executed tests](TEST_REPORT.md).

## GitHub automation

Push the complete project, including hidden `.github/` and `.gitattributes` files, your
configuration, and **all of `data/`**. Enable Pages with source **GitHub Actions**.
Run the workflow manually first. After a hosted request works, enable
`collection_enabled` in your config and commit that change.

The workflow collects, makes a recovery ZIP, verifies, commits **only `data/`**,
and performs a normal Git push **before** Pages deployment. Persistence runs
even when the preceding viewer build fails. Concurrent non-conflicting edits
can be rebased; archive conflicts are aborted, never force-pushed away.

If the fetch/build step or Git persistence fails, the workflow attempts to upload
`dubsfc-recovery-<run_id>-<attempt>` as an artifact. It has **14-day retention**,
so retrieve it promptly. Manual runs also have an optional `backup` input to
upload a recovery ZIP even when the run succeeds. Normal successful polls do
not consume one permanent artifact per run. These artifacts are not an independent
permanent backup and can fail to upload or expire.

The timer is `7,22,37,52 * * * *` (UTC by default). Schedules are best effort;
GitHub may delay/drop runs and disable inactive schedules. A running workflow is
not a promise of complete coverage. A local successful fetch does not establish
that EA accepts a hosted runner. 401/403 pause collection; 429 preserves a cooldown.

## What cannot be guaranteed

No software change can guarantee zero loss under every circumstance. Matches
that leave EA's recent-match feed before a successful collection cannot be
reconstructed here. A hosted runner lost before Git push/artifact upload may
lose newly received data. Disk failure, deletion of the repository and its backups,
account loss, quotas, and filesystem/hardware failures remain possible.

Keep a verified backup outside the repository and preferably outside this PC.
Monitor failed runs and the time of the last successful sync. The website alone
is not your raw-data backup. A public repository also makes its full raw archive
public, including player identifiers and opponent information.

## Reproduce the offline checks

```powershell
python -m unittest discover -s tests -v
node --test tests/test_stats.cjs
```

The Node tests are optional for using the tracker. Optional browser checks need
Playwright and Chromium: `python tests/browser_smoke.py` (mocked fetch) or
`python tests/browser_http.py` (actual loopback HTTP/CSP, on a machine that permits it).
Neither of those browser test scripts calls EA.
