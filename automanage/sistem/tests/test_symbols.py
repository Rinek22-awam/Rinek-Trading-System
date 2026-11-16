"""Matching setup pair names to a broker's MT5 symbol names."""
import shutil
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from panel import symbols
from panel.settings import Settings
from panel.symbols import candidates, search_names


def sym(name, visible=True, trade_mode=4, description=""):
    return NS(name=name, visible=visible, trade_mode=trade_mode, description=description)


EXNESS_DEMO = [sym("XAUUSDm"), sym("XAUUSD247m", visible=False), sym("XAUEURm"), sym("BTCUSDm"),
               sym("BTCUSDTm"), sym("EURUSDm"), sym("GBPUSDm"), sym("USDJPYm")]
EXNESS_CENT = [sym("XAUUSDc"), sym("BTCUSDc"), sym("EURUSDc"), sym("GBPUSDc"), sym("USDJPYc")]
OTHER = [sym("GOLD"), sym("GOLD.c", trade_mode=0), sym("BTCUSD.raw"), sym("EURUSD.raw"),
         sym("EURUSD.raw2"), sym("GBPUSD"), sym("USDJPY"), sym("XAGUSD")]


def names(key, syms):
    return [s.name for s in candidates(key, syms)]


def test_exness_demo_prefers_plain_suffix_over_247():
    assert names("XAUUSD", EXNESS_DEMO) == ["XAUUSDm", "XAUUSD247m"]
    assert names("BTCUSD", EXNESS_DEMO) == ["BTCUSDm", "BTCUSDTm"]
    assert names("EURUSD", EXNESS_DEMO) == ["EURUSDm"]


def test_cent_account():
    assert names("XAUUSD", EXNESS_CENT) == ["XAUUSDc"]
    assert names("GOLD", EXNESS_CENT) == ["XAUUSDc"]


def test_broker_that_calls_gold_gold_and_disabled_symbols():
    assert names("XAUUSD", OTHER) == ["GOLD"]          # GOLD.c is disabled for trading
    assert names("BTCUSD", OTHER) == ["BTCUSD.raw"]
    assert names("EURUSD", OTHER) == ["EURUSD.raw", "EURUSD.raw2"]
    assert names("GBPUSD", OTHER) == ["GBPUSD"]        # exact name first
    assert names("XAUEUR", EXNESS_DEMO) == ["XAUEURm"]   # unknown pairs still match by name


def test_named_mt5_account_passes_symbol_metadata_to_matcher(monkeypatch):
    from copier import mt5_worker
    from panel import mt5_accounts

    symbols = [sym("XAUUSDc", visible=False), sym("XAUUSDm", description="Gold"),
               sym("XAUUSD", trade_mode=0)]
    terminal = Mock()
    terminal.account.return_value = NS(login=123, server="Server")
    terminal.mt5.terminal_info.return_value = NS(connected=True)
    terminal.mt5.symbols_get.return_value = symbols
    monkeypatch.setattr(mt5_worker, "Broker", lambda _: terminal)
    pipe = Mock()
    pipe.recv.side_effect = [("symbol_catalog", ()), EOFError()]
    mt5_worker._serve(pipe, {}, "", ["123", "Server"], ())
    ok, catalog = pipe.send.call_args.args[0]
    assert ok

    connection = Mock()
    connection.symbol_catalog.return_value = catalog
    monkeypatch.setattr(mt5_accounts, "TerminalBroker", lambda *_: connection)
    _, broker_symbols = mt5_accounts._probe(NS(cfg=dict), "", symbols=True)
    assert names("XAUUSD", broker_symbols) == ["XAUUSDm", "XAUUSDc"]
    assert broker_symbols[1].description == "Gold"


def test_account_preview_keeps_valid_pairs_and_flags_choices_and_missing(monkeypatch):
    from copier import accounts_mt5
    from panel import mt5_accounts

    account = accounts_mt5.Account("AKUN 1", "terminal64.exe")
    monkeypatch.setattr(accounts_mt5, "metadata", lambda: {
        "akun 1": {"simbol": {"XAUUSD": "GOLD", "BTCUSD": "BTCUSD", "EURUSD": "EURUSD", "XAGUSD": "XAGUSD"}},
    })
    catalog = [sym("GOLD"), sym("BTCUSDc"), sym("EURUSDc"), sym("EURUSDm"), sym("XAGUSD", trade_mode=0)]
    monkeypatch.setattr(mt5_accounts, "_probe", lambda *_, **__: (NS(login=123, server="Server"), catalog))
    settings = NS(cfg=lambda: {"simbol": {"XAUUSD": "XAUUSD", "BTCUSD": "BTCUSD"}})

    identity, mapping, choices, missing = mt5_accounts.preview_account(settings, account, existing=True)
    assert identity == ["123", "Server"]
    assert mapping["XAUUSD"] == "GOLD"
    assert choices == {"BTCUSD": ["BTCUSDc"], "EURUSD": ["EURUSDc", "EURUSDm"]}
    assert missing == ["XAGUSD"]
    _, _, choices, missing = mt5_accounts.preview_account(settings, account)
    assert choices == {"XAUUSD": ["GOLD"], "BTCUSD": ["BTCUSDc"]}
    assert missing == []


def test_search_names():
    assert search_names("xauusd") == ["XAUUSD", "GOLD"]
    assert search_names("GOLD") == ["GOLD", "XAUUSD"]
    assert search_names("EURUSD") == ["EURUSD"]


@pytest.fixture
def settings(tmp_path):
    p = tmp_path / "config.yaml"
    shutil.copy("config.yaml", p)
    s = Settings(str(p))
    for k, v in {"XAUUSD": "XAUUSDm", "GOLD": "XAUUSDm", "BTCUSD": "BTCUSDm",
                 "EURUSD": "EURUSDm", "GBPUSD": "GBPUSDm", "USDJPY": "USDJPYm"}.items():
        s.set(("simbol", k), v)
    return s


def run_screen(monkeypatch, settings, syms, answers):
    fake = NS(account_info=lambda: NS(login=1, server="Broker", currency="USC"),
              symbols_get=lambda: tuple(syms))
    monkeypatch.setattr(symbols, "_connect", lambda: (fake, ""))
    monkeypatch.setattr(symbols, "clear", lambda: None)
    replies = iter(answers)
    monkeypatch.setattr(symbols, "ask", lambda *_: next(replies, ""))
    monkeypatch.setattr(symbols, "pause", lambda: None)
    symbols.match_screen(settings)
    return settings.cfg()["simbol"]


def test_switch_to_cent_account_saves_everything(monkeypatch, settings):
    m = run_screen(monkeypatch, settings, EXNESS_CENT, ["y"])
    assert m["XAUUSD"] == m["GOLD"] == "XAUUSDc" and m["BTCUSD"] == "BTCUSDc"
    assert m["USDJPY"] == "USDJPYc"


def test_ambiguous_pick_and_refuse_to_save(monkeypatch, settings):
    m = run_screen(monkeypatch, settings, OTHER, ["2", "t"])     # EURUSD.raw2, then don't save
    assert m["EURUSD"] == "EURUSDm"
    m = run_screen(monkeypatch, settings, OTHER, ["2", "y"])
    assert m["EURUSD"] == "EURUSD.raw2" and m["XAUUSD"] == "GOLD"


def test_missing_pair_is_left_alone(monkeypatch, settings):
    m = run_screen(monkeypatch, settings, [sym("XAUUSDc")], ["y"])
    assert m["XAUUSD"] == "XAUUSDc" and m["BTCUSD"] == "BTCUSDm"
