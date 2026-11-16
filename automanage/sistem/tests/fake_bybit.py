"""Bursa Bybit tiruan (USDT Perpetual, one-way) yang meniru sesi pybit.

Cukup lengkap untuk alur bot: order entry LIMIT / MARKET / STOP, satu posisi per simbol,
SL posisi (Full), order reduce-only (TP parsial dan penutupan), catatan closed PnL, dan
riwayat order. Gerakkan harga dengan set_price(); order yang tersentuh langsung terisi.
"""
import itertools

_OPEN = ("New", "Untriggered", "PartiallyFilled")

SPECS = {
    "BTCUSDT": {"tickSize": "0.10", "qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "100"},
    "POPCATUSDT": {"tickSize": "0.00001", "qtyStep": "1", "minOrderQty": "1", "maxOrderQty": "1000000"},
    "STXUSDT": {"tickSize": "0.0001", "qtyStep": "0.1", "minOrderQty": "0.1", "maxOrderQty": "1000000"},
}


# Tingkat risk limit asli (Bybit demo, Nov 2026): (nilai posisi maks USDT, leverage maks)
TIERS = {"BTCUSDT": [(300000, "150"), (2000000, "100")],
         "STXUSDT": [(5000, "75"), (7000, "70"), (10000, "65")],
         "POPCATUSDT": [(5000, "50"), (10000, "25")],
         "1000PEPEUSDT": [(200000, "50")]}
SPECS["1000PEPEUSDT"] = {"tickSize": "0.0000001", "qtyStep": "100", "minOrderQty": "100",
                         "maxOrderQty": "100000000"}


class BybitError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.status_code, self.message = code, message


def ok(result=None):
    return {"retCode": 0, "retMsg": "OK", "result": result or {}}


def r(x: float, step="0.0001") -> str:
    return format(round(float(x), 6), "f").rstrip("0").rstrip(".") or "0"


class FakeBybit:
    def __init__(self, clock, price=65000.0, spread=0.1, balance=10_000.0, hedge=False, margin="cross"):
        self.clock, self.spread, self.balance, self.hedge = clock, spread, balance, hedge
        self.margin = margin            # cross | isolated
        self.prices = {"BTCUSDT": price, "POPCATUSDT": 0.0366, "STXUSDT": 0.3330}
        self.orders = {}               # orderId -> dict
        self.pos = {}                  # symbol -> dict(side, size, avg, sl)
        self.closed = []               # closed-pnl records, newest last
        self.calls = []
        self.fail = {}
        self._ids = itertools.count(1)

    # --- helpers --------------------------------------------------------------
    def _ms(self):
        return int(self.clock() * 1000)

    def _rec(self, name, kw):
        self.calls.append((name, kw))
        if name in self.fail:
            raise self.fail.pop(name)

    def bid(self, sym):
        return self.prices[sym]

    def ask(self, sym):
        return self.prices[sym] + {"BTCUSDT": self.spread, "STXUSDT": 0.0001}.get(sym, 0.00001)

    def placed(self, **match):
        return [o for o in self.orders.values() if all(o.get(k) == v for k, v in match.items())]

    def set_price(self, price, sym="BTCUSDT"):
        self.prices[sym] = price
        self._process(sym)

    def _new(self, **o):
        oid = f"oid{next(self._ids)}"
        base = {"orderId": oid, "orderLinkId": "", "stopOrderType": "", "reduceOnly": False,
                "cumExecQty": "0", "avgPrice": "", "stopLoss": "", "takeProfit": "", "price": "0",
                "triggerPrice": "", "createdTime": str(self._ms()), "updatedTime": str(self._ms())}
        base.update(o)
        self.orders[oid] = base
        return base

    def _fill(self, o, px, qty=None):
        sym, side = o["symbol"], o["side"]
        before = float(o["cumExecQty"])
        remaining = round(float(o["qty"]) - before, 8)
        qty = remaining if qty is None else min(qty, remaining)
        filled = round(before + qty, 8)
        avg = (float(o["avgPrice"] or 0) * before + qty * px) / filled
        o.update(orderStatus="Filled" if qty == remaining else "PartiallyFilled",
                 cumExecQty=r(filled), avgPrice=r(avg), updatedTime=str(self._ms()))
        p = self.pos.get(sym)
        if o["reduceOnly"]:
            if not p:
                return
            qty = min(qty, p["size"])
            sign = 1 if p["side"] == "Buy" else -1
            self.closed.append({"symbol": sym, "orderId": o["orderId"], "closedSize": r(qty),
                                "avgEntryPrice": r(p["avg"]), "avgExitPrice": r(px),
                                "closedPnl": r((px - p["avg"]) * qty * sign),
                                "createdTime": str(self._ms())})
            p["size"] = round(p["size"] - qty, 8)
            if p["size"] <= 0:
                del self.pos[sym]
                for x in self.orders.values():      # Bybit membatalkan sisa reduce-only
                    if x["symbol"] == sym and x["reduceOnly"] and x["orderStatus"] in _OPEN:
                        x["orderStatus"] = "Cancelled"
            return
        if p:
            p["avg"] = (p["avg"] * p["size"] + px * qty) / (p["size"] + qty)
            p["size"] = round(p["size"] + qty, 8)
        else:
            self.pos[sym] = {"side": side, "size": qty, "avg": px, "sl": float(o["stopLoss"] or 0)}

    def _process(self, sym):
        bid, ask = self.bid(sym), self.ask(sym)
        for o in list(self.orders.values()):
            if o["symbol"] != sym or o["orderStatus"] not in _OPEN:
                continue
            buy = o["side"] == "Buy"
            if o["orderStatus"] == "Untriggered":              # STOP entry
                trig = float(o["triggerPrice"])
                if (buy and ask >= trig) or (not buy and bid <= trig):
                    self._fill(o, ask if buy else bid)
            elif o["orderType"] == "Limit":
                lim = float(o["price"])
                if (buy and ask <= lim) or (not buy and bid >= lim):
                    self._fill(o, lim)
        p = self.pos.get(sym)
        if p and p["sl"]:
            long_ = p["side"] == "Buy"
            if (long_ and bid <= p["sl"]) or (not long_ and ask >= p["sl"]):
                closer = self._new(symbol=sym, side="Sell" if long_ else "Buy", orderType="Market",
                                   qty=r(p["size"]), reduceOnly=True, stopOrderType="StopLoss",
                                   orderStatus="Untriggered")
                self._fill(closer, p["sl"])

    # --- endpoint pybit -------------------------------------------------------
    def get_server_time(self, **kw):
        self._rec("get_server_time", kw)
        return ok({"timeSecond": str(int(self.clock()))})

    def get_wallet_balance(self, **kw):
        self._rec("get_wallet_balance", kw)
        b = r(self.balance)
        return ok({"list": [{"totalEquity": b, "totalAvailableBalance": b, "totalWalletBalance": b,
                             "coin": [{"coin": "USDT", "walletBalance": b, "unrealisedPnl": "0"}]}]})

    def get_instruments_info(self, **kw):
        self._rec("get_instruments_info", kw)
        s = SPECS.get(kw["symbol"])
        if not s:                       # Bybit asli menolak simbol yang tidak ada
            raise BybitError(10001, "params error: symbol invalid")
        return ok({"list": [{"symbol": kw["symbol"], "priceFilter": {"tickSize": s["tickSize"]},
                             "lotSizeFilter": {"qtyStep": s["qtyStep"], "minOrderQty": s["minOrderQty"],
                                               "maxOrderQty": s["maxOrderQty"], "minNotionalValue": "5"},
                             "leverageFilter": {"maxLeverage": "100"}}]})

    def get_risk_limit(self, **kw):
        self._rec("get_risk_limit", kw)
        if kw["symbol"] not in TIERS:
            raise BybitError(10001, "params error: symbol invalid")
        return ok({"list": [{"riskLimitValue": str(v), "maxLeverage": lev, "maintenanceMargin": "0.005"}
                            for v, lev in TIERS[kw["symbol"]]]})

    def get_account_info(self, **kw):
        self._rec("get_account_info", kw)
        return ok({"marginMode": {"cross": "REGULAR_MARGIN", "isolated": "ISOLATED_MARGIN"}[self.margin]})

    def get_tickers(self, **kw):
        self._rec("get_tickers", kw)
        sym = kw["symbol"]
        return ok({"list": [{"bid1Price": r(self.bid(sym)), "ask1Price": r(self.ask(sym)),
                             "lastPrice": r(self.bid(sym)), "markPrice": r(self.bid(sym))}]})

    def set_leverage(self, **kw):
        self._rec("set_leverage", kw)
        return ok()

    def place_order(self, **kw):
        self._rec("place_order", kw)
        sym = kw["symbol"]
        if kw.get("reduceOnly") and sym not in self.pos:
            raise BybitError(110017, "current position is zero, cannot fix reduce-only order qty")
        link = kw.get("orderLinkId", "")
        if link and any(o["orderLinkId"] == link for o in self.orders.values()):
            raise BybitError(110072, "OrderLinkedID is duplicate")
        o = self._new(symbol=sym, side=kw["side"], orderType=kw["orderType"], qty=kw["qty"],
                      price=kw.get("price", "0"), triggerPrice=kw.get("triggerPrice", ""),
                      stopLoss=kw.get("stopLoss", ""), takeProfit=kw.get("takeProfit", ""),
                      reduceOnly=bool(kw.get("reduceOnly")), orderLinkId=link,
                      orderStatus="Untriggered" if kw.get("triggerPrice") else "New")
        if kw["orderType"] == "Market" and not kw.get("triggerPrice"):
            self._fill(o, self.ask(sym) if kw["side"] == "Buy" else self.bid(sym))
        else:
            self._process(sym)
        return ok({"orderId": o["orderId"], "orderLinkId": link})

    def amend_order(self, **kw):
        self._rec("amend_order", kw)
        return ok()

    def cancel_order(self, **kw):
        self._rec("cancel_order", kw)
        o = self.orders.get(kw["orderId"])
        if not o or o["orderStatus"] not in _OPEN:
            raise BybitError(110001, "order not exists or too late to cancel")
        o["orderStatus"] = "Cancelled"
        return ok({"orderId": o["orderId"]})

    def cancel_all_orders(self, **kw):
        self._rec("cancel_all_orders", kw)
        for o in self.orders.values():
            if o["symbol"] == kw["symbol"] and o["orderStatus"] in _OPEN:
                o["orderStatus"] = "Cancelled"
        return ok()

    def set_trading_stop(self, **kw):
        self._rec("set_trading_stop", kw)
        p = self.pos.get(kw["symbol"])
        if not p:
            raise BybitError(10001, "can not set tp/sl/ts for zero position")
        if "stopLoss" in kw:
            new = float(kw["stopLoss"])
            if new == p["sl"]:
                raise BybitError(34040, "not modified")
            p["sl"] = new
        self._process(kw["symbol"])
        return ok()

    def get_positions(self, **kw):
        self._rec("get_positions", kw)
        syms = [kw["symbol"]] if kw.get("symbol") else list(self.pos)
        out = []
        for sym in syms:
            p = self.pos.get(sym)
            idxs = (1, 2) if self.hedge else (0,)
            for idx in idxs:
                if not p:
                    out.append({"symbol": sym, "side": "", "size": "0", "positionIdx": idx})
                    continue
                sign = 1 if p["side"] == "Buy" else -1
                out.append({"symbol": sym, "side": p["side"], "size": r(p["size"]),
                            "avgPrice": r(p["avg"]), "stopLoss": r(p["sl"]) if p["sl"] else "",
                            "takeProfit": "", "markPrice": r(self.bid(sym)),
                            "unrealisedPnl": r((self.bid(sym) - p["avg"]) * p["size"] * sign),
                            "leverage": "10", "liqPrice": "", "positionIdx": idx})
        return ok({"list": out})

    def _find(self, kw, open_only):
        items = [o for o in self.orders.values()
                 if (o["orderStatus"] in _OPEN) == open_only and o["symbol"] == kw.get("symbol", o["symbol"])]
        if kw.get("orderId"):
            items = [o for o in items if o["orderId"] == kw["orderId"]]
        if kw.get("orderLinkId"):
            items = [o for o in items if o["orderLinkId"] == kw["orderLinkId"]]
        return ok({"list": [dict(o) for o in items]})

    def get_open_orders(self, **kw):
        self._rec("get_open_orders", kw)
        return self._find(kw, True)

    def get_order_history(self, **kw):
        self._rec("get_order_history", kw)
        return self._find(kw, False)

    def get_closed_pnl(self, **kw):
        self._rec("get_closed_pnl", kw)
        recs = [c for c in self.closed if c["symbol"] == kw.get("symbol", c["symbol"])
                and int(c["createdTime"]) >= kw.get("startTime", 0)]
        return ok({"list": list(reversed(recs))})

    def get_kline(self, **kw):
        self._rec("get_kline", kw)
        return ok({"list": []})
