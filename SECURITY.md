# Security policy

Mirto is a home security application that can arm/disarm protection and trigger physical devices.
Security reports are taken seriously.

## Reporting a vulnerability

**Please do not open a public issue.** Use GitHub's private reporting:
**Security → Report a vulnerability** on this repository.

Please include:
- affected version or commit;
- steps to reproduce and impact;
- whether a Telegram bot, email account, node or network position is required.

### Timeline

| | |
|---|---|
| First answer | within 7 days |
| Assessment and plan | within 14 days |
| Fix released | target 90 days |
| Public disclosure | when the fix is released, or after 90 days |

If 90 days pass without a fix, you are free to publish: the clock is there to protect users, not the
project. If the fix needs longer (a dependency, a device that has to be re-flashed), we will say so and
agree on a date with you. Reporters are credited in the release notes unless they prefer not to be.

This policy is also published as [`.well-known/security.txt`](.well-known/security.txt) (RFC 9116).

## Never post secrets

Never paste bot tokens, app passwords, HMAC keys, chat IDs or unredacted logs in issues or pull requests.
If you did, revoke them immediately (BotFather `/revoke`, Google app passwords, node key rotation).

## Secrets and logs

No password, token or key is written to `config.yaml`: they all live in the system keychain (service
"Mirto"), including the **camera passwords** — the configuration keeps the stream address without user
and password, and the credentials are added back only when the stream is opened. Logs and Telegram
messages show addresses redacted (`rtsp://user:***@…`), so a log attached to an issue carries no secret.

The configuration file is written with `0600` permissions on macOS and Linux. On Windows `os.chmod`
cannot set ACLs, so the file relies on what it inherits inside `%LOCALAPPDATA%`: your account, SYSTEM and
the local administrators. Another standard user of that PC cannot read it; an administrator can, and no
application can prevent that. A test checks this on both systems.

Versions before 0.2.0 did write camera passwords into the log; at startup Mirto now cleans the log files
it already wrote and says so, but a log file shared before that update still carries the password, so
change the camera password if you ever sent one.

**Known limit.** While recording an IP camera, the stream address is passed to `ffmpeg` on its command
line, so it is visible in the process list to other users of the same computer (`ps`). This is accepted on
a single-user desktop; on a shared machine, treat the camera password as readable by the other users of
that machine.

## Supported versions

Only the latest release on `main` receives security fixes.

## Scope

**In scope**, everything in this repository:
- the desktop app, including the Telegram command handling and the acceptance of terms;
- the client side of the node protocol (certificate pinning, HMAC signatures, replay protection);
- **the node** (`node/`): its HTTPS server, pairing and PIN, the per-pairing keys, recording, and the
  installation scripts. The node *is* this project — it used to be described here as a separate one, which
  was wrong;
- the release workflows and the packaging (`.github/workflows/`, `packaging/`);
- the statistics Worker (`tools/stats-worker`).

**Out of scope**:
- third-party services (Telegram, Gmail, SMS providers) and the Raspberry Pi OS itself;
- physical access to the node — whoever holds the device can read its microSD;
- misconfiguration, such as forwarding the node's port to the internet or putting cameras on a network
  reachable from outside;
- what the devices wired to a node do: Mirto sends a command, the responsibility for what that command
  moves is the installer's.

## Models downloaded at first use

The recognition models are not bundled: they are downloaded once from GitHub and Hugging Face and
**verified against a SHA-256 written in the source** (`mirto/detection/models.py`), so a compromised
mirror cannot slip a different file in. A model added later has its hash fixed at the first download and
checked at every start from then on.
