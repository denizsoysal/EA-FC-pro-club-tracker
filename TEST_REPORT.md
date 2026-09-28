# DubsFC Tracker v3 — verification report

Date: 28 September 2026. Tested source is included in this update.

## Executed results

| Suite | Result | What it establishes |
|---|---|---|
| Python unittest | 95 passed, 0 failed | Collector/decoder/storage/backup tests, fault injection, subprocess interruption, local Git integration and legacy upgrade |
| Node test runner | 26 passed, 0 failed | Viewer aggregation, denominators, filters, labels and CSV column selection |
| Chromium mocked-fetch DOM harness | 33 checks passed | Desktop/mobile layout, requested removals, safety alerts, exports, keyboard behavior and escaping |
| Workflow YAML parse | Passed; 14 steps | YAML structure only, not a hosted execution |

Raw test output is included in `docs/verification/`. Run the first two suites with:

```powershell
python -m unittest discover -s tests -v
node --test tests/test_stats.cjs
```

Playwright/Chromium is optional for `python tests/browser_smoke.py`; it is NOT a
collector dependency. That harness injects the shipped scripts into a test
document with a mocked JSON fetch. It removes CSP only in the test document;
the shipped site retains its CSP. It does not test production HTTP or CSP.

## History and failure cases exercised

- All fields of accepted match objects survive storage: both teams, player IDs,
  Unicode names, unknown nested fields and undecoded event IDs. UI column removal
  does not remove raw data.
- An identical repeat does not create a second match or rewrite an identical
  file. Full response objects are content-deduplicated, not discarded by age.
- Updated/sparse records retain their previous version. Current display follows
  the latest accepted version; contradictory versions are not silently merged.
- Empty/null feeds cannot delete earlier matches. Invalid feed structures are
  kept as snapshots before validation rejects them.
- Already tracked file corruption, deletion, untracked additions and a missing
  integrity inventory cause a failure instead of resetting checksums.
- Injected write/fsync/replace failures do not silently truncate the old record.
- Two actual child-process `os._exit(73)` tests interrupt before/after replacing
  a match target. The next run finishes the persisted transaction and verifies
  it; the OS lock is released after the child dies.
- JSON backup ZIPs preserve all data directories/editions and configuration.
  ZIP CRC and SHA-256 manifest checks run before successful backup reporting.
- A synthetic legacy installation containing ten match files was upgraded using
  the real CLI. Backup, verify and build succeeded; configuration and the ten
  original match files remained byte-identical. Subsequent deletion was caught.
- Ten local bare-Git integration cases cover push/clone round trips, no empty
  commits, non-conflicting concurrent changes, conflicting changes, rejected
  pushes, raw-data deletions, a broken viewer and Windows-style newline handling.
- With `core.autocrlf=true`, protected CRLF bytes survive Git push/clone. A staged
  transformation is detected before commit if the protection rule is missing.
- API HTTP bodies are kept before parsing; HTML/403 responses are preserved
  without cookie/auth headers. A response above the 15 MiB safety limit is saved
  as an explicitly marked prefix and the request fails, never a silently complete
  record. This is a safety bound, not a guarantee of arbitrary-size capture.

## Check against the user's pasted real event output

The provided PowerShell log, not a full raw archive, was parsed as an additional
check. The first (non-duplicated) search output contained 10 match IDs and 76
player bucket groups across both teams: 74 decoded and 2 empty/unavailable.
Thirteen groups contained event 115; 72 contained event 174. This only checks
bucket parsing and presence, not player attribution or the correctness of the
community meanings. It is NOT a checksum audit of the user's PC.

## UI checks

Branding is DubsFC Tracker. The slogan, explanatory card block, normal local-build
health boilerplate and event-coverage displays are removed. Passing exposes only
passes made, pass completion and successful through balls. Forward/long passing
fields remain in raw data, not the visible/exported selected tables. Dribbling
uses “Successful dribbles”; its tooltip says reported completions, not attempts.
The defending label is “Ball recovered (dispossessions)”, explicitly limited to
opponents dispossessed, not all loose-ball recoveries. Real failure messages and
short community/partial-data notes remain visible. Screenshots use synthetic
players/results only, never invented user statistics.

## Not verified and remaining limits

- No live EA request, user's GitHub run, GitHub permission configuration or Pages
  deployment was executed in this update session.
- Actual HTTP browser navigation was attempted but blocked by the environment's
  administrator (`net::ERR_BLOCKED_BY_ADMINISTRATOR`). It is not counted as a
  pass. `tests/browser_http.py` is an optional local test, separate from the 33
  successful mocked-DOM checks. Production HTTP/CSP remain untested here.
- Native Windows filesystem/locking execution was not run. Tests ran on Linux;
  Windows CRLF effects were reproduced through Git settings. POSIX directory
  fsync has no identical portable Windows counterpart in this implementation.
- Existing files receive a baseline as they stand. That cannot prove they were
  complete or unaltered before upgrading. Checksums are not signed authentication.
- No test suite proves all failures impossible. A total runner loss before push
  and recovery upload, disk loss, quota/permission problems, deletion of the
  whole archive, and uncollected matches outside EA's recent feed can still lose
  information. Artifact upload is a fallback attempt, not guaranteed persistence.
- Keep a verified backup on a separate device/account. Temporary GitHub artifacts
  and a local ZIP on the same disk are not permanent independent backups.

## Verify your actual archive

After merging this update into the existing project root:

```powershell
python scripts/tracker.py backup
python scripts/tracker.py verify
python scripts/tracker.py build
```

`backup` prints the new ZIP path and checksums. Copy that ZIP off-device. `verify`
checks the actual local manifest and returns nonzero on an error. Stop collection
and retain all files if it reports a problem; do not delete the inventory to hide
it. The archive's details and recovery instructions are in
`docs/HISTORY_SAFETY.md`.
