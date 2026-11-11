# How Mirto is built, and by whom

A security project that is coy about how it was built invites mistrust the day someone reads the commit
history anyway. So here it is, in the order that matters.

## Who decides

Mirto is designed, directed and reviewed by **Marco Dalicco**. His are the requirements and the order
they are tackled in, the architecture and the security decisions, and the decisions about what Mirto will
*not* do — no faces, no licence plates, no cloud, no device that could hurt someone.

His too is **every test on real hardware**: cameras, Raspberry nodes, a Telegram bot, a Mac and a Windows
PC, in a house where this thing is actually on. The tests you can run yourself cover the code; they
cannot cover a camera that is unplugged at the wrong moment, a node on a windowsill in the rain, or a
siren at two in the morning. Nothing reaches `main` without him.

## Who writes it

Most of the code, the tests and this documentation are written by **Claude** (Anthropic), one task at a
time against a written plan, with the full test suite green before each commit. Those commits say so in
their own trailers, so `git log` tells you the same story this page does:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_...
```

If you contribute and you used an AI assistant, [`CONTRIBUTING.md`](CONTRIBUTING.md) asks for an
`Assisted-By:` trailer. Same intent, and it does not count against your change.

## Why say it at all

Two reasons, and neither is modesty.

The first is that it would come out. The trailers are in every commit; anyone who looks at the history
finds them. A project that had not mentioned them would look like a project that hoped you would not
look — and the day that happens is the day you stop believing the other claims on the front page.

The second is that it tells you **where to look**. What protects you here is what a person decided,
reviewed and tried in a real house, not what was typed fastest. The parts worth your scepticism are the
ones nobody could test automatically: the hardware, the limits that stop a siren, the behaviour of a
node when the network goes away. Those have a name attached.

## How the work actually goes

The rules are not aspirational; they are what the project runs on.

- **The full suite is green before every commit.** Not after, not usually: before.
- **Every change says what cannot be tested.** A feature that depends on hardware ships with the
  sentence that says so, written before it is merged, not discovered by a user.
- **A guardian is not believed until it has been seen to fail.** A test that cannot fail is
  decoration, so each new one is deliberately broken once to prove it notices.
- **What a document claims is checked against the code**, by tests that read the code, not by hand.
  That is why the claims on the front page carry numbers: they come from `config.py` and are kept
  honest by `tests/test_docs.py`.

The things that will not change, whoever writes the code, are in [`GOVERNANCE.md`](GOVERNANCE.md).
