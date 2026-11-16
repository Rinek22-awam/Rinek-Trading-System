"""Telegram control and operational alerts for the three worker processes."""
import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging
import os
from pathlib import Path
import re
import secrets
from subprocess import Popen
import time

from telethon import Button, TelegramClient, errors, events, types

from . import bybit_cooldown, envfile
from .log_tail import LogTail
from .main import _need
from .process_control import (
    WORKER_PROGRAMS, is_ready, launch_background, log_file, managed, ready, request_stop,
    running_session,
)
from .single import BYBIT_BOT_PORT, SUPERVISOR_PORT, claim, is_running

log = logging.getLogger("supervisor")
HELP = (
    "/status — cek Reader, MT5, dan Bybit\n"
    "/start — pilih bot untuk dinyalakan\n"
    "/stop — pilih bot untuk dihentikan\n"
    "/restart — pilih bot untuk dinyalakan ulang"
)
ACTIONS = {"start": "Nyalakan", "stop": "Hentikan", "restart": "Restart"}
LOG_EVENT = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[,.]\d+ "
    r"(INFO|WARNING|ERROR|CRITICAL) (\S+) (.+)"
)
ALERT_INTERVAL = 60
STOP_TIMEOUT = 300
START_TIMEOUT = 90
TELEGRAM_ERRORS = (OSError, RuntimeError, ValueError, errors.RPCError)


@dataclass
class Alert:
    text: str
    due: float
    count: int = 1


@dataclass
class Operation:
    action: str
    phase: str
    deadline: float
    session: str | None = None
    child: Popen | None = None


class Worker:
    def __init__(self, name, port, log_name, command):
        self.name = name
        self.port = port
        self.command = command
        self.tail = LogTail(Path("logs") / log_name)
        self.active = is_running(port)
        self.last_session = running_session(port)
        self.operation = None
        self.last_error = ""
        self.issue_until = 0
        self.network_issues = set()

    def cooldown(self):
        return bybit_cooldown.remaining() if self.port == BYBIT_BOT_PORT else 0

    def status(self):
        if self.operation:
            return {"start": "Menyalakan", "stop": "Menghentikan", "restart": "Sedang restart"}[
                self.operation.action]
        if seconds := self.cooldown():
            state = "Jalan" if self.active else "Mati"
            return f"{state}, API dijeda {seconds} detik (403)"
        if not self.active:
            return "Mati"
        if self.network_issues or time.monotonic() < self.issue_until:
            return "Terganggu"
        if self.last_session and not is_ready(self.port, self.last_session):
            return "Menyalakan"
        return "Jalan"


@dataclass
class Menu:
    nonce: str
    message_id: int
    action: str
    choices: dict[str, Worker]
    expires: float


class Supervisor:
    def __init__(self, bot, admin, environment=None):
        self.bot = bot
        self.admin = admin
        self.environment = environment
        self.workers = [Worker(*program) for program in WORKER_PROGRAMS]
        self.menu = None
        self.outbox = deque()
        self.alerts = {}
        self.secret_values = [
            value.strip() for key, value in os.environ.items()
            if key.endswith(("TG_BOT_TOKEN", "_PASSWORD", "_API_KEY", "_API_SECRET", "_API_HASH"))
            and len(value.strip()) >= 6
        ]

    def redact(self, text):
        for value in self.secret_values:
            text = text.replace(value, "[rahasia]")
        text = re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{12,}", "[token]", text)
        text = re.sub(r"(https?://[^\s?]+)\?\S+", r"\1?[disamarkan]", text)
        text = re.sub(r"(?i)((?:api[-_ ]?(?:key|secret)|signature|password|token)[\"']?\s*[:=]\s*)"
                      r"[\"']?[^\s,;\]\"']+", r"\1[rahasia]", text)
        return text[:650]

    def record(self, worker, stamp, logger, message, now):
        message = self.redact(message)
        wire_403 = message.startswith("Trafik HTTP Bybit saat kirim request yang kena 403:")
        cooldown_403 = message.startswith("Bybit HTTP 403 pada ")
        if wire_403:
            message += " · Akses API Bybit terganggu."
        worker.last_error = message
        worker.issue_until = now + ALERT_INTERVAL
        fingerprint = re.sub(r"\d+", "#", message)
        key = worker.port, logger, fingerprint
        if wire_403 or cooldown_403:
            key = worker.port, "HTTP 403"
        alert = self.alerts.get(key)
        if alert:
            if cooldown_403:
                return  # The HTTP transport normally logged this same response already.
            if not alert.count:
                alert.text = f"⚠️ {worker.name} · {stamp}\n{message}"
            alert.count += 1
        else:
            self.alerts[key] = Alert(f"⚠️ {worker.name} · {stamp}\n{message}", now)

    def record_bybit_network(self, worker, stamp, level, message):
        headline, _, _ = message.partition(" | ")
        _, _, endpoint = headline.partition(" · ")
        if level == "INFO":
            worker.network_issues.discard(endpoint)
            icon = "✅"
        else:
            worker.network_issues.add(endpoint)
            worker.last_error = self.redact(message)
            icon = "⚠️"
        self.outbox.append(f"{icon} {worker.name} · {stamp}\n{self.redact(message)}")

    def scan(self):
        now = time.monotonic()
        for worker in self.workers:
            was_active = worker.active
            worker.active = is_running(worker.port)
            session = running_session(worker.port)
            if not worker.active or (worker.last_session and session != worker.last_session):
                worker.network_issues.clear()
            for line in worker.tail.read():
                if event := LOG_EVENT.match(line):
                    stamp, level, logger, message = event.groups()
                    if (worker.port == BYBIT_BOT_PORT and logger == "copier.broker_bybit"
                            and message.startswith(("Bybit API terganggu · ", "Bybit API pulih · "))):
                        self.record_bybit_network(worker, stamp, level, message)
                    elif level != "INFO":
                        self.record(worker, stamp, logger, message, now)
            if not worker.active and session and (was_active or session != worker.last_session):
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.record(worker, stamp, "process", "Proses berhenti mendadak.", now)
            worker.last_session = session
            if worker.operation:
                self.advance(worker, session, now)

    async def flush(self):
        while self.outbox:
            await self.send(self.outbox[0])
            self.outbox.popleft()
        now = time.monotonic()
        for key, alert in list(self.alerts.items()):
            if now < alert.due:
                continue
            if not alert.count:
                del self.alerts[key]
                continue
            count = alert.count
            suffix = f"\nKejadian dalam periode ini: {count}." if count > 1 else ""
            await self.send(alert.text + suffix)
            alert.count -= count
            alert.due = now + ALERT_INTERVAL

    def finish(self, worker, text):
        worker.operation = None
        self.outbox.append(f"{worker.name} · {text}")

    def spawn(self, worker, operation, now):
        if seconds := worker.cooldown():
            self.finish(worker, f"API Bybit masih dijeda (403). Coba lagi dalam {seconds} detik.")
            return
        worker.last_error = ""
        try:
            operation.child = launch_background(worker.command, environment=self.environment)
        except OSError as exc:
            self.finish(worker, f"Gagal menyalakan: {self.redact(str(exc))}")
            return
        operation.phase = "starting"
        operation.deadline = now + START_TIMEOUT

    def advance(self, worker, session, now):
        operation = worker.operation
        if operation.phase == "stopping":
            if not worker.active:
                if operation.action == "restart":
                    self.spawn(worker, operation, now)
                else:
                    self.finish(worker, "Berhenti.")
            elif session and session != operation.session:
                self.finish(worker, "Proses sudah diganti dari luar Pengawas.")
            elif now >= operation.deadline:
                self.finish(worker, "Proses belum berhenti. Cek dashboard atau PC.")
            return

        if worker.active and is_ready(worker.port, session):
            self.finish(worker, "Siap.")
        elif operation.child.poll() is not None and not worker.active:
            detail = worker.last_error or f"proses keluar dengan kode {operation.child.returncode}"
            self.finish(worker, f"Gagal menyalakan: {detail}")
        elif now >= operation.deadline:
            self.finish(worker, "Belum siap. Cek log di dashboard.")

    def begin(self, action, worker):
        if worker.operation:
            return "Proses sedang ditangani."
        if action != "stop" and (seconds := worker.cooldown()):
            return f"API Bybit masih dijeda (403). Coba lagi dalam {seconds} detik."
        active = is_running(worker.port)
        if action == "start" and active:
            return "Proses sudah jalan."
        if action == "stop" and not active:
            return "Proses sudah mati."
        now = time.monotonic()
        session = running_session(worker.port) if active else None
        if active and not session:
            return "Proses belum bisa dikontrol. Cek dashboard atau jendela asal."
        operation = worker.operation = Operation(action, "stopping", now + STOP_TIMEOUT, session)
        if active:
            try:
                request_stop(worker.port, session)
            except OSError as exc:
                worker.operation = None
                return f"Gagal meminta proses berhenti: {self.redact(str(exc))}"
        else:
            self.spawn(worker, operation, now)
        return {"start": "Sedang menyalakan…", "stop": "Sedang menghentikan…",
                "restart": "Sedang restart…"}[action]

    def private_admin(self, event):
        return event.is_private and event.chat_id == self.admin and event.sender_id == self.admin

    async def send(self, text, *, buttons=None):
        return await self.bot.send_message(self.admin, text, buttons=buttons, parse_mode=None)

    def status_text(self):
        return "\n".join(f"{worker.name} · {worker.status()}" for worker in self.workers)

    async def handle(self, event):
        if not self.private_admin(event):
            return
        command = (event.raw_text or "").strip()
        if command == "/status":
            self.scan()
            await self.send(self.status_text())
        elif command in ("/start", "/stop", "/restart"):
            self.scan()
            await self.show_menu(command[1:])
        else:
            await self.send("Perintah Pengawas\n\n" + HELP)

    async def show_menu(self, action):
        self.menu = None
        choices = {
            str(index): worker for index, worker in enumerate(self.workers)
            if not worker.operation and (action == "restart" or worker.active == (action == "stop"))
        }
        if not choices:
            await self.send(f"Tidak ada bot yang bisa dipilih untuk /{action}.")
            return
        nonce = secrets.token_hex(6)
        buttons = [[Button.inline(f"{w.name} · {w.status()}", f"{nonce}:{key}")] for key, w in choices.items()]
        buttons.append([Button.inline("Batal", f"{nonce}:cancel")])
        message = await self.send(f"{ACTIONS[action]} bot mana?", buttons=buttons)
        self.menu = Menu(nonce, message.id, action, choices, time.monotonic() + 180)

    async def choose(self, event):
        if not self.private_admin(event):
            return
        nonce, _, key = (event.data or b"").decode("ascii", errors="replace").partition(":")
        menu = self.menu
        if (not menu or menu.nonce != nonce or menu.message_id != event.message_id
                or time.monotonic() >= menu.expires or (key != "cancel" and key not in menu.choices)):
            await event.answer("Pilihan sudah kedaluwarsa. Buka menu lagi.")
            return
        self.menu = None
        if key == "cancel":
            text = "Dibatalkan."
        else:
            worker = menu.choices[key]
            text = f"{worker.name} · {self.begin(menu.action, worker)}"
        await event.edit(text, buttons=types.ReplyInlineMarkup([]), parse_mode=None)


async def watch_workers(supervisor, stopping):
    while not stopping():
        supervisor.scan()
        await asyncio.sleep(0.4)


async def connect(supervisor, token, connected):
    client = supervisor.bot
    while True:
        try:
            await client.start(bot_token=token)
            ready(log, "Bot Pengawas terhubung ke Telegram. Kirim /status di chat pribadi bot.")
            connected.set()
            await client.run_until_disconnected()
        except (errors.AccessTokenInvalidError, errors.AccessTokenExpiredError, errors.ApiIdInvalidError):
            raise ValueError("Token Pengawas atau API ID Telegram ditolak. Cek setelan di MENU.bat dan .env.") from None
        except TELEGRAM_ERRORS as exc:
            log.warning("Koneksi Telegram Pengawas terputus: %s", supervisor.redact(str(exc)))
        finally:
            connected.clear()
            await client.disconnect()
        await asyncio.sleep(2)


async def deliver(supervisor, connected):
    startup_retry_at = time.monotonic()
    while True:
        await connected.wait()
        if startup_retry_at is not None and time.monotonic() >= startup_retry_at:
            try:
                await supervisor.send("🤖 Bot Pengawas nyala.\n\n"
                                      + supervisor.status_text() + "\n\nKirim /status untuk cek lagi.")
            except TELEGRAM_ERRORS as exc:
                log.warning("Pesan awal Pengawas gagal dikirim: %s", supervisor.redact(str(exc)))
                startup_retry_at = time.monotonic() + 30
            else:
                startup_retry_at = None
        try:
            await supervisor.flush()
        except TELEGRAM_ERRORS as exc:
            log.warning("Pesan Pengawas gagal dikirim: %s", supervisor.redact(str(exc)))
            await asyncio.sleep(2)
        await asyncio.sleep(0.4)


async def run(stopping, environment):
    token = _need("SUPERVISOR_TG_BOT_TOKEN")
    bot_id = token.partition(":")[0]
    for key in ("TG_BOT_TOKEN", "BYBIT_TG_BOT_TOKEN"):
        if bot_id == os.getenv(key, "").strip().partition(":")[0]:
            raise SystemExit("Pakai token BotFather khusus Pengawas. Token ini sudah dipakai eksekutor.")
    try:
        admin_id = int(_need("TG_ADMIN_USER_ID"))
        api_id = int(_need("TG_API_ID"))
    except ValueError:
        raise SystemExit("TG_ADMIN_USER_ID dan TG_API_ID harus berupa angka.") from None
    api_hash = _need("TG_API_HASH")

    token_digest = hashlib.sha256(token.encode()).hexdigest()[:16]
    session = Path("data") / f"pengawas-{token_digest}.session"
    client = TelegramClient(session, api_id, api_hash, sequential_updates=True, flood_sleep_threshold=0)
    supervisor = Supervisor(client, admin_id, environment)
    client.add_event_handler(supervisor.handle, events.NewMessage(incoming=True))
    client.add_event_handler(supervisor.choose, events.CallbackQuery())
    connected = asyncio.Event()
    tasks = [asyncio.create_task(coro) for coro in (
        watch_workers(supervisor, stopping), connect(supervisor, token, connected),
        deliver(supervisor, connected),
    )]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main():
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.StreamHandler(), log_file("logs/pengawas.log")])
    environment = os.environ.copy()
    envfile.load()
    logging.getLogger("telethon").setLevel(logging.WARNING)
    lock = claim(SUPERVISOR_PORT)
    if lock is None:
        raise SystemExit("Bot Pengawas sudah jalan di jendela lain.")
    with lock, managed(SUPERVISOR_PORT, log) as stopping:
        try:
            asyncio.run(run(stopping, environment))
        except KeyboardInterrupt:
            pass
        log.info("Bot Pengawas dihentikan.")


if __name__ == "__main__":
    main()
