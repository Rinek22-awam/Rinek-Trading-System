import asyncio
from concurrent.futures import Future
from datetime import datetime, timezone
from threading import Event
import time
from types import SimpleNamespace

from copier import executor_telegram
from . import test_flow_bybit as bybit_flow
from .test_flow import ADMIN, INBOX, confirmed, make, post, press, step


def test_duplicate_events_stay_deduplicated_after_restart(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = Event()

    class Client:
        def __init__(self, session, api_id, api_hash, **options):
            assert options["catch_up"] and options["sequential_updates"]
            self.handlers = []

        def on(self, event):
            return lambda handler: self.handlers.append(handler)

        async def start(self, *, bot_token):
            assert bot_token == "123:fake"

        def is_connected(self):
            return True

        async def disconnect(self):
            pass

        async def send_message(self, chat, text, *, buttons, parse_mode):
            assert chat == ADMIN and text == "testing" and buttons is None and parse_mode is None
            return SimpleNamespace(id=99)

        async def run_until_disconnected(self):
            message, callback = self.handlers
            post = SimpleNamespace(is_channel=True, chat_id=INBOX, sender_id=88, id=5,
                                   date=datetime(2026, 11, 12, tzinfo=timezone.utc), raw_text="SETUP")
            click = SimpleNamespace(is_private=True, chat_id=ADMIN, sender_id=ADMIN, message_id=9,
                                    query=SimpleNamespace(query_id=55), data=b"ya:4")
            for _ in range(2):
                await message(post)
                await callback(click)
            seen.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(executor_telegram, "TelegramClient", Client)
    for first_run in (True, False):
        seen.clear()
        with executor_telegram.ExecutorTelegram("mt5", "123:fake", 10, "hash", INBOX, ADMIN) as bot:
            assert seen.wait(timeout=3)
            if first_run:
                assert bot.send(ADMIN, "testing").result(timeout=3) == {"message_id": 99}
                assert bot.pop_update()["channel_post"]["message_id"] == 5
                callback = bot.pop_update()["callback_query"]
                assert callback["id"] == "55" and callback["message"]["message_id"] == 9
            assert bot.pop_update() is None


def test_guard_moves_sl_while_telegram_is_offline():
    app, mt5, bot, _ = make()
    confirmed(app)
    step(app, mt5, 3989.7)
    mt5.set_price(4000.5)
    bot.pop_update = lambda: None
    deadline = time.monotonic() + 0.5
    app.run_telethon(lambda: time.monotonic() >= deadline
                    or all(position.sl == 3991.0 for position in mt5.positions.values()))
    assert mt5.positions and all(position.sl == 3991.0 for position in mt5.positions.values())


def test_bybit_places_partial_tp_while_telegram_is_offline():
    app, exchange, bot, clock = bybit_flow.make()
    bybit_flow.post(app, bybit_flow.LIMIT)
    bybit_flow.press(app, f"ya:{bybit_flow.latest(app)['id']}")
    exchange.set_price(64790)
    clock.t += 2
    bot.pop_update = lambda: None
    deadline = time.monotonic() + 0.5

    def targets():
        return exchange.placed(reduceOnly=True, orderStatus="New")

    app.run_telethon(lambda: time.monotonic() >= deadline or len(targets()) == 2)
    assert len(targets()) == 2


def test_confirmation_delivery_and_expired_button(monkeypatch):
    app, mt5, bot, clock = make()
    delivery = Future()
    monkeypatch.setattr(bot, "send", lambda *args, **kwargs: delivery)
    post(app)
    row = app.journal.get(1)
    assert row["confirm_deadline"] > clock() and row["confirm_msg_id"] is None
    delivery.set_result({"message_id": 99})
    app._settle_confirmations()
    assert app.journal.get(1)["confirm_msg_id"] == 99

    clock.t = row["confirm_deadline"] + 1
    press(app, "ya:1")
    assert app.journal.get(1)["status"] == "expired"
    assert not mt5.orders
