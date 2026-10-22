# Collecting from a Raspberry Pi

Since 21 October 2026 EA answers GitHub's servers with HTTP 403 Access Denied,
while it still answers a home connection. The Pi therefore does the collecting
and GitHub only publishes the website:

```text
Raspberry Pi (home connection), twice an hour
    collect both clubs  ->  commit data/  ->  normal git push
GitHub Actions, on every push
    tests  ->  build both clubs' viewer data  ->  publish Pages (no EA request)
```

The Pi needs Raspberry Pi OS Bookworm or newer (Python 3.10+, Git) and a network
connection. The collector uses only Python's standard library; nothing is installed
with pip.

## 1. Give the Pi write access to the repository

On the Pi, create a key used only for this repository:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/dubsfc_deploy -N "" -C "dubsfc raspberry pi"
cat >> ~/.ssh/config <<'EOF'
Host github-dubsfc
  HostName github.com
  User git
  IdentityFile ~/.ssh/dubsfc_deploy
  IdentitiesOnly yes
EOF
cat ~/.ssh/dubsfc_deploy.pub
```

On GitHub: repository **Settings → Deploy keys → Add deploy key**. Paste the
printed line, tick **Allow write access**, save. This key works for this one
repository only; remove it there to revoke the Pi's access.

## 2. Clone and check

```bash
sudo apt install -y git python3
git clone git@github-dubsfc:denizsoysal/EA-FC-pro-club-tracker.git ~/dubsfc-tracker
cd ~/dubsfc-tracker
python3 --version                  # 3.10 or newer
python3 scripts/tracker.py verify  # both clubs: "status": "ok"
```

## 3. First run

GitHub's 403 left collection paused for every club (`data/collector_state.json`).
Clear it once, from the Pi:

```bash
bash deploy/raspberry-pi/collect.sh --resume
```

Expect one line per club and match type (`received …, changed …`), then
`'status': 'pushed'`. If EA refuses the Pi too, the output says so and
collection pauses again; do not repeat the command in a loop.

## 4. Run it automatically

```bash
bash deploy/raspberry-pi/install.sh
```

This installs `dubsfc-collector.timer`, which runs `collect.sh` at :07 and :37
past every hour (plus up to two minutes of random delay). After a power cut it
runs once soon after the Pi restarts. Useful commands:

```bash
systemctl list-timers dubsfc-collector.timer   # next run
journalctl -u dubsfc-collector -n 50           # last output
sudo systemctl start dubsfc-collector          # run now
sudo systemctl disable --now dubsfc-collector.timer   # stop collecting
```

Setting `"collection_enabled": false` in `config.json` (and pushing it) also
stops collection; the Pi picks the change up at its next run.

## Why twice an hour

EA's feed holds only the last 10 matches of each competition. In this archive,
Dubs once played 10 league matches in 2.9 hours, so every 30 minutes leaves a wide
margin. Polling more often would not save more matches, but would send EA more
requests from your home connection.

## Notes

- Commits from the Pi are authored "DubsFC Raspberry Pi collector". Set
  `DUBSFC_GIT_NAME` / `DUBSFC_GIT_EMAIL` in the service to change this.
- Each run writes a recovery ZIP to `.runtime/recovery/`. After a successful
  push only the newest 10 are kept (`DUBSFC_KEEP_RECOVERY`); after a failed push
  they are all kept.
- Do not also commit a local `sync` from another computer; the Pi is now the
  only collector. Code changes pushed from elsewhere are pulled before each run.
- If the Pi is off, nothing is collected. A match drops out of EA's feed after 10
  newer matches in the same competition, so a long outage on a busy day can still
  lose matches.
