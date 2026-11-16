"""Position keeper: follows every active setup in the journal and applies the SOP.

All state lives in the journal and in MT5 itself, so after a restart the guard simply
picks up where it left off: it re-reads each leg from MT5 and recomputes what the SL
should be. Failed SL moves are retried on the next pass.
"""
import logging
from datetime import datetime

from .config import EXPIRY_SIGNAL_TIME_PRICE
from .models import (CANCELLED, DONE, EXPIRED, LEG_CANCELLED, LEG_CLOSED, LEG_FINAL, LEG_OPEN,
                     LEG_PENDING, LEG_SL, LEG_TP, PENDING, RUNNING, price_limit_reason)
from .setup_format import WIB, price_limit_from_text

log = logging.getLogger(__name__)
RETRY_SEC = 15


class Guard:
    def __init__(self, cfg: dict, broker, journal, notify, clock, accept=None, activity=None):
        """accept(baris setup) -> bool: setup mana yang dijaga guard ini (SOP per sumber);
        None = semua setup."""
        self.cfg, self.broker, self.journal, self.notify, self.clock = cfg, broker, journal, notify, clock
        self.sop = cfg["sop"]
        self.accept = accept
        self.activity = activity or (lambda row, message: None)
        self._observed = set()

    def mine(self, *statuses):
        rows = self.journal.by_status(*statuses)
        return rows if self.accept is None else [r for r in rows if self.accept(r)]

    def run_once(self):
        setups = self.mine(PENDING, RUNNING)
        active_ids = {s["id"] for s in setups}
        self._observed.intersection_update(active_ids)
        for s in setups:
            try:
                self._one(s)
            except Exception:  # one bad setup must not stop the others
                log.exception("guard failed on setup %s", s["id"])

    # --- per setup ----------------------------------------------------------
    def _leg_states(self, legs):
        for leg in legs:
            yield leg, self.broker.leg_state(leg["ticket"])

    def _one(self, s):
        sid, legs = s["id"], self.journal.legs(s["id"])
        live = {}
        observed = set()
        for leg, st in self._leg_states([l for l in legs if l["status"] not in LEG_FINAL]):
            observed.add(st.state)
            self._sync_leg(s, leg, st)
            if st.state == "open":
                live[leg["id"]] = st
        legs = self.journal.legs(sid)
        if sid not in self._observed:
            if s["status"] == RUNNING and live:
                self.activity(s, "Posisi terbuka di broker | dipantau")
                self._observed.add(sid)
            elif observed == {"pending"}:
                self.activity(s, "Order masih menunggu di broker")
                self._observed.add(sid)

        if s["status"] == PENDING and any(l["status"] == LEG_OPEN or l["status"] in (LEG_TP, LEG_SL, LEG_CLOSED)
                                          for l in legs):
            self.journal.update(sid, status=RUNNING)
            s["status"] = RUNNING
            fills = [l["fill_price"] for l in legs if l["fill_price"]]
            self.notify(f"✅ #{sid} {s['broker_symbol']} {s['side']} ke-fill di {fills[0] if fills else '-'}")

        pending = [l for l in legs if l["status"] == LEG_PENDING]
        if pending and s["status"] == PENDING:
            self._cancel_invalid_entry(s, pending)

        if live:
            self._apply_sop(s, legs, live)

        if all(l["status"] in LEG_FINAL for l in self.journal.legs(sid)):
            self._finish(s)

    def _cancel_invalid_entry(self, s, pending):
        cause, reason = s["pending_cancel"], s["reason"]
        first_attempt = not cause
        if first_attempt:
            if s["expires"] and self.clock() >= s["expires"]:
                until = datetime.fromtimestamp(s["expires"], WIB)
                cause, reason = EXPIRED, f"Limit tidak ke-fill sampai batas waktu ({until:%d/%m %H:%M} WIB)"
            else:
                watch_price = s["watch_entry_price"]
                if watch_price is None:
                    watch_price = self.cfg["entry"]["kedaluwarsa"] == EXPIRY_SIGNAL_TIME_PRICE
                if not watch_price:
                    return
                limit = price_limit_from_text(s["raw"])
                if not limit:
                    return
                price = self.broker.signal_price(s["broker_symbol"])
                reason = price_limit_reason(limit, price)
                if not reason:
                    return
                cause = CANCELLED
            # Keep the decision across retries and restarts, even if the price recovers.
            self.journal.update(s["id"], pending_cancel=cause, reason=reason)
            self.journal.event(s["id"], "cancel_requested", reason)

        for leg in pending:
            ok, err = self.broker.cancel(leg["ticket"])
            if first_attempt:
                result = "permintaan dikirim" if ok else f"belum berhasil: {err}"
                self.activity(s, f"{reason} | batal kaki TP{leg['idx'] + 1}: {result}")

    def _sync_leg(self, s, leg, st):
        sid = s["id"]
        if st.state == "open" and leg["status"] == LEG_PENDING:
            self.journal.update_leg(leg["id"], status=LEG_OPEN, fill_price=st.price, fill_time=st.time)
            self.journal.event(sid, "filled", f"kaki {leg['idx'] + 1} di {st.price}")
            self.activity(s, f"Entry terisi | kaki TP{leg['idx'] + 1} | qty {st.volume:g} @ {st.price:g}")
        elif st.state == "open" and not leg["fill_price"]:   # market legs: record the real fill
            self.journal.update_leg(leg["id"], fill_price=st.price, fill_time=st.time)
        elif st.state == "closed":
            status = {"tp": LEG_TP, "sl": LEG_SL}.get(st.reason, LEG_CLOSED)
            self.journal.update_leg(leg["id"], status=status, profit=st.profit,
                                    fill_price=leg["fill_price"] or st.open_price or None,
                                    close_time=self.clock())
            self.journal.event(sid, status, f"kaki {leg['idx'] + 1} di {st.price}, {st.profit:+.2f}")
            label = {LEG_TP: f"🎯 TP{leg['idx'] + 1} kena", LEG_SL: "🛑 Kena SL"}.get(status, "Ditutup")
            self.activity(s, f"{label} | kaki TP{leg['idx'] + 1} @ {st.price:g} | PnL {st.profit:+.2f}")
            self.notify(f"{label}: #{sid} {s['broker_symbol']} kaki {leg['idx'] + 1}, {st.profit:+.2f}")
        elif st.state == "cancelled":
            self.journal.update_leg(leg["id"], status=LEG_CANCELLED)
            self.journal.event(sid, "leg_cancelled", f"kaki {leg['idx'] + 1}")
            self.activity(s, f"Kaki TP{leg['idx'] + 1} batal terkonfirmasi di broker")

    def _apply_sop(self, s, legs, live):
        sid, sym, side, tps = s["id"], s["broker_symbol"], s["side"], s["tps"]
        buy = side == "BUY"
        bid, ask, now_srv = self.broker.tick(sym)
        if bid <= 0:
            return
        exit_price = bid if buy else ask   # price a position closes at

        peak = s["peak"]
        if peak is None:   # first look (or after restart): include moves we did not watch
            first_fill = min((st.time for st in live.values() if st.time), default=0)
            seen = self.broker.rates_extreme(sym, side, first_fill, now_srv) if first_fill else None
            if seen is not None and not buy:
                seen += ask - bid      # bars are bid prices; a SELL closes at ask
            peak = seen if seen is not None else exit_price
        peak = max(peak, exit_price) if buy else min(peak, exit_price)

        by_price = sum(1 for t in tps if (peak >= t if buy else peak <= t))
        by_legs = max((l["idx"] + 1 for l in legs if l["status"] == LEG_TP), default=0)
        level = max(by_price, by_legs, s["tp_level"] or 0)
        if level > (s["tp_level"] or 0):
            self.journal.event(sid, "tp_level", f"TP{level}")
            if by_legs < level:   # legs report their own TP; announce TPs without a leg here
                self.notify(f"🎯 #{sid} {sym} harga menyentuh TP{level}")
        self.journal.update(sid, peak=peak, tp_level=level)

        gap = self.broker.min_stop_distance(sym)
        for leg in legs:
            st = live.get(leg["id"])
            if st is None:
                continue
            want = self._wanted_sl(s, st.price, level)
            if want is None or (st.sl and (want <= st.sl if buy else want >= st.sl)):
                continue   # nothing to do, or SL is already at least this good
            if self.clock() < (leg["next_try"] or 0):
                continue
            room = (exit_price - want) if buy else (want - exit_price)
            if room <= 0:
                if self.sop["tutup_jika_sl_baru_terlewat"]:
                    ok, err = self.broker.close(leg["ticket"])
                    self._result(s, leg, ok, err, f"harga sudah melewati SL baru {want}, ditutup")
                continue
            if room < gap:
                continue   # too close for the broker right now, try again next pass
            ok, err = self.broker.modify_sl(sym, leg["ticket"], want, st.tp)
            self._result(s, leg, ok, err, f"SL kaki {leg['idx'] + 1} → {want}")

    def _wanted_sl(self, s, fill, level):
        buy, tps = s["side"] == "BUY", s["tps"]
        options = []
        be_after = self.sop["be_setelah_tp"]
        if be_after and level >= be_after:
            pad = abs(fill - s["sl"]) * float(self.sop["be_jarak_aman_r"])
            options.append(fill + pad if buy else fill - pad)
        if self.sop["trailing"] and level >= 2:
            options.append(tps[level - 2])
        if not options:
            return None
        return self.broker.normalize(s["broker_symbol"], max(options) if buy else min(options))

    def _result(self, s, leg, ok, err, what):
        sid = s["id"]
        if ok:
            self.journal.update_leg(leg["id"], next_try=0)
            self.journal.event(sid, "sop", what)
            self.activity(s, what)
            self.notify(f"🔒 #{sid} {s['broker_symbol']}: {what}")
            return
        first = not leg["next_try"]
        self.journal.update_leg(leg["id"], next_try=self.clock() + RETRY_SEC)
        self.journal.event(sid, "sop_failed", f"{what}: {err}")
        if first:
            self.activity(s, f"Gagal: {what} | {err} | akan dicoba ulang")
            log.warning("#%s %s: gagal (%s): %s. Dicoba ulang.", sid, s["broker_symbol"], what, err)

    def _finish(self, s):
        sid, legs = s["id"], self.journal.legs(s["id"])
        filled = [l for l in legs if l["status"] != LEG_CANCELLED]
        pnl = round(sum(l["profit"] or 0 for l in legs), 2)
        if not filled:
            status = s["pending_cancel"] or (EXPIRED if s["expires"] and self.clock() >= s["expires"]
                                             else CANCELLED)
            self.journal.update(sid, status=status, pnl=0)
            if s["pending_cancel"]:
                self.journal.event(sid, status, s["reason"])
                self.notify(f"🚫 #{sid} {s['broker_symbol']} pending order dibatalkan. {s['reason']}")
            return
        by_legs = max((l["idx"] + 1 for l in legs if l["status"] == LEG_TP), default=0)
        self.journal.update(sid, status=DONE, pnl=pnl, tp_level=max(by_legs, s["tp_level"] or 0))
        self.journal.event(sid, "done", f"{pnl:+.2f}")
        self.activity(s, f"Setup selesai | PnL {pnl:+.2f}")
        self.notify(f"🏁 #{sid} {s['broker_symbol']} {s['side']} selesai: {pnl:+.2f}")

    # --- manual controls ----------------------------------------------------
    def cancel_pending(self, setup_id=None) -> int:
        """Cancel unfilled orders (of one setup, or all). Returns how many were cancelled."""
        n = 0
        rows = self.journal.by_status(PENDING, RUNNING) if setup_id is not None else self.mine(PENDING, RUNNING)
        for s in rows:
            if setup_id is not None and s["id"] != setup_id:
                continue
            for l in self.journal.legs(s["id"]):
                if l["status"] == LEG_PENDING and self.broker.cancel(l["ticket"])[0]:
                    n += 1
            self.journal.event(s["id"], "cancel_request", "")
        return n

    def close_setup(self, setup_id: int) -> int:
        """Cancel the unfilled orders and close the open positions of one setup."""
        n = self.cancel_pending(setup_id)
        for l in self.journal.legs(setup_id):
            if l["status"] == LEG_OPEN and self.broker.close(l["ticket"])[0]:
                n += 1
        return n

    def close_all(self) -> int:
        n = self.cancel_pending()
        for s in self.mine(PENDING, RUNNING):
            for l in self.journal.legs(s["id"]):
                if l["status"] == LEG_OPEN and self.broker.close(l["ticket"])[0]:
                    n += 1
        return n
