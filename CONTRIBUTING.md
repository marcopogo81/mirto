# Contributing

Thanks for your interest in Mirto!

**Before you start.** This project is worked on at weekends: first answer within 7 days, decision
within 20. Weekday silence means nothing. The reasons and the whole picture are in
[`GOVERNANCE.md`](GOVERNANCE.md), which also lists where help is most needed.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## How to contribute

1. Open an issue first for larger changes, so we can agree on the approach.
2. Fork, create a branch, keep changes focused.
3. Run the tests before opening a pull request:
   ```bash
   python -m unittest discover -s tests -t .
   ```
4. Add tests for new behaviour, especially anything touching alerts, actions or safety rules.

## Rules that must not be weakened

- Siren max 3 minutes (180 s every 30 min);
- every output switches off by itself, audio never automatic;
- software only: no device is named or recommended that could hurt someone;
- an intrusion action (siren, lights) only when armed, and never for animals;
- a deterrent never for persons — the one exception is a *Prank* action, and only while Prank mode is
  on on that node and the house is **not** armed;
- credentials only in the OS keychain, never in files;
- no third-party cloud for video.

## Licensing of contributions

By submitting a contribution you agree that it is licensed under the Apache License 2.0
(section 5 of the license). Please sign off your commits (`git commit -s`) to certify the
[Developer Certificate of Origin](https://developercertificate.org/).

If you used an AI assistant for part of the work, say so in the commit, with the model you used:
`Assisted-By: <model> <email>`. It is not held against a contribution — the project itself is built that
way (see *Who does what* in the README) — and a history that says how it was written is worth more than one
that leaves it to be guessed.

New Python files start with:
```python
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco and Mirto contributors
```

## Language

The project is documented in English so that anyone can read it and contribute. Issues and pull
requests in Italian are welcome too — the maintainer is Italian — but the files in the repository
stay in English.

The application itself speaks both English and Italian, and more languages are welcome: see
*translations* in [`GOVERNANCE.md`](GOVERNANCE.md).

## Privacy

Never include real camera images, tokens, chat IDs, phone numbers, MAC/IP addresses of your home or
personal names in code, tests, screenshots or issues. Use fake values (e.g. `192.0.2.x`, `-1001234567890`).
