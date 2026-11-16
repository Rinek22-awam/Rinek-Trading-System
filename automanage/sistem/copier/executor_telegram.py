"""Telethon transport for the synchronous executor and its broker guard."""
import asyncio
from concurrent.futures import Future
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
from queue import Empty, Queue
import sqlite3
from threading import Event, Thread
import time

from telethon import Button, TelegramClient, errors, events, functions, types

log = logging.getLogger("copier.executor_telegram")
NETWORK_ERRORS = (OSError, RuntimeError, ValueError, errors.RPCError)


class ExecutorTelegram:
    """Keep network calls off the thread that owns the broker and trading journal."""

    def __init__(self, role, token, api_id, api_hash, inbox, admin):
        digest = hashlib.sha256(token.encode()).hexdigest()[:16]
        self.path = Path("data") / f"telegram-{role}-{digest}"
        self.token, self.api_id, self.api_hash = token, api_id, api_hash
        self.inbox, self.admin = inbox, admin
        self.outbox = Queue()
        self.stopping = Event()
        self.connected = Event()
        self.thread = Thread(target=self._thread_main, name=f"telegram-{role}", daemon=True)

    def __enter__(self):
        self.path.parent.mkdir(exist_ok=True)
        self.db_path = self.path.with_suffix(".db")
        self.db = sqlite3.connect(self.db_path, timeout=0.1)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS updates (event_key TEXT UNIQUE, payload TEXT)"
            )
            self.db.execute("CREATE INDEX IF NOT EXISTS pending_updates ON updates(event_key) WHERE payload IS NOT NULL")
            self.db.commit()
            self.thread.start()
        except BaseException:
            self.db.close()
            raise
        return self

    def __exit__(self, *exc):
        deadline = time.monotonic() + 1
        while self.connected.is_set() and self.outbox.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.05)
        self.stopping.set()
        self.thread.join(timeout=3)
        self.db.close()

    def pop_update(self):
        # Claim before dispatch, like the former Bot API offset. A callback
        # that can place an order must never be replayed after an uncertain exit.
        with self.db:
            row = self.db.execute(
                "SELECT rowid, payload FROM updates WHERE payload IS NOT NULL ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE updates SET payload = NULL WHERE rowid = ?", (row[0],))
        return json.loads(row[1])

    def _queue(self, method, *args):
        future = Future()
        self.outbox.put((future, method, args))
        return future

    def send(self, chat_id, text, buttons=None):
        return self._queue("send", chat_id, text, buttons)

    def edit(self, chat_id, message_id, text):
        self._queue("edit", chat_id, message_id, text)

    def answer(self, query_id):
        self._queue("answer", query_id)

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            log.error("Koneksi Telegram eksekutor berhenti: %s", self._safe_error(exc))

    def _safe_error(self, error):
        return str(error).replace(self.token, "[token]").replace(self.api_hash, "[api hash]")

    @staticmethod
    def _store(db, key, update):
        with db:
            db.execute("INSERT OR IGNORE INTO updates VALUES (?, ?)", (key, json.dumps(update)))

    async def _run(self):
        logging.getLogger("telethon").setLevel(logging.WARNING)
        client = TelegramClient(self.path.with_suffix(".session"), self.api_id, self.api_hash,
                                catch_up=True, sequential_updates=True, flood_sleep_threshold=0)
        with closing(sqlite3.connect(self.db_path)) as db:
            async def store(key, update):
                while not self.stopping.is_set():
                    try:
                        self._store(db, key, update)
                        return
                    except sqlite3.OperationalError as exc:
                        log.error("Update Telegram belum bisa disimpan: %s", exc)
                        await asyncio.sleep(1)

            @client.on(events.NewMessage(incoming=True))
            async def message(event):
                if event.is_channel and event.chat_id == self.inbox:
                    kind = "channel_post"
                elif event.is_private and event.chat_id == self.admin and event.sender_id == self.admin:
                    kind = "message"
                else:
                    return
                payload = {"chat": {"id": event.chat_id}, "from": {"id": event.sender_id},
                           "message_id": event.id, "date": event.date.timestamp(), "text": event.raw_text}
                await store(f"message:{event.chat_id}:{event.id}", {kind: payload})

            @client.on(events.CallbackQuery())
            async def callback(event):
                if not (event.is_private and event.chat_id == self.admin and event.sender_id == self.admin):
                    return
                query_id = str(event.query.query_id)
                payload = {"id": query_id, "from": {"id": event.sender_id},
                           "data": (event.data or b"").decode("utf-8", errors="replace"),
                           "message": {"message_id": event.message_id}}
                await store(f"callback:{query_id}", {"callback_query": payload})

            tasks = [asyncio.create_task(coro) for coro in (self._connect(client), self._deliver(client))]
            try:
                while not self.stopping.is_set() and not any(task.done() for task in tasks):
                    await asyncio.sleep(0.1)
                for task in tasks:
                    if task.done():
                        task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _connect(self, client):
        offline = False
        while not self.stopping.is_set():
            try:
                await client.start(bot_token=self.token)
                self.connected.set()
                if offline:
                    log.info("Telegram eksekutor tersambung kembali.")
                    offline = False
                else:
                    log.info("Telegram eksekutor terhubung lewat Telethon.")
                await client.run_until_disconnected()
                raise ConnectionError("Sesi Telegram terputus.")
            except (errors.AccessTokenInvalidError, errors.AccessTokenExpiredError, errors.ApiIdInvalidError):
                raise ValueError("Token bot atau API ID Telegram ditolak.") from None
            except NETWORK_ERRORS as exc:
                if not offline:
                    log.warning("Koneksi Telegram eksekutor terganggu: %s", self._safe_error(exc))
                    offline = True
            finally:
                self.connected.clear()
                await client.disconnect()
            await asyncio.sleep(2)

    async def _deliver(self, client):
        while not self.stopping.is_set():
            if not self.connected.is_set() or not client.is_connected():
                await asyncio.sleep(0.1)
                continue
            try:
                future, method, args = self.outbox.get_nowait()
            except Empty:
                await asyncio.sleep(0.1)
                continue
            try:
                if future.set_running_or_notify_cancel():
                    result = await asyncio.wait_for(self._request(client, method, args), timeout=15)
                    future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)
                if method != "answer":
                    log.warning("Telegram eksekutor gagal %s: %s", method, self._safe_error(exc))
            finally:
                self.outbox.task_done()

    @staticmethod
    async def _request(client, method, args):
        if method == "answer":
            await client(functions.messages.SetBotCallbackAnswerRequest(query_id=int(args[0])))
            return None
        if method == "edit":
            chat_id, message_id, text = args
            try:
                await client.edit_message(chat_id, message_id, text,
                                          buttons=types.ReplyInlineMarkup([]), parse_mode=None)
                return None
            except errors.MessageNotModifiedError:
                return None
            except (errors.MessageIdInvalidError, errors.MessageEditTimeExpiredError, ValueError):
                return await client.send_message(chat_id, text, parse_mode=None)
        chat_id, text, buttons = args
        markup = [[Button.inline(label, data) for label, data in row] for row in buttons] if buttons else None
        message = await client.send_message(chat_id, text, buttons=markup, parse_mode=None)
        return {"message_id": message.id}
