# Privacy

Mirto works locally. Video, photos and audio stay on your computer; alerts go only to the channels you
configure (Telegram, email, SMS). This page lists **everything else** Mirto sends over the internet.

An Italian translation is kept in [`PRIVACY.it.md`](PRIVACY.it.md). This English page is the one
that counts: a privacy notice has to be understood to be a notice at all, which is why this one
document — and only this one — exists in two languages.

One thing changed on 26 October 2026, and it is not about the internet: a node with a microphone can now record
that microphone **inside** its video, so that node's clips — including the one that goes to Telegram — carry
sound. It is **off by default** and switched on one node at a time (*Settings → Nodes → Disk*), because audio has
much stricter rules than video: in Italy the regulator considers audio recording in video surveillance excessive
in most cases. Nothing else about this page changes: that audio stays on your computer and on the node, and goes
only to the alert channels you configured.

## Usage statistics (only if you say yes)

At first start Mirto asks whether to send usage statistics. The default answer is **no**; closing the window
without choosing means you are asked again next time. You can change your answer any time in
**Settings → General**, where **What is sent…** shows the exact message. If no statistics server is configured,
Mirto does not ask and sends nothing.

- **If you say yes**, once a day Mirto sends:

  ```json
  {"id": "3f9c…", "version": "0.2.0", "os": "macOS 15", "lang": "en", "cameras": "2-4",
   "nodes": "1", "recording": "events", "features": ["telegram"]}
  ```

  `id` is a randomly generated number created when you say yes. It is not derived from your computer, your name or
  any account, but it stays the same until you renew it, so the data is **pseudonymous** rather than strictly
  anonymous. **New random ID** replaces it, and both a new ID and withdrawing consent **delete the old ID's data
  from the server**. Cameras and nodes are sent in ranges (0, 1, 2-4, 5-9, 10+). `recording` and `features` say which
  recording mode and which features are switched on (Telegram, email, SMS, presence, schedule, microphone, node
  actions, deterrents, categories other than people).
- **If you say no**, Mirto sends **once** the message `{"version": "0.2.0"}`. It carries no ID and no data about
  your computer, so people who decline are counted without being recognisable.
- **Never sent**: photos, video, audio, camera or node names, addresses (IP, MAC, URLs), Telegram, email or SMS
  data, events, thresholds or any other setting value beyond the list above.

Legal basis: your consent (GDPR art. 6(1)(a)), which you can withdraw at any time in Settings.

## Update check

Unless you switch it off in Settings → General, once a day Mirto asks the same server which version is the latest
(`GET /v1/latest`). The only data in the request is the Mirto version (in the User-Agent header). Mirto tells
you once when a newer version is out. This also happens before you answer the statistics question.

## Recognition models

The models that recognise people, bird species and sounds are not bundled with Mirto: the first time you
use one, Mirto downloads it from **github.com** (people and objects, bird species) or **huggingface.co**
(sounds), once, and verifies it against a SHA-256 written in the source. Those servers see what any download
shows them — your IP address, the time and which file you asked for — exactly as if you had clicked the link
in a browser. **Nothing of yours goes with the request**: no identifier, no statistics, no setting. If you
would rather not have that connection at all, put the files in the models folder yourself (or point
`MIRTO_MODELS_DIR` at them) before switching recognition on, and Mirto will not download anything.

## The server

A Cloudflare Worker with a D1 database (`tools/stats-worker`, open source like the rest). Our code never reads or
stores IP addresses and request logs are disabled; Cloudflare, as hosting provider (processor), handles the IP
address only to deliver the request. Data is deleted 13 months after an installation was last seen (for an
installation that is still active, only the date of its first message is kept longer). Only the project
maintainer can see the totals.

Data controller: the Mirto maintainer, Marco Dalicco. Contact: open an issue on the project's GitHub page, or
use the private channel described in [`SECURITY.md`](SECURITY.md).
