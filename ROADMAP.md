# Roadmap · Mirto

*Updated 19 October 2026*

This is where Mirto is going, in the order it is being built. It is derived from the maintainer's working
plan; the dates are deliberately absent, because a single maintainer should not promise them.

## Now

- **Tested on real Windows.** Mirto has run on macOS for months; Windows is covered by the automated
  tests on every push, but the experience — USB cameras, Credential Manager, paths, autostart — is being
  checked by hand on a real PC. Nothing is announced before that.
- **Signed downloads.** A `.dmg` notarised for macOS and a signed installer for Windows, so a download
  opens with a double click instead of a warning.
- **First release (0.2.0)** with release notes, SBOM and checksums.

## Next

- **Supply chain**: dependency lock files with hashes for each platform, GitHub Actions pinned to commit
  SHAs (done), SBOM attached to every release.
- **Home Assistant and MQTT**: events and armed/disarmed state published locally, so Mirto fits into the
  setups people already have — without a cloud in the middle.
- **Windows parity**: restart after a crash (today macOS has it, Windows does not), and documentation
  written for Windows paths rather than translated from macOS.
- **Performance with many cameras**: previews resized before conversion, inference without a single global
  lock. The honest version of "how many cameras does it handle".
- **More languages**: German, Spanish and French after English and Italian — the strings are already
  externalised.

## Not planned

Saying no is part of a roadmap.

- **No cloud for video.** Frames and recordings stay on the computer and in the home. There is no server
  to send them to, and adding one would change what Mirto is.
- **No closed "Pro" version, no open-core.** The whole program is Apache 2.0 and stays that way.
- **No face or licence-plate recognition.** They turn a home alarm into a surveillance system, and they
  carry legal duties that a program installed by one person cannot discharge.
- **No hardware sold.** The node is a Raspberry Pi with a camera and a speaker; anyone can build it, and
  the guide explains how.
- **No mobile app** for now. Telegram already delivers alerts, photos and commands on a phone.

## How this list changes

Open a Discussion (or an issue) and make the case. Features that are out of scope above can be argued for,
but the argument has to address the reason they are out, not only the usefulness of the feature.
