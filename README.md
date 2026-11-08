# Mirto

**Local-first desktop video surveillance for macOS and Windows.** Mirto:

- shows every camera the computer can see (built-in, USB, network RTSP);
- detects motion and checks whether a **person** is in the frame (YOLOX model, running locally);
- alerts the Mirto Admins: **photo on Telegram**, **email** with the photo attached and a link to the
  Telegram message, optional **SMS**;
- when **protection is on**, controls the outputs of external **nodes** (siren, lights, audio, generic
  relays and sockets) through signed HTTP requests.

**Guiding principle:** no third-party cloud. Video stays on your computer; only the alert snapshots leave
it, and only towards the channels you configure.

> ⚠️ Please read [Responsible use](#responsible-use) before installing: video surveillance and deterrent
> devices are regulated by law.

> 📖 User guide: [English](USER_GUIDE.md) · [Italiano](GUIDA_UTENTE.md). The app speaks English and Italian
> (Settings → General → Language; automatic from the system).

## Features

| Area | What it does |
|---|---|
| Cameras | Stable camera IDs (AVFoundation on macOS, Media Foundation on Windows), hot-plug, automatic reconnection, RTSP/HTTP network cameras, automatic discovery via mDNS (`_rtsp._tcp`) |
| Detection | Background subtraction (MOG2) on every frame, YOLOX Nano/S (ONNX Runtime, CoreML on macOS and DirectML on Windows, CPU otherwise) only on motion, multi-frame confirmation, 80 selectable COCO classes; bird groups (pigeons, corvids, gulls…) with a second iNaturalist model |
| Sounds | YAMNet sound recognition from the Mac microphone and node USB microphones: glass breaking, smoke alarm, sirens, screaming, gunshots, dog barking, knocking, voices, bird calls; audio is never stored or sent |
| Deterrents | Bird and animal deterrent sounds on the nodes (raptor, falcon, crow alarm, shot, gas cannon, your own WAV files), per species group or animal, always on, never for persons, rate-limited |
| Recording (NVR) | Per-camera *events only* clips (10 s before → 20 s after), *continuous* recording or off; network cameras copied without re-encoding, local cameras encoded with the hardware H.264 encoder; 1-minute MP4 segments, retention by days and disk quota, clips sent to Telegram, `/clip` for the last seconds |
| Alerts | Telegram bot (long polling, no open ports, buttons and commands), SMTP email with presets (Gmail app password by default), generic SMS providers, retry queue, anti-spam pause |
| Protection | Arm/disarm from the app or Telegram, weekly schedules and date ranges, presence detection from household phones on Wi-Fi |
| Nodes | Multiple external nodes, pinned TLS certificates, HMAC-SHA256 signed requests with replay protection, built-in node simulator |
| Safety rules | Siren capped at 3 minutes (180 s every 30 min), every output switches off by itself, audio never automatic, intrusion actions only for persons (or chosen sounds) and only when armed, deterrents never for persons — except a *Prank* action while Prank mode is on on that node and the house is not armed — and max 2 minutes per hour, old Telegram commands ignored |
| Privacy | All processing local, credentials in the OS keychain, automatic deletion of old events; optional usage statistics, off unless you say yes ([`PRIVACY.md`](PRIVACY.md)) |

## Why not Frigate, Blue Iris or Scrypted?

They are good programs, and for many people they are the right answer. Mirto exists for a different
starting point: *you have a computer, a camera and no wish to run a server.*

| | **Mirto** | **Frigate** | **Blue Iris** | **Scrypted** |
|---|---|---|---|---|
| What it is | a desktop app you open | a Docker container you host | a Windows NVR | a self-hosted hub with an NVR plugin |
| Install | download, open, done | Docker Compose (or a Home Assistant app) | Windows installer | Docker / npm |
| Runs on | macOS, Windows | Linux best; **Windows not officially supported**, VMs not recommended | Windows only | Linux, macOS, Windows (via Docker) |
| Price | 0, Apache 2.0 | 0, open source; **Frigate+** $50/year for custom models | **$99.95** full, **$39.95** one camera, renewals from $44.95/year | open-source core, **paid NVR subscription** |
| Extra hardware | none: it uses your Mac's Neural Engine or your GPU | an accelerator is "strongly recommended"; CPU detection is "for testing only" | a decent GPU for many cameras | depends on the plugins |
| Interface | native window, Italian and English | web UI | Windows window + web | web UI + its own apps |
| Remote | Telegram bot: no open ports, no account on anything | web/HA, usually behind a VPN or reverse proxy | web server, port or VPN | its own cloud |
| Drives physical devices | **yes**: nodes with siren, lights, relays, sounds | through MQTT and your automations | relays via plugins | through the home-automation platform |
| Faces, plates | **no** | yes, built in | plugins | plugins |

**What Mirto does not do**, so nobody finds out after installing it: no face recognition and no licence
plates (a deliberate choice: in Italy they turn a camera into something a private person usually may not
run); no HomeKit, Home Assistant or MQTT integration; no web interface and no phone app — Telegram is the
remote control; no multi-user accounts; it is not meant for twenty cameras on one machine; on Linux it runs,
but only development and tests are supported there.

*Prices and claims checked on 18 October 2026 on the vendors' own pages
([Frigate+](https://frigate.video/plus/), [Frigate docs](https://docs.frigate.video/frigate/installation),
[Blue Iris](https://blueirissoftware.com/), [Scrypted](https://docs.scrypted.app/)); they change, and this
table is only as honest as its date.*

## Requirements

- macOS (tested on Apple Silicon) or Windows; Linux works for development and tests.
- Python **3.11+**.
- A Telegram account for alerts; optionally a Gmail account (app password) for email.

## Quick start (macOS)

Ready-made builds (`.dmg` for macOS, installer for Windows) are published on the
[Releases page](https://github.com/marcopogo81/Mirto/releases/latest). To run from the sources:

> **While the `.dmg` is unsigned**, the first time you open it macOS says the app cannot be verified.
> Drag Mirto to Applications, try to open it, then go to **System Settings → Privacy & Security**,
> scroll to the bottom and press **Open Anyway**. From the second time it opens normally.
> (Up to macOS Sonoma a right-click on the app → *Open* was enough; from 15 it is not.)

```bash
git clone https://github.com/marcopogo81/Mirto.git
cd Mirto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m mirto --elenca-camere      # list cameras and their stable IDs
python -m mirto                      # start the app
```

- On first launch macOS asks for **Camera** permission for the app you start Mirto from (e.g. Terminal).
- The detection model (~3.7 MB) is downloaded from the official YOLOX release and verified with SHA-256.
- If an image stays black: `python -m mirto --diagnosi-camere` saves a test photo for each camera index.

## Quick start (Windows)

Tested on **Windows 11 Pro** with Python 3.14, on the evening of 19 October 2026 — the same run that
produced most of the Windows fixes in 0.2.0.

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e     # then reopen PowerShell: PATH has changed

git clone https://github.com/marcopogo81/Mirto.git
cd Mirto
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe -m mirto --elenca-camere   # list cameras and their stable IDs
.\.venv\Scripts\python.exe -m mirto                   # start the app
```

What is different from macOS:

- **Install Python from python.org or winget, not from the Microsoft Store.** The Store build runs
  sandboxed, and a sandboxed Python may not see USB cameras at all.
- **`ffmpeg` does not have to be installed**: it comes with `imageio-ffmpeg`, which is a dependency.
  Nothing has to be added to the PATH.
- **Detection runs on the GPU** through DirectML (`onnxruntime-directml`, installed automatically on
  Windows). The *System* column says which one it chose: "GPU (DirectML)" or "CPU".
- **Secrets live in Credential Manager** (Control Panel → User Accounts → Credential Manager → Windows
  Credentials), as `Mirto` (bot token), `rtsp:<camera>@Mirto` and `node_hmac_key:<node>@Mirto`.
  Nothing secret is written to `config.yaml`.
- **Autostart is the `Run` key**: Mirto starts at login, but — unlike macOS — it is **not** restarted
  after a crash. That limit is on the roadmap (W-08).
- **One Telegram bot per computer.** Telegram lets a single program read a bot's commands, so two
  Mirto installations sharing one token take turns and `/stato` answers from whichever got there
  first. Create a second bot with BotFather for the second computer; alerts would still arrive from
  both, which is the other reason not to share.

Configuration (Telegram, email, SMS, schedules, presence, nodes) is done from **Settings** inside the app;
see chapters 4–10 of the user guide.

## Try the node actions without hardware

```bash
python -m mirto --simulatore-nodo --porta 8443 --nome-nodo ingresso
```

It prints the address, certificate fingerprint and shared key to use in *Settings → Nodes and actions*.
Run several instances on different ports to simulate several nodes. The simulator is also the
**reference implementation** of the node HTTP API.

## The Raspberry node (`node/`)

`node/` contains **Mirto Node**, the service that runs on a Raspberry Pi and executes the actions requested
by Mirto. It speaks the same signed HTTPS API as the simulator, needs only Python's standard library and system
tools, and enforces its own safety limits (siren at most 3 minutes, and 3 minutes per 30 minutes). The
node offers the **camera** (MediaMTX with a password on the stream, normal or NoIR), a **siren** on a Bluetooth,
jack, USB or HDMI speaker, and a test light (the Pi's green LED).
The service runs as the system user `mirto-node`, **unprivileged**: no shell, no capabilities, one writable
path (its own configuration). The few jobs that need root — the PIN on the microSD's boot partition, and the
recordings folder on a disk plugged in later — are done by a separate service, on request.

- One-command install: `sudo ./node/install.sh`; guided setup on the Pi with `mirto-nodo`
  (status · wifi · audio · camera · name · pair).
- **One-click pairing**: pairing is open after the installation or `mirto-nodo abbina` — it never expires
  until the node has been paired once, then 5 minutes per reopening — with a
  6-digit PIN shown by the node and a separate key for every Mac ("New node found → Pair"),
  no key to copy; the key is delivered over TLS only after the pinned certificate is checked.
- **Node image**: `mirto-nodo prepara-immagine` cleans a working node; every copy becomes unique on first boot.

Step-by-step guide: chapter 12 "Nodes" of `USER_GUIDE.md` (12.1 "Setting up a Raspberry node"); details in `node/README.md`.

## Running 24/7

- *Settings → General → Start Mirto at login* installs a LaunchAgent that restarts it if it crashes.
- Mirto prevents system sleep while running, but a MacBook with the lid closed still sleeps.
- Headless mode: `python -m mirto --senza-interfaccia`.

## Build the app

```bash
pip install pyinstaller
pyinstaller packaging/mirto.spec     # macOS → dist/Mirto.app · Windows → dist/Mirto/Mirto.exe
```

On Windows, [Inno Setup 6](https://jrsoftware.org/isinfo.php) turns that folder into an installer:

```bat
iscc /DMirtoVersion=0.2.0 packaging\mirto.iss   :: → dist\Mirto-0.2.0-setup.exe
```

Tagging `v<version>` does all of this in CI and opens a draft Release with both files, their SHA-256 sums
and a CycloneDX SBOM (`.github/workflows/release.yml`).

## Data locations (macOS)

| What | Where |
|---|---|
| Configuration | `~/Library/Application Support/Mirto/config.yaml` |
| Events, snapshots, logs, action log | `~/Library/Application Support/Mirto/` |
| Recordings | `…/Mirto/recordings/` (or the folder chosen in Settings → Recording) |
| Node certificates | `…/Mirto/nodes/` |
| Credentials | Keychain (service "Mirto"): bot token, email password, node keys, camera passwords |

## Data locations (Windows)

| What | Where |
|---|---|
| Configuration | `%LOCALAPPDATA%\Mirto\config.yaml` |
| Events, snapshots, logs, action log | `%LOCALAPPDATA%\Mirto\` |
| Recordings | `%LOCALAPPDATA%\Mirto\recordings\` (or the folder chosen in Settings → Recording) |
| Node certificates | `%LOCALAPPDATA%\Mirto\nodes\` |
| Credentials | Credential Manager → Windows Credentials: bot token, email password, node keys, camera passwords |

`%LOCALAPPDATA%` is `C:\Users\<you>\AppData\Local`. The configuration file inherits that folder's
permissions — your account, SYSTEM and the local administrators — so another standard user of the same PC
cannot read it; an administrator can, and no application can prevent that. See `SECURITY.md`.

## Tests

```bash
python -m unittest discover -s tests -t .
```

Tests using the real model need `MIRTO_MODELS_DIR` (folder with `yolox_nano.onnx`) and
`MIRTO_TEST_IMAGES` (sample images from the YOLOX repository); otherwise they are skipped.

## Responsible use

Mirto is a tool: whoever installs it is responsible for how it is used.

- **Privacy (GDPR and local rules).** Point cameras only at your own property. If they cover shared or public
  areas you need a notice sign and short retention. Tell the people living with you, including about the
  *Presence* feature that uses their phones.
- **Siren.** Mirto caps the siren at 3 minutes, but repeated false alarms may still break noise laws.
- **Software only.** Mirto controls the nodes' outputs; what you connect to them is your choice and your
  responsibility. Only connect devices that cannot hurt anyone or stop anyone from leaving. At first start
  Mirto asks you to accept the terms of use.
- **No warranty.** Mirto is not a certified alarm system and does not replace professional security. It is
  provided "as is" (see the license).

## Security

Found a vulnerability? **Please do not open a public issue**: follow [`SECURITY.md`](SECURITY.md)
— private report through GitHub, an answer within 7 days, coordinated disclosure at 90 days.
Everything in this repository is in scope, the node included.

## Support the project

Mirto is free and stays free: no paid tier, no closed features, no ads, and no cloud to sell you. If you
want to help, [GitHub Sponsors](https://github.com/sponsors/marcopogo81) covers the costs the project has
anyway — the certificates that sign the macOS and Windows downloads, hardware to test on, the update server.
Donations do not change the licence and do not buy priority: the roadmap is in [`ROADMAP.md`](ROADMAP.md).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for how to send a change, and [`GOVERNANCE.md`](GOVERNANCE.md) for
who decides and what will not change. Where the project is going — and what it deliberately will not do —
is in [`ROADMAP.md`](ROADMAP.md).

There is **no CLA**: contributions are Apache 2.0 and each commit carries a `Signed-off-by` line (the
[Developer Certificate of Origin](https://developercertificate.org/)), which CI checks on every pull
request.

**This project is worked on at weekends**, and it is better said than discovered: first answer within
7 days, decision within 20. Weekday silence means nothing. One maintainer at weekends is enough to keep a
program alive and not enough to make it grow, so **help is genuinely wanted** — Windows, Home Assistant
and MQTT, interface tests, translations, other hardware. The list and the reasons are in
[`GOVERNANCE.md`](GOVERNANCE.md).

## Who does what

Mirto is designed, directed and reviewed by **Marco Dalicco**. His are the requirements and the order they
are tackled in, the architecture and the security decisions, and the decisions about what Mirto will *not*
do — no faces, no licence plates, no cloud, no device that could hurt someone. His too is every test on real
hardware: cameras, Raspberry nodes, a Telegram bot, a Mac and a Windows PC, in a house where this thing is
actually on. Nothing reaches `main` without him.

Most of the code, the tests and this documentation are written by **Claude** (Anthropic), one task at a time
against a written plan, with the full test suite green before each commit. Every commit says so in its own
trailer:

```
Assisted-By: Claude Opus 5 <noreply@anthropic.com>
```

We write this down for two reasons. A security project that is coy about how it was built invites mistrust
the day someone reads the commit history anyway. And it tells you where to look: what protects you here is
what a person decided, reviewed and tried in a real house — not what was typed fastest.

## License

Copyright 2026 Marco Dalicco. Licensed under the **Apache License 2.0**: see [`LICENSE`](LICENSE) and
[`NOTICE`](NOTICE). Third-party components keep their own licenses, listed in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). The "Mirto" name and logo are not licensed
(Apache 2.0, section 6).

Icons: [Lucide](https://lucide.dev) (ISC). Detection model: [YOLOX](https://github.com/Megvii-BaseDetection/YOLOX) by Megvii (Apache 2.0).
