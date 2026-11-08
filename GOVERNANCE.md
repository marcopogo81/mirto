# Governance

*Short on purpose: a project with one maintainer should not pretend to have a committee.*

## Who decides

Mirto has one maintainer, Marco Dalicco, who has the final word on what goes in. That is a fact to state
plainly rather than hide behind a plural: it means decisions are fast, and it means the project depends on
one person. Both halves are true.

Technical disagreements are settled in the open, in the issue or pull request, with reasons. When a change
affects safety (alerts, actions, the siren, what is recorded) the burden of proof is on the change.

## What will not change

These are not preferences, they are the shape of the program. A pull request that weakens one of them is
declined even if the code is good. The full list is in `CONTRIBUTING.md`; the three that matter most:

1. **The video stays local.** No third-party cloud, ever.
2. **Every output switches itself off**, and the siren has a hard limit on both sides (program and node).
3. **Credentials live in the system keychain**, never in files, never in logs.

## Rhythm and response times

This project is worked on **at weekends**. Not a pose: the maintainer has a job and two small children,
and a project that pretends otherwise ends up answering nobody. Said up front, it is something you can
plan around; left unsaid, it is three weeks of silence that look like contempt.

| | |
|---|---|
| First answer on an issue or pull request | **within 7 days** |
| Decision — merged, changes requested, or declined with reasons | **within 20 days** |
| Security reports | `SECURITY.md`, same first 7 days |

Weekday silence is normal and means nothing. A pull request still unanswered after 20 days is a defect in
this process, not patience on your part: say so in the thread.

## How to become a contributor

Send a pull request. There is no form to sign and no copyright to assign: contributions are Apache 2.0 and
each commit carries a `Signed-off-by` line (the Developer Certificate of Origin), checked in CI.

If you used an AI assistant, say so in the commit with an `Assisted-By:` trailer. It is not held against a
contribution — the project itself is built that way, and the README says exactly who did what.

A contributor who sends good changes over time is offered commit access. There is no secret threshold; it
is a judgement, and it is reversible.

**The project is looking for people, and this is not a formality.** One maintainer at weekends is enough to
keep a program alive; it is not enough to make it grow, and pretending otherwise is how single-maintainer
projects quietly die. Where help would change the most, today:

- **Windows**: it is tested, but on one PC only — USB cameras, email and restart-after-crash autostart are
  still open;
- **Home Assistant and MQTT**: the way local projects reach the people who would use them;
- **GUI tests**: a third of the code is the interface and it is the least covered part;
- **translations** beyond English and Italian, and **screenshots**;
- **other hardware**: Raspberry Pi 3, other cameras, other Bluetooth speakers — most defects live there.

If you are reading this and one of those is a Saturday afternoon of yours, open an issue and say so first:
it is the cheapest way to avoid two people writing the same thing.

## Language

The repository is written in English, so that the project is readable by anyone who finds it. The
maintainer is Italian and answers in Italian too, in issues and pull requests — that is about people, not
about files.

## Security

Never in a public issue. The process, the scope and the response times are in `SECURITY.md`.

## If the maintainer disappears

The licence (Apache 2.0) allows anyone to fork and continue, and this is the honest answer to "what if".
Nothing in the project — not the name, not the signing keys — is needed to keep the code alive: a fork
under a different name can be released by anyone, immediately.
