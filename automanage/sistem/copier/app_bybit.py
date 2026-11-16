"""Bot eksekutor Bybit: alur dan SOP sama persis dengan bot MT5, beda di "colokan" broker.

MT5 punya banyak posisi (satu tiket per kaki TP). Bybit (mode one-way) hanya punya SATU
posisi per koin. Jembatannya:

  * Satu setup = SATU order entry untuk total qty, dengan SL penuh (tpslMode Full).
  * Begitu entry terisi, tiap kaki mendapat order TP sendiri: LIMIT reduce-only di harga
    TP kaki itu, qty kaki itu. Itulah TP parsial.
  * BE / trailing menggeser SL posisi; karena semua kaki berbagi satu posisi, SL-nya sama.
  * Tutup satu kaki = market reduce-only sebesar qty kaki itu.

`BybitAdapter` meniru fungsi-fungsi `Broker` MT5 (place, leg_state, modify_sl, ...), jadi
gerbang, penjaga posisi (guard), jurnal, dan semua tombol Telegram dipakai apa adanya.
Tiket satu kaki berupa teks "SIMBOL|orderId entry|tag|idx|qty|tp", sehingga semua yang
perlu diketahui tentang kaki itu tetap ada setelah bot restart.
"""
import logging
import re
import time
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from functools import partial
from types import SimpleNamespace
from typing import Optional, Tuple

from .app import App
from .broker import LegState
from .executor import Executor
from .gate import Gate
from .guard import Guard
from .models import ACTIVE, AWAITING, LEG_OPEN, LEG_PENDING, PENDING, REJECTED, RUNNING
from .profiles import Profile, SingleProfile
from .sizing import SymbolSpec, plan_legs

log = logging.getLogger("copier.bybit")

_OPEN = ("New", "PartiallyFilled", "Untriggered", "Triggered", "Active", "Created")
_SL_KINDS = ("StopLoss", "PartialStopLoss", "TrailingStop")
_TP_KINDS = ("TakeProfit", "PartialTakeProfit")


def _f(x) -> float:
    try:
        return float(x) if x not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def make_ticket(symbol, order_id, tag, idx, qty, tp) -> str:
    return f"{symbol}|{order_id}|{tag}|{idx}|{qty}|{tp}"


def parse_ticket(ticket: str):
    sym, oid, tag, idx, qty, tp = str(ticket).split("|")
    return sym, oid, tag, int(idx), float(qty), float(tp)


def tp_link(tag: str, idx: int) -> str:
    return f"{tag}-tp{idx + 1}"


_SL_MOVE = re.compile(r"^🔒 #(\d+) (\S+): SL kaki \d+ → (\S+)$")


def _risk_text(cfg) -> str:
    r = cfg["risiko"]
    return f"{r['persen']}% saldo" if r["mode"] == "persen" else f"{r['dolar']} USDT"


def bybit_words(text: str) -> str:
    """Pesan bot MT5 -> istilah Bybit: MT5 -> Bybit, lot -> koin, "Lot total" -> "Qty total"."""
    text = text.replace("MT5", "Bybit").replace("Lot total", "Qty total")
    return re.sub(r"\blot\b", "koin", text)


class BybitAdapter:
    """Bicara seperti `copier.broker.Broker` (MT5), tapi menjalankan semuanya di Bybit."""

    def __init__(self, cfg: dict, bybit, clock=time.time):
        self.b, self.clock = bybit, clock
        self.by = cfg["bybit"]
        self.journal = None
        self.activity = None

    # --- akun ---------------------------------------------------------------
    def account(self):
        bal, err = self.b.balance()
        if err:
            log.warning("saldo Bybit tidak terbaca: %s", err)
            bal = {"wallet": 0.0, "equity": 0.0}
        return SimpleNamespace(login=f"Bybit {self.b.mode}", balance=bal["wallet"],
                               equity=bal["equity"], currency="USDT")

    def is_demo(self) -> bool:
        return self.b.is_demo()

    def money_factor(self) -> float:
        return 1.0

    def is_hedging(self) -> bool:   # tidak dipakai: Bybit justru wajib one-way
        return False

    # --- simbol dan harga ---------------------------------------------------
    def symbol_info(self, symbol):
        sp, err = self.b.spec(symbol)
        return sp if not err else None

    def tick(self, symbol) -> Tuple[float, float, float]:
        t, err = self.b.tick(symbol)
        if err:
            return 0.0, 0.0, 0.0
        return t["bid"], t["ask"], self.clock()

    def signal_price(self, symbol) -> float:
        t, err = self.b.tick(symbol)
        return t["last"] if not err else 0.0

    def spec(self, symbol) -> SymbolSpec:
        """1 "lot" di Bybit = 1 koin. Qty maks dibatasi nilai posisi maks (USDT) di config."""
        sp, _ = self.b.spec(symbol)
        bid, ask, _ = self.tick(symbol)
        vmax = float(sp.max_qty) if sp.max_qty > 0 else float("inf")
        cap = float(self.by.get("nilai_posisi_maks_usdt") or 0)
        if cap > 0 and ask > 0:
            vmax = min(vmax, cap / ask)
        tick = float(sp.tick_size)
        return SymbolSpec(tick, tick, float(sp.min_qty), vmax, float(sp.qty_step))

    def normalize(self, symbol, price: Optional[float]) -> float:
        if not price:
            return 0.0
        sp, _ = self.b.spec(symbol)
        return float(self.b.norm_price(sp, price))

    def min_stop_distance(self, symbol) -> float:
        return 0.0

    def loss_per_lot(self, symbol, side, entry, sl) -> float:
        return abs(float(entry) - float(sl))   # USDT per 1 koin

    # --- leverage -----------------------------------------------------------
    def leverage_for(self, symbol, qty, price, sl) -> Tuple[Decimal, str]:
        """Leverage untuk satu entry. Mode "maks": leverage tertinggi koin ini untuk nilai posisi
        segini (tingkat risk limit Bybit). Qty TIDAK ikut berubah: rugi kalau kena SL tetap
        = risiko per setup; leverage cuma menentukan margin yang dipakai.
        Akun isolated: leverage diturunkan supaya harga likuidasi tetap di luar SL."""
        if self.by.get("leverage_mode", "maks") != "maks":
            return Decimal(str(self.by.get("leverage", 10))), "tetap"
        notional = float(qty) * float(price)
        tiers = self.b.risk_tiers(symbol)
        tier = next((t for t in tiers if t[0] >= notional), tiers[-1] if tiers else None)
        if tier:
            lev, mmr = tier[1], tier[2]
        else:
            sp, _ = self.b.spec(symbol)
            lev, mmr = (sp.max_leverage if sp and sp.max_leverage > 0 else Decimal(10)), Decimal("0.005")
        note = "maks koin ini"
        if self.margin_mode() in ("isolated", None) and price and sl:   # tidak terbaca = anggap isolated
            dist = Decimal(str(abs(float(price) - float(sl)) / float(price)))
            safe = (Decimal(1) / (dist + mmr + Decimal("0.002"))).to_integral_value(ROUND_DOWN)
            if safe < lev:
                lev, note = max(Decimal(1), safe), "isolated: diturunkan agar likuidasi di luar SL"
        return lev.to_integral_value(ROUND_DOWN) if lev >= 1 else Decimal(1), note

    def margin_mode(self) -> Optional[str]:
        if getattr(self, "_margin", None) is None:     # gagal baca tidak disimpan: dicoba lagi nanti
            self._margin, _ = self.b.margin_mode()
        return self._margin

    # --- order --------------------------------------------------------------
    def place_entry(self, symbol, side, kind, qty, price, sl, tag) -> Tuple[Optional[str], str]:
        ref = price if price else self.tick(symbol)[1]
        lev, note = self.leverage_for(symbol, qty, ref, sl)
        ok, err = self.b.set_leverage(symbol, lev)
        if not ok:
            log.warning("set leverage %sx %s gagal: %s", lev, symbol, err)
        self.last_leverage = (lev, note)
        return self.b.place(symbol, side, kind, qty, price if kind != "MARKET" else None, sl=sl,
                            link_id=tag)

    def place(self, *a, **k):
        raise NotImplementedError("Bybit memakai place_entry lewat BybitExecutor")

    def cancel(self, ticket) -> Tuple[bool, str]:
        """Batalkan entry yang belum terisi. Semua kaki berbagi satu entry, jadi kaki
        kedua dst. menemukan entry sudah batal: itu tetap dihitung berhasil."""
        sym, oid, *_ = parse_ticket(ticket)
        ok, err = self.b.cancel(sym, oid)
        if ok:
            return True, ""
        o, _ = self.b.order(sym, order_id=oid)
        if o and o.get("orderStatus") not in _OPEN and _f(o.get("cumExecQty")) <= 0:
            return True, ""
        return False, err

    def modify_sl(self, symbol, ticket, sl: float, tp: float) -> Tuple[bool, str]:
        return self.b.set_sl_tp(symbol, sl=sl)

    def close(self, ticket) -> Tuple[bool, str]:
        sym, oid, tag, idx, qty, tp = parse_ticket(ticket)
        entry = self.entry_for_guard(ticket)
        if entry is None or entry.get("orderStatus") in _OPEN:
            return False, "Status akhir entry belum terkonfirmasi"
        qty = self._filled_leg_qty(sym, tag, idx, qty, entry)
        if not qty:
            return False, "Kuantitas kaki yang terisi belum tersedia"
        tpo, err = self.b.order(sym, link_id=tp_link(tag, idx))
        if err:
            return False, err
        if tpo and tpo.get("orderStatus") in _OPEN:
            ok, err = self.b.cancel(sym, tpo["orderId"])
            if not ok:
                return False, err
            tpo, err = self.b.order(sym, order_id=tpo["orderId"])
            if err or not tpo or tpo.get("orderStatus") in _OPEN:
                return False, err or "Pembatalan TP belum terkonfirmasi"
        qty = round(qty - _f((tpo or {}).get("cumExecQty")), 12)
        if qty <= 0:
            return False, "Kaki sudah ditutup oleh TP"
        return self.b.close(sym, qty)

    # --- keadaan satu kaki --------------------------------------------------
    def leg_state(self, ticket) -> LegState:
        sym, oid, *_ = parse_ticket(ticket)
        entry, err = self.b.order(sym, order_id=oid)
        return self._leg_state(ticket, None if err else entry)

    def entry_for_guard(self, ticket):
        """Cancel the unfilled remainder once per pass, then read the final fill from Bybit."""
        sym, oid, tag, *_ = parse_ticket(ticket)
        entry, err = self.b.order(sym, order_id=oid)
        if err or entry is None:
            return None
        if entry.get("orderStatus") in _OPEN and _f(entry.get("cumExecQty")) > 0:
            ok, why = self.b.cancel(sym, oid)
            if not ok:
                log.warning("sisa entry Bybit %s %s belum dibatalkan: %s", sym, oid, why)
            refreshed, err = self.b.order(sym, order_id=oid)
            if refreshed and not err:
                if refreshed.get("orderStatus") in ("Cancelled", "PartiallyFilledCanceled"):
                    self._entry_activity(tag, f"Sisa entry batal terkonfirmasi | qty terisi {_f(refreshed.get('cumExecQty')):g}")
                return refreshed
        return entry

    def _entry_activity(self, tag, message):
        if self.activity and self.journal:
            row = self.journal.get(int(tag.split("-", 1)[0][2:]))
            if row:
                self.activity(row, message)

    def _filled_leg_qty(self, sym, tag, idx, planned, entry) -> Optional[float]:
        filled = _f(entry.get("cumExecQty"))
        if filled >= _f(entry.get("qty")) > 0:
            return planned
        if not filled:
            return 0.0
        if self.journal is None:
            return None
        # Old and new tickets both encode the journal setup ID in the entry link.
        legs = self.journal.legs(int(tag.split("-", 1)[0][2:]))
        instrument, err = self.b.spec(sym)
        if err or not legs:
            return None
        spec = SymbolSpec(0, 0, float(instrument.min_qty), float(instrument.max_qty),
                          float(instrument.qty_step))
        split = {len(legs): [leg["volume"] for leg in legs]}
        allocation = plan_legs(filled, [leg["tp"] for leg in legs], split, spec)
        if not allocation:
            # Even a fill below the minimum TP order size is still a live position.
            return filled if idx == legs[0]["idx"] else 0.0
        return next((qty for leg, (qty, _) in zip(legs, allocation) if leg["idx"] == idx), 0.0)

    def _leg_state(self, ticket, e) -> LegState:
        sym, _, tag, idx, planned, tp = parse_ticket(ticket)
        if e is None:
            return LegState("unknown")
        entry_open = e.get("orderStatus") in _OPEN
        qty = self._filled_leg_qty(sym, tag, idx, planned, e)
        if qty is None:
            return LegState("unknown")
        if not qty:
            if not entry_open:
                return LegState("cancelled")
            return LegState("pending", price=_f(e.get("price")) or _f(e.get("triggerPrice")),
                            sl=_f(e.get("stopLoss")), tp=tp, volume=planned)

        fill = _f(e.get("avgPrice"))
        filled_at = int(e.get("updatedTime") or 0) / 1000 or self.clock()
        sign = 1 if e.get("side") == "Buy" else -1
        link = tp_link(tag, idx)
        tpo, err = self.b.order(sym, link_id=link)
        if err:
            return LegState("unknown")
        if tpo and tpo.get("orderStatus") == "Filled":
            px = _f(tpo.get("avgPrice")) or tp
            return LegState("closed", price=px, open_price=fill, profit=round((px - fill) * qty * sign, 4),
                            reason="tp")

        pos, err = self.b.positions(sym)
        if err:
            return LegState("unknown")
        if pos:
            p = pos[0]
            if tp and tpo is None and not entry_open:   # size only once the remainder is settled
                opp = "SELL" if sign > 0 else "BUY"
                tp_qty = min(qty, p.size)
                tid, terr = self.b.place(sym, opp, "LIMIT", tp_qty, price=tp,
                                         reduce_only=True, link_id=link)
                if tid is None:
                    log.warning("TP%d %s belum terpasang: %s", idx + 1, sym, terr)
                else:
                    self._entry_activity(tag, f"TP{idx + 1} reduce-only diterima Bybit | qty {tp_qty:g} @ {tp:g}")
            return LegState("open", price=fill, sl=p.sl, tp=tp, volume=qty,
                            profit=round((p.mark - fill) * qty * sign, 4), time=filled_at)

        # posisi sudah tidak ada: kaki ini ikut tertutup (SL, ditutup manual, atau likuidasi)
        if entry_open:
            return LegState("unknown")   # the remainder could still fill before cancellation
        if tpo and tpo.get("orderStatus") in _OPEN:
            self.b.cancel(sym, tpo["orderId"])
        since = int(filled_at * 1000)
        if _f(e.get("cumExecQty")) < _f(e.get("qty")):
            since = int(e.get("createdTime") or since)
        recs, err = self.b.closed_pnl(sym, since)
        if err or not recs:
            return LegState("unknown")   # catatan penutupan belum muncul: cek lagi nanti
        last = recs[0]
        exit_px = _f(last.get("avgExitPrice"))
        closer, _ = self.b.order(sym, order_id=last.get("orderId")) if last.get("orderId") else (None, "")
        kind = (closer or {}).get("stopOrderType", "")
        reason = "sl" if kind in _SL_KINDS else "tp" if kind in _TP_KINDS else "other"
        return LegState("closed", price=exit_px, open_price=fill,
                        profit=round((exit_px - fill) * qty * sign, 4), reason=reason)

    # --- akun hari ini dan riwayat harga ------------------------------------
    def day_pnl(self, symbol_for_clock: str) -> Tuple[float, float, float]:
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        recs, _ = self.b.closed_pnl(None, start)
        realized = sum(_f(r.get("closedPnl")) for r in recs)
        pos, _ = self.b.positions()
        floating = sum(p.pnl for p in pos)
        return realized, floating, self.account().balance - realized

    def bot_tickets(self) -> set:
        return set()

    def rates_extreme(self, symbol, side, since: float, until: float) -> Optional[float]:
        return self.b.kline_extreme(symbol, side, int(since * 1000), int((until + 60) * 1000))


class BybitGuard(Guard):
    def _leg_states(self, legs):
        if legs:
            entry = self.broker.entry_for_guard(legs[0]["ticket"])
            for leg in legs:
                yield leg, self.broker._leg_state(leg["ticket"], entry)


class BybitExecutor(Executor):
    """Satu entry untuk total qty semua kaki; TP parsial dipasang adaptor setelah terisi."""

    def execute(self, setup_id, s, market, plan) -> Tuple[bool, str]:
        wanted = [(i, vol, tp) for i, (vol, tp) in enumerate(plan.legs) if vol > 0]
        total = float(sum(Decimal(str(v)) for _, v, _ in wanted))
        tag = f"su{setup_id}-{int(self.clock()) % 10 ** 8}"
        oid, err = self.broker.place_entry(market.symbol, s.side, market.kind, total, plan.entry,
                                           s.sl, tag)
        if oid is None:
            self.journal.update(setup_id, status=REJECTED, reason=f"Order ditolak Bybit: {err}")
            self.journal.event(setup_id, "order_failed", err)
            return False, err
        leg_status = LEG_OPEN if market.kind == "MARKET" else LEG_PENDING
        for i, vol, tp in wanted:
            self.journal.add_leg(setup_id, i, make_ticket(market.symbol, oid, tag, i, vol, tp),
                                 vol, tp, leg_status)
        self.journal.update(setup_id, status=RUNNING if market.kind == "MARKET" else PENDING,
                            broker_symbol=market.symbol, risk_amount=plan.actual_risk,
                            total_lot=plan.total_lot, expires=self.expiry_for(s)[0],
                            order_type=market.kind, watch_entry_price=self.watch_entry_price(s))
        self.journal.event(setup_id, "placed", f"{market.kind} 1 entry {total} untuk {len(wanted)} "
                                               f"kaki TP, risiko {plan.actual_risk}")
        return True, ""


class BybitGate(Gate):
    """Gerbang MT5 + aturan Bybit: koin dicari sendiri di Bybit (tanpa daftar), dan satu
    posisi per koin, jadi satu setup aktif per koin."""

    _price = None

    def broker_symbol(self, raw: str):
        # tanpa daftar pair: koin selalu dicari dan dicocokkan sendiri di Bybit (keputusan user);
        # harga sinyal ikut dikirim supaya koin berkelipatan (1000PEPE) bisa dicocokkan dari harganya
        sym, self._why = self.broker.b.find_symbol(raw, self._price)
        return sym

    def check(self, s, posted=None, skip_id=None, recheck=False):
        self._why = ""
        self._price = s.entry or s.sl
        v = super().check(s, posted=posted, skip_id=skip_id, recheck=recheck)
        if v.market is not None and not recheck:
            plain = re.sub(r"\.P$|[^A-Z0-9]", "", s.symbol.upper().replace(".P", ""))
            if v.market.symbol not in (plain, f"{plain}USDT", f"{plain[:-3]}USDT"):
                v.warnings.append(f"Koin {s.symbol} di Bybit adalah {v.market.symbol}: harga sinyal "
                                  "cocok dengan skala koin berkelipatan ini")
        v.reasons = [self._why if (r.startswith("Simbol ") and "tidak ada" in r and self._why)
                     else r.replace("MT5", "Bybit") for r in v.reasons]
        if v.market is not None:
            sym = v.market.symbol
            # Saat setup masuk: yang masih menunggu konfirmasi juga dihitung. Saat mau dieksekusi
            # (recheck): setup ini sendiri masih "menunggu", jadi cukup yang sudah aktif.
            states = ACTIVE if recheck else (AWAITING, *ACTIVE)
            busy = [r for r in self.journal.by_status(*states)
                    if r["broker_symbol"] == sym and r["id"] != skip_id
                    and (self.same_account is None or self.same_account(r))]
            if busy:
                word = "menunggu konfirmasi" if busy[0]["status"] == AWAITING else "aktif"
                who = f" dari sumber {busy[0]['kode']}" if busy[0].get("kode") else ""
                v.reasons.append(f"Sudah ada setup {word} #{busy[0]['id']}{who} di {sym} "
                                 f"(Bybit: satu posisi per koin per akun)")
            else:
                pos, _ = self.broker.b.positions(sym)
                if pos:
                    v.reasons.append(f"Sudah ada posisi {sym} di Bybit di luar bot")
        v.ok = not v.reasons
        return v


class BybitApp(App):
    DEST = "bybit"   # hanya postingan berlabel "Ke: bybit" yang dikerjakan bot ini
    AUTO_SOURCE_CANCEL = True

    def __init__(self, cfg, broker: BybitAdapter, journal, bot, inbox_chat, admin_id, clock=time.time,
                 sources_root=None, universal_path=None, brokers=None, accounts_folder=None,
                 make_broker=None):
        """broker: adaptor akun utama (juga untuk setup lama tanpa sumber). brokers: nama akun ->
        BybitBroker (akun-bybit.env). make_broker(akun) -> BybitBroker untuk akun yang baru
        ditambah saat bot jalan."""
        # "lot maksimal" MT5 tidak berlaku di Bybit (1 lot = 1 koin): batasnya nilai posisi USDT,
        # yang sudah dipasang adaptor di spec(). Tanpa ini bawaan MT5 (5) berarti 5 koin.
        cfg["risiko"]["lot_maks"] = float("inf")
        self.brokers = dict(brokers or {})
        self.primary = next(iter(self.brokers), "")
        self.accounts_folder, self.make_broker = accounts_folder, make_broker
        self._checked = {}          # nama akun -> "" (siap) atau alasan ditolak
        self._last_sl = None
        super().__init__(cfg, broker, journal, bot, inbox_chat, admin_id, clock=clock,
                         sources_root=sources_root, universal_path=universal_path)

    # --- SOP per sumber + akun per sumber -------------------------------------
    def extra_setting_paths(self) -> list:
        from . import accounts
        return [accounts.path(self.accounts_folder)] if self.make_broker else []

    def _account(self, name: str):
        """(BybitBroker, "") atau (None, alasan) untuk akun bernama ini."""
        if not name:
            return None, "sumber Bybit ini belum memilih akun (MENU > Sumber sinyal > Akun Bybit)"
        if name not in self.brokers and self.make_broker:
            from . import accounts
            acc = accounts.by_name(accounts.load(self.accounts_folder), name)
            if acc is not None:
                try:
                    self.brokers[name] = self.make_broker(acc)
                except ValueError as e:
                    return None, f"akun Bybit '{name}': {e}"
        if name not in self.brokers:
            return None, f"akun Bybit '{name}' tidak ada di akun-bybit.env"
        if name not in self._checked:
            self._checked[name] = self.check_account(self.brokers[name], name)
        if self._checked[name]:
            return None, self._checked[name]
        return self.brokers[name], ""

    def _account_of(self, row) -> str:
        prof = getattr(self.profiles, "by_code", {}).get((row.get("kode") or "").upper())
        return (prof.extra.get("akun") if prof is not None else "") or self.primary

    def build_profile(self, code, src, cfg, accept) -> Profile:
        cfg["risiko"]["lot_maks"] = float("inf")
        name = f"{src.nama} ({code})" if src is not None else ""
        if src is None:   # cadangan / satu akun: adaptor utama
            adapter, akun = (self.broker if not code and cfg is self.cfg else
                             BybitAdapter(cfg, self.broker.b, clock=self.clock)), self.primary
        else:
            akun = src.akun
            bybit, why = self._account(akun)
            if bybit is None:
                return Profile(code, name, cfg, src=src, blocked=f"Sumber {name}: {why}")
            adapter = BybitAdapter(cfg, bybit, clock=self.clock)
        adapter.journal = self.journal
        adapter.activity = partial(self.activity, account=akun)
        gate = BybitGate(cfg, adapter, self.journal, self.clock, accept=accept, label=name)
        if src is not None or self.brokers:
            gate.same_account = lambda r, a=akun: self._account_of(r) == a
        return Profile(code, name, cfg, broker=adapter, src=src, gate=gate,
                       executor=BybitExecutor(cfg, adapter, self.journal, self.clock),
                       guard=BybitGuard(cfg, adapter, self.journal, self.notify, self.clock, accept=accept,
                                        activity=adapter.activity),
                       extra={"akun": akun})

    def check_account(self, b, name: str = "") -> str:
        """Syarat satu akun Bybit: mode demo (kalau hanya demo), tersambung, API key tanpa izin
        Withdraw, one-way, cross margin. "" kalau siap, selain itu alasannya."""
        who = f"Akun Bybit '{name}'" if name else "Akun Bybit"
        if self.cfg["akun"]["hanya_demo"] and not b.is_demo():
            return (f"BYBIT_MODE=live tapi akun.hanya_demo: true ({who}). "
                    f"Matikan 'hanya demo' di Setelan universal Bybit kalau memang siap live.")
        ok, msg = b.connect()
        if not ok:
            return f"Tidak bisa masuk ke Bybit ({who}): {msg}"
        info, _ = b.key_info()
        if info and info.get("withdraw"):
            return f"{who}: API key punya izin Withdraw. Buat key baru tanpa Withdraw (hanya Contract)."
        mode, _ = b.position_mode("BTCUSDT")
        if mode == "hedge":
            return (f"{who} dalam hedge mode. Ubah ke one-way mode "
                    f"(Pengaturan derivatif > Position mode), lalu nyalakan ulang.")
        margin, _ = b.margin_mode()
        if margin != "cross":   # keputusan user: akun Bybit WAJIB cross margin
            return (f"Mode margin {who}: {margin or 'tidak terbaca'}. Bot hanya jalan di "
                    f"CROSS margin (leverage maks aman karena likuidasi dihitung dari saldo akun). "
                    f"Ubah di Bybit: Aset > Akun Unified > Mode margin > Cross Margin, "
                    f"lalu nyalakan ulang bot.")
        return ""

    def notify(self, text, buttons=None):
        # Satu posisi = satu SL: geser SL untuk kaki 2, 3, 4 adalah SATU kejadian, satu pesan.
        m = _SL_MOVE.match(text)
        if m:
            key = (m.group(1), m.group(3))
            if key == self._last_sl:
                return None
            self._last_sl = key
            text = f"🔒 #{m.group(1)} {m.group(2)}: SL posisi → {m.group(3)}"
        return super().notify(bybit_words(text), buttons=buttons)

    def _summary(self, sid, s, v, plan, prof=None) -> str:
        """Konfirmasi MT5 + baris leverage dan margin yang akan dipakai (+ akun Bybit)."""
        prof = prof or self.profiles.for_row({"kode": s.code})
        lines = super()._summary(sid, s, v, plan, prof).split("\n")
        try:
            lev, note = prof.broker.leverage_for(v.market.symbol, plan.total_lot, plan.entry, s.sl)
            margin = plan.total_lot * plan.entry / float(lev)
            extra = f"Leverage {lev}x ({note}) · margin ≈ {margin:.2f} USDT"
        except Exception:   # info saja; jangan gagalkan konfirmasi
            log.exception("hitung leverage gagal")
            extra = "Leverage: tidak terbaca, dicek lagi saat eksekusi"
        at = next((i for i, l in enumerate(lines) if l.startswith("Lot total")), len(lines) - 1)
        lines.insert(at + 1, extra)
        if prof.src is not None and prof.extra.get("akun"):
            lines.insert(at + 2, f"Akun Bybit: {prof.extra['akun']}")
        return "\n".join(lines)

    def _execute(self, sid) -> str:
        row = self.journal.get(sid) or {}
        adapter = self.profiles.for_row(row).broker
        prof, _ = self.profiles.get(row.get("kode") or "")
        adapter = prof.broker if prof is not None else adapter
        adapter.last_leverage = None
        text = super()._execute(sid)
        lev = getattr(adapter, "last_leverage", None)
        if text.startswith("📌") and lev:
            text += f", leverage {lev[0]}x"
        akun = prof.extra.get("akun") if prof is not None else ""
        if text.startswith("📌") and akun and len(self.brokers) > 1:
            text += f", akun {akun}"
        return text

    def _reply(self, msg_id, text):
        super()._reply(msg_id, bybit_words(text))

    def _edit(self, row, text):
        super()._edit(row, bybit_words(text))

    def startup_checks(self) -> bool:
        by = self.cfg["bybit"]
        if isinstance(self.profiles, SingleProfile):   # satu akun, satu SOP (susunan lama / tes)
            why = self.check_account(self.broker.b)
            if why:
                log.error("Bot Bybit belum bisa jalan: %s", why)
                return False
            _, msg = self.broker.b.connect()
            lev = "maks per koin" if by.get("leverage_mode", "maks") == "maks" else f"tetap {by['leverage']}x"
            lines = [f"🤖 Bot Bybit nyala. {msg}",
                     f"Mode margin cross · leverage {lev} · nilai posisi maks {by['nilai_posisi_maks_usdt']} USDT"]
        else:
            profs = list(self.profiles.by_code.values())
            bad = [p.blocked for p in profs if p.blocked]
            if bad:
                for reason in bad:
                    log.error("Bot Bybit belum bisa jalan: %s", reason)
                return False
            lines = ["🤖 Bot Bybit nyala. Mode margin cross semua akun · "
                     f"nilai posisi maks {by['nilai_posisi_maks_usdt']} USDT"]
            for name in sorted({p.extra.get("akun") for p in profs}):
                _, msg = self.brokers[name].connect()
                users = ", ".join(p.code for p in profs if p.extra.get("akun") == name)
                lines.append(f"Akun {name} ({users}): {msg}")
            for p in profs:
                b = p.cfg["bybit"]
                lev = "maks" if b.get("leverage_mode", "maks") == "maks" else f"tetap {b['leverage']}x"
                lines.append(f"{p.name}: risiko {_risk_text(p.cfg)}, leverage {lev}")
            if not profs:
                lines.append("⚠️ Belum ada sumber ke Bybit (MENU > Sumber sinyal). Semua setup akan ditolak.")
        lines.append("Koin dicari sendiri di Bybit USDT Perpetual (tanpa daftar pair).")
        lines.append(f"Setup aktif di jurnal: {len(self.journal.by_status(*ACTIVE))}")
        if self.journal.get_kv("paused", False):
            lines.append("⏸ Masih dalam mode stop. /lanjut untuk menerima setup.")
        self.notify("\n".join(lines))
        return True
