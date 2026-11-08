# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/); versions follow [Semantic Versioning](https://semver.org/).

> **The program used to be called MyHouse** (until 7 November 2026). Entries below that say *Mirto* for
> dates before then are not a mistake: the name was changed throughout rather than left half-and-half.

## [Unreleased]

### Changed
- **The program is called Mirto.** `MyHouse` was already taken — by applications, by packages, and on
  GitHub — and a public project needs a name of its own. The whole name changed at once: what you see
  (windows, menus, messages, guides), the Python package (`myhouse` → `mirto`), the data folder, the
  environment variables (`MYHOUSE_*` → `MIRTO_*`), the names the nodes announce on the network
  (`_myhouse-node._tcp` → `_mirto-node._tcp`, `myhouse-garage.local` → `mirto-garage.local`), the macOS
  bundle identifier and the installers. **The nodes you already have stop being found until their microSD
  is rewritten**, and macOS asks again for camera and microphone permission, because to the system it is a
  different application. The configuration is not lost: at the first start the old folder is moved to the
  new one, once, and `MYHOUSE_*` variables still work with a note in the log. The statistics server keeps
  its old address: it is already published under that name, and renaming it here would only create an
  empty one. New icon, a myrtle berry that is also a camera lens.
- **User guide rewritten from the code** (`GUIDA_UTENTE.md`, `USER_GUIDE.md`). The guide had grown by
  addition — chapters numbered 7bis to 10quinquies, notes dated by the day they were written, terminal steps
  for things the app now does itself. It is now one chapter per area, in the order of the Settings tabs
  (17 chapters and 5 appendices: Telegram commands, installing from source and command line, node commands,
  where the data is, defaults and limits), the same numbering in both languages, and every **📖 Full guide**
  link in Settings points to its own chapter. The Actions tab used to open chapter 8 of the old guide, which
  was about keeping the computer awake.

### Fixed
- **References to Settings tabs that no longer exist.** Several messages still pointed to *Sounds*,
  *General* (for the microphone) or *Nodes* (for actions), and the node windows were named by their old
  button labels. They now give the real path, e.g. *Settings → Nodes → Configuration → Audio*. After pairing
  a node, the offer to create its actions opens the **Actions** tab instead of *Nodes*, and the pairing
  window no longer says the node is renamed on the network: the name is a label, the network name stays
  `mirto-node-XXXX.local`.

## [0.2.0] – da rilasciare

### Security
- **A node that does not say which certificate it has is not proposed for pairing** (S-06). The
  announcement on the local network carries the fingerprint of the node's certificate in a `fp` field, and
  that fingerprint is what Mirto pins at pairing: it is the whole reason a stolen name or a borrowed
  address does not get a key. An announcement **without** that field was still shown as "New node found",
  and pairing then went ahead against whatever certificate answered — which is exactly the moment someone
  on the same network could answer in a node's place. Announcements with no fingerprint are now ignored,
  with the reason written to the log; a node of ours has always sent one, so nothing real stops working.
- **The public node image no longer carries a password anyone can guess** (S-05). It shipped the user
  `mirto` with the password `mirto` and `ssh_pwauth: true`, relying on `passwd --expire` to force a
  change at first login — but on a network the first to log in is not necessarily the owner. The user now
  starts locked, and first boot gives it a **random password, different on every card**, written to
  `/boot/firmware/mirto-accesso.txt`, which is read by putting the card back in a computer. A password
  chosen in Raspberry Pi Imager is left alone. The image build fails if a password ever reappears in it,
  and a test runs the generator line taken from the script itself to check that three cards get three
  different passwords. This matters now because the first batch of nodes is about to be written: five in
  one house and then a card for each tester.
- **A new node's pairing window no longer expires before the printed PIN can be used** (node 0.7.1, API 1.5).
  The window lasted 5 minutes from first boot; after that the PIN was dead and only a terminal could open a
  new one. The plug & play plan decided on 20 October ships the PIN **printed on the sheet that travels with
  the device**: a card written today and powered up a week later has to be pairable by someone who has never
  opened a shell. So while a node has **never** been paired the window stays open and the PIN stays the one
  on the sheet. From the first successful pairing on nothing changes: each reopening lasts 5 minutes, and a
  paired node never reopens by itself. What actually protects the key is untouched — it is handed over only
  inside the home network, only to a client that knows the 6-digit PIN, only after the pinned certificate
  matches, and **5 wrong PINs close the window for good**, taking the PIN off the microSD as well. `GET
  /abbina` now carries `senza_scadenza`, so Mirto no longer shows a countdown that isn't running and no
  longer drops a brand-new node from "New node found" after five minutes — the suggestion used to come and
  go between one scan and the next. Nodes on an older version don't send the field and keep the old
  behaviour.

### Changed
- **The *System* column folds: closed rows say the state, open rows say everything** (asked for on 22
  October, looking at the real window). *System* had grown to the point where a single node filled a
  screenful — actions, network, camera, speaker, siren, recording, temperature, version — and a house
  with five nodes would have been unreadable. Every long row now sits behind a **plus** on its left:
  closed it shows the title and a summary, open it shows exactly what it showed before. What is open is
  remembered between runs, so whoever watches their nodes does not reopen them at every start.
  - a **node is one line**: name, whether anything is on, and five signs for camera, speaker, siren,
    recording and network, each green, amber, red or grey. They are fixed-width symbols rather than
    emoji on purpose — with five nodes stacked the signs line up in a column and are read as a column,
    and emoji have different widths and a different drawing on Windows. Each node opens on its own;
    a node that is **not answering** is never folded away, because that is the one you must not have to
    click on;
  - **Notifications** keeps the channel lines (*Telegram connected*, *Email off*) and puts the group,
    the bot and the authorized users behind the plus: six lines read once, when setting it up;
  - **Cameras** no longer lists the switched-off ones among the working ones. The count stays
    ("Off: 2"), the names appear if you open it — in a house with six cameras, two of them old, the
    thing that matters (how many are watching now) was drowning;
  - **AI Recognition** keeps the model name and folds what it watches, where it runs and the threshold;
    **Recording** and **Sounds** fold the same way. A microphone fault is still said with the row closed,
    because it is a fault, not a detail. A plus never appears where there is nothing to open.
- **The *System* lines moved out of the window code and into `gui/sistema`** (same day). They are text
  with colours in it, and they were sitting inside the module that imports Qt: they could be tested only
  where PySide6 is installed, which is CI and not the machine they are written on, so they were written
  blind. They are now a Qt-free module (with the palette split into `gui/colori`), and the first run of
  the new tests found a function that had been calling `re.sub` without importing `re` — it would have
  thrown the moment a node with actions was drawn. Two existing tests that used to be skipped locally
  now run everywhere, and a test refuses the day Qt comes back into that file.
- **A node's name is a label, and the network name never moves** (node 0.7.2, decided on 21 October).
  Renaming a node used to rename the device as well: `mirto-studio.local` became
  `mirto-ingresso.local`, Mirto rewrote the address it had saved, and the root helper restarted the
  service that announces the name. It worked, but
  it let a cosmetic choice reach into the plumbing: every rename answered `ssh` with "REMOTE HOST
  IDENTIFICATION HAS CHANGED", a second computer paired with that node went on looking for the old name until
  someone fixed it by hand, and in between the two names the node was reachable under neither. The name a
  person types is now only what they read — on the node and in Mirto — while on the network the node keeps
  the generic name it was born with (`mirto-node-XXXX.local`), random suffix and all, which is also the name
  the saved address, the camera stream and `ssh` use. `POST /configura/nome` answers with that unchanged name,
  nothing is written for the root helper any more, and the only things that follow a rename are the ones that
  carried the old name: the node's camera and the proposed actions.

### Fixed
- **A "no" from the keychain is no longer asked again every two seconds, forever** (found on
  22 October, in the first `.dmg` that started). The consent window came up about ten times and did
  not stop. A secret that has been read is remembered, but a **refusal** was not: the comment said "an
  error is not an answer, we try again next time", and next time was two seconds later, because the
  recorder re-read the camera credentials inside its own watch loop. Being told no and seeing the same
  window return two seconds later, endlessly, is the fastest way to make someone quit a program and
  not open it again. The principle stands — five minutes later it tries again, and writing that secret
  clears the refusal at once, because whoever just wrote it plainly has permission. And the recorder's
  loop now compares the **address**, which costs nothing, instead of the full stream URL that reads the
  keychain; if the credentials do change, ffmpeg fails, that part restarts and reads them once.
  (Some prompts are still expected on every launch until the app is signed: that is what signing is
  for, and it is the next step of W-05.)
- **The packaged app starts** (found on 22 October, the first time the `.dmg` was ever built and
  opened). It closed before showing anything, with *"attempted relative import with no known parent
  package"*. The PyInstaller spec handed it `mirto/__main__.py` as the starting script, and
  PyInstaller runs a script **as a program of its own**, calling it `__main__`: that file is a
  package's entry point and uses relative imports, which outside the package have nothing left to
  refer to. Nothing could have shown this before opening the package — `python -m mirto` works
  perfectly, because there the package is there. The app now starts from `packaging/avvia.py`, which
  comes in by the front door with an absolute import, and a test **runs** that file rather than
  reading it, with a companion test that the old entry point still cannot be run as a script, so the
  reason for the launcher stays written down.
- **A disk that is pulled out no longer leaves the node showing green** (node 0.7.7, found a few
  minutes after the fix below, by pulling the stick again). This is the worse half of the same story.
  Linux does not remove a mount point when a stick is yanked: it stays, pointing at a `/dev/sda` that
  no longer exists. `lsblk` showed nothing, `findmnt` still said "mounted", and the node — which only
  asked "is something mounted there?" — said nothing at all: the row in Mirto stayed **green** while
  not a byte was being recorded. That is worse than stopping, because an error gets acted on and green
  does not. The node now also checks that the device behind the mount still exists (it disappears from
  `/dev` the instant the stick leaves), with a `statvfs` as a second net for a filesystem that stops
  answering while the device is still there; and the part that remounts clears a dead mount first,
  because nothing can be mounted on top of one.
- **A recording disk that is unplugged and plugged back in starts recording again by itself** (node
  0.7.6, found by testing it on 22 October). It never did. The `/etc/fstab` line mounts the disk **at
  boot** and nothing else: pull the stick out of a running node, push it back in, and nothing remounts
  it — the node just kept saying "disk not connected" until someone rebooted it. Meanwhile the panel
  and the diagnosis both promised the recording would resume on its own, which is the kind of promise
  that gets believed and then quietly broken. The node now leaves a note for the part of it that can
  mount, once a minute while the disk is missing, and that note is honoured only for a mount point
  that is **already in `/etc/fstab`** — a note must never be able to mount something nobody chose.
- **The installation key can now actually be removed where the program says to remove it** (node 0.7.5,
  found while using it on 22 October). *System* and the diagnosis both said "still accepts the
  installation key: remove it in **Other computers…**" — and once there, there was nothing to click.
  The list only held paired computers; the one useful button, **Detach…**, acted on the selected row,
  and the only row was *this computer*. Whoever tried stopped a moment before detaching their own Mac,
  and was right to. The note at the bottom, for good measure, suggested `--revoca-tutti`, which
  detaches **everything** including that Mac: the exact opposite of what was wanted. The installation
  key is now a row of its own, last in the list, which says what it is and how long is left before it
  retires by itself; it can be selected and detached like the rest, with its own question — this one is
  final, a reinstall will not bring it back, and the pairings above are untouched. The node accepts
  that name (it refused it before, because the key is not a pairing), the guard that matters is
  unchanged — the last paired computer still cannot be detached from here — and a special name can no
  longer shadow a real key. The note now points at the button, and names `--revoca installazione` as
  the fallback instead of the command that would have cut the user off.
- **An update that fails no longer leaves the house without a camera** (node 0.7.4, found on hardware
  on 22 October). `install.sh` stops MediaMTX on purpose, to free the camera long enough to recognise
  the sensor, and starts it again fifty lines further down. In between, `set -e` means any failing
  command ends the script — with the camera off and nothing said about it. That is exactly what
  happened: the installation died at step 4/8 and the node sat there with no stream until someone
  noticed, eleven hours of recording later. An update is allowed to fail; it is not allowed to leave a
  house blind. The moment MediaMTX is stopped, the script now takes on the obligation to start it again
  even if it dies on the way, and drops that obligation only once the real restart has happened.
- **A recording disk without Unix permissions no longer kills the installation** (node 0.7.4, the cause
  of the above). The USB stick in use was **vfat**, as sticks come from a shop: there, ownership and
  setgid do not exist — the mount decides them, the same for every file — so `chown` and `chmod` fail,
  and rightly so. What was wrong is that they took the whole installation down with them. Those two
  commands are now allowed to fail, the installation says in one line that this disk cannot hold Unix
  permissions and that this is fine, and `tmpfiles.d` is not written for a disk that would reject it at
  every boot. Recording on such a disk already worked and still does.
- **The ring no longer drowns the node's log** (node 0.7.4, seen while hunting the bug above). Trimming
  one segment a minute is what the ring does forever once it reaches its cap, and it was written at
  INFO: thirty identical lines filled the "last errors" view of the status script, and there was no way
  to tell whether any real error had happened — while looking for one. The ordinary trim is now DEBUG;
  removing more than one segment at once stays INFO, because that means something piled up (another
  program filling the disk, a node that sat still, a cap just lowered).
- **The node wakes its own Bluetooth controller up before giving up** (found on hardware, late the
  same evening — and this was the real one). After an afternoon of failed attempts the Raspberry's
  controller stays in a state where it refuses everything with generic errors
  (`br-connection-unknown`, `page-timeout`), with the speaker factory-reset, in pairing mode and half
  a metre away; `l2ping` says the host is down on a device advertising at −42 dBm. Restarting
  `bluetooth` and cycling `hci0` (`down` / `reset` / `up`) made the **same** sequence answer "Pairing
  successful" at the first attempt. So a failed try is now followed by a wake-up and one more go, five
  seconds inside a minute-long operation — and, since a freshly paired speaker closes the link itself
  (`disconnected with reason 2`), the connection is retried up to three times once the bond exists,
  instead of stopping one step from the end. Temperature and `get_throttled` were checked and ruled
  out, as was the shared Wi-Fi antenna.
- **When a speaker refuses, the node says which of the three situations it is** (found on hardware,
  the same evening). "The speaker will not connect" covered three different problems with one useless
  sentence. The node now reads its own session log and says: the speaker **gave its place away** —
  `br-connection-unknown`, which is what happens when a phone pairs with it, because many speakers
  keep a single key, so the remedy is to hold the Bluetooth button until it blinks fast and press
  Pair *while* it blinks; or the node **saw it and it refused**, so it only needs to be put into
  pairing mode; or the node **never saw it at all**, so it is off, far away, or still held by a phone.
  The panel says the same while it works ("keep it close and in pairing mode"), and both guides carry
  the one-key explanation, with the jack or a USB speaker as the way out for a permanent siren.
- **A Bluetooth speaker now actually connects, and what it takes was measured, not guessed** (node
  0.7.2, found on hardware). After four defects fixed from the logs the speaker still refused, with
  `Device … not available` on a JBL Flip 6 switched on and sitting next to the node. A script that
  tried the known recipes in turn, on the node, settled it in one run. Two things were wrong, and both
  were assumptions. **One command at a time cannot work**: each `bluetoothctl` invocation is a separate
  process, and between them BlueZ discards a speaker that is not bonded — the next command then talks
  about a device that, for it, no longer exists. Everything now happens inside **one session**, with
  discovery running and the waits in between, because it takes about twenty seconds for the speaker to
  appear at all. And **pairing is not what matters**: the speaker connected and played with `Paired:
  no`, `Bonded: no`, `Trusted: yes` — A2DP speakers often leave no stored bond. So `pair` is attempted
  and allowed to fail, `trust` and `connect` are what count, and success is measured on `Connected:
  yes` rather than on a field that means nothing here.
- **A speaker that works is no longer declared lost** (same evening). The rule written on 18 October —
  "without `Paired: yes` do not even try" — came from one broken bond and was wrong: it would have
  refused to reconnect the very speaker that works, leaving the siren silent after any disconnection.
  What decides now is the error the connection returns: `br-connection-unknown` means the bond is
  broken (and Mirto says so, with the panel as the remedy, and stops retrying every minute),
  `not available` means BlueZ has forgotten the device (a short search, then one more attempt), and
  anything else is a speaker to try again within the minute.
- **Pairing a Bluetooth speaker from the panel: four defects found in one evening on hardware**
  (node 0.7.2). The speaker never paired, and each attempt was harder to understand than the last.
  (1) **The list offered everything that advertises nearby.** In one flat, twelve BLE devices —
  phones, watches, a "BLE MF8470" — and one real speaker. Audio cannot travel over a random BLE
  address at all, so those rows could only waste attempts; now a device is offered when it is already
  paired, or when it has a real name and a **public** address. (2) **The list forgot the speaker.**
  BlueZ keeps an unpaired device only while it keeps advertising, and a speaker in pairing mode stops
  as soon as something touches it: the search found "JBL Flip 6" and the listing half a minute later
  did not have it any more. What passes by **during** the search is taken as it goes. (3) **Pairing
  was attempted on a device BlueZ had already dropped**, which is the whole of "Device … not
  available" on a speaker that is switched on and sitting there: the node now takes a short look
  around first, and declares itself pairable. (4) **The reason was thrown away.** The root helper ran
  the pairing with `>/dev/null 2>&1`, so the log said "speaker … NOT paired" and nothing else, and the
  error of `pair` — the real one — was swallowed by the error of `connect`, which is only its
  consequence. Both are kept now, the reason comes first in the message (it is cut at 300 characters,
  and the part that explains must not be the part that falls off), and it names what is nearly always
  true: the speaker is still connected to a phone.
- **Installing the node can no longer leave the program unreadable to the service that runs it**
  (found on hardware). The node vanished from Mirto with "connection refused" while its camera went
  on working — two different services, and only one of them was dead: `PermissionError: [Errno 13] …
  /opt/mirto-node/mirto_node/server.py`. The files had arrived from a computer where they were
  `rw-------`; `rsync -a` carries permissions as they are and `cp -r` copies them as they are, so the
  program landed in `/opt` readable by root alone — and the service runs **without privileges**
  (S-03), on purpose. `umask 022` in the installer does not help, because it applies to files being
  created, not to copies. The installer and the image build now make what they install readable
  (`chmod -R u=rwX,go=rX`), whatever the source looked like: a tarball unpacked with a strict umask, a
  USB stick, a copy from someone's laptop. A test holds both in place.
- **The audio panel now says what it is doing, and stops when the node goes quiet** (found while
  testing it). Three defects in the same waiting loop. The node leaves its answer in a file, and that
  file still holds the answer to the **previous** attempt: the panel only checked which action it
  belonged to, so pressing "Find speakers…" could show "search finished" instantly, with the old list
  — which is why it was impossible to tell whether anything was happening. The wait had **no end**: if
  the node never wrote an answer (root helper stopped, ticket lost, node rebooted) the bar span for
  ever and the buttons stayed switched off, with nothing said; now the panel gives up thirty seconds
  after the time the node itself declared, and says the node did not answer. And **"Test sound" was
  always offered**, including with Bluetooth chosen and no speaker paired, where it could only do
  nothing and look broken; it is now enabled when the node is already set up the way the panel shows,
  with a tooltip saying to apply the choice first. The three rules live in
  `mirto/actions/pannello_audio.py`, outside the window, because windows cannot be run where this
  code is written — so they are covered by tests everywhere, not only on the runners.
- **The speaker you chose stays chosen** (found on hardware). The list is rebuilt after every search
  and every attempt, and the selection was only restored to the speaker configured on the node: with
  none configured it fell back to the first row. After a failed attempt the panel showed a BLE device
  nobody had picked, and the next attempt would have gone to it. The choice survives now — the
  attempt in progress first, then what the person picked, then the node's — and the outcome names the
  speaker it was talking about.
- **Renaming a node now renames what only carried its name** (found on hardware). The node was renamed
  from "Studio" to "Ingresso": the device and the node row followed, and the camera
  in *All cameras* was still called "Studio" — on a node named Ingresso — along with two actions ending
  in "– Studio". A node's camera is created with the node's name, and the proposed actions are named
  "Siren … – Studio", so after a rename those names belong to nobody. They follow the node now, and
  **only** they: a name a person chose stays theirs, and so do the cameras and actions of other nodes.
  The camera's id never changes, because the keychain entry and the recorded events hang from it.
- **While the node works, the window says so** (found on hardware). Connecting to Wi-Fi takes up to half
  a minute, and the window said it in a small grey line at the bottom; half a minute with no signal
  looks like a frozen program, and the person presses again or closes. There is now a bar that moves, a
  countdown using the time the node itself declares, and a coloured outcome at the end — green when it
  worked, amber when it didn't. It lives in one line that behaves like the label it replaces, so the
  three slow panels (Wi-Fi, disk, audio) use it without changing anything else; it matters most for
  formatting a disk, which takes minutes.
- **A Bluetooth speaker that no longer recognises the node is re-paired without a terminal** (found on
  hardware). Pairing from the audio panel failed with `br-connection-unknown`: the same defect as
  18 October — many speakers, put into pairing mode, replace the previous device's key, so the bond dies
  on one side only. The remedy was written in the guide (`bluetoothctl remove`, then `pair` again) and
  had to be typed into a terminal, which is exactly what this panel exists to avoid. The node now does
  it by itself, but **only** for that error: if the speaker were merely switched off, throwing the bond
  away would make things worse. When it still fails, the message says what to do instead of printing a
  Python list.
- **"No speaker chosen" and "the speaker does not answer" are two different problems** (found on
  hardware). Every node born from the image starts with Bluetooth as its output and no speaker known, and
  the diagnosis called it a fault, with the wrong sentence. Nothing is broken there: a choice is missing,
  and the remedy is to make it — the 3.5 mm jack included, which on a node just put in a house is the
  shortest way to a siren that sounds.

### Added
- **The node says how it is attached: cable or Wi-Fi, and how strong the signal is** (node 0.7.3, asked
  for on 22 October). *System* listed a node's camera, speaker, siren and recording but never said where
  the node was reachable *from* — and that is the first question when one stops answering. The morning
  before, a Raspberry Pi 4 had been switched on with no network cable and had come back on Wi-Fi by
  itself: it worked, and from the window there was no way to tell. The node now reports it, read from
  the system's own files (`/sys/class/net`, `/proc/net/wireless`) rather than by running a program, so
  the status Mirto asks for at every heartbeat can never hang on the network; the only command
  involved is `iwgetid`, and only when a wireless card is actually up. With both up the cable wins,
  because that is what carries the traffic, and the Wi-Fi is named as the reserve — the situation of
  anyone setting Wi-Fi up from the panel with the cable still plugged in. A signal below 40 % is a
  warning in *System* and in *Status and diagnosis*, with the only remedy that works: move the node, or
  put it on the cable. There is no setting that fixes a bad spot, which is also why the network scan is
  done by the node and not by the computer. Loopback, Docker and VPN interfaces are ignored. Nodes on
  an older version don't send the field, and then nothing is shown rather than an "unknown" that reads
  like a fault.
- **The Wi-Fi password can come from the computer's own keychain** (N-08). Whoever is putting a node in
  their house already has that password: it is in the Mac's keychain or in Windows' saved profiles,
  because the computer is on the same network. Retyping thirty characters by hand is where one gets
  mistyped and the node gets the blame. **Node Wi-Fi…** now offers **Take it from this computer** for a
  network this machine already knows, and offers nothing for one it doesn't. Nothing is stored on our
  side: Mirto asks the system, sends it to the node over the pinned, signed connection, and forgets
  it — it is never shown (the field stays masked), never written to a log, and never saved to a file.
  The consent is the operating system's, not ours: on macOS the keychain opens its own window (Touch ID
  or the user's password) for every request, and on Windows `netsh` only reveals a key to an
  administrator. Mirto says what is about to happen before the system prompt appears, and when the
  answer is no it says what to do instead rather than failing silently. The text parsers are separate
  from the commands, so a Windows in Italian, English, Spanish, French or German is read correctly and
  all of it is tested everywhere.
- **A node now says what is wrong with it, and how to fix it.** Settings → Nodes → **Status and
  diagnosis…**: the problems first, each with the remedy next to it, then the numbers. The node answers
  with some twenty fields across `/stato`, `/info` and `/configura/camera` — facts, not a diagnosis — and
  until now reading them was our job, over a chat, one question at a time. A camera switched on in the
  settings that the system cannot see now says so, with the fix that actually worked on 20 October
  (refit the ribbon cable at both ends); an unplugged recording disk, a speaker whose Bluetooth bond is
  gone, a node at 84 °C that is throttling itself, the installation key still lying around, a pairing
  window left open, an old node that needs `install.sh` — each one is a line with a remedy a person can
  carry out. A node in order says exactly that, instead of a wall of green ticks.
  **Copy for support** puts a plain-text report on the clipboard: identity, versions, addresses, uptime,
  camera, recording, siren, who is paired, and the problem list. A tester who writes "it doesn't work"
  costs three messages to find out what; this answers all three at once, and a test checks that neither
  the pairing PIN nor the camera password can end up in it. The part that decides what counts as a
  problem lives outside the window on purpose (`mirto/actions/diagnosi.py`), because windows cannot be
  run where this code is written — twelve tests cover it, and they run everywhere.
- **A paired node can be renamed, and a second computer added, from Mirto** (`GET`/`POST
  /configura/abbinamenti`, node 0.7.1, API 1.5). Two holes left by the wizard, both found by using it.
  The name is asked once, at the first pairing: whoever typed "Balocne" instead of "Balcone" — which
  happened — had the terminal as the only cure. **Rename the node…** now does it in the right order: the
  device first (if it refuses, nothing here changed), then the name in Mirto, then the camera and the
  actions that still carried the old name.
  And **Other computers…** lists every Mirto holding a key, with name, address and date, marking which
  one is this computer; **Open pairing** shows the PIN to type on the other machine and can be closed
  again immediately, so the window is open for a minute instead of until someone remembers it; **Detach…**
  takes one key away after a confirmation that says what it costs. The PIN only ever leaves the node while
  the window is open, and the **last** pairing cannot be detached from here — it would leave the house
  with no Mirto at all and no way back in but a terminal, so that one stays where a person has to be
  standing in front of the node. Adding the household laptop used to mean `mirto-nodo abbina` over ssh;
  the six node-configuration buttons also moved out of **Cameras**, which is not where anyone looks for
  Wi-Fi, into a **Node configuration** branch of their own.
- **The recording disk is chosen, and prepared, from Mirto** (`GET`/`POST /configura/disco`, node 0.7.1,
  API 1.5). Settings → Nodes now has **Node disk…**: the USB disks **the node** can see with size, format
  and free space, where it is recording right now and how much of it is video, the two ring limits (how
  much to use at most, how much to always keep free), and recording on or off. A disk that is already
  formatted properly takes one button; one that is not has to be **prepared**, which erases it.
  That is the only thing in all of Mirto that destroys data, and it is built to be hard to do by
  accident: a separate button, a window that lists the other partitions on the disk, no preselected
  value, Enter on the confirmation cancels instead of formatting, and the OK stays dead until the device
  name — `/dev/sdb` — is typed again by hand. The node then refuses the request unless it repeats that
  name and unless the disk is one from its own last scan, and that scan never offers the disk the system
  boots from; the root helper re-runs the same checks before erasing anything, because it is the one
  holding the knife. Until now this was `sudo mirto-nodo disco` over ssh — which, for a tester who
  received a node and an SSD in the same box, means the box is useless until someone opens a terminal.
- **The node's Wi-Fi is set from Mirto** (`GET`/`POST /configura/wifi`, node 0.7.1, API 1.5).
  Settings → Nodes now has **Node Wi-Fi…**: the networks **the node** can see, with signal strength and
  security, the password field, and the outcome of the attempt. Scanning from the node is the point —
  what matters is whether the signal reaches the shelf the device will sit on, not whether it reaches the
  laptop — and it also works when the computer is on a cable and can see no networks at all. Scanning and
  connecting need privileges (the radio, the regulatory domain), so the node leaves a one-line note for
  the root helper and the panel comes back to read the result instead of freezing. The password is the most
  delicate secret that passes through here: it never reaches the log, it is not in the note for root (which
  says only "connect"), it sits in a `0600` file that whoever uses it deletes immediately, and it is handed
  to `nmcli` as an argument, not as a shell line. The node stays on its cable throughout, so a wrong
  password costs a retry and not a card rewrite — the panel says so, and says to unplug the cable only once
  the new address has appeared. Wi-Fi was the piece the testers need first: a node that has to stay on a
  cable is a node that cannot be put where a camera should go.
- **When the CI is red, the reason arrives in the annotations too.** The full log of a run lives on a
  storage service that is not always reachable from where this work is done, and on 20 October Windows
  stayed red for half a day because the only copy of the reason was in a log that could not be downloaded.
  `tools/tempi-test.py` now repeats every failed test as a GitHub `::error::` annotation — name and last
  lines of the traceback — which can be read from the check page and from the API.
- **Pulling the plug no longer leaves a node that cannot be paired** (found on hardware). A node whose
  power was cut a minute after first boot came back with `pin` and `abbina_fino` **zero bytes long**: the
  file names had been recorded, the contents had not. The window then looked open — the PIN file existed —
  while the PIN itself was gone, so pairing answered "PIN expired" and, without a terminal, that card was
  only good for rewriting. Three things changed. State files are now written whole: into a temporary file,
  flushed to the disk, renamed (a rename is atomic), with the directory flushed too. An unreadable PIN no
  longer counts as an open window. And a node that has **never** been paired makes itself a new PIN when
  it restarts, so a card yanked in the wrong minute heals itself; a node that has been paired does not,
  because a window that reopens by itself is a door. The boot partition is FAT with no journal, so writing
  or deleting the PIN there is followed by a `sync`. The image also keeps a persistent journal now: of the
  first boot that went wrong, nothing at all was left to read.
- **A node built from the public image announces itself again.** The step that writes the access sheet
  ran `umask 077`, and that umask stayed for the rest of the script — including the installation that
  runs right after it. The two Avahi service files were created `0600`, the daemon runs unprivileged
  and could not read them, so the node never appeared on the network: Mirto simply did not see it.
  It only happened on the public image, because on a personal one that branch never runs (the user
  already has a password), which is why a whole afternoon of tests never showed it. The umask is gone —
  the sheet gets its 0600 from the `chmod` that already followed — and the installation now sets `644`
  on both announcements itself, so it no longer depends on the umask of whoever runs it. First boot is
  now exercised end to end in a fake root: a test creates a file from the stubbed installation and fails
  if it is born 0600.
- **The public image carries its user, instead of hoping one appears** (S-05, found on hardware). The
  first node ever booted from this image, on 20 October, had **no user at all**: nobody could log in,
  not the owner and not us. The image removes every user and left cloud-init to create `mirto` at
  first boot from `user-data`; when `firstboot.sh` reached the step that gives that user a random
  password, no account with uid ≥ 1000 existed yet, so it skipped the whole thing **in silence** — no
  password, no `mirto-accesso.txt`, and a sealed box. The user is now created while the image is
  built, locked and without a password, in the groups it needs; first boot still gives it a random
  password, different on every card, so S-05 holds without depending on which service wins the race.
  The build refuses an image whose user is missing, has a password, or cannot use `sudo`. And if a node
  somehow still comes up with no user, first boot now writes a sheet that says so, with what to do,
  rather than leaving the card silent.
- **Building the node image picks a compression level the machine can afford.** `make_public_image.sh`
  always ran `xz -6`, which on a Raspberry Pi 3B with 1 GB of RAM silently drops to a single thread:
  measured on 20 October, 264 MB compressed in 25 minutes on a 4.2 GB image, with over an hour to go.
  Half the file size is not worth an hour, so below 2 GB of memory it now uses `-2` — four times
  faster, about 15 % larger — and says which level it chose and why. `MIRTO_XZ=6` overrides it.
- **Where the siren sounds is chosen from Mirto** (`GET`/`POST /configura/audio`, node 0.7.1, API
  1.5). Settings → Nodes now has **Node audio…**: the outputs the node really has (the jack, a USB card,
  HDMI, a Bluetooth speaker), the speaker list, a **Find speakers…** button and a test chime — none of it
  needs a terminal any more. Looking for speakers and pairing one take seconds and need privileges, so
  the node does not do them itself: it leaves a one-line note for the root helper, writes the result to a
  file, and the panel comes back to read it instead of freezing on an open connection. The node refuses
  an output it does not have, an address that is not an address, and Bluetooth without a speaker. This is
  the first piece of a rule set on 20 October: **if configuring a node needs a terminal, that piece is
  not finished** — Wi-Fi, the recording disk, renaming and adding a second computer are still to come.
- **The node's camera is set from Mirto** (`GET`/`POST /configura/camera`, node 0.7.1, API 1.5).
  Whether a camera is a NoIR model cannot be detected: the sensor is the same in both, so a person has
  to say. Until now that person had to open an ssh session on each node — and the fleet of test nodes
  will have mixed cameras, NoIR where there are infrared lamps and standard everywhere else. Settings →
  Nodes now has **Node camera…**: on or off, NoIR or not, resolution, with the node's own list of
  resolutions and the sensor it actually sees — and, when it sees none, the ribbon-cable advice instead
  of silence. The node validates (a resolution nobody offered is refused) and leaves the real work to
  the root helper, which rebuilds the MediaMTX configuration and restarts the stream, because the node
  service has no privileges (S-03). The NoIR question is now phrased so that it can be answered: it
  explains that the model only helps with infrared lamps, and that when in doubt the answer is no.
- **The node takes its name from Mirto, not from a terminal** (`POST /configura/nome`, node 0.7.1, API
  1.5). Naming a node meant `ssh` and `mirto-nodo nome Garage` on the Raspberry: with five cards to put
  in one house, five ssh sessions — and the plug & play plan has the wizard ask for the name right after
  pairing, so that someone configuring one node after another always knows which box is in their hands.
  The request is signed like every other one; the node validates the name, writes it where it survives a
  restart, and answers with the network name it keeps — the name is a label (decided on 21 October), so the
  address Mirto saved goes on working and no rename can leave a computer looking for a name that no longer
  answers. Writing this turned up a real bug: `registrazioni-da-preparare`
  was a note nobody was waiting for — it was missing from `mirto-boot-segnali.path`, so a recordings
  folder was only prepared by accident, at the next boot or when another note arrived. Both are watched
  now, and a test fails if a note the script reads is not in that list.
- **The pairing window asks for the node's name, and that name reaches the device** (plug & play, step
  one). The box asked for a "Name in Mirto", which was only a label in this computer's configuration: the
  node kept the name it gave itself at first boot, `Node 17a9`. Now the first pairing of a never-paired node
  sends the chosen name to it, and the node writes it where a restart does not lose it; the network name is
  left alone, so there is nothing to fix afterwards. A factory name is no
  longer offered as a suggestion, because in front of five identical boxes `Node 17a9` tells nobody
  anything. A node already in service is left alone when a second computer pairs with it: renaming it would
  leave the first computer looking for a name that no longer answers. A node too old to know the request
  keeps its name and the pairing still succeeds.
- **Windows documentation** (W-09). Until now the README and the 25-chapter guide described a Mac only:
  Terminal, Keychain, `~/Library/Application Support`, `source .venv/bin/activate`. None of that exists on
  Windows, and a reader stopped at the second command. Both READMEs now carry a Windows quick start, and
  both guides a chapter "2b" with the PowerShell commands, plus a table of what is genuinely different:
  Credential Manager instead of the Keychain, `%LOCALAPPDATA%\Mirto\` instead of Application Support,
  DirectML instead of CoreML, a filled tray icon instead of an outlined menu-bar one, Ctrl+Q instead of
  ⌘Q. Three things are said where they bite rather than in a footnote: Python from the Store runs
  sandboxed and may not see USB cameras; autostart on Windows is the `Run` key, so Mirto does **not**
  come back after a crash the way it does on macOS; and Telegram allows one program per bot, so a second
  computer needs a second bot — discovered the hard way with a Mac and a PC polling the same token. Five
  tests keep the two languages aligned and tie the claims to the code: if autostart stops being the `Run`
  key, the test that guards that sentence fails.

### Fixed
- **The timeline bar no longer regroups every event on every repaint.** The dots on the *Review* bar were
  sorted and grouped inside `paintEvent`, which runs ten times a second while the video plays: with a day
  full of events the bar became sluggish (reported on the Windows PC on 19 October). Their positions depend
  only on the visible window, the width and the events themselves, so they are now computed once and kept
  until one of those three changes. The grouping rule moved to `timeline.group_events()`, outside the
  drawing code, where four tests check it: close events merge into one bigger dot, distant ones stay
  separate, a person wins over an animal and a sound in the same dot, and events outside the window are
  not drawn.
- **The player's skip buttons came out in colour on Windows.** "⏮", "⏭" and "⏸" are emoji code points:
  Windows draws them with the colour emoji font, so in the *Review* control bar they stood out against the
  white symbols next to them, while "▶" — a plain text character — looked right. That is also why only two
  of the five buttons were wrong. The four player controls are now drawn icons, recoloured by the theme
  like every other icon in the app: the same on every system, with no font in the way. A test fails if an
  emoji code point ends up on a button again.
- **Detection no longer runs one camera at a time** (Q-03). A `Lock` around `session.run` let a single
  inference through at a time, so with four cameras the fourth waited for the other three and the promised
  cadence ("two frames out of five") became seconds for it. The lock was never about correctness —
  `InferenceSession.run` is safe to call from several threads — it was about not eating the whole CPU, and
  two things already do that better: `intra_op_num_threads = 2` inside each inference, and now a ceiling on
  how many run together (half the cores, never fewer than two, so two cameras never queue behind each other
  even on a small machine). A test with a deliberately slow fake model checks that more than one gets
  through and that the ceiling is respected. The bird-species model keeps its single lock: it runs once in
  a while, on an event, not on every frame.
- **A node that does not show up now says why.** Node discovery needs an IPv4 address — a URL built on an
  IPv6 link-local (`fe80::…`) loses the interface index and cannot be reached — and it used to drop such
  announcements silently, so the node simply did not exist for whoever was looking at the list. It now logs
  which node was skipped and what addresses it announced. This is not hypothetical on Windows: there
  `mirto-ingresso.local` resolves to a `fe80::…` first, and the IPv4 only comes with `ping -4` (W-01).
- **The tray icon was invisible on Windows.** Mirto drew a 2 px grey outline of a house with a coloured
  dot inside — right for the macOS menu bar, where icons are thin monochrome glyphs, and lost at 16 px in
  the Windows notification area, where every neighbouring icon is a solid saturated square. Outside macOS
  the house is now filled with the state colour (green protected, amber paused, grey off, red alarm) and
  outlined a shade darker so it still reads on a light taskbar; macOS keeps the outline. Found by looking
  at a real Windows taskbar during W-01.
- **"1 camere"** in the *Recording* row of the System column, and the same in the message about cameras made
  visible again: a single camera now gets its own sentence in both languages. Small, but it sits in the first
  screen, and a line that gets its own plural wrong is what a careful reader uses to judge the rest.
- **Recording worked on Windows; Mirto just could not see it** (W-01, 19/10, on the node's camera). The
  recorder decides the stream is alive by watching the newest segment grow, and asked the folder entry for
  the size (`os.scandir`). On Unix that number is always current; on Windows it is **not updated while the
  file is open**, so a segment that was growing looked frozen. After 30 seconds Mirto declared "no data
  from the camera", killed ffmpeg and started again — for ever. The log said both things in the same minute:
  "deleted 2 segments (retention and space)" and "recording stopped: no data from the camera". Measured on
  the PC: the folder reported 0 bytes for a file that really held 524316. The size now comes from the file
  itself (open and seek to the end), with the folder entry as a fallback; a test makes the directory entry
  lie the way Windows does and fails if the size is taken from it.
- **The interface no longer tells a Windows user about "this Mac"** (found in the first minute of W-01 on
  a real Windows 11 PC). Eleven texts named the reader's computer a Mac, because that is where Mirto had
  been written and tried: "No alerts. Events stay only on this Mac", "Mac microphone", "The Mac's first
  camera", "Mac and USB cameras", "is the Mac connected to the home Wi-Fi?", "on the same network as the
  Mac", "when you turn on the Mac", and the Telegram line about the archive. On Windows every one of them
  described a computer the user does not own. The word now follows the system — Mac, PC, or "computer"
  elsewhere — through `questo_computer()`, and the strings carry a `{computer}` placeholder in both
  languages. The name of the operating system stays where it is the subject ("macOS asks for permission"
  became "the system asks", since Windows asks too). A test fails if any translatable text calls the
  reader's computer a Mac again.
- **Two promises nobody was checking on Windows** (found during W-01, on a real Windows 11 PC, with the
  suite green there: 472 tests, but 12 skipped instead of 8). The test that measures how long an event clip
  lasts asked `ffprobe`, which `imageio-ffmpeg` does not ship: on Windows it was skipped, so "ten seconds
  before, twenty after" was verified everywhere except the system the project most wants to call tested. It
  now measures the clip by decoding it with `ffmpeg -progress`, which is always present. The test that
  checks the configuration file is readable only by its owner was skipped on Windows as well — and Windows
  is exactly where `os.chmod(0o600)` does nothing, because it only moves the read-only flag and never
  touches the ACLs. It now reads the real ACLs as SDDL, so the check does not depend on the language of
  Windows, and fails if Everyone, Users or Authenticated Users are granted anything. `SECURITY.md` no
  longer claims `0600` on every system: it says the file inherits what `%LOCALAPPDATA%` grants — your
  account, SYSTEM and the local administrators — and that an administrator can read it, which no
  application can prevent. `--diagnostica` no longer mentions a missing `ffprobe`, because nothing uses
  ffprobe any more.

### Added
- **The siren is never silent: the node falls back to another output** (asked for while testing S-03 with a
  real JBL speaker that would not reconnect). The siren used to depend entirely on the configured output: if
  the Bluetooth speaker was off, out of range or taken over by a phone — all three happen in a real house —
  `ready()` failed and nothing sounded at all. Now, when the chosen output does not answer, the node tries
  the 3.5 mm jack, then a USB card, then the system output, and the siren sounds from there. The fallback
  does not hide the fault: `last_error` keeps the reason, `status()` still reports the speaker as not
  connected, and it adds `uscita_in_uso` and `ripiego`, which Mirto shows in the *System* column as
  "⚠ not connected · playing from the 3.5 mm jack". A node with no speaker configured at all now sounds
  from the system output too, instead of raising an error during an intrusion; when there is genuinely no
  output left, the error reaches Mirto as before.

### Added
- **Public roadmap and governance, and a way to support the project** (C-02, F-01). Three documents a
  contributor or a sponsor looks for on the first day, and the honest version of each. `ROADMAP.md` says
  what is being built and, more usefully, what the project **will not** do — no cloud for video, no closed
  "Pro" tier, no faces or licence plates, no hardware sold, no mobile app for now — each with the reason,
  and no dates, because one maintainer should not promise them. `GOVERNANCE.md` says there is one
  maintainer by name rather than hiding behind a plural, which cuts both ways and says so; it repeats the
  rules a pull request cannot weaken, and answers "what if he stops" with the only true answer: the licence
  lets anyone fork and continue, and nothing in the project is needed to keep the code alive.
  `.github/FUNDING.yml` adds GitHub Sponsors, and both READMEs say what a donation does **not** buy: it
  does not change the licence and it does not buy priority. In the app, *Help → Support Mirto* explains
  what the money is for — the certificates that sign the downloads, hardware, the update server — and opens
  the page only if the person chooses to. Tests check that the roadmap keeps its "no", that the public
  documents carry none of the private plan's numbers, that no CLA reappears anywhere, and that the funding
  file names only a channel that actually exists.
- **`--diagnostica`: a report that cannot contain a secret** (part of C-03). The issue template asked for
  "version and OS" and warned against pasting tokens, IPs or chat IDs — leaving the reporter to judge what
  is sensitive while support still needs to know how the installation is put together. The new command
  prints versions, whether ffmpeg is there, the ONNX providers, how many cameras / nodes / actions, the
  recording mode, which alert channels are on, and the last ten error lines. Every line goes through one
  filter that removes passwords inside addresses, bot tokens, mail addresses, IPv4 addresses, long hex
  strings (keys) and long numeric IDs (Telegram chats); camera names are never printed at all, because a
  camera can be named after a person or a street. The safety is tested the only way it means anything: the
  test feeds real-shaped secrets through the report and fails if any of them survives. The issue template
  and both guides now ask for its output.

### Fixed
- **A broken configuration no longer means "it doesn't open"** (Q-06). `yaml.safe_load` raised straight
  through startup, so an invalid `config.yaml` — which happens to anyone who edits it by hand, or if the
  disk fills during a save — showed up as a program that simply does not start, with nothing said. Now the
  file is set aside as `config.yaml.rotto` (never deleted: it holds the cameras and schedules of someone's
  house), Mirto starts from the defaults, and it says so in a dialog that names the detail and where the
  old file went. A file that is merely unreadable — permissions — is a different case and is left where it
  is, because moving a valid file would be the damage.
- **An uncaught error is written down instead of vanishing** (Q-06). There was no `sys.excepthook` and no
  `threading.excepthook`. In a packaged build there is no console for a traceback to appear in, so an
  unexpected error made the window disappear, or killed a thread, leaving nothing to read — exactly the
  case where someone reports "it closed by itself". Both hooks are installed as soon as the log file is
  open, Ctrl-C excepted.
- **The settings window's timers stop when it closes** (Q-06). Its parent is the main window, so it does
  not die on close: `_rec_timer` kept asking for the recording state every two seconds, and opening
  Settings ten times left ten timers doing it. The window is now destroyed on close and every timer is
  stopped, with a guard test that fails if a timer added to `__init__` is forgotten in `done()`.
- **An alert's photo is compressed once, not once per channel** (Q-06). `jpeg()` read its cache but never
  filled it, so the same photo was re-encoded for Telegram, for the email and for the event folder, up to
  six quality attempts each time.
- **A test was poisoning the ones after it.** `test_config_discovery` swapped a fake `cv2` into
  `sys.modules` and then *removed* it instead of restoring the real one, so every later `import cv2`
  produced a second copy of the module, different from the one Mirto's modules hold — and a later test
  patching `cv2.imencode` silently patched the wrong object. Found because it did exactly that to one of
  the new tests above.
- **Event clips are measured, not assumed** (Q-05). The test for a clip checked that the file was larger
  than 2 KB — which a video of the wrong hour passes comfortably. The new tests build three segments of
  three different colours, ask for a window that falls inside the second one, and read the first frame back:
  if the clip starts red instead of green, it jumped into the wrong segment. A second test asks ffprobe how
  long the file really is (skipped where ffprobe is absent, since `imageio-ffmpeg` ships only ffmpeg). The
  concat list now also declares each piece's duration instead of letting the demuxer read it from the file
  header, where a fragmented MP4 can report 0 — the durations come from the file names, which are clock
  times, and the last segment is left undeclared because no start depends on it and it may still be being
  written. **Honestly**: the wrong-segment jump the plan feared did not reproduce here with ffmpeg 7.0.2,
  so this is correctness by construction rather than a fix for an observed failure — declaring the duration
  makes the result independent of how well a given ffmpeg version guesses. Both guides now explain why a
  clip can start a second or two early: the cut is made without re-encoding, so it can only begin at a
  keyframe, and "10 seconds before" is therefore an *at least*.
- **A camera that drops off no longer shows a frozen picture as if it were live** (found while running the
  Q-02 manual check on 19/10, by playing with the node's services). When a camera went offline the tile
  kept its last frame, dimmed, with "Not reachable" in small text along the bottom. For a surveillance
  program that is the worst kind of defect: you look at the empty corridor and you are looking at how it
  was ten minutes ago. The old frame is worth keeping — it says what was in view — but only if it says
  *when* it is from, so the tile now writes it across the middle of the dimmed image: "Picture frozen ·
  12:21" and, past a minute, how long ago (`· 2 min ago`, `· 3 h ago`, `· 1 d ago`). The frame's own
  timestamp already reached the window and was being thrown away. Under a minute only the time is shown,
  with no "ago": five seconds is not a wait. With no timestamp at all it says "Picture frozen" and invents
  nothing. The wording lives in `gui/testi.py`, so it is tested line by line without Qt, and a guard test
  checks the tile asks for it. The camera list in *All cameras* is deliberately left alone: there the
  status is written in words beside the thumbnail, so the old picture does not pretend to be live.
- **Camera passwords left in already-written logs are cleaned at startup** (found by running the S-02
  manual check on 19/10). `redact_url` landed on 18/10 at 10:24 and the newest leaking line was written at
  08:07 that morning, so nothing leaks any more — but two camera passwords sat in `logs/mirto.log` in
  lines from 17 and 18 October, and that file is exactly what a user attaches to an issue. A password
  written by mistake stays written: `scrub_logs()` now rewrites `mirto.log` and its rotated copies,
  replacing the password inside any address with `***` and leaving everything else (the user name, the
  host, the rest of the line) so the history stays readable. It runs from `setup_logging()` **before** the
  log file is reopened — rewriting a file the program already holds open would leave rubbish in the middle
  — and files with nothing to remove are not rewritten at all. When it does clean something it says so,
  and says the thing that matters: a log already sent to someone stays sent, so the camera password should
  be changed, not just deleted from here. Mail addresses and the word "password" in an error message are
  left alone: only `scheme://user:password@` is touched.
- **A lost Bluetooth pairing is reported as such, with the remedy** (found on the real node the same
  evening). The node only ever read `Connected:` from `bluetoothctl info`, so a speaker whose bond had
  been lost — `Paired: no`, `Trusted: yes`, connections dying with `br-connection-unknown`, which is what
  happens when a speaker is put into pairing mode and replaces the previous device's key — was reported as
  "Bluetooth speaker off or out of range". That sentence sends the user to wait, switch the speaker off and
  on, and wait again, and no amount of waiting restores a pairing: only a person can, from the node. The
  same reply already contains `Paired:`, so the node now reads it, says *«cassa … non più abbinata al nodo:
  sul nodo lancia «mirto-nodo audio» per riabbinarla»*, and does not waste a connection attempt that
  cannot succeed. `status()` reports `abbinata` beside `collegata` and Mirto shows "⚠ pairing lost · on
  the node: mirto-nodo audio" instead of the generic warning. When the state cannot be read at all,
  nothing is claimed: the node tries to connect, as before. A speaker that is merely off is still retried,
  and now within a minute instead of four — four minutes of silence is too long for a siren during a
  break-in — while an unpaired one is left alone, since insisting only fills the journal.
- **"Test" on a node action now actually switches it on** (found while testing S-03 on the real node). The
  button in Settings → Nodes → ⚡ Actions called `test_action(dry_run=True)`, which checks that the node
  answers and that the action exists in its list and then stops: it replied *"dry run successful"* without
  ever reaching the line that activates anything. Nothing in the program ever called the real branch. The
  effect was worse than an error — it reported a working chain while the chain was never exercised, and it
  cost an evening of hunting two genuine but unrelated bugs on the Raspberry because the light "worked
  according to Mirto" and did nothing in the room. Now the button switches the action on for five seconds
  (the cap was already there), the siren and the deterrent ask for confirmation first since the whole house
  hears them, and the message afterwards names what was switched on and for how long. The dry run stays —
  it is the useful answer when a node is unreachable — but it now says in plain words that nothing was
  switched on. A guard test reads `settings.py` and fails if any caller of `test_action` leaves the choice
  implicit again.

### Security
- **The node's server cannot be tied up by someone without a key** (S-04). Three ways in, all of them
  available to anyone on the home network — where there may also be a cheap camera with curious firmware
  or a guest's laptop. The body of a request was read **before** the signature was checked, up to 20 MB;
  a negative `Content-Length` became `rfile.read(-1)`, which means "read until the connection closes", so
  a request that sent nothing kept the node waiting; and no timeout ever closed a slow connection. Now a
  connection that does not speak is dropped after 15 seconds, the body is capped per path (1 KB for
  `/abbina`, 20 MB for `/audio/riproduci`, 64 KB for everything else) with a negative or non-numeric
  length refused as 400 and anything oversized as 413, `Verifier.precheck()` rejects a request with no
  signature headers or an out-of-tolerance timestamp **before the body is read at all**, and more than 16
  requests at once get an honest 503 instead of slowing everything down for everyone.
- **A silent connection no longer stops the whole node, alarm included** (found while writing S-04's
  "slow connections" test). The listening socket was wrapped in TLS, so the handshake ran inside
  `accept()` — in the loop that accepts connections. Twenty connections that opened the port and said
  nothing were enough to stop the node answering anybody, and the test proved it: a signed request timed
  out. The handshake now happens in the request's own thread (`_Server.get_request` hands over the plain
  socket with a timeout, `finish_request` wraps it), so whoever stays silent costs one thread for fifteen
  seconds and everyone else gets through. This was not in the plan's description of S-04: the plan listed
  the body and the timeouts, and the real hole was one layer below them.
- **Each credential is read from the keychain once per run** (W-05). `SecretStore.get()` went to the keychain
  every single time, and the Telegram token is read on every notification: on an unsigned `.app` macOS asks
  for consent at each read, so an intrusion alert could end up waiting behind a Keychain dialog. Secrets are
  now remembered in memory for the life of the process (`set` and `delete` keep that memory honest, `forget()`
  drops it when something may have changed from outside), and a failed read is not remembered as "empty" —
  a locked keychain is not an answer, so the next read tries again.
- **Camera passwords out of `config.yaml` and out of the logs** (S-02). The RTSP address of a node or network
  camera was saved complete with `user:password` in the configuration file and printed to `logs/mirto.log`
  — the file users attach to issues — while README and docstrings claimed no password was ever stored there.
  Now the configuration keeps the address without user info, the credentials go to the system keychain under
  `rtsp:<camera id>` and are put back only when the stream is opened (`mirto/netutil.py`); existing
  configurations are migrated at startup and the file is rewritten. Every address written to a log or a
  message passes through `redact_url()` (`rtsp://user:***@…`), and `Config.save()` now writes the file with
  `0600` permissions. The remaining exposure — the address on the `ffmpeg` command line, visible via `ps` to
  other users of the same computer — is documented in `SECURITY.md`.
- **A key per pairing, and a PIN** (S-01, node 0.6.0). Until now `POST /abbina` handed out *the* node key — the
  one written by the installer and shared by everyone — to the first client with a private IP address, and the
  pairing window reopened at **every restart of the node service** until the node was paired: whoever won the
  race kept full control of the node, and Mirto never showed it. Now:
  - every pairing gets **its own key**, in `/etc/mirto-node/chiavi/<client>.key` (folder `0700`, file `0600`),
    with a card beside it holding client name, address and date: `mirto-nodo abbina --elenco` lists them one per
    line, `--revoca <name>` detaches a single one (the others keep working) and `--revoca-tutti` detaches them all;
  - pairing needs the **6-digit PIN** shown by the node (`mirto-nodo abbina`, `mirto-nodo stato`, or
    `mirto-pin.txt` in the boot partition of the microSD at the first boot of an image); after **5 wrong PINs**
    the window closes;
  - the window opens for **5 minutes** (was 15) only when the user asks for it: installation, first boot of the
    image, `mirto-nodo abbina`, or the `mirto-abbina` file on the microSD — **never** on a plain service
    restart;
  - `/stato`, System → Nodes and `mirto-nodo stato` show **who** paired the node and **when**; Mirto sends a
    Telegram alert the first time a node reports being paired by another Mirto.
  - the shared **installation key is only a bridge**, so that nodes paired before this version keep working: from
    the first pairing with a key of its own, `mirto-nodo stato` and Mirto (System → Nodes, plus one Telegram
    alert) ask for it to be retired, and after **7 days it retires by itself**. Retiring it — by hand with
    `mirto-nodo abbina --revoca installazione`, with `--revoca-tutti`, or automatically — deletes the file and
    leaves `chiave-revocata`, so a reinstall does not bring it back. Image preparation deletes the pairing keys
    and the PIN.

### Fixed
- **Windows fixes found by the new CI** (W-02): the event store is now closed when Mirto stops, so the
  SQLite file is not left locked (on Windows a locked file cannot even be deleted); the files generated for
  the Raspberry — `mediamtx.yml` and the `/etc/fstab` line — always use POSIX separators, whatever system
  generates them; `write_atomic` no longer assumes `os.chown` exists; presence detection recognises the
  Windows error codes for "connection refused"; and `.gitattributes` keeps the node's shell scripts on LF
  endings, so a checkout on Windows cannot produce scripts the Raspberry's bash refuses to run. The second
  run brought out three more, all of them in the tests: a core opened inside a temporary folder is now closed
  **inside** that folder — `addCleanup` runs after the folder has already been removed, and Windows refuses to
  delete an open SQLite file — and a check in `tests/test_core.py` makes sure no test goes back to the old
  habit; the check that parses the node's shell scripts now looks for a `bash` that really runs a command,
  because the one in `PATH` on Windows can be the Linux subsystem's with no distribution installed, and the
  check is skipped, not failed, when no usable `bash` exists; and the test on a camera answering at two
  addresses no longer needs `127.0.0.2` to be a local address, which it is on Linux but not on macOS.
- **Closing a camera no longer freezes the window** (Q-02). Removing a source joined its thread for up to 5
  seconds and then joined the detection pipeline for 5 more — on the Qt thread, from *Settings*, from a
  reload and from the periodic scan. With an unreachable RTSP camera (the read blocks until the timeout) the
  window froze for 5 to 10 seconds. Now the stop is requested immediately and the waiting happens in a single
  background worker, while closing the app still joins every source in parallel within its deadline.
- **Camera scanning never touches a camera that is already open, and stays off the GUI thread** (W-03). The
  scan ran on the main thread on every platform (`threaded_discovery=False` was justified only for macOS and
  AVFoundation), and when the native enumeration was unavailable the fallback opened `VideoCapture(0..4)` —
  including the cameras Mirto itself was streaming. With Media Foundation on Windows, where a camera is
  exclusive, that made the camera in use look unplugged: the source was stopped and reopened at the next
  scan, so the preview flickered and the recording had holes every minute. Now the scan runs in its own
  thread everywhere except macOS, the probing fallback is given the indices Mirto has open and takes them
  as present without touching them, the fallback refresh itself runs in a background thread, and a camera is
  only declared unplugged after it is missing from **two consecutive scans**.
- **Email alerts are retried like Telegram ones** (Q-01). If Telegram failed, the event went into the retry
  queue for 24 hours; if the **email** failed, it was only written to the log — so for anyone using email
  alone, an intrusion during an SMTP outage produced no alert at all. The pending queue now tracks which
  channels are still owed (`telegram`, `email`), retries only those, with a growing wait (1 min, 5, 15) per
  item, the same 24-hour age and 20-item caps, and delivers a single copy with the event's own frozen photo.
  `last_error["email"]` is cleared on success, and System → Notifications shows "N alerts waiting to be sent
  (email)". SMS are still not retried: they cost money and have a daily cap.

### Changed
- **Supply chain** (S-08): `tools/aggiorna-lock.sh` produces a per-platform
  `requirements-<os>.lock` with SHA-256 hashes (`pip-compile --generate-hashes`), and CI installs from it
  with `--require-hashes` when the file is there, falling back to `requirements.txt` otherwise;
  `tools/fissa-action.py` pins every workflow action to its commit SHA, keeping the version in a comment so
  Dependabot can still update it; Dependabot now also watches the statistics Worker's `npm` dependencies;
  `.wrangler/` is ignored. The four actions in `tests.yml` are now pinned — `actions/checkout` and
  `actions/setup-python` at v7, `gitleaks/gitleaks-action` at v3, each with its 40-character commit next to
  the version in a comment — which also clears the Node 20 deprecation warnings. `fissa-action.py` resolves
  those commits with `git ls-remote` instead of the GitHub API: no token, no request limit, and it correctly
  prefers the `^{}` line of an annotated tag, whose own SHA is a tag object that Actions refuses to run.
  `tests/test_tools.py` covers that rule and fails whenever a workflow goes back to a movable tag. The
  CycloneDX SBOM that was missing here is now produced by the release workflow; still to come are the lock
  files for Windows and Linux, which have to be generated on those systems.
- **DirectML is real on Windows** (W-04): `person.py` looked for `DmlExecutionProvider` and the README
  promised "DirectML acceleration", but the dependency was plain `onnxruntime` (CPU only), so on Windows
  detection always fell back to the CPU without saying so. Windows now installs `onnxruntime-directml`
  instead of `onnxruntime` (they cannot coexist), the PyInstaller spec collects its DLLs, and System →
  AI Recognition names the provider actually in use: *Apple Neural Engine (CoreML)*, *GPU (DirectML)*,
  *GPU (CUDA)* or *CPU*.
- **CI covers Windows and Python 3.11** (W-02): the test matrix is now ubuntu / macOS / windows × 3.11 / 3.12
  (six combinations, `fail-fast: false`), it runs on pushes to the release branch as well, it checks the entry
  point starts (`python -m mirto --version`) before running the suite, and it keeps the statistics server
  out of the way (`MIRTO_STATS_URL=off`). File-permission assertions that mean nothing on Windows are
  skipped there instead of failing.
- **Software only, generic outputs**: the *fog* and *lock* action types and the `/apri` `/chiudi` (`/unlock`
  `/lock`) commands are gone. Node commands Mirto does not know become **generic outputs** (relay, socket,
  HTTP service) that ask for confirmation by default; saved fog/lock actions are migrated to generic outputs with
  confirmation. Siren, lights, audio and deterrents are unchanged. Guides and READMEs updated.

### Added
- **Retiring the shared key no longer kills the node at the next restart** (S-01, found on the real node).
  `mirto-nodo abbina --revoca installazione` does exactly what S-01 asks — it deletes
  `/etc/mirto-node/chiave` — but the service read that file without a guard at startup. Nothing happened
  while the process stayed up; at the first restart the node died and respawned every five seconds, and
  Mirto, getting no answer, reported the node as "pairing closed". The service now asks `KeyStore` for the
  install key, which knows how to answer "there isn't one", and starts on its own per-pairing keys; with no
  keys at all it still starts and says so, so the node can be paired again. A node that does what we told it
  to do must not take itself down.
- **`mirto-nodo stato` now shows what the *service* sees, not what root sees.** The command runs as root
  and reads the files directly, so during the first real test it reported the pairing window **open with a
  PIN** while Mirto was told it was closed — and separately said "not recording" with no reason. Both were
  the same thing: files written by root with `0600` permissions, which the unprivileged service cannot even
  open. The status now lists the files that do not belong to the service user, explains that this is exactly
  why pairing looks closed and recording does not start, and prints the one command that fixes it. The node
  also logs a warning when it cannot read the MediaMTX API password instead of falling silent. A test checks
  that every new state file in `server.py` and every key in `keys.py` is handed back to the service user, so
  a file added later cannot reintroduce the same invisible failure.
- **A manual key is a permanent key, and the question now says so.** At the end of `mirto-nodo abbina`
  came "do you also need a key to copy by hand?", and a single `s` created a key that never expires, always
  called *copiata a mano*. An afternoon of testing on the real node produced four of them, indistinguishable
  in the list and none of them in use — each one able to command the node forever. The question now says it
  creates a **permanent** key, asks what to call it so it is recognisable in `--elenco`, and prints the exact
  command to revoke it. It is also preceded by the plain truth: the PIN just shown is almost always enough.
- **"Nodes found" now reads like a sentence addressed to a person.** Searching for nodes answered with one
  dense line — `Mirto Node mirto-ingresso (https://…:8443): pairing closed: on the node run
  mirto-nodo abbina and try again` — which left the reader unsure whether a node had been found at all and
  what they were supposed to do. It now says how many nodes were found, lists each one with its address and
  state on its own lines, and ends with the command to run **on its own line**, so it can be copied without
  cleaning it up — and only when there is actually something to do: a node already paired asks for nothing.
  The text moved to `mirto/gui/testi.py`, which has no Qt in it, so the words a user reads at a confusing
  moment are covered by tests like any other behaviour.
- **The node service no longer runs as root** (S-03, node 0.7.0). An HTTPS server listening on the whole home
  network, running `aplay` and `bluetoothctl` and writing to `/sys/class/leds`, had no `User=` in its unit:
  every bug in that code was a bug with root's powers. It now runs as the system user `mirto-node` — no
  shell, no home — with `NoNewPrivileges`, an empty `CapabilityBoundingSet`, `ProtectKernelTunables`,
  `RestrictSUIDSGID` and a single writable path, its own configuration folder. It keeps only the groups it
  actually uses: `audio` for the speaker, `bluetooth` for the siren, `video` for the camera.

  Three things it used to do as root had to find another way, and each one is the kind of detail that decides
  whether dropping privileges really works or only looks like it does:
  - **the green LED.** Two obstacles, one on top of the other, and both only showed up on the real board.
    First the hardening: `ProtectKernelTunables=yes` makes `/sys` read-only, so the test light did nothing
    however right the file permissions were — the unit now reopens `/sys/class/leds/ACT` and `led0` by name.
    Then the LED still did not light, because **writing `brightness` does not drive it on this board**: on a
    Pi 4 the same LED appears four times in `/sys/class/leds` (`ACT`, `default-on`, `mmc0`, `mmc0::`) and
    microSD activity overwrites whatever we write. Checked as root with the trigger set to `none` and the
    brightness at its maximum: dark. With the kernel's `timer` trigger: blinking. So the LED is back to that
    trigger and `LedDevice` has no blinking thread at all — it writes `trigger`, which always exists and
    which the udev rule (`node/99-mirto-leds.rules`) makes group-writable, and nothing else. `delay_on`
    and `delay_off` are born with that trigger and therefore owned by root: the node tries them and, when it
    cannot, keeps the kernel's half second. It blinks either way, which is all a test light owes anyone.
    The board data behind this — the full LED table and the commands that produced it — is written down in
    `privato/documenti/Hardware del nodo.md`, so a second node on older hardware does not start from zero.
  - **the microSD.** The boot partition is FAT and belongs to root: the service cannot write the pairing PIN
    there, nor delete it when it expires, nor remove the `mirto-abbina` file someone put there to re-open
    pairing. Rather than giving it the whole card, it leaves a note in its own folder and
    `mirto-boot-segnali.path` — a root one-shot — does the job.
  - **the state files.** `mirto-nodo`, the command line, still runs as root — it has to install and
    repair — and the files it writes are `0600`. Written by root, the service could no longer read them:
    the first real test on the Raspberry generated a PIN from the command line and Mirto was told it had
    already expired. `mirto_node/fs.py` now writes every state file and hands it back to the service user,
    and the same rule covers the pairing keys.
  - **the recordings.** MediaMTX writes them, but the node runs the ring that deletes the oldest ones, and
    deleting depends on the permissions of the *folder*, not the file. The folder is now `mediamtx`-group
    with `setgid` and group write, the node is in that group, and a `tmpfiles.d` entry restores it at every
    boot; a disk plugged in later, which belongs to root, is prepared through the same note mechanism.
- **A Telegram rate limit no longer costs an alert its buttons** (Q-04). Every `ok: false` from Telegram was
  the same kind of error, so a 429 — "too many messages, retry in N seconds", which is not a failure but a
  wait — sent the alert to the retry queue, where it came back a minute later **without** its buttons: no
  *Stop everything* exactly while someone is in the house. `TelegramRateLimited` now carries the
  `retry_after`, and the dispatcher waits what Telegram asks (capped at 30 seconds, so one slow message
  cannot hold up the queue) and retries once **with the same buttons**; only then does it fall back to the
  queue. Stopping the bot also stops it properly: `stop()` unblocks the long poll with a zero-timeout call
  and waits for the thread, and the wait between attempts is now interruptible — so changing the token no
  longer leaves the old bot reading for up to 45 seconds, and saving settings while Telegram is unreachable
  no longer holds the window. A `Conflict` in the first minute after a start is logged as what it is, the
  previous copy shutting down, instead of accusing the user of running two Mirto.
- **A gitleaks false positive, closed where it can be audited.** Scanning the whole history flagged
  `mirto/__main__.py:111` of the 0.1.0 commit as a `generic-api-key`. The line is
  `api = cv2.CAP_AVFOUNDATION if … else cv2.CAP_ANY` — a variable called `api` holding a long expression of
  OpenCV constants. It is now listed in `.gitleaksignore` by fingerprint, with the reason written above it,
  and `tests/test_docs.py` fails if any entry there lacks its explanation or is not a well-formed
  fingerprint: silencing an alarm has to cost at least one line of justification, so a real leak cannot be
  hidden by a one-word commit.
- **The test suite no longer touches the network** — and says where its time goes. Tests were opening
  24 TCP connections and resolving 13 names outside this machine (`mirto-ingresso.local`, `192.168.x`,
  `192.0.2.1`): instant here and on a real Mac, but on a machine where none of those exist each unanswered
  `.local` lookup costs macOS about five seconds of mDNS waiting, and each connection its full timeout.
  `tests/senza_rete.py` now makes anything outside `127.0.0.1` unreachable and unresolvable, with two tests
  that fail if that net springs a leak; the resolution itself moved into `network_discovery.risolvi()`, one
  place that can be replaced, instead of two inline `getaddrinfo` calls. Measured with the macOS delays
  applied to the real calls: 116s before, 43.7s after. `tools/tempi-test.py` runs the suite in CI in place
  of plain `unittest` and prints the slowest tests, so a slowness that only appears on one platform is
  visible in the log instead of being guessed at. The effect on CI was larger than the measurement
  predicted: the macOS jobs went from about 510 seconds to **109–117**, the suite itself from 480 to 94,
  and macOS is now quicker than Windows. The matrix stays full — three systems, two Pythons, every push —
  because the cost was never the matrix, it was the tests reaching for a network that was not there.
- **"Who does what" instead of a vague line about AI assistance.** The README used to carry a short
  "AI-assisted development" note; it now says plainly which decisions are Marco Dalicco's — requirements and
  their order, architecture, security, what Mirto will *not* do, every test on real hardware, and the fact
  that nothing reaches `main` without him — and that most of the code, tests and documentation are written
  by Claude under that direction. From now on every commit carries an `Assisted-By:` trailer, so the history
  says it too, commit by commit; `CONTRIBUTING.md` asks contributors for the same line when they use an
  assistant, as a note of provenance and not a mark against the contribution.
- **An honest comparison with Frigate, Blue Iris and Scrypted** (R-02), in both READMEs. The first question
  a technical reader asks is "why not one of those?", and a project that does not answer it looks like it has
  not looked. The table compares what each one *is* (a desktop app you open, a Docker container you host, a
  Windows NVR, a self-hosted hub), where it runs, what it costs, what extra hardware it expects and whether
  it drives physical devices — with prices taken from the vendors' own pages and the date of the check
  printed underneath, because a price table without a date ages in silence. It is followed by what Mirto
  **does not** do: no faces and no licence plates (a deliberate choice, not a gap), no HomeKit, Home
  Assistant or MQTT, no web interface and no phone app, no multi-user, not meant for twenty cameras, and
  Linux supported for development only. `tests/test_docs.py` checks the two language versions keep the same
  number of rows, so one cannot quietly drift from the other.
- **Scope, disclosure and privacy told straight** (R-03). `SECURITY.md` said the node was "a separate
  project" and out of scope while `node/` sits in this repository — a reader could find that contradiction
  with one `grep`, and it is the kind of thing that costs trust before the code is even read. Everything in
  the repository is now in scope, the node first of all (server, pairing, keys, recording, installation
  scripts), together with the release workflows, the packaging and the statistics Worker; out of scope are
  third-party services, the Raspberry's own OS, physical access to the node, misconfiguration, and what the
  devices wired to a node do. The report now comes with a timeline — answer in 7 days, assessment in 14, fix
  within 90, and after 90 days the reporter is free to publish, because the clock protects users and not the
  project — and the same policy is published as `.well-known/security.txt` (RFC 9116). `PRIVACY.md` was
  missing the only other connection Mirto makes on its own: the recognition models downloaded once from
  GitHub and Hugging Face, which see an IP address like any download does, with nothing of the user's
  attached; `tests/test_docs.py` now reads the hosts out of `models.py` and fails if one of them is not
  declared in `PRIVACY.md`, so a model added from a new host cannot slip in silently.
- **Windows installer** (W-06, first half): `packaging/mirto.iss` builds a per-user installer with Inno
  Setup — no administrator prompt, Italian and English, Start Menu entry, optional desktop icon, an opt-in
  "start Mirto when I turn on the computer" that is removed again when the program is uninstalled, and an
  uninstaller that leaves configuration, events and recordings alone because they belong to the user. The
  `AppId` is fixed for good: changing it would install a new version *beside* the old one instead of over it,
  so a test checks it, along with the rule that the version comes from `mirto.__version__` on the ISCC
  command line and never from the installer script. Signing goes through SignPath Foundation (free for fully
  open-source projects) and, like the Apple signing, runs only when its secrets are set. The release workflow
  now builds macOS and Windows in parallel and a third job waits for **both** before opening the draft, so a
  Release can never go out with half the files.
- **Release workflow for macOS** (W-05): a tag `v*` now builds the `.app` with PyInstaller, wraps
  it in a `.dmg`, produces a CycloneDX SBOM (the piece left over from S-08) and a `SHA256SUMS` file, takes the
  release notes straight from this CHANGELOG (`tools/note-di-rilascio.py`, so they cannot drift from it) and
  opens a **draft** Release with everything attached — nothing is published without a human. The workflow
  refuses to run if the tag and `mirto.__version__` disagree. Signing and notarisation
  (`codesign --options runtime` with `packaging/entitlements.plist`, then `notarytool` and `stapler`) run only
  when the Apple secrets are set, and the build says out loud, in the job and in the release notes, when it is
  unsigned; the entitlements are five and each is commented with the reason it is there. Enrolling in the
  Apple Developer Program and loading the six secrets is Marco's part; the Windows installer joins the same
  Release with W-06.
- **Terms of use** to accept at first start, before cameras and microphones open (checkbox + *I accept*;
  declining quits): software only, the user is responsible for connected devices, film only your own property,
  tell the people in the house, no device that can cause harm, no warranty, use at your own risk. Also in
  Help → Terms of use and guide chapter 12. Acceptance version and date saved in the configuration.
- **Usage statistics (opt-in)** and **update check**: at first start Mirto asks (default *no*; only when a
  statistics server is configured); with yes, a daily message with a random, renewable ID (deleted from the server
  on withdrawal or renewal), version, OS, language, cameras/nodes in ranges and enabled features; with
  no, a single anonymous decline (version only, no ID). Settings → General: on/off, *What is sent…*, *New random
  ID*, update check. Free server in `tools/stats-worker` (Cloudflare Worker + D1, no IP addresses, 13-month
  retention, token-protected page with active installs, retention, consent rate). See `PRIVACY.md`.
- **Recording on the node** (node 0.5.0, API 1.4): MediaMTX records the node camera by itself (1-minute fMP4, UTC
  names), independently of Mirto; ring buffer kept by the node service (min 10% free, `massimo_gb`, 2 GB on the
  microSD), recording stopped when the configured USB disk is missing and resumed when it comes back;
  `mirto-nodo disco` prepares and mounts a USB SSD/stick (fstab with `nofail`); `GET /registrazioni` and
  `GET /registrazioni/file`; recording state in `/stato`, `mirto-nodo stato` and System → Nodes; Telegram alert
  after 5 minutes stopped. Review shows node-only parts in teal and downloads them on click; export uses them too.
  Image preparation deletes recordings from the microSD.
- **Video recording (NVR)**: per-camera mode *events only* (clip from 10 s before to 20 s after every event, next to
  the photo), *continuous* (kept for N days) or *off*. Network cameras are recorded by copying the RTSP stream with
  no re-encoding; local cameras are encoded with the hardware H.264 encoder (VideoToolbox, Media Foundation…).
  One-minute fragmented MP4 segments named by their UTC start time; retention by days plus a disk quota (half of the
  available space by default) and a minimum of free space, with automatic pause and Telegram alert when the disk is
  full. ffmpeg runs as a separate supervised process (shipped by `imageio-ffmpeg`).
- Node 0.4.1: camera bitrate set to 1.5 Mbit/s (`[camera] bitrate`, MediaMTX default was 5 Mbit/s) and a
  keyframe every 2 s, so continuous recording takes ~16 GB/day and event clips are cut more precisely.
- Per-camera "only when protection is on" recording (`recording.armed_only`), cameras grouped by node in
  Settings → Recording.
- **Review** view: per-camera day timeline (recorded spans, coloured event marks, zoom with the wheel), player
  that moves on to the next segment by itself, 1×–16× speed, previous/next event, ±10 s, frame photo, MP4 export of
  an interval; event clips of *events only* cameras are playable from the bar; right-click an event → Review.
- Event clips: ▶ on the event thumbnail (double-click to watch), sent to Telegram as a reply to the photo;
  `/clip [camera] [seconds]` (`/video`) for the last seconds of a camera.
- "Recording" row in System, Settings → Recording tab, recording line in `/stato`, `--prova-registrazione`
  (`--test-recording`) command to measure space and CPU per camera, user guide chapter 7quinquies.
- Bird species and sound models pinned by SHA-256; `ai-edge-litert` added as a dependency to run the species
  model (OpenCV cannot read its quantized tensors).
- Nodes as first-class objects: a node brings its own cameras (created automatically, with stream credentials)
  besides its actions; node API 1.2.
- Raspberry node: one-command installer with MediaMTX (password-protected stream), `mirto-nodo` guided tool
  (status, Wi-Fi, audio output Bluetooth/jack/USB/HDMI, camera normal/NoIR, name, pairing), node image tools
  (`prepara-immagine`, first-boot regeneration of hostname, SSH keys, node key and certificate).
- One-click pairing: "New node found → Pair" within 15 minutes of power-on; key delivered over TLS after the
  pinned certificate is verified, once, only to local-network clients.
- User guide chapter "Un nodo pronto in 10 minuti".
- **English**: translation system (`_()`, catalog `mirto/locales/en.json`, `tools/i18n_check.py`), language in
  Settings → General (automatic from the system), English Telegram commands (`/status`, `/arm`…), English
  command-line options, English user guide (`USER_GUIDE.md`); the bot sets its own command menu.
- **Bird species**: second model (iNaturalist birds, Apache 2.0) on YOLOX "bird" boxes, grouped by genus
  (pigeons, corvids, starlings, sparrows, gulls, herons, cormorants, parakeets, blackbirds, birds of prey).
- **Deterrents**: new action kind for birds and animals (targets `bird`, `bird:<group>`, animals, sounds),
  always on, never for persons, cooldown, daily limit, optional time window, rotating sounds; node API 1.3
  `dissuasore` device with built-in synthesized sounds (raptor, falcon, crow alarm, shot, gas cannon) and
  custom WAV files, max 30 s per run and 2 minutes per hour, never over the siren.
- **Sound events**: YAMNet sound recognition from the Mac microphone and node USB microphones (glass breaking,
  smoke alarm, sirens, screaming, crying, gunshots, dog barking, knocking, voices, bird calls); alerts with the
  photo of the nearest camera; sounds as action targets (glass breaking → siren); smoke alarm alerts even when
  disarmed (configurable); node `GET /audio/flusso` and working `POST /audio/registra` (`/ascolta`).
- Model downloads verified with SHA-256 pinned on first use (`ASSETS` in `detection/models.py`).

## [0.1.0] – 2026-10-17

First public release.

### Added
- Camera discovery with stable IDs (macOS AVFoundation, Windows Media Foundation, Linux), hot-plug and
  automatic reconnection; RTSP/HTTP network cameras with mDNS discovery (`_rtsp._tcp`) and IP tracking.
- Motion detection (MOG2) and object detection with YOLOX Nano/S (ONNX Runtime, CoreML acceleration),
  multi-frame confirmation, 80 selectable COCO classes, per-class thresholds and anti-spam pause.
- Alerts: Telegram bot (photo, buttons, commands, authorized users, long polling), SMTP email with photo
  attached/inline and link to the Telegram message (provider presets, Gmail app password), generic SMS
  providers (HTTP, Twilio, email-to-SMS) with daily limit; retry queue.
- Arm/disarm with persistent mute, schedules (weekdays, date ranges), presence detection from phones on Wi-Fi.
- External nodes: multiple nodes, certificate pinning, HMAC-SHA256 signed requests with replay protection,
  action editor, confirmation via Telegram, "Stop all", node simulator.
- Safety rules: siren max 180 s every 30 min, lock and audio never automatic, actions only for persons and
  only when armed, stale Telegram commands ignored, confirmation buttons expire.
- "Night console" interface: camera grid, all-cameras page, event history, animated status logo,
  built-in user guide (Italian), settings with "How to" panels, test menu.
- 24/7 operation: start at login with restart, sleep prevention, PyInstaller spec.
- Local event archive with automatic deletion; credentials in the OS keychain.

Development history before the public release (16–17 October 2026) is kept privately.
