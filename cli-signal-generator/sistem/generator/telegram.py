import http.client
import json
import logging
import time

from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .settings import DATA, GeneratorError

NETWORK_ERRORS = (URLError, OSError, http.client.HTTPException)


class RetryableSend(GeneratorError):
    def __init__(self, delay=0):
        super().__init__("Koneksi Telegram terganggu atau Telegram sedang sibuk.")
        self.delay = delay


def send_with_retry(sender, chat, message, sleep=time.sleep):
    for attempt in range(1, 4):
        try:
            sender.send(chat, message)
            return
        except RetryableSend as exc:
            if attempt == 3:
                raise
            logging.warning("Kirim Telegram terganggu. Coba lagi (%d/3).", attempt + 1)
            sleep(max(2 ** (attempt - 1), exc.delay))


class BotSender:
    def __init__(self, token):
        if not token:
            raise GeneratorError("GEN_TG_BOT_TOKEN masih kosong.")
        self.token = token

    def call(self, method, **fields):
        try:
            request = Request(
                f"https://api.telegram.org/bot{self.token}/{method}",
                data=json.dumps(fields, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urlopen(request, timeout=12) as response:
                    payload = json.load(response)
            except HTTPError as exc:
                with exc:
                    if exc.code == 429:
                        try:
                            delay = int(json.load(exc).get("parameters", {}).get("retry_after", 1))
                        except (ValueError, TypeError, AttributeError):
                            delay = 1
                        raise RetryableSend(min(delay, 60)) from None
                    if exc.code >= 500:
                        raise RetryableSend() from None
                    try:
                        reason = str(json.load(exc).get("description", ""))[:160]
                    except (ValueError, TypeError, AttributeError):
                        reason = ""
                    raise GeneratorError(explain_refusal(exc.code, reason)) from None
        except NETWORK_ERRORS:
            raise RetryableSend() from None
        except ValueError:
            raise GeneratorError("Token bot atau jawaban Telegram tidak dikenali.") from None
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise GeneratorError("Telegram menolak permintaan. Cek token, ID chat, dan izin bot.")
        return payload.get("result")

    def send(self, chat, message):
        self.call("sendMessage", chat_id=int(chat), text=message)

    def updates(self):
        # Tanpa offset: pembacaan untuk channel dan PM berbagi update yang sama.
        return self.call("getUpdates", allowed_updates=["message", "my_chat_member"], timeout=0) or []

    def find_private_chat(self):
        """ID chat pribadi terakhir yang mengirim pesan ke bot (misal /start). None kalau belum ada."""
        updates = self.updates()
        for update in reversed(updates):
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private" and isinstance(chat.get("id"), int):
                return chat["id"]
        return None


def explain_refusal(code, reason):
    """Pesan penolakan Telegram yang bisa dipahami user (tanpa token)."""
    low = reason.lower()
    if "can't initiate" in low or "blocked by the user" in low or "user is deactivated" in low:
        return "Bot belum bisa mengirim ke chat pribadimu. Buka chat dengan bot generator dan tekan Start (atau buka blokirnya)."
    if "chat not found" in low:
        return "Chat tujuan tidak ditemukan. Cek ID kotak_masuk / laporan_ke, dan bot sudah ditambahkan ke channel."
    if "not enough rights" in low or "have no rights" in low or "not a member" in low:
        return "Bot belum punya izin posting di channel kotak masuk. Jadikan bot admin dengan izin posting."
    if code == 401:
        return "Token bot ditolak Telegram. Cek GEN_TG_BOT_TOKEN di .env."
    return f"Telegram menolak permintaan (HTTP {code}{': ' + reason if reason else ''})."


def wait_for(awaitable, timeout):
    import asyncio

    return asyncio.wait_for(awaitable, timeout=timeout)


class AccountSender:
    def __init__(self, secrets):
        import asyncio   # hanya mode akun; mode bot tidak perlu memuatnya
        from telethon import TelegramClient

        api_id = secrets["GEN_TG_API_ID"]
        api_hash = secrets["GEN_TG_API_HASH"]
        if not api_id.isascii() or not api_id.isdigit() or not api_hash:
            raise GeneratorError("Isi GEN_TG_API_ID (angka) dan GEN_TG_API_HASH di .env.")
        DATA.mkdir(parents=True, exist_ok=True)
        self.loop = asyncio.new_event_loop()
        try:
            self.client = TelegramClient(
                str(DATA / "generator"), int(api_id), api_hash, loop=self.loop,
                timeout=10, request_retries=0, connection_retries=0, flood_sleep_threshold=0,
            )
        except Exception:
            self.loop.close()
            raise GeneratorError("Session Telegram generator tidak bisa dibuka.") from None

    def run(self, coroutine):
        return self.loop.run_until_complete(coroutine)

    async def connect(self):
        if not self.client.is_connected():
            await self.client.connect()
            if not await self.client.is_user_authorized():
                await self.client.disconnect()
                raise GeneratorError("Akun Telegram belum login. Buka menu Telegram → Login akun Telegram.")

    def authorized(self):
        async def check():
            await self.client.connect()
            return await self.client.is_user_authorized()

        try:
            return self.run(wait_for(check(), timeout=15))
        except Exception:
            raise GeneratorError("Status login Telegram tidak bisa diperiksa.") from None

    def login(self):
        try:
            async def sign_in():
                await self.client.start(
                    phone=lambda: input("Nomor HP Telegram (dengan kode negara): "),
                    code_callback=lambda: input("Kode login Telegram: "),
                    password=lambda: input("Password verifikasi dua langkah: "),
                )

            self.run(sign_in())
        except Exception:
            raise GeneratorError("Login akun Telegram gagal. Cek API ID/hash, kode, dan jaringan.") from None

    def close(self):
        try:
            async def disconnect():
                await self.client.disconnect()

            self.run(disconnect())
        except Exception:
            logging.warning("Session Telegram gagal ditutup dengan normal.")
        finally:
            self.loop.close()

    def send(self, chat, message):
        from telethon.errors import FloodWaitError, ServerError

        async def deliver():
            await self.connect()
            if chat == "me":   # laporan_ke kosong di mode akun = Saved Messages
                await self.client.send_message("me", message, parse_mode=None)
                return
            try:
                peer = await self.client.get_input_entity(int(chat))
            except ValueError:
                # Sesi baru perlu mengenali channel dan chat dari daftar dialog.
                await self.client.get_dialogs()
                peer = await self.client.get_input_entity(int(chat))
            await self.client.send_message(peer, message, parse_mode=None)

        try:
            self.run(wait_for(deliver(), timeout=20))
        except FloodWaitError as exc:
            raise RetryableSend(min(exc.seconds, 60)) from None
        except (OSError, ServerError):
            raise RetryableSend() from None
        except GeneratorError:
            raise
        except Exception:
            raise GeneratorError("Akun Telegram gagal mengirim. Cek ID chat dan izin akun.") from None


@contextmanager
def open_sender(config, secrets):
    if config.sender == "bot":
        yield BotSender(secrets["GEN_TG_BOT_TOKEN"])
    else:
        sender = AccountSender(secrets)
        try:
            yield sender
        finally:
            sender.close()
