"""Eksekutor Bybit dasar: USDT Perpetual (category "linear"), mode posisi one-way.

Fungsi polos tanpa SOP: buka posisi market, pasang/ubah/batal limit, pasang/geser SL-TP,
tutup penuh atau sebagian, baca saldo/posisi/order. Pass `session` untuk menyuntik tiruan
(tes tidak butuh internet maupun API key).

Semua fungsi yang mengubah sesuatu mengembalikan (hasil, pesan_error). Error tidak pernah
melempar exception ke luar, supaya menu atau bot tidak mati karena satu penolakan Bybit.
"""
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
request_log = log.getChild("request_traffic")

CATEGORY = "linear"
MODES = ("demo", "testnet", "live")
_NOT_MODIFIED = {34040, 110043}       # SL/TP atau leverage sudah sama: bukan kegagalan

# Penjelasan awam untuk kode error Bybit yang paling sering muncul.
HINTS = {
    10001: "parameter ditolak (cek angka harga/qty, atau akun dalam mode hedge, bukan one-way)",
    10002: "jam komputer tidak sinkron dengan server; sinkronkan jam Windows",
    10003: "API key tidak dikenal; pastikan key dibuat di mode yang sama (demo/testnet/live)",
    10004: "tanda tangan salah; API secret keliru atau terpotong",
    10005: "izin API key kurang; aktifkan izin Contract (Orders + Positions)",
    10010: "IP komputer ini tidak ada di daftar IP API key",
    110007: "saldo tidak cukup untuk order ini",
    110017: "reduce-only ditolak: tidak ada posisi yang bisa dikurangi",
    110012: "saldo tidak cukup",
    110001: "order tidak ditemukan (mungkin sudah terisi atau dibatalkan)",
    110003: "harga di luar batas yang diizinkan",
    110094: "nilai order di bawah minimum (minNotional)",
    10006: "terlalu banyak permintaan; tunggu sebentar",
}


@dataclass
class Spec:
    tick_size: Decimal
    qty_step: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal
    max_leverage: Decimal


@dataclass
class Position:
    symbol: str
    side: str              # BUY | SELL
    size: float
    entry: float
    sl: float              # 0 = belum ada
    tp: float              # 0 = belum ada
    pnl: float
    leverage: str
    liq: float
    mark: float


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str              # BUY | SELL
    kind: str              # Limit | Market | TakeProfit | StopLoss | ...
    qty: float
    price: float
    trigger: float
    sl: float
    tp: float
    status: str
    reduce_only: bool


def _d(x) -> Decimal:
    return Decimal(str(x)) if x not in (None, "") else Decimal(0)


def _f(x) -> float:
    try:
        return float(x) if x not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _s(x: Decimal) -> str:
    """Decimal -> teks tanpa notasi ilmiah dan tanpa nol berlebih ('0.0100' -> '0.01')."""
    t = format(x.normalize(), "f")
    return t if t not in ("-0",) else "0"


def _side(side: str) -> str:
    s = side.upper()
    if s in ("BUY", "LONG"):
        return "Buy"
    if s in ("SELL", "SHORT"):
        return "Sell"
    raise ValueError(f"arah harus BUY atau SELL, bukan {side!r}")


def _side_back(side: str) -> str:
    return {"Buy": "BUY", "Sell": "SELL"}.get(side, side.upper())


NETWORK_PREFIX = "gagal terhubung ke Bybit (koneksi putus/timeout): "
_NET_WORDS = ("timed out", "timeout", "connection", "max retries", "temporarily unavailable",
              "remote end closed", "connectionpool", "name resolution", "ssl")


def is_network_error(err: Exception) -> bool:
    """Gangguan koneksi (bukan jawaban Bybit): timeout, putus, DNS. Aman dicoba ulang untuk baca data."""
    if getattr(err, "status_code", None) not in (None, 0):
        return False   # Bybit menjawab dengan kode error: bukan masalah koneksi
    text = f"{type(err).__name__} {err}".lower()
    return any(w in text for w in _NET_WORDS)


def explain(err: Exception) -> str:
    """Ubah exception pybit/jaringan jadi satu kalimat yang bisa dibaca user."""
    code = getattr(err, "status_code", None)
    msg = getattr(err, "message", None) or str(err)
    if code is None:
        return f"gagal terhubung ke Bybit: {msg}"
    hint = HINTS.get(code)
    return f"[{code}] {msg}" + (f" -> {hint}" if hint else "")


class BybitRequestLog:
    WINDOW_SECONDS = 5

    def __init__(self):
        self.recent = deque()

    def wrap(self, send):
        def tracked_send(request, **kwargs):
            now = time.monotonic()
            while self.recent and now - self.recent[0] >= self.WINDOW_SECONDS:
                self.recent.popleft()
            self.recent.append(now)
            rolling_count = len(self.recent)

            response = send(request, **kwargs)
            if response.status_code == 403:
                url = urlsplit(request.url)
                request_log.warning(
                    "Trafik HTTP Bybit saat kirim request yang kena 403: %d percobaan / 5 detik | %s %s",
                    rolling_count, url.hostname, f"{request.method} {url.path}",
                )
            return response

        return tracked_send


class BybitBroker:
    IP_COOLDOWN_SECONDS = 600
    _ip_cooldown_until = 0.0  # semua akun Bybit dalam proses memakai IP yang sama
    _request_log = BybitRequestLog()

    def __init__(self, mode: str = "demo", api_key: Optional[str] = None,
                 api_secret: Optional[str] = None, session=None, recv_window: int = 10000,
                 *, on_ip_cooldown=None, account_name=""):
        mode = (mode or "demo").strip().lower()
        if mode not in MODES:
            raise ValueError(f"BYBIT_MODE harus salah satu dari {MODES}, bukan {mode!r}")
        self.mode = mode
        self.on_ip_cooldown = on_ip_cooldown
        self.account_name = account_name
        self._network_incidents = {}
        if session is None:
            from pybit.unified_trading import HTTP
            session = HTTP(testnet=(mode == "testnet"), demo=(mode == "demo"),
                           api_key=api_key, api_secret=api_secret, recv_window=recv_window)
            session.client.send = self._request_log.wrap(session.client.send)
        self.s = session
        self._specs = {}

    # --- pemanggil aman -------------------------------------------------------
    READ_RETRIES = 2        # baca data (get_*) yang putus/timeout dicoba ulang sekian kali
    RETRY_PAUSE = 1.5

    def _call(self, name: str, **kw):
        """Panggil satu endpoint. Hasil: (result_dict, "") atau (None, pesan_error).
        Hanya pembacaan (get_*) yang dicoba ulang kalau koneksi putus/timeout; order TIDAK
        pernah diulang otomatis (bisa terpasang dobel)."""
        tries = 1 + (self.READ_RETRIES if name.startswith("get_") else 0)
        for attempt in range(tries):
            if time.monotonic() < BybitBroker._ip_cooldown_until:
                return None, "Bybit HTTP 403: permintaan dijeda sementara setelah penolakan IP"
            try:
                resp = getattr(self.s, name)(**kw)
                break
            except Exception as e:  # pybit melempar exception untuk setiap retCode != 0
                code = getattr(e, "status_code", None)
                if code == 403:
                    BybitBroker._ip_cooldown_until = time.monotonic() + self.IP_COOLDOWN_SECONDS
                    if self.on_ip_cooldown is not None:
                        try:
                            self.on_ip_cooldown(self.IP_COOLDOWN_SECONDS)
                        except OSError:
                            log.exception("Gagal menyimpan jeda HTTP 403 Bybit")
                    log.warning("Bybit HTTP 403 pada %s, permintaan dijeda %d detik: %s",
                                name, self.IP_COOLDOWN_SECONDS, e)
                    return None, explain(e)
                if code in _NOT_MODIFIED:
                    return {}, ""
                if is_network_error(e):
                    if name not in self._network_incidents:
                        self._network_incidents[name] = time.monotonic()
                        log.warning("Bybit API terganggu · %s | %s", self._endpoint_label(name), e)
                    if attempt + 1 < tries:
                        time.sleep(self.RETRY_PAUSE)
                        continue
                    return None, NETWORK_PREFIX + str(e)[:160]
                quiet = "symbol invalid" in str(getattr(e, "message", e)).lower()   # koin tidak ada: hal biasa
                (log.debug if quiet else log.warning)("bybit %s gagal: %s", name, e)
                return None, explain(e)
        code = resp.get("retCode", 0)
        if code not in (0, *_NOT_MODIFIED):
            hint = HINTS.get(code)
            return None, f"[{code}] {resp.get('retMsg', '')}" + (f" -> {hint}" if hint else "")
        started = self._network_incidents.pop(name, None)
        if started is not None:
            log.info("Bybit API pulih · %s | request berhasil setelah %.1f detik",
                     self._endpoint_label(name), time.monotonic() - started)
        if name.startswith("get_"):
            for endpoint in list(self._network_incidents):
                if not endpoint.startswith("get_"):
                    del self._network_incidents[endpoint]
                    log.info("Bybit API pulih · %s | koneksi terverifikasi lewat %s. "
                             "Hasil request sebelumnya belum terkonfirmasi.",
                             self._endpoint_label(endpoint), name)
        return resp.get("result") or {}, ""

    def _endpoint_label(self, name):
        return f"{self.account_name} / {name}" if self.account_name else name

    # --- koneksi dan akun -----------------------------------------------------
    def is_demo(self) -> bool:
        return self.mode != "live"

    def connect(self) -> Tuple[bool, str]:
        """Cek server bisa dihubungi dan API key diterima (dengan membaca saldo)."""
        _, err = self._call("get_server_time")
        if err:
            return False, err
        bal, err = self.balance()
        if err:
            return False, err
        return True, f"terhubung ke Bybit {self.mode.upper()}, ekuitas {bal['equity']:.2f} USD"

    def key_info(self) -> Tuple[Optional[dict], str]:
        """Izin API key. Tidak semua mode menyediakan endpoint ini, jadi boleh gagal."""
        r, err = self._call("get_api_key_information")
        if err:
            return None, err
        perms = r.get("permissions") or {}
        flat = [p for v in perms.values() for p in (v or [])]
        return {"read_only": str(r.get("readOnly")) == "1", "permissions": perms,
                "withdraw": any("withdraw" in p.lower() for p in flat),
                "ips": r.get("ips") or [], "expired_at": r.get("expiredAt", "")}, ""

    def balance(self) -> Tuple[Optional[dict], str]:
        r, err = self._call("get_wallet_balance", accountType="UNIFIED")
        if err:
            return None, err
        acc = (r.get("list") or [{}])[0]
        usdt = next((c for c in acc.get("coin") or [] if c.get("coin") == "USDT"), {})
        return {"equity": _f(acc.get("totalEquity")),
                "available": _f(acc.get("totalAvailableBalance")),
                "wallet": _f(acc.get("totalWalletBalance")),
                "usdt": _f(usdt.get("walletBalance")),
                "upnl": _f(usdt.get("unrealisedPnl"))}, ""

    # --- simbol dan harga -----------------------------------------------------
    def spec(self, symbol: str) -> Tuple[Optional[Spec], str]:
        symbol = symbol.upper()
        if symbol in self._specs:
            return self._specs[symbol], ""
        r, err = self._call("get_instruments_info", category=CATEGORY, symbol=symbol)
        if err:
            if "10001" in err and "symbol" in err.lower():
                return None, f"koin {symbol} tidak ada di Bybit USDT Perpetual"
            return None, err
        items = r.get("list") or []
        if not items:
            return None, f"koin {symbol} tidak ada di Bybit USDT Perpetual"
        i = items[0]
        if i.get("status", "Trading") != "Trading":
            return None, f"koin {symbol} ada di Bybit tapi tidak bisa ditrade (status {i.get('status')})"
        pf, lf, lev = i.get("priceFilter", {}), i.get("lotSizeFilter", {}), i.get("leverageFilter", {})
        sp = Spec(_d(pf.get("tickSize")), _d(lf.get("qtyStep")), _d(lf.get("minOrderQty")),
                  _d(lf.get("maxOrderQty")), _d(lf.get("minNotionalValue")),
                  _d(lev.get("maxLeverage")))
        self._specs[symbol] = sp
        return sp, ""

    SCALE_MATCH = 0.30   # koin berkelipatan dipakai kalau harga sinyal sedekat ini dengan harganya

    def find_symbol(self, raw: str, price: Optional[float] = None) -> Tuple[Optional[str], str]:
        """Cari koin di Bybit dari tulisan sinyal: '$STX', 'STX', 'STX/USDT', 'STXUSDT.P',
        'STXUSD' -> 'STXUSDT'. Hasil: (simbol, "") atau (None, alasan + petunjuk).
        Koin berkelipatan (1000PEPEUSDT) hanya dipilih kalau `price` (entry di sinyal) dekat
        dengan harga pasar koin itu: harganya sendiri yang membuktikan skalanya."""
        s = raw.upper().strip()
        s = re.sub(r"\.P$|PERP$", "", s)
        s = re.sub(r"[^A-Z0-9]", "", s)
        if not s:
            return None, "nama koin kosong"
        base = s[:-4] if s.endswith("USDT") else s[:-3] if s.endswith("USD") else s
        tried = []
        for cand in dict.fromkeys([s, f"{base}USDT"]):
            sp, err = self.spec(cand)
            if sp is not None:
                return cand, ""
            tried.append(err)
        # gagal karena koneksi / Bybit menolak permintaan, BUKAN karena koinnya tidak ada:
        # katakan apa adanya, jangan "koin tidak ada"
        other = next((e for e in tried if e and "tidak ada di Bybit" not in e and "status" not in e), "")
        if other:
            return None, (f"{other if other.startswith(NETWORK_PREFIX) else 'Bybit gagal dicek: ' + other}. "
                          f"Koin {s} belum bisa dipastikan, setup belum dieksekusi. Kirim ulang sebentar lagi")
        hints = []
        for alt in (f"1000{base}USDT", f"10000{base}USDT", f"1000000{base}USDT", f"{base}1000USDT"):
            if self.spec(alt)[0] is not None:
                hints.append(alt)
        why = next((e for e in tried if "status" in e), f"koin {base}USDT tidak ada di Bybit USDT Perpetual")
        if hints and price:
            seen = []
            for alt in hints:
                t, _ = self.tick(alt)
                mark = (t or {}).get("mark") or (t or {}).get("last") or 0
                if mark > 0 and abs(price - mark) / mark <= self.SCALE_MATCH:
                    return alt, ""
                seen.append(f"{alt} {mark:g}" if mark else alt)
            return None, (f"{why}. Yang ada: {', '.join(seen)}, tapi harga sinyal {price:g} tidak cocok "
                          f"dengan skala harganya, jadi tidak dieksekusi")
        if hints:
            why += (f". Yang ada: {', '.join(hints)}, tapi harganya dikali kelipatan, "
                    f"jadi tidak dieksekusi otomatis (angka sinyal tidak cocok)")
        return None, why

    def risk_tiers(self, symbol: str) -> List[Tuple[float, Decimal, Decimal]]:
        """Tingkat risk limit: (nilai posisi maks USDT, leverage maks, maintenance margin), urut
        dari yang terkecil. Kosong kalau tidak terbaca."""
        r, err = self._call("get_risk_limit", category=CATEGORY, symbol=symbol.upper())
        out = [(_f(t.get("riskLimitValue")), _d(t.get("maxLeverage")), _d(t.get("maintenanceMargin")))
               for t in (r or {}).get("list") or []]
        return sorted(t for t in out if t[0] > 0 and t[1] > 0)

    def margin_mode(self) -> Tuple[Optional[str], str]:
        """'cross', 'isolated', atau 'portfolio' (mode margin akun Unified)."""
        r, err = self._call("get_account_info")
        if err:
            return None, err
        mode = (r or {}).get("marginMode", "")
        return {"REGULAR_MARGIN": "cross", "ISOLATED_MARGIN": "isolated",
                "PORTFOLIO_MARGIN": "portfolio"}.get(mode, mode.lower() or None), ""

    def tick(self, symbol: str) -> Tuple[Optional[dict], str]:
        r, err = self._call("get_tickers", category=CATEGORY, symbol=symbol.upper())
        if err:
            return None, err
        items = r.get("list") or []
        if not items:
            return None, f"harga {symbol} tidak tersedia"
        t = items[0]
        return {"bid": _f(t.get("bid1Price")), "ask": _f(t.get("ask1Price")),
                "last": _f(t.get("lastPrice")), "mark": _f(t.get("markPrice"))}, ""

    def norm_price(self, sp: Spec, price) -> str:
        """Bulatkan harga ke kelipatan tickSize terdekat."""
        p = _d(price)
        if sp.tick_size <= 0:
            return _s(p)
        return _s((p / sp.tick_size).quantize(Decimal(1), ROUND_HALF_UP) * sp.tick_size)

    def norm_qty(self, sp: Spec, qty) -> str:
        """Bulatkan qty ke BAWAH ke kelipatan qtyStep, dibatasi maxOrderQty."""
        q = _d(qty)
        if sp.qty_step > 0:
            q = (q / sp.qty_step).to_integral_value(ROUND_DOWN) * sp.qty_step
        if sp.max_qty > 0:
            q = min(q, sp.max_qty)
        return _s(q)

    def set_leverage(self, symbol: str, leverage) -> Tuple[bool, str]:
        lev = _s(_d(leverage))
        _, err = self._call("set_leverage", category=CATEGORY, symbol=symbol.upper(),
                            buyLeverage=lev, sellLeverage=lev)
        return (not err), err

    # --- order ----------------------------------------------------------------
    def place(self, symbol: str, side: str, kind: str, qty, price=None, sl=None, tp=None,
              reduce_only: bool = False, link_id: Optional[str] = None) -> Tuple[Optional[str], str]:
        """kind: MARKET | LIMIT | STOP. sl/tp opsional (None = tidak dipasang).
        STOP = order market bersyarat yang aktif saat harga menyentuh `price`
        (BUY STOP di atas harga, SELL STOP di bawah harga). Hasil: (order_id, error)."""
        symbol, kind = symbol.upper(), kind.upper()
        if kind not in ("MARKET", "LIMIT", "STOP"):
            return None, f"jenis order harus MARKET, LIMIT, atau STOP, bukan {kind!r}"
        try:
            bside = _side(side)
        except ValueError as e:
            return None, str(e)
        sp, err = self.spec(symbol)
        if err:
            return None, err
        q = self.norm_qty(sp, qty)
        if _d(q) <= 0 or _d(q) < sp.min_qty:
            return None, f"qty {qty} di bawah minimum {_s(sp.min_qty)} (kelipatan {_s(sp.qty_step)})"
        kw = {"category": CATEGORY, "symbol": symbol, "side": bside,
              "orderType": "Limit" if kind == "LIMIT" else "Market", "qty": q, "positionIdx": 0}
        if kind in ("LIMIT", "STOP") and not price:
            return None, f"order {kind} butuh harga"
        if kind == "LIMIT":
            kw.update(price=self.norm_price(sp, price), timeInForce="GTC")
        elif kind == "STOP":
            kw.update(triggerPrice=self.norm_price(sp, price), triggerBy="LastPrice",
                      triggerDirection=1 if bside == "Buy" else 2)
        if reduce_only:
            kw["reduceOnly"] = True
        if sl:
            kw["stopLoss"] = self.norm_price(sp, sl)
        if tp:
            kw["takeProfit"] = self.norm_price(sp, tp)
        if sl or tp:
            kw["tpslMode"] = "Full"
        if link_id:
            kw["orderLinkId"] = link_id[:36]
        r, err = self._call("place_order", **kw)
        if err:
            return None, err
        return r.get("orderId"), ""

    def amend(self, symbol: str, order_id: str, price=None, qty=None, sl=None,
              tp=None) -> Tuple[bool, str]:
        """Ubah order limit yang belum terisi. Isi hanya yang mau diubah; sl/tp 0 = hapus."""
        symbol = symbol.upper()
        sp, err = self.spec(symbol)
        if err:
            return False, err
        kw = {"category": CATEGORY, "symbol": symbol, "orderId": order_id}
        if price:
            kw["price"] = self.norm_price(sp, price)
        if qty:
            kw["qty"] = self.norm_qty(sp, qty)
        if sl is not None:
            kw["stopLoss"] = self.norm_price(sp, sl) if sl else "0"
        if tp is not None:
            kw["takeProfit"] = self.norm_price(sp, tp) if tp else "0"
        if len(kw) == 3:
            return False, "tidak ada yang diubah"
        _, err = self._call("amend_order", **kw)
        return (not err), err

    def cancel(self, symbol: str, order_id: str) -> Tuple[bool, str]:
        _, err = self._call("cancel_order", category=CATEGORY, symbol=symbol.upper(),
                            orderId=order_id)
        return (not err), err

    def cancel_all(self, symbol: str) -> Tuple[bool, str]:
        _, err = self._call("cancel_all_orders", category=CATEGORY, symbol=symbol.upper())
        return (not err), err

    def set_sl_tp(self, symbol: str, sl=None, tp=None) -> Tuple[bool, str]:
        """Pasang/geser SL-TP posisi yang sedang terbuka. None = tidak diubah, 0 = hapus."""
        symbol = symbol.upper()
        if sl is None and tp is None:
            return False, "tidak ada yang diubah"
        sp, err = self.spec(symbol)
        if err:
            return False, err
        kw = {"category": CATEGORY, "symbol": symbol, "positionIdx": 0, "tpslMode": "Full"}
        if sl is not None:
            kw["stopLoss"] = self.norm_price(sp, sl) if sl else "0"
        if tp is not None:
            kw["takeProfit"] = self.norm_price(sp, tp) if tp else "0"
        _, err = self._call("set_trading_stop", **kw)
        return (not err), err

    def close(self, symbol: str, qty=None) -> Tuple[bool, str]:
        """Tutup posisi dengan market reduce-only. qty None = tutup semua."""
        symbol = symbol.upper()
        pos, err = self.positions(symbol)
        if err:
            return False, err
        if not pos:
            return False, f"tidak ada posisi {symbol} yang terbuka"
        p = pos[0]
        amount = p.size if not qty else min(float(qty), p.size)
        oid, err = self.place(symbol, "SELL" if p.side == "BUY" else "BUY", "MARKET", amount,
                              reduce_only=True)
        return oid is not None, err

    # --- keadaan --------------------------------------------------------------
    def positions(self, symbol: Optional[str] = None) -> Tuple[List[Position], str]:
        kw = {"category": CATEGORY}
        kw.update({"symbol": symbol.upper()} if symbol else {"settleCoin": "USDT"})
        r, err = self._call("get_positions", **kw)
        if err:
            return [], err
        out = []
        for p in r.get("list") or []:
            if _f(p.get("size")) <= 0:
                continue
            out.append(Position(p.get("symbol", ""), _side_back(p.get("side", "")), _f(p.get("size")),
                                _f(p.get("avgPrice")), _f(p.get("stopLoss")),
                                _f(p.get("takeProfit")), _f(p.get("unrealisedPnl")),
                                str(p.get("leverage", "")), _f(p.get("liqPrice")),
                                _f(p.get("markPrice"))))
        return out, ""

    def order(self, symbol: str, order_id: Optional[str] = None,
              link_id: Optional[str] = None) -> Tuple[Optional[dict], str]:
        """Satu order mentah dari Bybit (pakai orderId atau orderLinkId): dicari di order
        terbuka dulu, lalu di riwayat. (None, "") = belum/tidak ditemukan."""
        key = {"orderId": order_id} if order_id else {"orderLinkId": link_id}
        for name in ("get_open_orders", "get_order_history"):
            r, err = self._call(name, category=CATEGORY, symbol=symbol.upper(), **key)
            if err:
                return None, err
            items = r.get("list") or []
            if items:
                return items[0], ""
        return None, ""

    def closed_pnl(self, symbol: Optional[str], since_ms: int) -> Tuple[List[dict], str]:
        """Catatan penutupan posisi sejak `since_ms` (milidetik), terbaru dulu.
        symbol None = semua simbol."""
        kw = {"category": CATEGORY, "startTime": int(since_ms), "limit": 100}
        if symbol:
            kw["symbol"] = symbol.upper()
        r, err = self._call("get_closed_pnl", **kw)
        if err:
            return [], err
        return [x for x in r.get("list") or [] if int(x.get("createdTime") or 0) >= since_ms], ""

    def kline_extreme(self, symbol: str, side: str, since_ms: int, until_ms: int) -> Optional[float]:
        """Harga terbaik sejak `since_ms` dari candle 1 menit: high untuk BUY, low untuk SELL."""
        r, err = self._call("get_kline", category=CATEGORY, symbol=symbol.upper(), interval="1",
                            start=int(since_ms), end=int(until_ms), limit=1000)
        bars = (r or {}).get("list") or []
        if err or not bars:
            return None
        if side == "BUY":
            return max(_f(b[2]) for b in bars)
        return min(_f(b[3]) for b in bars)

    def position_mode(self, symbol: str) -> Tuple[Optional[str], str]:
        """'oneway' atau 'hedge'. Hedge mode mengembalikan dua baris posisi (idx 1 dan 2)."""
        r, err = self._call("get_positions", category=CATEGORY, symbol=symbol.upper())
        if err:
            return None, err
        idx = {int(p.get("positionIdx") or 0) for p in r.get("list") or []}
        return ("hedge" if idx & {1, 2} else "oneway"), ""

    def open_orders(self, symbol: Optional[str] = None) -> Tuple[List[Order], str]:
        kw = {"category": CATEGORY}
        kw.update({"symbol": symbol.upper()} if symbol else {"settleCoin": "USDT"})
        r, err = self._call("get_open_orders", **kw)
        if err:
            return [], err
        out = []
        for o in r.get("list") or []:
            kind = o.get("stopOrderType") or o.get("orderType", "")
            out.append(Order(o.get("orderId", ""), o.get("symbol", ""), _side_back(o.get("side", "")),
                             kind, _f(o.get("qty")), _f(o.get("price")), _f(o.get("triggerPrice")),
                             _f(o.get("stopLoss")), _f(o.get("takeProfit")),
                             o.get("orderStatus", ""), bool(o.get("reduceOnly"))))
        return out, ""
