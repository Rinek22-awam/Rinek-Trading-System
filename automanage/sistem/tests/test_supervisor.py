import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from copier import supervisor


def controller(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    active = {}
    monkeypatch.setattr(supervisor, "is_running", lambda port: port in active)
    monkeypatch.setattr(supervisor, "running_session", active.get)
    bot = Mock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(id=7))
    return supervisor.Supervisor(bot, 42), bot, active


def message(text, sender=42, private=True):
    return SimpleNamespace(chat_id=42, sender_id=sender, is_private=private, raw_text=text)


def test_only_admin_can_use_menu_and_click_once(monkeypatch, tmp_path):
    control, bot, _ = controller(monkeypatch, tmp_path)
    asyncio.run(control.handle(message("/restart", sender=99)))
    asyncio.run(control.handle(message("/restart", private=False)))
    bot.send_message.assert_not_called()
    asyncio.run(control.handle(message("halo")))
    assert supervisor.HELP in bot.send_message.call_args.args[1]
    asyncio.run(control.handle(message("/restart")))
    buttons = bot.send_message.call_args.kwargs["buttons"]
    assert len(buttons) == 4
    first_button = buttons[0][0]
    data = getattr(first_button, "type", first_button).data
    click = SimpleNamespace(chat_id=42, sender_id=99, is_private=True, data=data,
                            message_id=7, answer=AsyncMock(), edit=AsyncMock())
    begin = Mock(return_value="Sedang restart")
    monkeypatch.setattr(control, "begin", begin)
    asyncio.run(control.choose(click))
    begin.assert_not_called()
    click.sender_id = 42
    asyncio.run(control.choose(click))
    asyncio.run(control.choose(click))
    begin.assert_called_once_with("restart", control.workers[0])
    assert click.edit.call_args.kwargs["buttons"].rows == []
    click.answer.assert_awaited_once_with("Pilihan sudah kedaluwarsa. Buka menu lagi.")


def test_restart_waits_for_old_process_and_new_readiness(monkeypatch, tmp_path):
    control, bot, active = controller(monkeypatch, tmp_path)
    worker = control.workers[0]
    active[worker.port] = "old"
    stop = Mock()
    child = Mock()
    child.poll.return_value = None
    spawn = Mock(return_value=child)
    monkeypatch.setattr(supervisor, "request_stop", stop)
    monkeypatch.setattr(supervisor, "launch_background", spawn)
    monkeypatch.setattr(supervisor, "is_ready", lambda port, session: session == "ready")
    control.begin("restart", worker)
    stop.assert_called_once_with(worker.port, "old")
    control.scan()
    spawn.assert_not_called()
    del active[worker.port]
    control.scan()
    spawn.assert_called_once_with(worker.command, environment=None)
    active[worker.port] = "starting"
    control.scan()
    asyncio.run(control.flush())
    bot.send_message.assert_not_called()
    active[worker.port] = "ready"
    control.scan()
    asyncio.run(control.flush())
    bot.send_message.assert_awaited_once_with(42, "Reader · Siap.", buttons=None, parse_mode=None)


def test_alerts_are_redacted_grouped_and_retryable(monkeypatch, tmp_path):
    monkeypatch.setenv("TG_BOT_TOKEN", "12345:top-secret")
    control, bot, _ = controller(monkeypatch, tmp_path)
    now = 100.0
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: now)
    worker = control.workers[0]
    for number in (1, 2):
        control.record(worker, "2026-11-12 10:00:00", "reader",
                       f"gagal memproses pesan {number} dengan 12345:top-secret", now)
    bot.send_message.side_effect = OSError("offline")
    try:
        asyncio.run(control.flush())
    except OSError:
        pass
    bot.send_message.side_effect = None
    asyncio.run(control.flush())
    text = bot.send_message.call_args.args[1]
    assert "top-secret" not in text and "[rahasia]" in text
    assert "Kejadian dalam periode ini: 2" in text
    control.record(worker, "2026-11-12 10:00:01", "reader",
                   "gagal memproses pesan 3 dengan 12345:top-secret", now)
    asyncio.run(control.flush())
    assert bot.send_message.call_count == 2
    now += supervisor.ALERT_INTERVAL
    asyncio.run(control.flush())
    assert bot.send_message.call_count == 3


def test_bybit_cooldown_blocks_restart_before_stop(monkeypatch, tmp_path):
    control, _, active = controller(monkeypatch, tmp_path)
    worker = control.workers[-1]
    active[worker.port] = "bybit"
    monkeypatch.setattr(supervisor.bybit_cooldown, "remaining", lambda: 480)
    stop = Mock()
    monkeypatch.setattr(supervisor, "request_stop", stop)
    assert "480 detik" in control.begin("restart", worker)
    stop.assert_not_called()


def test_bybit_recovery_is_sent_after_warning_and_clears_status(monkeypatch, tmp_path):
    control, bot, active = controller(monkeypatch, tmp_path)
    worker = control.workers[-1]
    active[worker.port] = "bybit"
    monkeypatch.setattr(supervisor, "is_ready", lambda *_: True)
    log_file = tmp_path / "logs" / "bot-bybit.log"
    log_file.parent.mkdir()
    log_file.write_text(
        "2026-11-12 11:24:46,000 WARNING copier.broker_bybit "
        "Bybit API terganggu · Utama / get_open_orders | timed out\n",
        encoding="utf-8",
    )
    control.scan()
    assert worker.status() == "Terganggu"
    with log_file.open("a", encoding="utf-8") as log:
        log.write("2026-11-12 11:24:48,000 INFO copier.broker_bybit "
                  "Bybit API pulih · Utama / get_open_orders | request berhasil\n")
    control.scan()
    asyncio.run(control.flush())
    sent = [call.args[1] for call in bot.send_message.call_args_list]
    assert "Bybit API terganggu" in sent[0] and "Bybit API pulih" in sent[1]
    assert worker.status() == "Jalan"


def test_sends_startup_status_once_to_admin(monkeypatch, tmp_path):
    _, bot, _ = controller(monkeypatch, tmp_path)
    monkeypatch.setenv("SUPERVISOR_TG_BOT_TOKEN", "123:fake")
    monkeypatch.setenv("TG_ADMIN_USER_ID", "42")
    monkeypatch.setenv("TG_API_ID", "123")
    monkeypatch.setenv("TG_API_HASH", "fake")
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BYBIT_TG_BOT_TOKEN", raising=False)
    monkeypatch.setattr(supervisor, "TelegramClient", lambda *args, **kwargs: bot)
    bot.start = AsyncMock()
    bot.disconnect = AsyncMock()

    async def listen():
        await asyncio.Event().wait()

    bot.run_until_disconnected = AsyncMock(side_effect=listen)
    asyncio.run(asyncio.wait_for(supervisor.run(lambda: bot.send_message.await_count > 0, {}), 3))

    bot.start.assert_awaited_once_with(bot_token="123:fake")
    bot.run_until_disconnected.assert_awaited_once()
    bot.disconnect.assert_awaited_once()
    assert bot.add_event_handler.call_count == 2
    bot.send_message.assert_awaited_once()
    admin, text = bot.send_message.call_args.args
    assert admin == 42
    assert text.startswith("🤖 Bot Pengawas nyala.")
    assert all(f"{name} · Mati" in text for name in ("Reader", "Eksekutor MT5", "Eksekutor Bybit"))
