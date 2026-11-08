# Collecting match data with a Raspberry Pi

This guide assumes you have never used a Raspberry Pi. It takes about an hour,
most of it waiting for downloads. You only do it once; afterwards the Pi works on
its own.

## Why a Raspberry Pi?

Until 21 October 2026, GitHub collected the match data every 15 minutes. Since
then EA refuses requests coming from GitHub's servers (HTTP 403 "Access
Denied"), but still answers requests from a normal home internet connection.

A Raspberry Pi is a small, cheap computer that uses about as much power as a
phone charger. Plugged in at home, it collects both clubs' matches twice an hour
and sends them to GitHub. GitHub then rebuilds and publishes the website, as
before:

```text
Raspberry Pi at home, every 30 minutes
    asks EA for both clubs' recent matches
    saves them and pushes them to GitHub

GitHub, after every push from the Pi
    runs the tests, rebuilds the website, publishes it (never contacts EA)
```

You will not need a screen, keyboard or mouse for the Pi. You set it up from your
Windows PC over your home network.

## What you need

- **The Raspberry Pi** with its power supply. Any Pi 3, 4, 5 or Zero 2 W works
  well. An older Pi (Pi 2, Pi 1, original Zero) also works, just more slowly.
  The model is printed on the board.
- **A microSD card** of 8 GB or more. Everything on it will be erased.
- **A way to put the card into your PC**: a card slot or a USB card reader.
- **Your Wi-Fi name and password**, or a network cable to plug the Pi into your
  router.
- **Your GitHub login**, to give the Pi permission to push.

In the commands below, replace anything in `<angle brackets>` with your own value,
without the brackets.

---

## Part 1 — Prepare the memory card (on your PC)

The Pi has no built-in storage: its operating system lives on the microSD card.
The official tool **Raspberry Pi Imager** downloads the system and writes it to
the card.

1. Download and install Raspberry Pi Imager from
   <https://www.raspberrypi.com/software/>.
2. Put the microSD card into your PC. If Windows offers to format it, click
   **Cancel**.
3. Open Raspberry Pi Imager and choose:
   - **Device**: your Pi model.
   - **Operating system**: scroll past the first entries to *Raspberry Pi OS
     (other)* → **Raspberry Pi OS Lite (64-bit)**. (The plain "Raspberry Pi OS
     (64-bit)" at the top also works, but installs a desktop you won't use.) "Lite" has no desktop, which is all a collector needs. For a Pi 2,
     Pi 1 or original Zero, choose the **32-bit** Lite version instead.
   - **Storage**: your microSD card. Double-check it is the card and not another
     USB drive.
4. Imager now offers **OS customisation** (in older versions: click **Next**,
   then **Edit settings**). This step is what lets you set up the Pi without a
   screen. Fill in:
   - **Hostname**: `dubsfc`. Your PC will find the Pi under this name.
   - **Username and password**: choose your own, for example username `deniz`
     and a strong password. Write them down; you need them in Part 2.
   - **Wireless LAN**: your Wi-Fi name (SSID) and password, and your country
     (for example BE). Skip this if you'll use a network cable. A Pi 3B or older
     only connects to **2.4 GHz** Wi-Fi: if your router shows a second network
     name ending in "5G", use the other one.
   - **Locale / time zone**: for example Europe/Brussels.
   - **Services** tab (or the "Remote access" step): enable **SSH** with
     **password authentication**. SSH is how your PC will talk to the Pi.
5. Save the settings, confirm that the card will be erased, and wait until Imager
   says writing and verifying are done (5–15 minutes).
6. Take the card out of the PC.

### No card reader?

Connecting the Pi to your PC with a USB cable does **not** work: the Pi doesn't
act as a card reader for your PC. Instead, either:

- **Get a USB microSD card reader** (about €5–10 in most electronics shops), or
  borrow a computer that has one. Many laptops have an SD slot, and microSD cards
  often come with an adapter that fits it.
- **Install from the Pi itself (Network Install).** This only works on a
  **Raspberry Pi 4B, 5, 400, 500 or 500+**. It needs a screen, a USB keyboard and
  a **network cable** to your router, just for this step; Wi-Fi doesn't work for
  it. Pi 4 and Pi 5 need a micro-HDMI cable for the screen.
  1. Put the microSD card in the Pi, and connect the screen, keyboard and network
     cable.
  2. Hold **Shift** while plugging in the power. If the card is blank, holding
     Shift isn't necessary.
  3. The Pi downloads Raspberry Pi Imager and shows it on the screen. Follow
     steps 3–5 above, with the same choices and settings.
  4. When Imager has finished, unplug the power, disconnect the screen and
     keyboard, and continue with Part 2. You can keep the network cable or rely
     on the Wi-Fi you entered.

  If the Pi starts an existing system instead of the installer, the card already
  has one; restart while holding Shift.

## Part 2 — Start the Pi and connect to it

1. Put the microSD card into the slot underneath the Pi.
2. If you use a network cable, plug it into the Pi and your router.
3. Plug in the power supply. There is no power button: the Pi starts as soon as
   it has power. A small light blinks while it works.
4. **Wait about 5 minutes.** The first start takes longer than later ones.

Now connect from your PC. Open **PowerShell** (Start menu → type `PowerShell` →
Enter) and type, with your own username:

```powershell
ssh <username>@dubsfc.local
```

- The first time, it asks *"Are you sure you want to continue connecting?"*.
  Type `yes` and press Enter. This only happens once.
- Type the Pi password you chose. **Nothing appears while you type**, not even
  dots. That is normal; press Enter when done.

You are in when the prompt changes to something like:

```text
deniz@dubsfc:~ $
```

From now on, every command in this guide is typed into this window, and runs on
the Pi, not on your PC. To leave, type `exit`. To come back later, run the same
`ssh` command again.

**If it says "Could not resolve hostname"**: wait two more minutes and try again.
If it still fails, open your router's web page, find the device called `dubsfc`
in its list of connected devices, note its IP address (like `192.168.1.23`), and
use `ssh <username>@192.168.1.23` instead.

**Copying and pasting**: to paste into the window, right-click (or press
Ctrl+V). To copy text out of it, select it with the mouse, then press Ctrl+C or
right-click, depending on your terminal.

## Part 3 — Update the Pi and install Git

First bring the system up to date. `sudo` means "run as administrator"; it may
ask for your Pi password again.

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y git python3
```

This can take 5–20 minutes and prints a lot of text. When the prompt comes back,
restart the Pi:

```bash
sudo reboot
```

The connection closes. Wait one minute, then connect again with the same `ssh`
command as in Part 2.

Check that Python is recent enough:

```bash
python3 --version
```

It must say **3.10 or higher** (for example `Python 3.11.2` or `Python 3.13.5`).
If it shows 3.9 or lower, the card has an old system version: redo Part 1 with
the current Raspberry Pi OS.

## Part 4 — Let the Pi push to GitHub

The Pi needs permission to push data to your repository. You give it a **deploy
key**: a key that works for this one repository only, so the Pi cannot touch the
rest of your GitHub account.

On the Pi, create the key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/dubsfc_deploy -N "" -C "dubsfc raspberry pi"
```

Tell the Pi to use this key for GitHub. Paste the whole block at once:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-dubsfc
  HostName github.com
  User git
  IdentityFile ~/.ssh/dubsfc_deploy
  IdentitiesOnly yes
EOF
```

Show the public half of the key:

```bash
cat ~/.ssh/dubsfc_deploy.pub
```

It prints one long line starting with `ssh-ed25519` and ending with
`dubsfc raspberry pi`. Copy that whole line. It is safe to share; the private
half stays on the Pi.

Now on GitHub, in your browser:

1. Open <https://github.com/denizsoysal/EA-FC-pro-club-tracker>.
2. Click **Settings** (top of the repository page), then **Deploy keys** in the
   left menu, then **Add deploy key**.
3. **Title**: `Raspberry Pi`. **Key**: paste the line you copied.
4. Tick **Allow write access**. Without it, the Pi can download but not push.
5. Click **Add key**.

Back on the Pi, test it:

```bash
ssh -T git@github-dubsfc
```

Type `yes` if it asks whether to continue connecting. Success looks like:

```text
Hi denizsoysal/EA-FC-pro-club-tracker! You've successfully authenticated, but GitHub does not provide shell access.
```

"does not provide shell access" is expected. If it says **Permission denied
(publickey)**, check that you copied the whole line and saved the key on GitHub.

## Part 5 — Download the project

```bash
git clone git@github-dubsfc:denizsoysal/EA-FC-pro-club-tracker.git ~/dubsfc-tracker
cd ~/dubsfc-tracker
```

This puts the project in a folder called `dubsfc-tracker` in your home folder.
`cd` moves into it; every later command must be run from there. If you reconnect
later, run `cd ~/dubsfc-tracker` first.

Check that the downloaded archive is intact (this does not contact EA):

```bash
python3 scripts/tracker.py verify
```

It prints a block of text. Both clubs, `696276` (Dubs VzeDoux) and `976704`
(FEN V12), must show `"status": "ok"`.

## Part 6 — The first collection

When GitHub was blocked, the collector paused itself for every club so it would
stop sending refused requests. This first run lifts that pause and collects:

```bash
bash deploy/raspberry-pi/collect.sh --resume
```

It sends EA four requests, a couple of seconds apart, and prints a lot of text.
Look for lines like these near the end:

```text
[Dubs VzeDoux (696276)] leagueMatch: received 10, changed 0
[Dubs VzeDoux (696276)] playoffMatch: received 10, changed 0
[FEN V12 (976704)] leagueMatch: received 10, changed 0
[FEN V12 (976704)] playoffMatch: received 5, changed 0
...
{'status': 'pushed', ...}
```

- **received** is how many matches EA returned; **changed** is how many were new
  or updated. `changed 0` just means there was nothing new.
- **`'status': 'pushed'`** means the data reached GitHub. A minute or two later,
  the website is updated.

**If you see "EA returned HTTP 403"**, EA refuses your home connection too.
Don't run the command again and again; that only makes it worse. Send the output
to whoever helps you with the tracker.

## Part 7 — Make it run automatically

```bash
bash deploy/raspberry-pi/install.sh
```

It asks for your Pi password, then shows when the next run is due, for example:

```text
NEXT                         LEFT       ...  UNIT                    ACTIVATES
Thu 2026-10-22 18:07:43 CEST 12min left ...  dubsfc-collector.timer  dubsfc-collector.service
```

That's it. From now on the Pi collects at 7 and 37 minutes past every hour,
including after a power cut: it starts again on its own when power returns.

You can now type `exit`. The Pi keeps working with the window closed and your PC
switched off. It only needs power and internet.

---

## Checking that it works

**Without connecting to the Pi**: on GitHub, look at the repository's commits.
About twice an hour you should see "Archive DubsFC match data and retained
revisions" by **DubsFC Raspberry Pi collector** (only when something changed).
The website's footer shows when each club's archive last changed.

**On the Pi** (connect with `ssh`, then `cd ~/dubsfc-tracker`):

| What you want | Command |
|---|---|
| When is the next run? | `systemctl list-timers dubsfc-collector.timer` |
| What did the last runs print? | `journalctl -u dubsfc-collector -n 50 --no-pager` |
| Run a collection right now | `sudo systemctl start dubsfc-collector` |
| Check the archive | `python3 scripts/tracker.py verify` |

If `journalctl` says "No journal files were found", put `sudo` in front of it.

## Everyday questions

**Do I need to keep my PC on?** No. Only the Pi needs to stay plugged in.

**How do I switch the Pi off?** Don't just pull the plug: that can damage the
memory card. Connect and run `sudo shutdown now`, wait until the light stops
blinking, then unplug. Plugging it back in starts it again, collection included.

**What if the Pi was off for a while?** It catches up shortly after it starts
again. But EA only lists the last 10 matches of each competition, so matches
played while it was off can be lost if more than 10 newer ones were played
meanwhile. On a quiet day an outage loses nothing; on a busy evening it can.

**How do I stop collecting?** Either:

- on the Pi: `sudo systemctl disable --now dubsfc-collector.timer`
  (start again with `sudo systemctl enable --now dubsfc-collector.timer`), or
- from anywhere: set `"collection_enabled": false` in `config.json` and push it.
  The Pi reads the change at its next run.

**I changed the code or config on my PC. Does the Pi need updating?** No. Push
the change to GitHub; before each run the Pi downloads the latest version.

**Should I still run `sync` on my PC?** No, not into the repository. The Pi is now
the only collector, and two collectors pushing the same files would conflict.
Running `build` and `verify` on your PC is fine.

**Can I change the Wi-Fi?** Connect and run `sudo nmtui`, choose **Edit a
connection**, and follow the menus. Do this before switching the old network
off, or redo Part 1 with the new Wi-Fi details.

**How do I take back the Pi's access to GitHub?** Repository **Settings → Deploy
keys** → delete "Raspberry Pi". Nothing else on your account is affected.

## When something goes wrong

| What you see | What it means / what to do |
|---|---|
| `Could not resolve hostname dubsfc.local` | The Pi is still starting, or your network doesn't support `.local` names. Wait, or use its IP address (Part 2). |
| `Connection refused` or `timed out` | The Pi is off, still starting, or SSH wasn't enabled in Part 1. Redo Part 1 with SSH enabled if it keeps happening. |
| `Permission denied (publickey)` when cloning or pushing | The deploy key is missing on GitHub, incomplete, or saved without **Allow write access**. Redo the GitHub steps in Part 4. |
| `Python 3.10 or newer is required` | The memory card has an old system. Redo Part 1 with the current Raspberry Pi OS Lite. |
| `Collection paused after access denial` | EA refused a request earlier (403). Once you believe it works again, run `bash deploy/raspberry-pi/collect.sh --resume` once. |
| `Rate-limit cooldown until …` | EA asked to slow down. The Pi waits on its own; nothing to do. |
| `Another tracker command is using this archive` | Two runs overlapped, for example a manual run during a scheduled one. Wait a minute and try again. |
| `Git push failed` | Usually a network problem. The data is kept on the Pi and pushed at the next run. |
| A club shows `"status": "error"` in `verify` | A data file is damaged or missing. Don't delete anything; ask for help with the exact message. |

## Words used in this guide

- **SSH**: a way to type commands on another computer over the network. `ssh`
  on your PC opens a command window "inside" the Pi.
- **Terminal / prompt**: the text window where you type commands. The prompt
  (`deniz@dubsfc:~ $`) means it's waiting for the next command.
- **sudo**: run one command as administrator.
- **Deploy key**: a GitHub key that gives access to one repository only.
- **systemd timer**: Linux's built-in scheduler, like Windows Task Scheduler. It
  runs the collector twice an hour.

## Details for the curious

- Each run executes `deploy/raspberry-pi/collect.sh`. It pulls the latest code,
  runs `tracker.py sync`, commits and pushes only `data/` with
  `persist_archive.py`, and fails if anything needs attention.
- Commits are authored "DubsFC Raspberry Pi collector". To change the name or
  email, set `DUBSFC_GIT_NAME` / `DUBSFC_GIT_EMAIL` as `Environment=` lines in
  `/etc/systemd/system/dubsfc-collector.service`.
- The Pi makes no per-run ZIP backup: the archive stays on its card, and data
  that could not be pushed stays in a local commit for the next run. Keep an
  occasional `python3 scripts/tracker.py backup` copy elsewhere if you want one.
- **Why every 30 minutes?** EA's feed holds only the last 10 matches of each
  competition. The archive shows Dubs once played 10 league matches in 2.9
  hours, so twice an hour leaves a wide margin. Asking more often would not save
  more matches; it would only send EA more requests from your home connection.
- The collector uses only Python's standard library; nothing is installed with
  `pip`.
