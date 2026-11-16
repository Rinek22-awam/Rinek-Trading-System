"""Eksekutor Bybit dengan sesi pybit tiruan: tanpa internet, tanpa API key."""
from types import SimpleNamespace

import pytest

from copier import broker_bybit
from copier.broker_bybit import BybitBroker


class BybitError(Exception):
    """Meniru pybit InvalidRequestError (punya status_code dan message)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.status_code, self.message = code, message


def ok(result=None):
    return {"retCode": 0, "retMsg": "OK", "result": result or {}}


class FakeSession:
    def __init__(self):
        self.calls = []
        self.fail = {}                       # nama endpoint -> exception
        self.position = None                 # dict posisi Bybit, atau None
        self.orders = []

    def _rec(self, name, kw):
        self.calls.append((name, kw))
        if name in self.fail:
            raise self.fail[name]

    def last(self, name):
        return [kw for n, kw in self.calls if n == name][-1]

    def get_server_time(self, **kw):
        self._rec("get_server_time", kw)
        return ok({"timeSecond": "1"})

    def get_wallet_balance(self, **kw):
        self._rec("get_wallet_balance", kw)
        return ok({"list": [{"totalEquity": "10000.5", "totalAvailableBalance": "9000",
                             "totalWalletBalance": "10000",
                             "coin": [{"coin": "USDT", "walletBalance": "10000",
                                       "unrealisedPnl": "0.5"}]}]})

    def get_api_key_information(self, **kw):
        self._rec("get_api_key_information", kw)
        return ok({"readOnly": 0, "permissions": {"ContractTrade": ["Order", "Position"],
                                                  "Wallet": ["AccountTransfer"]}, "ips": ["*"]})

    def get_instruments_info(self, **kw):
        self._rec("get_instruments_info", kw)
        if kw["symbol"] != "BTCUSDT":
            return ok({"list": []})
        return ok({"list": [{"symbol": "BTCUSDT",
                             "priceFilter": {"tickSize": "0.10"},
                             "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001",
                                               "maxOrderQty": "100", "minNotionalValue": "5"},
                             "leverageFilter": {"maxLeverage": "100.00"}}]})

    def get_tickers(self, **kw):
        self._rec("get_tickers", kw)
        return ok({"list": [{"bid1Price": "65000.0", "ask1Price": "65000.1",
                             "lastPrice": "65000.0", "markPrice": "65000.05"}]})

    def set_leverage(self, **kw):
        self._rec("set_leverage", kw)
        return ok()

    def place_order(self, **kw):
        self._rec("place_order", kw)
        return ok({"orderId": f"oid-{len(self.calls)}"})

    def amend_order(self, **kw):
        self._rec("amend_order", kw)
        return ok()

    def cancel_order(self, **kw):
        self._rec("cancel_order", kw)
        return ok()

    def cancel_all_orders(self, **kw):
        self._rec("cancel_all_orders", kw)
        return ok()

    def set_trading_stop(self, **kw):
        self._rec("set_trading_stop", kw)
        return ok()

    def get_positions(self, **kw):
        self._rec("get_positions", kw)
        return ok({"list": [self.position] if self.position else []})

    def get_open_orders(self, **kw):
        self._rec("get_open_orders", kw)
        return ok({"list": self.orders})


def mk():
    s = FakeSession()
    return s, BybitBroker("demo", session=s)


def test_rejects_unknown_mode():
    with pytest.raises(ValueError):
        BybitBroker("asli", session=FakeSession())


def test_connect_reads_balance():
    _, b = mk()
    good, msg = b.connect()
    assert good and "DEMO" in msg and "10000.50" in msg
    assert b.is_demo()


def test_connect_explains_bad_key():
    s, b = mk()
    s.fail["get_wallet_balance"] = BybitError(10003, "API key is invalid.")
    good, msg = b.connect()
    assert not good and "10003" in msg and "mode yang sama" in msg


def test_http_request_log_counts_attempts_across_accounts(monkeypatch, caplog):
    import requests

    now = 1000.0
    monkeypatch.setattr(broker_bybit.time, "monotonic", lambda: now)
    monkeypatch.setattr(BybitBroker, "_request_log", broker_bybit.BybitRequestLog())
    responses = iter([requests.ConnectionError("reset"), 200, 403, 200])

    def fake_send(self, request, **kwargs):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(status_code=result)

    monkeypatch.setattr(requests.Session, "send", fake_send)
    first, second = BybitBroker(), BybitBroker()
    request = requests.Request(
        "GET", "https://api-demo.bybit.com/v5/order/realtime?orderLinkId=private-id"
    ).prepare()

    with pytest.raises(requests.ConnectionError):
        first.s.client.send(request)
    now += 1
    first.s.client.send(request)
    second.s.client.send(request)
    now += 60
    with caplog.at_level("INFO", logger=broker_bybit.__name__):
        second.s.client.send(request)

    assert "3 percobaan / 5 detik" in caplog.text
    assert "Trafik HTTP Bybit:" not in caplog.text
    assert "api-demo.bybit.com GET /v5/order/realtime" in caplog.text
    assert "private-id" not in caplog.text
    assert len(BybitBroker._request_log.recent) == 1


def test_bybit_timeout_reports_recovery_only_after_endpoint_succeeds(monkeypatch, caplog):
    import requests

    session, broker = mk()
    monkeypatch.setattr(broker, "RETRY_PAUSE", 0)
    session.fail["get_open_orders"] = requests.ReadTimeout("request timed out")
    with caplog.at_level("INFO", logger=broker_bybit.__name__):
        assert broker.open_orders("ATOMUSDT")[1]
        assert not broker.positions()[1]
        assert "Bybit API pulih" not in caplog.text
        del session.fail["get_open_orders"]
        assert not broker.open_orders("ATOMUSDT")[1]
        assert not broker.open_orders("ATOMUSDT")[1]
    assert caplog.text.count("Bybit API terganggu · get_open_orders") == 1
    assert caplog.text.count("Bybit API pulih · get_open_orders") == 1


def test_http_403_pauses_all_accounts_and_resumes_after_cooldown(monkeypatch):
    now = 1000.0
    monkeypatch.setattr(broker_bybit.time, "monotonic", lambda: now)
    monkeypatch.setattr(BybitBroker, "_ip_cooldown_until", 0.0)
    first, broker = mk()
    second, other = mk()
    first.fail["get_open_orders"] = BybitError(403, "IP rate limit or restricted region")

    assert "[403]" in broker.open_orders("ATOMUSDT")[1]
    assert "dijeda" in other.open_orders("ATOMUSDT")[1]
    assert "dijeda" in other.cancel("ATOMUSDT", "1")[1]
    now += 599
    assert "dijeda" in other.open_orders("ATOMUSDT")[1]
    assert second.calls == []

    now += 1
    assert other.open_orders("ATOMUSDT") == ([], "")
    assert len(second.calls) == 1


def test_network_error_does_not_raise():
    s, b = mk()
    s.fail["get_server_time"] = ConnectionError("timeout")
    good, msg = b.connect()
    assert not good and "gagal terhubung" in msg


def test_key_info_flags_withdraw():
    s, b = mk()
    info, _ = b.key_info()
    assert not info["withdraw"] and not info["read_only"]
    s.get_api_key_information = lambda **kw: ok({"readOnly": 0,
                                                 "permissions": {"Wallet": ["Withdraw"]}})
    info, _ = b.key_info()
    assert info["withdraw"]


def test_market_order_with_sl_tp_rounded():
    s, b = mk()
    oid, err = b.place("btcusdt", "BUY", "MARKET", 0.0019, sl=64000.04, tp=66000.06)
    assert oid and err == ""
    kw = s.last("place_order")
    assert kw["category"] == "linear" and kw["side"] == "Buy" and kw["orderType"] == "Market"
    assert kw["qty"] == "0.001"                       # dibulatkan ke bawah
    assert kw["stopLoss"] == "64000" and kw["takeProfit"] == "66000.1"
    assert kw["tpslMode"] == "Full" and kw["positionIdx"] == 0
    assert "price" not in kw and "reduceOnly" not in kw


def test_limit_order_without_sl_tp():
    s, b = mk()
    oid, _ = b.place("BTCUSDT", "SELL", "LIMIT", 0.01, price=66000)
    kw = s.last("place_order")
    assert oid and kw["side"] == "Sell" and kw["price"] == "66000" and kw["timeInForce"] == "GTC"
    assert "stopLoss" not in kw and "tpslMode" not in kw


def test_limit_needs_price_and_min_qty():
    s, b = mk()
    assert b.place("BTCUSDT", "BUY", "LIMIT", 0.01)[1] == "order LIMIT butuh harga"
    oid, err = b.place("BTCUSDT", "BUY", "MARKET", 0.0005)
    assert oid is None and "minimum 0.001" in err
    assert not any(n == "place_order" for n, _ in s.calls)


def test_unknown_symbol():
    _, b = mk()
    oid, err = b.place("XYZUSDT", "BUY", "MARKET", 1)
    assert oid is None and "tidak ada" in err


def test_bybit_rejection_returned_not_raised():
    s, b = mk()
    s.fail["place_order"] = BybitError(110007, "ab not enough for new order")
    oid, err = b.place("BTCUSDT", "BUY", "MARKET", 0.01)
    assert oid is None and "saldo tidak cukup" in err


def test_set_sl_tp_partial_and_remove():
    s, b = mk()
    assert b.set_sl_tp("BTCUSDT", sl=64500.03) == (True, "")
    kw = s.last("set_trading_stop")
    assert kw["stopLoss"] == "64500" and "takeProfit" not in kw and kw["positionIdx"] == 0
    b.set_sl_tp("BTCUSDT", tp=0)
    assert s.last("set_trading_stop")["takeProfit"] == "0"
    assert b.set_sl_tp("BTCUSDT") == (False, "tidak ada yang diubah")


def test_not_modified_counts_as_success():
    s, b = mk()
    s.fail["set_trading_stop"] = BybitError(34040, "not modified")
    assert b.set_sl_tp("BTCUSDT", sl=64000) == (True, "")
    s.fail["set_leverage"] = BybitError(110043, "leverage not modified")
    assert b.set_leverage("BTCUSDT", 10) == (True, "")


def test_amend_only_given_fields():
    s, b = mk()
    assert b.amend("BTCUSDT", "abc", price=64990.07, sl=0)[0]
    kw = s.last("amend_order")
    assert kw == {"category": "linear", "symbol": "BTCUSDT", "orderId": "abc",
                  "price": "64990.1", "stopLoss": "0"}
    assert b.amend("BTCUSDT", "abc") == (False, "tidak ada yang diubah")


def test_cancel_and_cancel_all():
    s, b = mk()
    assert b.cancel("BTCUSDT", "abc") == (True, "")
    assert s.last("cancel_order")["orderId"] == "abc"
    assert b.cancel_all("btcusdt") == (True, "")
    assert s.last("cancel_all_orders")["symbol"] == "BTCUSDT"


def test_leverage_sent_as_text():
    s, b = mk()
    b.set_leverage("BTCUSDT", 10.0)
    kw = s.last("set_leverage")
    assert kw["buyLeverage"] == kw["sellLeverage"] == "10"


LONG = {"symbol": "BTCUSDT", "side": "Buy", "size": "0.010", "avgPrice": "65000",
        "stopLoss": "64000", "takeProfit": "", "unrealisedPnl": "1.2", "leverage": "10",
        "liqPrice": "59000", "markPrice": "65120"}


def test_positions_parsed_and_empty_skipped():
    s, b = mk()
    s.position = {**LONG, "size": "0"}
    assert b.positions("BTCUSDT") == ([], "")
    s.position = LONG
    pos, _ = b.positions()
    assert s.last("get_positions") == {"category": "linear", "settleCoin": "USDT"}
    p = pos[0]
    assert (p.side, p.size, p.entry, p.sl, p.tp) == ("BUY", 0.01, 65000.0, 64000.0, 0.0)


def test_close_full_and_partial_reduce_only():
    s, b = mk()
    assert b.close("BTCUSDT") == (False, "tidak ada posisi BTCUSDT yang terbuka")
    s.position = LONG
    assert b.close("BTCUSDT")[0]
    kw = s.last("place_order")
    assert kw["side"] == "Sell" and kw["qty"] == "0.01" and kw["reduceOnly"] is True
    b.close("BTCUSDT", 0.004)
    assert s.last("place_order")["qty"] == "0.004"
    b.close("BTCUSDT", 5)                             # lebih dari posisi -> dibatasi
    assert s.last("place_order")["qty"] == "0.01"


def test_open_orders_kinds():
    s, b = mk()
    s.orders = [{"orderId": "1", "symbol": "BTCUSDT", "side": "Buy", "orderType": "Limit",
                 "stopOrderType": "", "qty": "0.01", "price": "64000", "stopLoss": "63000",
                 "takeProfit": "66000", "orderStatus": "New", "reduceOnly": False},
                {"orderId": "2", "symbol": "BTCUSDT", "side": "Sell", "orderType": "Market",
                 "stopOrderType": "StopLoss", "qty": "0", "price": "0", "triggerPrice": "63000",
                 "orderStatus": "Untriggered", "reduceOnly": True}]
    orders, _ = b.open_orders("BTCUSDT")
    assert [o.kind for o in orders] == ["Limit", "StopLoss"]
    assert orders[0].sl == 63000.0 and orders[1].trigger == 63000.0 and orders[1].reduce_only
