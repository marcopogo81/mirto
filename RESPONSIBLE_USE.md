# Responsible use

Mirto is a tool: whoever installs it is responsible for how it is used. This page is short on purpose,
and it is the one page in this repository we would ask you to read before pointing a camera at anything.

## Privacy: GDPR and your local rules

Point cameras **only at your own property**. A camera that also covers a shared hallway, a neighbour's
garden, a pavement or any other public area puts you under data-protection law in most of Europe, which
in practice means a visible notice sign and a short retention period. Mirto helps with the second part —
recordings delete themselves by age or by disk quota, and you choose the limits — but the sign and the
judgement are yours.

**Tell the people you live with**, and tell them specifically about *Presence*: that feature notices when
the phones on your Wi-Fi leave and come back, which means it knows when each person is at home. It is off
until you turn it on, and it should stay off until everyone whose phone is on that list has agreed.

## The siren, and the neighbours

Mirto caps the siren at **3 minutes** per activation and at 3 minutes in any half hour, and the limit is
enforced twice — in the program and again in the node, which does not trust the program. That protects
against a bug. It does not protect against a camera pointed at a windy hedge: repeated false alarms at
night can still break noise rules, and will certainly cost you the goodwill of the people next door.

## Software only: what you connect is your choice

Mirto switches the outputs of its nodes on and off. **What you attach to those outputs is your decision
and your responsibility.** Connect only devices that cannot hurt anyone and cannot stop anyone from
leaving: a siren, a lamp, a sound. Never a lock on an exit, never anything that heats, moves, or carries
mains voltage into a place a person can reach. Every output switches itself off by itself, and a deterrent
sound is never aimed at a person — those are rules in the code, not preferences, and they are listed in
[`GOVERNANCE.md`](GOVERNANCE.md) among the things that will not change.

At first start Mirto asks you to accept the terms of use. That dialog is this page.

## No warranty

Mirto is **not a certified alarm system** and does not replace professional security. It is provided
"as is", without warranty of any kind: see [`LICENSE`](LICENSE). If something must not fail — a shop, a
warehouse, anything insured on the condition that an alarm is monitored — buy a monitored system and use
Mirto for everything else.

## What Mirto will never do

Not a promise about the future, a decision already taken and written into
[`ROADMAP.md`](ROADMAP.md): no face recognition, no licence plates, no video in anybody's cloud, and no
support for devices that could injure a person. If you need those, Mirto is the wrong program, and that
is the honest answer rather than a feature request.

---

Questions about data, retention and what leaves your machine are answered in
[`PRIVACY.md`](PRIVACY.md). Found a security problem instead? [`SECURITY.md`](SECURITY.md).
