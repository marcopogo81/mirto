# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Bot Telegram: canale principale degli avvisi e comandi remoti.

* Invia la foto dell'evento nel gruppo "Mirto Admin" con i pulsanti
  "Falso allarme", "Silenzia 1 ora" e "Ferma tutto".
* Riceve i comandi con il *long polling*: è Mirto a contattare Telegram,
  nessuna porta viene aperta sul router.
* Accetta comandi e pulsanti SOLO dagli utenti autorizzati (ID Telegram).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .httputil import multipart, request
from ..i18n import N_, _

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
VIDEO_MAX_BYTES = 45_000_000  # limite dei bot Telegram: 50 MB per file
MAX_MESSAGE_AGE = 60  # secondi: i comandi più vecchi vengono ignorati (es. inviati a Mac spento)

HELP = (
    N_("Comandi Mirto:\n/stato – camere, nodi e stato della protezione\n/foto [camera] – foto istantanea\n/pausa [minuti] – sospende gli avvisi (default 60)\n/riprendi – riattiva gli avvisi\n/attiva, /disattiva – attiva o disattiva la protezione (anche /inserisci, /disinserisci)\n/ferma – spegne subito tutte le azioni attive\n/programma – orari automatici (/programma sospendi, /programma riattiva)\n/ascolta [secondi] – registra l'audio dal nodo\n/clip [camera] [secondi] – video degli ultimi secondi (default 30)\n/id – mostra gli ID di chat e utente (per la configurazione)\nUn messaggio vocale viene riprodotto dall'altoparlante del nodo.")
)


# Comandi in inglese: stessi effetti dei comandi italiani
ALIASES = {
    "/status": "/stato", "/photo": "/foto", "/snapshot": "/foto", "/pause": "/pausa", "/resume": "/riprendi",
    "/arm": "/attiva", "/disarm": "/disattiva", "/stop": "/ferma", "/schedule": "/programma",
    "/listen": "/ascolta",
    "/video": "/clip",
}

# Menu dei comandi impostato dal bot stesso (niente /setcommands in BotFather):
# italiano di serie, inglese per chi ha Telegram in inglese.
COMMANDS_IT = [
    ("stato", "Stato di camere e sistema"), ("foto", "Foto istantanea (es. /foto ingresso)"),
    ("attiva", "Attiva la protezione (avvisi e azioni)"), ("disattiva", "Disattiva la protezione"),
    ("pausa", "Sospende gli avvisi (minuti, default 60)"), ("riprendi", "Riattiva gli avvisi"),
    ("ferma", "Ferma tutto e disinserisce"), ("programma", "Orari automatici (sospendi / riattiva)"),
    ("ascolta", "Audio dal nodo (secondi)"),
    ("clip", "Video degli ultimi secondi (es. /clip ingresso 30)"), ("id", "Mostra gli ID di chat e utente"), ("aiuto", "Elenco comandi"),
]
COMMANDS_EN = [
    ("status", "Cameras, nodes and protection status"), ("photo", "Instant photo (e.g. /photo entrance)"),
    ("arm", "Turn protection on (alerts and actions)"), ("disarm", "Turn protection off"),
    ("pause", "Pause alerts (minutes, default 60)"), ("resume", "Resume alerts"),
    ("stop", "Stop everything and disarm"), ("schedule", "Automatic times (pause / resume)"),
    ("listen", "Audio from the node (seconds)"),
    ("video", "Video of the last seconds (e.g. /video entrance 30)"), ("id", "Show chat and user IDs"), ("help", "List of commands"),
]


class TelegramError(RuntimeError):
    pass


class TelegramRateLimited(TelegramError):
    """Telegram chiede di aspettare prima di riprovare (``parameters.retry_after``).

    È un errore diverso dagli altri: non c'è niente di rotto, c'è solo da aspettare.
    Chi invia può riprovare lo stesso messaggio, con gli stessi pulsanti.
    """

    def __init__(self, retry_after: float, description: str = ""):
        super().__init__(description or f"troppi messaggi: riprova fra {float(retry_after):.0f} s")
        self.retry_after = float(retry_after)


CONFLICT_GRACE = 60  # secondi dopo l'avvio in cui un «Conflict» è solo il bot precedente che chiude


def message_link(chat_id: str | int, message_id: int) -> str | None:
    """Link al messaggio per i supergruppi privati (``-100…``): https://t.me/c/<id>/<msg>."""
    cid = str(chat_id)
    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/{message_id}"
    return None


class TelegramAPI:
    def __init__(self, token: str, timeout: float = 40):
        self.token = token
        self.timeout = timeout

    def call(self, method: str, params: dict | None = None, files: dict | None = None,
             timeout: float | None = None):
        url = f"{API}/bot{self.token}/{method}"
        params = params or {}
        if files:
            fields = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in params.items()}
            body, ctype = multipart(fields, files)
        else:
            body, ctype = json.dumps(params).encode(), "application/json"
        resp = request("POST", url, data=body, headers={"Content-Type": ctype}, timeout=timeout or self.timeout)
        try:
            data = resp.json()
        except Exception as exc:
            raise TelegramError(_('risposta non valida ({status})', status=resp.status)) from exc
        if not data or not data.get("ok"):
            descrizione = f"{method}: {data.get('description') if data else resp.status}"
            attesa = (data or {}).get("parameters", {}).get("retry_after")
            if attesa is not None:
                raise TelegramRateLimited(attesa, descrizione)
            raise TelegramError(descrizione)
        return data["result"]

    def download_file(self, file_id: str) -> bytes:
        info = self.call("getFile", {"file_id": file_id})
        resp = request("GET", f"{API}/file/bot{self.token}/{info['file_path']}", timeout=60)
        if resp.status != 200:
            raise TelegramError(_('download fallito ({status})', status=resp.status))
        return resp.body


class BotHandlers:
    """Operazioni che il bot può chiedere al resto del programma (implementate dal nucleo)."""

    def status_text(self) -> str: return _('Stato non disponibile.')
    def snapshot(self, camera_query: str) -> tuple[bytes, str] | None: return None
    def pause(self, minutes: float) -> str: return _('Non disponibile.')
    def resume(self) -> str: return _('Non disponibile.')
    def arm(self, armed: bool) -> str: return _('Azioni non configurate.')
    def stop_all(self) -> str: return _('Azioni non configurate.')
    def listen(self, seconds: int) -> tuple[bytes, str] | None: return None
    def play_voice(self, audio: bytes, mime: str) -> str: return _('Audio non configurato.')
    def false_alarm(self, event_id: str) -> str: return _('Non disponibile.')
    def mute_event_camera(self, event_id: str, seconds: float) -> str: return _('Non disponibile.')
    def confirm_action(self, token: str, user: str) -> str: return _('Non disponibile.')
    def schedule(self, arg: str) -> str: return _('Programmazione non disponibile.')
    def clip(self, camera_query: str, seconds: int) -> tuple[bytes, str, str] | str: return _('Registrazione non disponibile.')


class TelegramBot:
    def __init__(self, api: TelegramAPI, chat_id: str, allowed_user_ids: list, handlers: BotHandlers,
                 sleep: Callable[[float], None] | None = None):
        self.api = api
        self.chat_id = str(chat_id)
        self.sent_events: dict[str, int] = {}  # evento -> messaggio della foto (per agganciare la clip)
        self.allowed = {int(u) for u in allowed_user_ids or []}
        self.handlers = handlers
        # L'attesa fra due tentativi si interrompe appena qualcuno ferma il bot: altrimenti
        # stop() resterebbe a guardare un thread che dorme, e con Telegram irraggiungibile
        # salvare le impostazioni terrebbe ferma la finestra per tutto il backoff.
        self._sleep = sleep if sleep is not None else (lambda secondi: self._stop.wait(secondi))
        self._offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="telegram-cmd")
        self.online = False
        self.state = "starting"  # starting → online / offline (per mostrare "collegamento…" all'avvio)
        self.clock = time.time
        self.info: dict = {}      # nome del bot, gruppo, utenti autorizzati (per l'interfaccia)
        self._info_at = 0.0
        self._started_at = 0.0    # per distinguere un «Conflict» d'avvio da uno vero (vedi _run)

    # --- informazioni per l'interfaccia ---------------------------------------
    INFO_EVERY = 900  # secondi tra due aggiornamenti delle informazioni

    @staticmethod
    def _person(user: dict) -> str:
        name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
        return name or (f"@{user['username']}" if user.get("username") else str(user.get("id", "")))

    def refresh_info(self) -> dict:
        """Legge da Telegram nome del bot, gruppo e nomi degli utenti autorizzati.

        Telegram non dice ai bot chi è online: qui ci sono solo nomi e appartenenza al gruppo.
        """
        info: dict = {"updated": self.clock(), "users": []}
        try:
            me = self.api.call("getMe", timeout=15)
            info["bot"] = f"@{me.get('username', '')}"
            info["bot_name"] = me.get("first_name", "")
            info["privacy_off"] = bool(me.get("can_read_all_group_messages"))
        except Exception as exc:  # noqa: BLE001
            info["error"] = str(exc)
        chat_id = self.chat_id
        if chat_id:
            try:
                chat = self.api.call("getChat", {"chat_id": chat_id}, timeout=15)
                info["chat_type"] = chat.get("type", "")
                info["chat_title"] = chat.get("title") or self._person(chat)
            except Exception as exc:  # noqa: BLE001
                info["chat_error"] = str(exc)
            if info.get("chat_type") in ("group", "supergroup"):
                try:
                    info["members"] = int(self.api.call("getChatMemberCount", {"chat_id": chat_id}, timeout=15))
                except Exception:  # noqa: BLE001
                    pass
        for uid in sorted(self.allowed):
            u: dict = {"id": uid, "name": str(uid), "in_group": None}
            try:
                if chat_id:
                    m = self.api.call("getChatMember", {"chat_id": chat_id, "user_id": uid}, timeout=15)
                    u["name"] = self._person(m.get("user", {}))
                    u["username"] = m.get("user", {}).get("username", "")
                    u["in_group"] = m.get("status") not in ("left", "kicked")
                    u["admin"] = m.get("status") in ("creator", "administrator")
                else:
                    chat = self.api.call("getChat", {"chat_id": uid}, timeout=15)
                    u["name"] = self._person(chat)
                    u["username"] = chat.get("username", "")
            except Exception:  # noqa: BLE001  (utente mai visto dal bot o ID sbagliato)
                u["unknown"] = True
            info["users"].append(u)
        self.info = info
        self._info_at = self.clock()
        return info

    def refresh_info_async(self) -> None:
        try:
            self._workers.submit(self._safe_refresh)
        except RuntimeError:  # bot fermato
            pass

    def _safe_refresh(self) -> None:
        try:
            self.refresh_info()
        except Exception:  # noqa: BLE001
            log.exception("Telegram: lettura delle informazioni non riuscita")

    # --- invio ------------------------------------------------------------
    def send_event(self, ev, extra_buttons: list[tuple[str, str]] | None = None) -> str | None:
        """Invia la foto dell'evento. Restituisce il link al messaggio (se supergruppo)."""
        buttons = [[{"text": _('Falso allarme'), "callback_data": f"fa:{ev.id}"},
                    {"text": _("Silenzia 1 ora"), "callback_data": f"mu:{ev.id}"}]]
        for text, data in extra_buttons or []:
            buttons.append([{"text": text, "callback_data": data}])
        buttons.append([{"text": _('⛔ Ferma tutto'), "callback_data": "st"}])
        head = _('🧪 Avviso di PROVA (nessun allarme reale)') if getattr(ev, "is_test", False) else (ev.headline() if hasattr(ev, "headline") else _('🚨 Persona rilevata'))
        caption = _('{head}\nCamera: {camera_name}\nOra: {when_text}\nConfidenza: {score:.0%}', head=head, camera_name=ev.camera_name, when_text=ev.when_text(), score=ev.score)
        msg = self.api.call(
            "sendPhoto",
            {"chat_id": self.chat_id, "caption": caption, "reply_markup": {"inline_keyboard": buttons}},
            files={"photo": (ev.filename(), ev.jpeg(), "image/jpeg")},
        )
        self.sent_events[ev.id] = msg["message_id"]  # la clip arriverà come risposta a questa foto
        while len(self.sent_events) > 200:
            self.sent_events.pop(next(iter(self.sent_events)))
        return message_link(self.chat_id, msg["message_id"])

    def send_video(self, chat_id, data: bytes, filename: str, caption: str, reply_to: int | None = None) -> None:
        params = {"chat_id": chat_id, "caption": caption, "supports_streaming": "true"}
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        self.api.call("sendVideo", params, files={"video": (filename, data, "video/mp4")}, timeout=180)

    def send_event_clip(self, event_id: str, path, caption: str) -> bool:
        """Manda la clip di un evento come risposta alla sua foto. False se la foto non è (ancora) partita."""
        msg_id = self.sent_events.get(event_id)
        if not msg_id:
            return False
        from pathlib import Path  # noqa: PLC0415

        p = Path(path)
        if p.stat().st_size > VIDEO_MAX_BYTES:
            log.info("Clip %s troppo grande per Telegram (%d MB)", p.name, p.stat().st_size // 1_000_000)
            return True
        self.send_video(self.chat_id, p.read_bytes(), p.name, caption, reply_to=msg_id)
        return True

    def send_text(self, text: str, buttons: list[list[dict]] | None = None) -> None:
        params = {"chat_id": self.chat_id, "text": text}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        self.api.call("sendMessage", params)

    def send_photo(self, chat_id, data: bytes, caption: str) -> None:
        self.api.call("sendPhoto", {"chat_id": chat_id, "caption": caption},
                      files={"photo": ("foto.jpg", data, "image/jpeg")})

    # --- ricezione --------------------------------------------------------
    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="telegram", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ferma il bot e aspetta che il suo thread sia davvero finito.

        Senza sbloccarlo, ``getUpdates`` resta appeso fino a 30 secondi: un bot nuovo con lo
        stesso token riceverebbe «Conflict» per tutto quel tempo, e l'utente leggerebbe
        «un'altra copia di Mirto usa lo stesso bot» mentre di copie ce n'è una sola.
        Una chiamata con ``timeout: 0`` fa tornare subito quella appesa.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        self._workers.shutdown(wait=False, cancel_futures=True)
        try:
            self.api.call("getUpdates", {"offset": self._offset, "timeout": 0}, timeout=10)
        except Exception:  # noqa: BLE001  (serve solo a sbloccare: se fallisce si aspetta il timeout)
            pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(2)

    def set_commands(self) -> None:
        """Imposta il menu dei comandi (tocca "/" nella chat), in italiano e in inglese."""
        for lang, cmds in ((None, COMMANDS_IT), ("en", COMMANDS_EN)):
            params = {"commands": [{"command": c, "description": d} for c, d in cmds]}
            if lang:
                params["language_code"] = lang
            try:
                self.api.call("setMyCommands", params, timeout=15)
            except Exception as exc:  # noqa: BLE001  (il menu è una comodità: mai bloccare il bot)
                log.info("Telegram: menu dei comandi non impostato (%s)", exc)
                return

    def _skip_backlog(self) -> None:
        """All'avvio scarta i comandi arrivati mentre Mirto era spento (es. un vecchio /attiva)."""
        try:
            last = self.api.call("getUpdates", {"offset": -1, "timeout": 0}, timeout=15)
            if last:
                self._offset = last[-1]["update_id"] + 1
                self.api.call("getUpdates", {"offset": self._offset, "timeout": 0}, timeout=15)  # conferma
                log.info("Telegram: ignorati i comandi arrivati mentre Mirto era spento")
            self.online, self.state = True, "online"
        except Exception as exc:
            log.warning("Telegram: impossibile scartare i comandi arretrati: %s", exc)

    @staticmethod
    def _urgent(upd: dict) -> bool:
        """Pulsanti e /ferma non devono mai aspettare in coda dietro comandi lenti (audio, foto, ecc.).

        La gestione di un pulsante è immediata (la sirena parte in un altro thread), quindi si fa subito:
        altrimenti Telegram mostra la rotellina finché la coda non si libera.
        """
        if "callback_query" in upd:
            return True
        text = (upd.get("message", {}).get("text") or "").strip().lower()
        return text.startswith(("/ferma", "/stop"))

    def _run(self) -> None:
        backoff = 2.0
        self._started_at = self.clock()
        self._skip_backlog()
        self.set_commands()
        while not self._stop.is_set():
            try:
                updates = self.api.call(
                    "getUpdates",
                    {"offset": self._offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]},
                    timeout=45,
                )
                self.online = True
                self.state = "online"
                backoff = 2.0
                if self.clock() - self._info_at > self.INFO_EVERY:
                    self._info_at = self.clock()
                    self.refresh_info_async()
                for upd in updates:
                    if self._stop.is_set():  # bot sostituito o fermato: non gestire più nulla
                        return
                    self._offset = max(self._offset, upd["update_id"] + 1)
                    if self._urgent(upd):
                        self._safe_handle(upd)
                    else:
                        self._workers.submit(self._safe_handle, upd)
            except Exception as exc:
                if "Conflict" in str(exc):
                    # Un'altra copia del bot legge i messaggi. Nei primi secondi dopo un avvio è
                    # normale (è quella di prima che sta chiudendo); dopo, è un vero doppione.
                    if self.clock() - self._started_at < CONFLICT_GRACE:
                        log.info("Telegram: il bot precedente sta ancora chiudendo, riprovo")
                    else:
                        log.warning("Telegram: un'altra copia di Mirto usa lo stesso bot. Tieni aperto un solo Mirto.")
                else:
                    self.online = False
                    self.state = "offline"
                    log.warning("Telegram non raggiungibile: %s", exc)
                self._sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _safe_handle(self, upd: dict) -> None:
        try:
            self.handle_update(upd)
        except Exception:
            log.exception("Errore nella gestione di un aggiornamento Telegram")

    def authorized(self, user_id: int | None) -> bool:
        return user_id is not None and int(user_id) in self.allowed

    def handle_update(self, upd: dict) -> None:
        if "callback_query" in upd:
            self._handle_callback(upd["callback_query"])
        elif "message" in upd:
            self._handle_message(upd["message"])

    def _reply(self, chat_id, text: str) -> None:
        self.api.call("sendMessage", {"chat_id": chat_id, "text": text})

    def _handle_message(self, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        user = msg.get("from", {})
        if msg.get("date") and self.clock() - float(msg["date"]) > MAX_MESSAGE_AGE:
            log.warning("Comando Telegram ignorato perché troppo vecchio")
            return
        text = (msg.get("text") or "").strip()
        cmd, _sep, arg = text.partition(" ")
        cmd = ALIASES.get(cmd.split("@")[0].lower(), cmd.split("@")[0].lower())
        if cmd == "/id":  # utile in configurazione, non rivela dati della casa
            self._reply(chat_id, _('ID chat: {chat_id}\nIl tuo ID utente: {get}', chat_id=chat_id, get=user.get('id')))
            return
        if not self.authorized(user.get("id")):
            if text.startswith("/") or "voice" in msg:
                log.warning("Comando Telegram rifiutato da utente non autorizzato %s", user.get("id"))
                self._reply(chat_id, _('Utente non autorizzato.'))
            return
        if "voice" in msg or "audio" in msg:
            item = msg.get("voice") or msg.get("audio")
            audio = self.api.download_file(item["file_id"])
            self._reply(chat_id, self.handlers.play_voice(audio, item.get("mime_type", "audio/ogg")))
            return
        h = self.handlers
        if cmd in ("/start", "/aiuto", "/help"):
            self._reply(chat_id, _(HELP))
        elif cmd == "/stato":
            self._reply(chat_id, h.status_text())
        elif cmd == "/foto":
            shot = h.snapshot(arg.strip())
            if shot:
                self.send_photo(chat_id, *shot)
            else:
                self._reply(chat_id, _('Nessuna immagine disponibile per questa camera.'))
        elif cmd == "/pausa":
            self._reply(chat_id, h.pause(float(arg) if arg.strip().replace(".", "", 1).isdigit() else 60))
        elif cmd == "/riprendi":
            self._reply(chat_id, h.resume())
        elif cmd in ("/inserisci", "/attiva"):
            self._reply(chat_id, h.arm(True))
        elif cmd in ("/disinserisci", "/disattiva"):
            self._reply(chat_id, h.arm(False))
        elif cmd == "/ferma":
            self._reply(chat_id, h.stop_all())
        elif cmd == "/programma":
            self._reply(chat_id, h.schedule(arg))
        elif cmd == "/ascolta":
            secs = int(arg) if arg.strip().isdigit() else 30
            clip = h.listen(min(max(secs, 5), 120))
            if clip:
                data, mime = clip
                self.api.call("sendAudio", {"chat_id": chat_id, "caption": _('Audio dal nodo ({secs} s)', secs=secs)},
                              files={"audio": ("ascolto.wav" if "wav" in mime else "ascolto.ogg", data, mime)})
            else:
                self._reply(chat_id, _('Audio non disponibile.'))
        elif cmd == "/clip":
            words = arg.split()
            secs = int(words.pop()) if words and words[-1].isdigit() else 30
            res = h.clip(" ".join(words), min(max(secs, 5), 120))
            if isinstance(res, tuple):
                data, caption, name = res
                self.send_video(chat_id, data, name, caption)
            else:
                self._reply(chat_id, res or _('Video non disponibile.'))
        elif text.startswith("/"):
            self._reply(chat_id, _('Comando sconosciuto. Scrivi /aiuto.'))

    def _handle_callback(self, cq: dict) -> None:
        user = cq.get("from", {})
        data = cq.get("data", "")
        if not self.authorized(user.get("id")):
            log.warning("Pulsante Telegram da utente non autorizzato: %s", user.get("id"))
            self.api.call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": _('Utente non autorizzato')})
            return
        kind, _sep, value = data.partition(":")
        log.info("Pulsante Telegram '%s' premuto da %s", kind, user.get("first_name") or user.get("id"))
        h = self.handlers
        # Telegram deve SEMPRE ricevere una risposta, altrimenti il pulsante resta "in caricamento"
        try:
            if kind == "fa":
                answer = h.false_alarm(value)
            elif kind == "mu":
                answer = h.mute_event_camera(value, 3600)
            elif kind == "st":
                answer = h.stop_all()
            elif kind == "ok":
                answer = h.confirm_action(value, user.get("first_name") or str(user.get("id")))
            else:
                answer = _('Pulsante sconosciuto')
        except Exception as exc:  # noqa: BLE001
            log.exception("Pulsante Telegram %s non gestito", kind)
            answer = _('Errore: {exc}', exc=exc)
        try:
            self.api.call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": str(answer)[:190]})
        except Exception as exc:  # noqa: BLE001 (pulsante troppo vecchio: Telegram rifiuta la risposta)
            log.warning("Risposta al pulsante non consegnata: %s", exc)
        chat_id = cq.get("message", {}).get("chat", {}).get("id")
        if chat_id is not None:
            self._reply(chat_id, f"{user.get('first_name', 'Admin')}: {answer}")
