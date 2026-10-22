#!/usr/bin/env bash
# One collection run on the Raspberry Pi: collect every configured club from this
# home connection, then commit and push data/ only. GitHub publishes the site.
#
#   deploy/raspberry-pi/collect.sh            what the timer runs
#   deploy/raspberry-pi/collect.sh --resume   once, after an access denial is resolved
#
# Exit status is non-zero when collection or the push needs attention; the
# systemd journal keeps the output (journalctl -u dubsfc-collector).
set -u
cd "$(dirname "$0")/../.." || exit 1

branch="${DUBSFC_BRANCH:-main}"
name="${DUBSFC_GIT_NAME:-DubsFC Raspberry Pi collector}"
email="${DUBSFC_GIT_EMAIL:-dubsfc-pi@users.noreply.github.com}"
keep="${DUBSFC_KEEP_RECOVERY:-10}"

if [[ "${1:-}" == "--resume" ]]; then
  # Manual: clears a persistent 401/403 pause, never a rate-limit cooldown.
  sync_args=(--event manual --resume --defer-failure)
else
  # Scheduled: respects collection_enabled in config.json.
  sync_args=(--event schedule --defer-failure)
fi

# Pick up code/config changes pushed from elsewhere. If this cannot fast-forward,
# the push step below still fetches and rebases without force.
git pull --ff-only --quiet origin "$branch" || echo "git pull skipped; the push step will rebase." >&2

python3 scripts/tracker.py sync "${sync_args[@]}"
sync_status=$?

# Runs even when the sync or viewer build failed: accepted matches are on disk.
python3 scripts/persist_archive.py --branch "$branch" --git-name "$name" --git-email "$email" --keep-recovery "$keep"
persist_status=$?

python3 scripts/tracker.py check-status
report_status=$?

if (( sync_status || persist_status || report_status )); then
  echo "Needs attention: sync=$sync_status push=$persist_status report=$report_status" >&2
  exit 1
fi
