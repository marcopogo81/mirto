# Mirto – User guide

Mirto is a video surveillance application for **macOS** and **Windows** that runs entirely on your home
computer: it receives the camera feeds, recognises people, animals, vehicles and sounds with local
artificial-intelligence models and sends alerts on **Telegram**, by **email** and, optionally, by **SMS**. With
**nodes** (for example a Raspberry Pi) it adds cameras, sirens, lights and deterrents.

Images and recordings stay on the computer: only the photos and clips of the alerts leave it.

> **The guide inside the app.** Open it from **Help → User guide**. In every **Settings** tab the
> **📖 Full guide** link goes straight to the chapter that describes it.

---

## Contents

- [1. Introduction](#1-introduction)
- [2. Installing on macOS](#2-installing-on-macos)
- [2b. Installing on Windows](#2b-installing-on-windows)
- [3. First launch](#3-first-launch)
- [4. The main window](#4-the-main-window)
- [5. Cameras](#5-cameras)
- [6. Protection and alerts](#6-protection-and-alerts)
- [7. Notifications](#7-notifications) – [7.1 Telegram](#71-telegram) · [7.2 Email](#72-email) · [7.3 SMS](#73-sms)
- [8. Recognition (AI)](#8-recognition-ai) – [8.1 Cameras](#81-cameras-what-to-recognise) · [8.2 Microphones](#82-microphones-sounds)
- [9. Recording and Review](#9-recording-and-review)
- [10. Schedule](#10-schedule)
- [11. Presence](#11-presence)
- [12. Nodes](#12-nodes)
- [13. Actions](#13-actions)
- [14. General settings and continuous operation](#14-general-settings-and-continuous-operation)
- [15. Tests and diagnostics](#15-tests-and-diagnostics)
- [16. Troubleshooting](#16-troubleshooting)
- [17. Security, privacy and terms of use](#17-security-privacy-and-terms-of-use)
- Appendices: [A. Telegram commands](#appendix-a-telegram-commands) ·
[B. Installing from source and command line](#appendix-b-installing-from-source-and-command-line) ·
[C. Node commands](#appendix-c-node-commands) ·
[D. Where the data is](#appendix-d-where-the-data-is) ·
[E. Defaults and limits](#appendix-e-defaults-and-limits)

---

## 1. Introduction

### How it works

1. Mirto receives video from the cameras you have switched on: built-in camera, USB cameras, iPhone
   (Continuity Camera), RTSP/HTTP network cameras and node cameras.
2. When the picture changes, the recognition model checks the frame. If it recognises what you chose to watch
   (a **person** by default), it creates an **event** with a photo and, if recording is on, a video clip.
3. If **protection** is on, the event becomes an **alert**: photo on Telegram and by email, optional SMS and
   optional **actions** on the nodes (siren, lights, sounds).

### Requirements

| Requirement | Detail |
|---|---|
| Operating system | macOS (Apple Silicon or Intel) or Windows 10/11 |
| Cameras | At least one built-in, USB, network or node camera |
| Network | Internet connection for Telegram, email and the first download of the models |
| Accounts | Telegram (recommended), an email account for alerts; optional SMS through a provider |
| Nodes (optional) | Raspberry Pi 4 or 5 with Raspberry Pi OS Lite 64-bit (see [12.1](#121-setting-up-a-raspberry-node)) |

Approximate time for the first setup: 30 minutes.

---

## 2. Installing on macOS

1. Download the `.dmg` file from the [Releases page](https://github.com/marcopogo81/mirto/releases).
2. Open it and drag **Mirto** into the **Applications** folder.
3. Start Mirto from Applications.

> **App not yet signed.** The first time, macOS may say the app cannot be verified. Try to open it, then go to
> **System Settings → Privacy & Security**, scroll to the bottom and press **Open Anyway**. From then on it
> opens normally.

To install from source with Python see [Appendix B](#appendix-b-installing-from-source-and-command-line).

---

## 2b. Installing on Windows

1. Download the installer (`Mirto-<version>-setup.exe`) from the
   [Releases page](https://github.com/marcopogo81/mirto/releases).
2. Run it and follow the steps; Mirto appears in the Start menu.
3. If Windows SmartScreen says the app is not recognised, choose **More info → Run anyway**.

`ffmpeg`, used for recording, is included: there is nothing to install.

**Differences from macOS**

| macOS | Windows |
|---|---|
| Keychain | **Credential Manager → Windows Credentials** (entries `Mirto`, `rtsp:…`, `node_hmac_key:…`) |
| `~/Library/Application Support/Mirto/` | `%LOCALAPPDATA%\Mirto\`, i.e. `C:\Users\<user>\AppData\Local\Mirto\` |
| Recognition on the Apple Neural Engine (CoreML) or CPU | Recognition on the **GPU (DirectML)** or CPU: the *System* column says which |
| Icon in the menu bar, at the top | Icon in the **notification area**, bottom right |
| After a crash Mirto restarts by itself | After a crash it has to be reopened by hand (see [14](#14-general-settings-and-continuous-operation)) |
| ⌘Q to quit, ⌘, for Settings | **Ctrl+Q** to quit, **Ctrl+,** for Settings |

Installing from source on Windows is described in [Appendix B](#appendix-b-installing-from-source-and-command-line).

---

## 3. First launch

1. **Terms of use.** Read them, tick **I have read and accept the terms of use** and press **I accept**.
   Without accepting them Mirto closes. They remain available in **Help → Terms of use** (see
   [17](#17-security-privacy-and-terms-of-use)).
2. **Usage statistics.** Mirto asks **"Will you help improve Mirto?"**. The default answer is **No, thanks**;
   with **Yes, send usage statistics** a short anonymous message is sent once a day. **Show what is sent**
   displays the exact message. You can change your choice in
   [Settings → General](#14-general-settings-and-continuous-operation).
3. **Camera permission.** The system asks for access to the **Camera**: answer **Allow**. On macOS the
   permission is managed in *System Settings → Privacy & Security → Camera*.
4. **Services start.** In the *System* column the rows go from amber ("connecting…", "loading…") to green
   within a few seconds. On the first launch the recognition model is downloaded (about 3.7 MB) and verified
   with a SHA-256 fingerprint.
5. **Choose the cameras** to use in **All cameras** (see [5](#5-cameras)) and **set up Telegram** (see
   [7.1](#71-telegram)).

> **Black picture?** Quit Mirto completely (⌘Q / Ctrl+Q) and open it again: the first time, while the system
> asks for permission, the camera may not switch on. If the problem persists see [16](#16-troubleshooting).

---
## 4. The main window

| Area | Content |
|---|---|
| **Status band** (top) | The colour of the protection status; it flashes red when a person is detected |
| **Left column** | The status in large type with the logo, the **TURN PROTECTION ON/OFF** commands, **Mute 1 hour**, **STOP ALL** (only when needed), **Settings** and the **System** panel |
| **Central area** | The **Camera grid**, **All cameras**, **Review** and **Nodes** views (the last one only with at least one paired node) |
| **Events strip** (below the cameras) | Recent events, newest on the left |
| **Bottom bar** | Program messages, version and author |

### The views

- **Camera grid**: one tile per active camera, with name and status over the video. **Double-click** enlarges a
  camera; **Back to grid** (or another double-click) returns. Hovering over a camera shows the **Photo** button;
  **right-click** offers **Photo now** and **Enlarge / back to grid**. The slider at the bottom right sets the
  size of the previews. The last dashed tile opens **All cameras**.
- **All cameras**: every camera the computer sees, to choose and manage them (see [5](#5-cameras)).
- **Review**: the recordings day by day (see [9](#9-recording-and-review)).
- **Nodes**: all paired nodes in one table (see [12.4](#124-the-nodes-table)).

### The System panel

Shows the live status of each component with a coloured dot: **green** ready, **amber** starting or to be
checked, **red** problem. Hovering over a row shows the details; a click expands or collapses the rows that
have more information.

| Row | What it shows |
|---|---|
| **Cameras online** | Cameras in use and their status; then the switched-off, disconnected and new network cameras |
| **AI Recognition** | Model in use, where it runs (Apple Neural Engine, DirectML GPU or CPU), watched categories and threshold |
| **Notifications** | Telegram (group, bot, authorised users with ✓ or ⚠), email and SMS; any queued alerts |
| **Sounds** | Microphones being listened to and the last sound recognised |
| **Recording** | Cameras recording, space used and free |
| **Presence** | Who is home (🏠) and who is away (🚶) |
| **Nodes online** | For each node: what is on, speaker, camera, recording, temperature and version; an action row can be switched on or off with a click |
| **New node found** | Appears when there is a node to pair on the network, with **Pair…** and **Ignore** |

### Events

The **Events** strip shows time, camera and photo of each event; a purple dot marks animals, vehicles and
objects, the **▶** symbol events with video. The **Photo | Video** switch filters the strip.

- **Double-click**: opens the photo (or the video, in the *Video* view).
- **Right-click**: **Review in Mirto**, **Open the photo**, **Delete selected**.
- **Folder** opens the photos and videos folder, **Delete** (or the ⌫/Del key) deletes the selected events,
  **Clear** deletes them all.

Events older than the retention period (30 days by default) are deleted automatically.

### Menus

| Menu | Items |
|---|---|
| **Mirto** | **Settings…** (⌘, / Ctrl+,), **Close window (keeps running in background)** (⌘W / Ctrl+W), **Quit Mirto** (⌘Q / Ctrl+Q) |
| **Tests** | The tests in [15](#15-tests-and-diagnostics) |
| **Help** | **Telegram commands**, **User guide**, **Open logs folder**, **Support Mirto**, **Terms of use**, **About Mirto** |

Confirmations of important operations (quitting, Stop all, clearing events, switching off the only camera)
appear with a yellow sign: the red button confirms, **Cancel** (or Esc) leaves everything as it is.

---

## 5. Cameras

**All cameras** shows every camera the computer sees, grouped by origin (cameras of this computer and network
cameras, cameras of each node, disconnected cameras). For each camera:

| Control | Function |
|---|---|
| **Switch** | Green = **on**: it appears in the grid, is analysed and sends alerts. Grey = **off**: Mirto does not open it |
| **Preview** | Only for switched-off cameras: turns it on for a few seconds to see what it frames |
| **Photo** | Saves and opens a photo from the camera |
| **Rename** | Name used in the app and in alerts (for example "Entrance") |
| **Not this one?** | If the picture belongs to another camera, choose which one: the two names are swapped |
| **Remove** | Only for network cameras added by hand |

The choice is saved: a switched-off camera stays off even if you unplug and reconnect it. For example, switch off
the iPhone so that Mirto does not use it when the phone is near the computer.

### Network cameras

- **Found on the network**: cameras that announce themselves on the local network appear in this panel. **Add**
  brings them into the grid (Mirto finds them again even if their IP address changes); **Hide** removes them
  from the panel, **Show hidden cameras** brings them back. **Check again now** repeats the search at once.
- **Add network camera…**: for an IP camera, enter a name and the `rtsp://…` or `http://…` address given in the
  camera's manual. User name and password are stored in the Keychain (Windows Credentials).
- **Node** cameras do not need to be added: they appear by themselves after pairing (see [12](#12-nodes)).

The card of each network camera shows the address in use; "also responds on …" signals a second reachable
address (for example cable and Wi-Fi at the same time).

> The built-in camera cannot see in the dark. When the picture is too dark Mirto says so once per camera, and
> the tile shows an amber border until the light comes back.

---

## 6. Protection and alerts

### States

| State | Colour | Meaning |
|---|---|---|
| **Protected** | Green | Every detection generates an alert and can start the actions |
| **Paused** | Amber | Protection on, alerts suspended until the time shown |
| **Not protected** | Grey | No alerts: events stay only on the computer |

When a person is detected the logo turns red, **DETECTION** appears under the status and the camera tile is
highlighted: **red** = person, **purple** = animal, vehicle or object, **blue** = motion, **amber** = muted camera or
picture too dark. Turning protection off removes the red highlights.

### Commands

| Command | Function |
|---|---|
| **TURN PROTECTION ON / TURN PROTECTION OFF** | Changes the state. The same can be done from Telegram (`/arm`, `/disarm`), the schedule and presence |
| **Mute 1 hour** | Appears with protection on after a recent alarm: suspends alerts for 60 minutes. While they are suspended it becomes **Resume alerts** |
| **STOP ALL** | Appears only when an action is running or waiting for confirmation: switches off the actions on the nodes, pauses the deterrents for one hour and turns protection off. From Telegram `/stop` always works |

A manual change lasts until the next scheduled time (see [10](#10-schedule)).

### The alert

Each alert arrives on Telegram with the photo (and, if recording is on, the clip a few seconds later) and by
email with the photo attached. Under the photo Telegram offers:

- **False alarm**: marks the event as wrong;
- **Mute 1 hour**: suspends the alerts from that camera;
- **Trigger \<action\>**: confirms an action set to "Ask for confirmation on Telegram";
- **⛔ Stop all**: like the button in the app.

After an alert the same camera does not send another one for **2 minutes**. The Telegram commands are listed in
[Appendix A](#appendix-a-telegram-commands).

---
## 7. Notifications

**Settings → Notifications** lists the **Telegram**, **Email** and **SMS** channels on the left (● on, ○ off) and the
settings of the selected channel on the right. After every change press **Save**.

### 7.1 Telegram

Telegram is the main channel: it receives the photo with buttons to react and lets you control Mirto remotely.
You need a **bot** and a **group**.

**Create the bot**

1. In Telegram search for **@BotFather** (with the blue tick) and press **Start**.
2. Send `/newbot`, choose a name (e.g. *Mirto*) and a username ending in `bot`.
3. Copy the **token** BotFather returns (e.g. `7123456789:AAH…`).

> The token is the bot's password: do not share it and do not use **Revoke** in BotFather, or Mirto stops
> receiving commands.

**Create the group**

1. Create a group (e.g. **Mirto Admin**) and add the bot and your family.
2. In the group info: **Edit → Chat history for new members → Visible**. The group becomes a supergroup: emails
   will include a direct link to the Telegram message.
3. *(Optional, to send voice messages to the nodes)* in BotFather: `/setprivacy` → choose the bot → **Disable**.

Instead of a group you can use the private chat with the bot.

**Connect Mirto**

1. **Settings → Notifications → Telegram**: tick **Send alerts on Telegram (main channel)**, paste the **token** in
   **Bot token** and press **Save**.
2. In the group send `/id`. The bot replies with the **chat ID** (it starts with `-100`) and your **user ID**.
3. Enter the chat ID in **Group ID** and your ID in **Authorized users** (several IDs separated by commas).
4. Press **Send test message**, then **Save**.

**Authorized users** can send commands and use the buttons; the other members of the group only receive the
alerts. To add a family member: they join the group and send `/id`, and you add their user ID.

Mirto sets the bot's command menu (in English and Italian) automatically. The connection status is shown in the
**Notifications** row of the *System* panel: group name, number of members, bot and authorised users (red ⚠ = no
longer in the group, amber ⚠ = unknown ID).

> **One bot per computer.** Telegram delivers a bot's commands to a single program: two Mirto installations
> with the same token fight over them. For a second computer create a second bot with BotFather.

### 7.2 Email

Mirto sends an email with the photo attached and, if the Telegram group is a supergroup, the link to the
message. With Gmail you need an **app password**.

1. On [myaccount.google.com](https://myaccount.google.com) → **Security** turn on **2-Step Verification**.
2. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), create a password named
   **Mirto** and copy the 16 letters.
3. Fill in **Settings → Notifications → Email**:

| Field | Value |
|---|---|
| **Send email with photo attached and link to Telegram** | Ticked |
| Provider | **Gmail** (Yahoo, iCloud, Libero, Aruba and a custom server are also available) |
| SMTP server / Port | Empty and 0: the provider's values are used (the server is needed only for "custom") |
| User | Your address |
| Password | The app password, **without spaces** |
| Recipients | Addresses separated by commas |

4. Press **Send test email** (check the Spam folder the first time), then **Save**.

Outlook/Hotmail is not supported: it requires a different kind of Microsoft sign-in.

### 7.3 SMS

SMS is a paid backup channel: messages arrive even without mobile data. They contain only text (camera, time,
type of detection) and the link to the Telegram message; they are sent **only for people**.

1. **Settings → Notifications → SMS**: tick **Enable SMS (backup channel, paid)** and choose the **provider**; only
   the fields it needs are shown.

| Provider | Use | Required data |
|---|---|---|
| **Generic HTTP** | Services with a web API (Skebby, Aruba SMS, Brevo…) | URL, method, body, Content-Type, headers and API key |
| **Twilio** | International service | Account SID, token and sender number |
| **Email-to-SMS** | Providers that turn an email into an SMS | Address template, e.g. `{numero}@provider.it` (uses the configured email) |

2. For the **Generic HTTP** provider: the URL and body accept the placeholders `{numero}` (number), `{testo}`
   (text) and `{api_key}`, for example `{"to": "{numero}", "message": "{testo}"}`. If the key goes in a header,
   write it in **HTTP – headers (JSON)**, e.g. `{"Authorization": "Bearer {api_key}"}`. In **HTTP – response must
   contain** you can enter a word the provider returns on success (e.g. `OK`).
3. **Recipients** in international format (e.g. `+393331234567`), separated by commas.
4. **When**: *For every event* or *Only if Telegram doesn't respond*.
5. **Daily limit** (20 by default) to keep costs down.
6. **Send test SMS (to the first recipient)**, then **Save**.

The API key and the Twilio token are stored in the Keychain (Windows Credentials).

---

## 8. Recognition (AI)

The **Settings → AI** tab gathers everything Mirto recognises, in two sub-tabs: **Cameras** and
**Microphones**. Recognition runs on the computer: images and audio are not sent to external services.

### 8.1 Cameras: what to recognise

| Setting | Function | Default |
|---|---|---|
| **Model that looks at the pictures** | *YOLOX Nano* (fast, ~3.7 MB, suited to the CPU) or *YOLOX-S* (more accurate, ~36 MB, recommended on Apple Silicon) | YOLOX Nano |
| **Person threshold for alerts** | Minimum confidence to alert about a person | 40% |
| **Pause between two alerts from the same camera (s)** | Avoids bursts of messages | 120 s |
| **Categories** | What generates an alert: People, Animals, Vehicles, Objects; **Show every category…** opens the others (80 in all) | *Person* only |
| **Threshold for animals, vehicles and objects** | Minimum confidence for categories other than people | 50% |
| **For birds, also identify the group** | A second model (~3.5 MB, downloaded with the first bird) gives the group: pigeons and doves, corvids, starlings, sparrows, gulls, herons, cormorants, parakeets, blackbirds and thrushes, raptors | On |

Behaviour by category:

- **People**: red band, alert, optional SMS and burglar-alarm actions.
- **Animals, vehicles and objects**: alert on Telegram and by email (e.g. "🐾 Detected: Dog"), purple frame; they
  never start sirens or SMS.
- Each category has its own 2-minute pause per camera.
- For birds the alert shows the group and, in brackets, the most likely species, which is only indicative. Below
  35% confidence it stays "Bird".

**Tuning the person threshold.** After a few days of use: if you find events without people (curtains, shadows,
animals) raise it by 0.05 at a time; if a person is not reported lower it by 0.05 and check there is enough
light. The thresholds in this tab are the defaults: an action can use its own (see [13](#13-actions)).

### 8.2 Microphones: sounds

Mirto recognises sounds with a local model (YAMNet, ~15 MB, downloaded on first use).

1. Tick **Listen to the computer microphone**: the first time the system asks for **Microphone** permission.
2. In **Photo from camera** choose which camera to photograph when the computer hears a sound.
3. A **node**'s microphone is turned on in **Settings → Nodes → Configuration → Audio → Microphone** with **Use
   this microphone**; **Mirto listens to this microphone** uses it for recognition (see
   [12.3](#123-configuring-a-node)).
4. In **Sounds that send an alert** choose the sounds and press **Save**.

| Sound | Examples | Default |
|---|---|---|
| 🪟 Glass breaking | Window or shop window shattering | Alert |
| 🔥 Smoke or fire alarm | Smoke detector | Alert, **even with protection off** |
| 🚨 Siren or alarm | Burglar alarm, car alarm | Alert |
| 😱 Screaming | People shouting | Alert |
| 😢 Crying | A baby crying | — |
| 💥 Gunshots or explosions | Shots, firecrackers, bangs | — |
| 🐕 Dog barking | The dog in the garden | — |
| 🚪 Knocking or door slam | Knocking, a slammed door, the doorbell | — |
| 🗣 Voices | People talking | — |
| 🐦 Bird calls | Cawing, pigeons cooing | — |

The alert arrives like the camera alerts (e.g. "🪟 Sound: Glass breaking") with the photo of the associated
camera. Each sound has a 2-minute pause per microphone. While a node is playing (siren, deterrent) and for the
10 seconds after, sounds are ignored, except the smoke alarm. **Mute** and **Paused** also apply to sounds. To make
a node **react** to a sound (e.g. glass breaking → siren) add the sound to the targets of an action (see
[13](#13-actions)).

> Listening is neither recorded nor sent: the audio stays in memory for a few seconds, just long enough to
> recognise the sound. The exception is recording a node's audio, which must be turned on explicitly (see
> [9](#9-recording-and-review)).

---
## 9. Recording and Review

### Settings → Recording

| Setting | Function | Default |
|---|---|---|
| **Record the cameras** | Turns recording on or off | On |
| **Keep photos and events (days)** | After how many days photos and events are deleted; videos follow the disk space | 30 |
| **Folder** / **Choose…** | Where recordings are saved (an external disk works too) | Mirto data folder |
| **Default** / **Per camera** | Recording mode for all cameras or for each one | Events only |
| **only when protection is on** | Per camera: with protection off it records nothing (the event photo is kept) | Off |
| **Keep continuous recording for** | Days of continuous recording kept | 7 |
| **Maximum space** | Maximum share of the available space | 50% |
| **Event clip** | Seconds before and after the event | 10 before, 20 after |
| **Computer and USB cameras** | Frames per second recorded | 10 |
| **Send the clip to Telegram after the photo** | Sends the clip as a reply to the photo | On |

| Mode | What it saves | Indicative space |
|---|---|---|
| **Events only** | One clip per event, from 10 s before to 20 s after | A few MB per day |
| **Continuous** | Everything, day and night, for the chosen days | About 10 GB per day per camera |
| **Off** | Only the event photo | — |

The **Now** column shows the actual state with the saved settings.

**Disk space.** The oldest recordings are deleted automatically; Mirto does not exceed the chosen quota and
always leaves at least 2 GB free. If the disk fills up anyway, recording is suspended and an alert is sent on
Telegram.

Files are one-minute MP4s, one folder per camera, playable with any player (QuickTime, VLC). Network cameras are
recorded without re-encoding; computer and USB cameras are compressed with the available hardware acceleration.

### The Review view

**Review** (at the top, next to *Camera grid* and *All cameras*) shows a camera's recordings day by day.

- **Camera and day**: choose the camera; ◀ ▶ change day, **Today** returns to today, **📁 Folder** opens the
  day's files.
- **24-hour bar**: recorded parts in blue; dots mark events with video (🔴 people, 🟣 animals and objects,
  🔵 sounds). Parts stored only on the node are teal: a click downloads them. Click an hour = go there; click an
  event = start 5 seconds before. **Mouse wheel** = zoom around the pointer, **drag** = choose an interval;
  **‹ Back**, **24 hours**, double-click or Esc go back.
- **Playback**: ⏮ ⏭ previous and next event, ±10 s (← → arrows), play/pause (space bar), **Speed** up to 16×,
  **📷 Photo** saves the frame, **⤓ Export…** saves an interval as MP4 (copied without re-encoding).
- From the **Events** strip, right-click → **Review in Mirto** opens that moment directly.

In *Events only* mode the bar holds the event clips and the last few minutes; to review a whole day use
*Continuous* mode.

### Recording on the node

Each node also records its own camera in a ring buffer, as a backup copy (see [12.3](#123-configuring-a-node),
*Disk*). The **node microphone's audio** can be included in recordings only if you turn on **Settings → Nodes →
Configuration → Disk → Also record the node's microphone** (off by default).

> **Privacy.** For cameras that film the street or shared areas, data-protection rules apply (in Italy those of
> the Garante Privacy): a sign, a limited view and short retention (prefer *Events only*). Audio has stricter rules
> than video: turn it on only if you are sure you may, and tell the people who live in the house.

---

## 10. Schedule

**Settings → Schedule** turns protection on and off automatically at the times you choose.

1. Check that **Automatic schedule on** is ticked.
2. Press **Add…** and fill in the rule: **Name** (e.g. *Night*), **What to do** (🔒 Turn protection on / 🔓 Turn
   protection off), **At what time**, **Days** (*Every day* or specific days) and, optionally, **Only between two
   dates** (e.g. holidays).
3. Press **OK**, then **Save**.

**Edit…** and **Delete** act on the selected rule; **Night + morning example** creates two ready-made rules (on at
23:00, off at 07:00).

- A manual change lasts until the next scheduled time.
- Every automatic change is announced in the Telegram group.
- From Telegram: `/schedule` shows the rules, `/schedule pause` suspends them, `/schedule resume` resumes
  them.

---

## 11. Presence

Mirto can tell who is home from the **phones connected to Wi-Fi**: when everyone leaves it turns protection on,
when someone comes back it turns it off. Every change is announced on Telegram (👥).

### Preparing the phones

Phones are recognised by their **MAC address**, which must stay fixed:

- **iPhone**: Settings → Wi-Fi → **(i)** next to the home network → **Private Wi-Fi Address: Fixed** (or Off).
  "Configure IP" stays on *Automatic*. The **Wi-Fi Address** is shown on the same screen.
- **Android**: Wi-Fi → home network → Advanced → **Privacy: Use device MAC**.

### Setup

1. **Settings → Presence**: tick **Detect who is home from phones connected to Wi-Fi**.
2. **Find devices on the network…**: after about 10 seconds the list of devices appears. Choose the one with the
   same MAC address as the phone, press **Add** and say whose it is. Alternatively use **Add manually…** and type
   the address.
3. Repeat for each person, then press **Save**.

| Setting | Function | Default |
|---|---|---|
| **When everyone leaves, turn protection on** | Turns protection on when no phone answers | On |
| **When someone comes back, turn protection off** | Turns protection off at the first return | On |
| **Consider "away" after (minutes without response)** | Avoids false "left" when the phone is asleep | 15 |

Presence acts only on changes: if you turn protection on by hand with everyone home, it stays on. At start-up
Mirto waits the set number of minutes before deciding nobody is home. The status is shown in the **Presence**
row of the *System* panel and with `/status` on Telegram.

> **Security.** A stolen phone or a copied MAC address could turn protection off. For maximum security leave
> *When someone comes back, turn protection off* unticked and turn protection off from Telegram.

---
## 12. Nodes

A **node** is a Mirto device, for example a Raspberry Pi, with everything connected to it: cameras, speakers
(for the siren, sounds and voice messages), lights, microphone, disk for recordings. It is paired once:

- its cameras appear by themselves in *All cameras*, grouped under the node's name, and follow it even if its
  address changes (cable, Wi-Fi, new IP);
- it is configured entirely from Mirto, without a terminal;
- removing it also removes its cameras and the actions that act only on it.

### 12.1 Setting up a Raspberry node

**You need**: a Raspberry Pi 4 or 5 with power supply, a microSD card of 16 GB or more, a Camera Module (standard
or NoIR, optional), a speaker (jack, USB or Bluetooth), a network cable for the first setup.

- **With a ready-made card** (Mirto image): insert the microSD, connect the network cable and power on. The node
  is called `mirto-node-XXXX` and can be paired straight away; the **PIN** is on the sheet supplied with the
  device or in the `mirto-pin.txt` file on the microSD.
- **Starting from Raspberry Pi OS**: write *Raspberry Pi OS Lite (64-bit)* with Raspberry Pi Imager with SSH
  enabled, then install the node software as described in [Appendix C](#appendix-c-node-commands). At the end the
  installation shows the address, the certificate fingerprint and the PIN.

Connect the camera to the **CAMERA** socket with the Raspberry Pi switched off.

### 12.2 Pairing a node

1. Power on the node. In the *System* column **New node found → Pair…** appears; alternatively **Settings →
   Nodes → Find nodes on the network**.
2. Check that the **certificate fingerprint** matches the node's.
3. Enter the **6-digit PIN** and choose the node's **name** (e.g. *Garage*): it is the name shown in Mirto and in
   alerts. The network name stays `mirto-node-XXXX.local`.
4. Press **Pair**. The node's cameras appear within a few seconds; Mirto then offers to create the actions (see
   [13](#13-actions)).
5. **If the node is on the network cable, Mirto asks whether to put it on Wi-Fi now** and opens the step
   described in [12.4](#124-the-nodes-table). Say yes: once the node is on a pole the cable comes out, and a
   node that has never seen your Wi-Fi disappears the moment you unplug it — and to configure Wi-Fi you have
   to reach it, which you no longer can. The cable stays plugged in until Wi-Fi works, so a wrong password
   costs you nothing. A node already on Wi-Fi is not asked.

| Situation | Length of the pairing window |
|---|---|
| Node never paired | Does not expire until it is paired |
| Later reopening | **5 minutes**, with a new PIN |
| After 5 wrong PINs | The window closes and has to be reopened |

**Ignore** hides the proposal until Mirto restarts. To pair a **second computer** use **Other computers → Open
pairing** (see below), which shows the PIN to enter on the other computer. Each computer receives its own key; if
an unknown computer pairs, you get an alert on Telegram.

### 12.3 Configuring a node

In **Settings → Nodes** the **PAIRED NODES** list shows each node with its status (green online, red not
responding), **Rename the node** and **Remove**. Selecting a node opens three sub-tabs.

**Cameras** – the cameras streamed by the node with their status in Mirto; clicking a camera renames it.

**Configuration** – the node's management windows:

| Item | Functions |
|---|---|
| **Other computers** | List of paired Mirto installations (name, address, date). **Open pairing** shows the PIN for another computer, **Close pairing** closes it, **Detach…** revokes a computer's key. The last pairing can only be revoked on the node |
| **Audio** | **The node's default output** with **Test sound**; **Jack**, **Bluetooth** (**Find speakers…**, **Pair**, **Forget**), **USB** and **Microphone** tabs (**Use this microphone**, **Mirto listens to this microphone**, live level); volume and gain apply immediately |
| **Camera** | **Camera on**, **It is the NoIR model** (only with infrared illuminators), **Resolution** (1280×720 is enough to recognise a person, 1920×1080 to read a face or a number plate) |
| **Disk** | **Scan for disks…** lists the SSDs and USB sticks connected to the node; **Record here** uses a disk that is already prepared, **Prepare the disk…** formats it (erases everything: confirm by retyping the device name). Sets ring-buffer recording, maximum space, space to keep free and **Also record the node's microphone** |
| **Prank mode** | Turns on for 10, 30, 60 or 120 minutes the mode that removes the hourly budget of the deterrents (see [13](#13-actions)); **Switch off now** ends it |
| **Status & diagnosis** | What is wrong and how to fix it, then version, addresses, temperature, recording, siren and pairings. **Read again**, **Update the node**, **Copy for support** (text without passwords or PIN) |
| **Wi-Fi** | **Scan for networks…** shows the networks the node sees, with signal strength; type the password or use **Take it from this computer** (from the Keychain or the Windows Wi-Fi profiles, as administrator), then **Connect**. The node stays on the cable until Wi-Fi works |

**Sounds** – the sounds the node can play (`rapace`, `falco`, `allarme_corvi`, `sparo`, `cannoncino` and those you
uploaded). **Add a sound…** uploads an audio file (WAV, MP3, M4A, AAC, OGG, FLAC), converted automatically;
**Play** plays it on the node, **Remove** deletes it (Mirto warns you if an action uses it).

**Recording on the node.** The node records its own camera in a ring buffer even with the computer off or the
network down: without a disk it uses the microSD with a low limit (about 2 GB); with an SSD or USB stick
(recommended) it keeps days. If the disk is disconnected recording stops and, after 5 minutes, an alert is sent on
Telegram. In *Review* the parts stored only on the node are downloaded with a click.

**Tips.** For a permanent installation prefer a jack or USB speaker: a Bluetooth speaker can switch off or be
taken over by a phone. If the chosen speaker does not respond the siren still sounds from the next available
output (jack, USB, system output) and *System* says so. A standard camera sees in the dark only with a light on,
a NoIR one with an infrared illuminator.

### 12.4 The nodes table

With at least one paired node the **Nodes** view appears at the top: one row per node with **Node**, **State**,
**Version**, **Space**, **Temp.**, **Network**, **Warnings** and **Last event**. The last column reveals a camera that
has stopped seeing events while staying online. **Double-click** a node to open it in Settings; **Update** appears
when a newer version is available.

### 12.5 Updating a node

When the node software included in Mirto is newer than the one installed, a notice appears at the top of
**Settings → Nodes**, in the node's row in *System* and in the **Nodes** table. **Update the node** sends the signed
package over the encrypted pairing channel; the node installs it and restarts (about a minute, during which it
does not respond). Configuration, keys, pairings and recordings are kept.

---
## 13. Actions

An **action** is what Mirto has the nodes do when it recognises something: sound the siren, switch on a light,
play a call against birds. Actions are managed in **Settings → Actions** and can involve several nodes (for
example a light at the entrance and the siren in the garage).

### The four kinds

The kind is not chosen: it follows from what the action does and what it recognises. You choose **when it's
active** and **what it does**.

| Kind | When it fires | Limits |
|---|---|---|
| 🚨 **Burglar alarm** | Only with protection on, for people recognised with certainty (60% threshold) or for the chosen sounds (e.g. glass breaking); never for animals | No usage limits; the siren sounds for at most 3 minutes at a time and 3 minutes every half hour |
| 🔔 **Doorbell and alerts** | Even with protection off: a sound, a light or an alert when someone arrives | The ones you set: pause between triggers, max triggers per day, time window |
| 🦅 **Deterrent** | Always, for birds and animals; **never for people** and never if a person is also in the frame | 30 seconds per trigger and 2 minutes of sound per hour (enforced by the node), plus your own limits |
| 🎭 **Prank** | Only with **Prank mode** on for that node **and** the house not protected; it is the only kind that may aim a sound at a person | No hourly budget; 30 seconds per trigger still apply. It goes quiet as soon as you turn protection on |

The node limits are enforced by the node itself and no program can exceed them. When a budget is used up, the
alert says when it resumes.

### Action library

**Action library** offers ready-made actions for the paired nodes, one per kind: **Burglar alarm** (siren),
**Doorbell**, **Deterrent for birds and animals** (pigeons and corvids, 40 triggers per day) and **Prank (a joke)**
(imported switched off). Tick the ones you want and press **Import**: they can be edited like any other.

### New action

**New action** opens a four-step wizard (**Back** / **Next**, **Save** at the last step):

1. **General information** – **Name** (also read on Telegram), **When it's active** (*Only when the house is
   protected (burglar alarm)*, *Always, even with protection off* or *Prank: a joke, only while the mode is on at
   the node*), **When it runs** (*Immediately, automatic* or *Ask for confirmation on Telegram*), **Confirmation
   wait**, **If no one confirms** (*Do nothing* or *Run anyway (only if certain it's a person)*) and **Time window**
   (e.g. `06:00-20:30`), depending on the kind.
2. **What makes it fire** – **From which cameras** and/or **From which microphones**, and **What the AI model
   should recognise**: person, any bird, a group of birds, an animal or a sound. An action can use its own
   threshold instead of the default, which stays shown next to it.
3. **Set up the actions** – the **chain** of operations to run (see below), **Sounds to play**, **Pause between
   triggers**, **Max triggers per day** and **Alert on Telegram when it triggers**, depending on the kind.
4. **Switch it on now?** – a summary of the kind and its limits; the action can be saved on or off.

**The chain.** It is a sequence of pieces on any detected device: **+** (**Start here** / **Add a piece**) offers
what the nodes and their speakers can do (siren, lights, outputs, node sounds, uploaded sounds) and **Wait** to
insert a pause. For each piece you choose the speaker, the sound, how many times and for how long. Several pieces
can run **together** or **in turn** (a different one at each trigger); the arrow between two pieces sets the wait;
**repeat all ×** repeats the whole strip. **Test the whole chain** runs it on the node before saving.

### Managing actions

Each action in the list shows its kind, the nodes involved and its state (**ON**, **OFF**, **UNSAVED**), with:

- **Test**: really switches the action on for 5 seconds, after a confirmation;
- **Edit…** and **Delete**.

Changes take effect with **Save**. Every action must have a different name. From the *System* panel an action can
also be switched on or off with a click on its row.

### Rules that cannot be changed

- Siren, lights and generic outputs (burglar alarm) run only with protection on and never for animals.
- Deterrents never fire for people, except a **Prank** action with the mode on and the house not protected.
- Every output switches off by itself when its time is up; **Stop all** switches everything off and pauses the
  deterrents for one hour.
- Audio to the node never starts by itself: it is used from Telegram with `/listen` and voice messages.
- Generic outputs (relays, sockets, HTTP services) control whatever you connect to them: choosing and using those
  devices lawfully is the responsibility of whoever installs them.

> **Respect your neighbours and the rules.** Loud sounds such as `sparo` (gunshot) and `cannoncino` (gas cannon)
> can be a nuisance: use the time window, keep the volume moderate (Settings → Nodes → Configuration → Audio) and
> check your local rules on acoustic deterrents.

---

## 14. General settings and continuous operation

**Settings → General**:

| Item | Function |
|---|---|
| **Lingua / Language** | *Automatic* (the system language), *Italiano* or *English*. Alerts and messages change at once, the window when reopened |
| **Start Mirto at login** | Starts Mirto when you log in to the computer. On macOS it also restarts it if it closes unexpectedly; on Windows it uses the `Run` registry key and, after a crash, Mirto has to be reopened by hand |
| **Prevent the computer from sleeping** | The computer does not go to sleep while Mirto is open |
| **Closing the window leaves it running in the background** | Closing the window keeps surveillance going |
| **Send usage statistics** | Once a day an anonymous message: random number, version, system, language, number of cameras and nodes in ranges, features in use. Never photos, video, audio, names or addresses. **What is sent…** shows the message, **New random ID** renews the number. Details in `PRIVACY.md` |
| **Check for new versions of Mirto** | Checks once a day for the latest available version; notifies once per version |

New settings apply as soon as you press **Save**. Model and thresholds are in **AI** (see
[8](#8-recognition-ai)), retention of photos and events in **Recording** (see [9](#9-recording-and-review)).

### Keeping Mirto always on

- Turn on **Start Mirto at login** and **Prevent the computer from sleeping**.
- On a laptop **keep the lid open**: closed, the computer goes to sleep anyway. Keep it plugged in and, if
  possible, limit battery charging to 80%.
- Keep **only one copy** of Mirto open.
- **In the background**: when you close the window Mirto asks whether to keep running in the background (with
  *Don't ask again* it stops asking). Surveillance continues and Mirto stays as an icon in the menu bar (macOS) or
  the notification area (Windows), in the colour of the status: green protected, amber paused, grey not protected,
  red person detected. A click reopens the window; the icon's menu offers **Open Mirto**, **Turn protection
  on/off** and **Quit Mirto**.
- **To quit completely**: **Mirto → Quit Mirto** (⌘Q / Ctrl+Q) and confirm. Surveillance stops.

---

## 15. Tests and diagnostics

The tests are in **Settings → Tests** and in the **Tests** menu:

| Test | Function |
|---|---|
| **Send a full test alert** | Live photo on Telegram, email and any SMS, even with protection off |
| **Test Telegram message** / **Test email** / **Test SMS** | Checks a single channel |
| **Take a photo from all cameras** | Checks what the cameras frame |
| **Check the nodes** | Checks that the nodes respond |
| **Run the app's automatic tests** | Checks the internal workings (about 30 seconds) |

Diagnostic tools:

- **Settings → Nodes → Configuration → Status & diagnosis** for each node, with **Copy for support**.
- **Help → Open logs folder** for the detailed log.
- **Help → About Mirto** for the version and build in use.
- From the command line, `--diagnostics` prints a report to attach to an issue, without camera names,
  addresses, recipients or tokens (see [Appendix B](#appendix-b-installing-from-source-and-command-line)).

---
## 16. Troubleshooting

### Cameras and recognition

| Problem | Solution |
|---|---|
| Black picture or camera that does not switch on | Quit Mirto completely (⌘Q / Ctrl+Q) and open it again. Check the permission in *System Settings → Privacy & Security → Camera* (macOS). From source: `--diagnose-cameras` saves a photo for each camera |
| The camera name does not match the picture | In **All cameras** press **Not this one?** and choose the right camera |
| No alerts arrive | The state must be **Protected** and not *Paused*; wait 2 minutes between two tests; check the categories in **AI → Cameras** |
| Too many false alerts | Raise the threshold in **AI → Cameras** by 0.05 at a time; for animals and objects use their own threshold |
| Birds stay "Bird" without a group | The species model is downloaded with the first bird (internet needed); below 35% confidence the group is not shown |
| Sounds are not recognised | **AI → Microphones**: is the microphone ticked? On macOS grant the permission in *Privacy & Security → Microphone*. The **Sounds** row in *System* shows the microphones being listened to |
| The computer goes to sleep | Lid open, power supply connected, **Prevent the computer from sleeping** on |

### Notifications

| Problem | Solution |
|---|---|
| The bot does not answer `/id` | Mirto must be open, with Telegram ticked and the token saved |
| Telegram in red in the *System* panel | Hover over the row to read the error; check the internet connection |
| "Another copy of Mirto is using the same bot" | Only one Mirto per bot: close the other copies or create a second bot |
| "Unauthorized user" on the buttons | Add your user ID to **Authorized users** |
| Email error | Check the app password (without spaces) and Google's 2-Step Verification |

### Presence

| Problem | Solution |
|---|---|
| I show as "away" but I am home | The phone must use a **Fixed** private Wi-Fi address and the same MAC registered in **Presence**; increase the "away" minutes |
| *Find devices* does not find the phone | Unlock the phone and search again |

### Nodes

| Problem | Solution |
|---|---|
| A node behaves strangely | **Configuration → Status & diagnosis**: it says what is wrong and how to fix it. **Copy for support** prepares a report without passwords or PIN |
| "New node found" does not appear | The node is already paired or the 5 minutes of a reopening have passed: reopen pairing (**Other computers** from a computer that is already paired, or on the node) and press **Find nodes on the network** |
| "Wrong PIN" | Every opening generates a new PIN; after 5 wrong attempts the window closes and has to be reopened |
| Alert "Node … paired by …" on Telegram | Another computer has paired. If you do not recognise it, revoke it in **Other computers → Detach…** or on the node with `mirto-nodo abbina --revoca-tutti` |
| Node camera black or unreachable | **Configuration → Camera**: if the node sees no camera, switch it off and reseat the ribbon cable (contacts towards the HDMI connector) |
| The node does not connect to Wi-Fi | Leave it on the cable and use **Configuration → Wi-Fi**: if the network is not listed or the signal is below 40%, move the node closer to the router. Unplug the cable only when the new address appears |
| The siren does not sound | **Configuration → Audio**: check the output and press **Test sound**. A switched-off USB speaker cannot be told from one that is on: check its switch |
| The Bluetooth speaker shows "pairing lost" or does not respond | It is often connected to a phone or has replaced the node's key with another device's. Put the speaker in pairing mode (Bluetooth button flashing fast) and press **Pair** in **Configuration → Audio**; turn off Bluetooth on the phone if needed. For the siren prefer jack or USB |
| The deterrent does not fire | The node must be online; check targets, pause between triggers, daily limit, time window and hourly budget (the alert says when it resumes) |
| `/listen` replies "Audio not available" | Connect a USB microphone to the node and turn it on in **Configuration → Audio → Microphone** |
| Node update failed | **Status & diagnosis** gives the reason; the node stays on the previous version and the update can be repeated |

The detailed log opens from **Help → Open logs folder**.

---

## 17. Security, privacy and terms of use

### Security

- Tokens, email passwords, node keys and camera passwords are stored in the **Keychain** (macOS) or in **Windows
  Credentials**, never in text files. In the logs camera addresses appear with the password hidden.
- No port is opened on the router: Mirto contacts Telegram, not the other way round.
- The bot obeys only **authorised users** and ignores commands sent while Mirto was off.
- Nodes communicate over HTTPS with a verified certificate and signed requests; each paired computer has its own
  key and can be revoked. The service on the node does not run as administrator.

### Privacy

- Image and sound recognition runs on the computer. Images leave it only as alert photos and clips; the audio
  being listened to is neither saved nor sent.
- Presence reads only the addresses of the devices on the local network; no data leaves the computer.
- Usage statistics are optional and anonymous (see [14](#14-general-settings-and-continuous-operation) and
  `PRIVACY.md`).
- Tell family, guests and domestic staff about the cameras, microphones and phone-based presence.

### Terms of use

They are the same accepted at the first launch and available in **Help → Terms of use**:

- **Software only**: Mirto analyses images and sends commands to the nodes and their outputs. Choosing,
  installing and lawfully using the connected devices is the responsibility of whoever installs them.
- **Frame only your own property**: for streets, stairs and shared courtyards data-protection rules apply (in
  Italy those of the Garante Privacy): limited view and a "video surveillance area" sign.
- **Tell whoever lives or works in the house**: for staff, the rules on remote monitoring of workers also apply.
- **No device must be able to cause harm**, injure, endanger or prevent anyone from leaving; avoid repeated
  acoustic alarms without reason (they stop after 3 minutes anyway).
- **No warranty**: Mirto is not a certified burglar alarm and does not replace a security service; it may fail to
  detect an event or to send an alert. It is provided "as is" under the Apache 2.0 licence.
- **Use at your own risk**, to the extent permitted by law.

---

## Appendix A. Telegram commands

| Command | Italian | Function |
|---|---|---|
| `/status` | `/stato` | Status of cameras, nodes, presence, microphones and next scheduled time |
| `/photo [camera]` | `/foto` | Instant photo (e.g. `/photo entrance`; also `/snapshot`) |
| `/arm` | `/attiva`, `/inserisci` | Turns protection on |
| `/disarm` | `/disattiva`, `/disinserisci` | Turns protection off |
| `/pause [minutes]` | `/pausa` | Pauses alerts (60 minutes by default) |
| `/resume` | `/riprendi` | Resumes alerts |
| `/stop` | `/ferma` | Stops all actions and turns protection off |
| `/schedule [pause\|resume]` | `/programma` | Shows, suspends or resumes the automatic schedule |
| `/listen [seconds]` | `/ascolta` | Records from the node's microphone and sends the audio to the group |
| `/video [camera] [seconds]` | `/clip` | Video of the last seconds (30 by default, at most 120) from a camera that records |
| `/id` | — | Shows the chat and user IDs |
| `/help` | `/aiuto` | List of commands |

A **voice message** sent to the bot is played by the node's speaker. The list is also available in **Help →
Telegram commands**.

---

## Appendix B. Installing from source and command line

### macOS

Python 3.11 or newer is required ([python.org](https://www.python.org/downloads/) or `brew install python`):

```bash
cd ~/Documents/Mirto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m mirto
```

On macOS camera and microphone permission is requested for the app you start Mirto from (for example
Terminal).

### Windows

In PowerShell. Install Python from python.org or with winget, **not** from the Microsoft Store: that version runs in
a sandbox and may not see USB cameras.

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
# close and reopen PowerShell
git clone https://github.com/marcopogo81/mirto.git
cd mirto
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m mirto
```

### Command-line options

Use them with `python -m mirto <option>` (on Windows `.\.venv\Scripts\python.exe -m mirto <option>`):

| Option | Italian | Function |
|---|---|---|
| `--list-cameras` | `--elenca-camere` | Lists the cameras with their stable ID |
| `--test-camera ID` | `--prova-camera` | Saves a test photo from the given camera (`--output` for the file name) |
| `--diagnose-cameras` | `--diagnosi-camere` | Tries every camera index and saves a photo for each |
| `--headless` | `--senza-interfaccia` | Starts Mirto without a window |
| `--check-models` | `--verifica-modelli` | Downloads and tests the bird-species and sound models |
| `--test-recording [S]` | `--prova-registrazione` | Records S seconds (60 by default) from each active camera and estimates space and load (with Mirto closed) |
| `--diagnostics` | `--diagnostica` | Prints a report to attach to an issue, without personal data |
| `--node-simulator` | `--simulatore-nodo` | Starts a simulated node for testing (`--port`, `--node-name`) |
| `--version` | — | Shows the version |
| `-v` | `--verbose` | Detailed log |

---

## Appendix C. Node commands

### Installing the node software on Raspberry Pi OS

From the computer, in the Mirto source folder (`<user>` and `<node>` are the ones chosen in Raspberry Pi
Imager):

```bash
scp -r node <user>@<node>.local:
ssh -t <user>@<node>.local "sudo ./node/install.sh"
```

The installation sets up the node service, the camera (MediaMTX with a password on the stream), audio, network
announcement and the `mirto-nodo` command; at the end it shows the address, fingerprint and PIN. It is needed
only once: later updates are sent from Mirto (see [12.5](#125-updating-a-node)).

### The `mirto-nodo` command

On the Raspberry Pi, over SSH. Without arguments it opens a menu. Every function is also available from Mirto.

| Command | Function |
|---|---|
| `mirto-nodo stato` | Summary: services, camera, audio, network, temperature, fingerprint, pairing, recording |
| `mirto-nodo wifi` | Connects the node to a Wi-Fi network |
| `mirto-nodo audio` | Siren output: Bluetooth, 3.5 mm jack, USB or HDMI, with a test sound |
| `mirto-nodo camera` | Standard or NoIR camera, resolution, camera off |
| `mirto-nodo nome Garage` | Changes the node's label (the network name does not change) |
| `mirto-nodo abbina` | Reopens pairing for 5 minutes and shows the PIN |
| `mirto-nodo abbina --elenco` | Lists the paired Mirto installations |
| `mirto-nodo abbina --revoca NAME` | Revokes one Mirto (`--revoca installazione` withdraws the shared key of installations older than 0.6) |
| `mirto-nodo abbina --revoca-tutti` | Revokes every Mirto and reopens pairing |
| `mirto-nodo disco` | Prepares and uses an SSD or USB stick for recording |
| `mirto-nodo prepara-immagine` | Cleans the node to make an image to copy onto other cards, then shuts it down |

**Reopening pairing without a terminal**: create an empty file called `mirto-abbina` in the boot partition of
the microSD; at the next power-on the node reopens pairing and writes the PIN to `mirto-pin.txt`.

**Several identical nodes.** On a working node, `mirto-nodo prepara-immagine` (confirm by typing `PREPARA`)
removes Wi-Fi, keys, certificate, passwords, paired speakers, logs and videos, then shuts the Raspberry Pi down.
Back up the card first. On first boot each copy becomes unique (`mirto-node-XXXX.local`, new keys, pairing open
with the PIN in `mirto-pin.txt`). Details in `node/README.md`.

API contract, security and technical details of the node: `node/README.md`.

---

## Appendix D. Where the data is

| Content | macOS | Windows |
|---|---|---|
| Configuration | `~/Library/Application Support/Mirto/config.yaml` | `%LOCALAPPDATA%\Mirto\config.yaml` |
| Event photos and clips | `…/Mirto/events/` | `%LOCALAPPDATA%\Mirto\events\` |
| Recordings | `…/Mirto/recordings/` (or the chosen folder) | `%LOCALAPPDATA%\Mirto\recordings\` (or the chosen folder) |
| Logs | `…/Mirto/logs/` | `%LOCALAPPDATA%\Mirto\logs\` |
| Recognition models | `…/Mirto/models/` | `%LOCALAPPDATA%\Mirto\models\` |
| Credentials | Keychain ("Mirto" entry) | Windows Credentials (*Control Panel → User Accounts → Credential Manager*) |

The configuration file can be read only by your account (and by the computer's administrators).

---

## Appendix E. Defaults and limits

| Item | Value |
|---|---|
| Person threshold for alerts | 40% |
| Person threshold for burglar-alarm actions | 60% |
| Animals, vehicles and objects threshold | 50% |
| Bird group threshold | 35% |
| Pause between two alerts from the same camera or sound | 120 seconds |
| Retention of photos and events | 30 days |
| Default recording | Events only, 10 s before and 20 s after |
| Retention of continuous recording | 7 days |
| Maximum space / space always free | 50% of the available space / 2 GB |
| Presence: "away" after | 15 minutes |
| SMS per day | 20 |
| `/pause` and Mute | 60 minutes |
| `/video` | 30 seconds (at most 120) |
| Siren (node limit) | 3 minutes at a time, 3 minutes every 30 minutes |
| Deterrent (node limit) | 30 seconds per trigger, 2 minutes per hour |
| Deterrent from the Library | 5-minute pause, 40 triggers per day |
| Prank mode | 10, 30, 60 or 120 minutes |
| Stop all | Pauses the deterrents for 1 hour |
| Pairing | No expiry on a never-paired node, then 5 minutes; 5 wrong PINs close the window |
| Node recording without a disk | About 2 GB on the microSD |
| Node recording with a disk | Ring buffer, 10% of the disk always free |
