"""Executor: turns an approved setup into MT5 orders, one leg per TP."""
import logging
from dataclasses import dataclass
from typing import List, Tuple

from .config import EXPIRY_MANUAL, EXPIRY_SIGNAL_TIME_PRICE
from .models import LEG_CANCELLED, LEG_CLOSED, LEG_OPEN, LEG_PENDING, PENDING, REJECTED, RUNNING, Setup
from .sizing import lot_for_risk, plan_legs, risk_amount

log = logging.getLogger(__name__)


@dataclass
class Plan:
    entry: float               # price used for sizing (limit price, or market price)
    risk: float                # money at risk if every leg hits SL
    total_lot: float
    legs: List[Tuple[float, float]]   # (volume, tp) per TP from TP1; volume 0 = no order there
    actual_risk: float
    error: str = ""


class Executor:
    def __init__(self, cfg: dict, broker, journal, clock):
        self.cfg, self.broker, self.journal, self.clock = cfg, broker, journal, clock

    def expiry_for(self, s: Setup) -> Tuple[float, str]:
        """Kapan limit yang belum terisi dibatalkan, dan dari mana angkanya.
        Kedua mode sinyal memakai "Berlaku:" kalau ada; selain itu jam manual."""
        e = self.cfg["entry"]
        if e["kedaluwarsa"] != EXPIRY_MANUAL and s.valid_until:
            return float(s.valid_until), "dari sinyal"
        hours = e["limit_kedaluwarsa_jam"]
        return self.clock() + hours * 3600, f"{hours:g} jam"

    def watch_entry_price(self, s: Setup) -> bool:
        return bool(s.cancel_before_entry and self.cfg["entry"]["kedaluwarsa"] == EXPIRY_SIGNAL_TIME_PRICE)

    def plan(self, s: Setup, market) -> Plan:
        r = self.cfg["risiko"]
        mode = s.risk_mode or r["mode"]
        value = s.risk_value if s.risk_mode else (r["persen"] if mode == "persen" else r["dolar"])
        sym = market.symbol
        entry = s.entry if market.kind != "MARKET" else (
            market.ask if s.side == "BUY" else market.bid)
        balance = self.broker.account().balance
        if mode == "dolar":   # config and setups speak US dollars; cent accounts count in USC
            value = float(value) * self.broker.money_factor()
        risk = risk_amount(balance, mode, value, value)
        per_lot = self.broker.loss_per_lot(sym, s.side, entry, s.sl)
        spec = self.broker.spec(sym)
        total = lot_for_risk(risk, per_lot, spec, float(r["lot_maks"]))
        if total <= 0:
            need = spec.volume_min * per_lot
            return Plan(entry, risk, 0, [], 0, f"Lot di bawah minimum broker (risiko {risk:.2f}, "
                                                f"butuh minimal {need:.2f} untuk {spec.volume_min} lot)")
        tp = self.cfg["tp"]
        split = tp["bagi"]
        if tp["mode"] == "satu":   # the whole lot on one TP: a split with zeros before it
            k = min(tp["satu_ke"], len(s.tps))
            split = {k: [0] * (k - 1) + [100]}
        legs = plan_legs(total, s.tps, split, spec)
        if not legs:
            return Plan(entry, risk, total, [], 0, "Lot tidak bisa dibagi ke TP")
        return Plan(entry, risk, total, legs, round(sum(v for v, _ in legs) * per_lot, 2))

    def execute(self, setup_id: int, s: Setup, market, plan: Plan) -> Tuple[bool, str]:
        """Place every leg. If any leg fails, roll back the ones already placed."""
        wanted = [(i, vol, tp) for i, (vol, tp) in enumerate(plan.legs) if vol > 0]   # i = TP index
        leg_status = LEG_OPEN if market.kind == "MARKET" else LEG_PENDING
        expires = self.expiry_for(s)[0]
        total_lot = 0.0
        for i, vol, tp in wanted:
            try:
                ticket, err = self.broker.place(market.symbol, s.side, market.kind, vol, plan.entry,
                                                s.sl, tp, f"su{setup_id}-{i + 1}")
            except Exception as exc:
                log.exception("gagal memasang kaki %s untuk setup %s", i + 1, setup_id)
                ticket, err = None, str(exc)
            if ticket is None:
                return self._rollback(setup_id, market.kind, err)
            total_lot += vol
            fields = dict(status=RUNNING if market.kind == "MARKET" else PENDING,
                          broker_symbol=market.symbol, risk_amount=round(plan.actual_risk * total_lot / plan.total_lot, 2),
                          total_lot=total_lot, expires=expires, order_type=market.kind,
                          watch_entry_price=self.watch_entry_price(s))
            self.journal.add_leg(setup_id, i, ticket, vol, tp, leg_status, setup_fields=fields)
        self.journal.event(setup_id, "placed", f"{market.kind} {len(wanted)} kaki, "
                                               f"lot {plan.total_lot}, risiko {plan.actual_risk}")
        return True, ""

    def _rollback(self, setup_id: int, kind: str, error: str) -> Tuple[bool, str]:
        confirmed, unresolved = [], []
        for leg in self.journal.legs(setup_id):
            ticket = leg["ticket"]
            try:
                if kind == "MARKET":
                    _, why = self.broker.close(ticket)
                else:
                    _, why = self.broker.cancel(ticket)
                state = self.broker.leg_state(ticket)
                if state.state == "open" and kind != "MARKET":
                    _, why = self.broker.close(ticket)
                    state = self.broker.leg_state(ticket)
                if state.state in ("cancelled", "closed"):
                    confirmed.append((leg, state))
                    continue
                unresolved.append(f"{ticket} ({why or state.state})")
            except Exception as exc:
                log.exception("gagal memeriksa rollback tiket %s", ticket)
                unresolved.append(f"{ticket} ({exc})")

        self.journal.event(setup_id, "order_failed", error)
        if unresolved:
            detail = f"{error}. Rollback belum terkonfirmasi untuk tiket {', '.join(unresolved)}. Bot tetap memantau setup."
            self.journal.update(setup_id, reason=detail)
            self.journal.event(setup_id, "rollback_unconfirmed", detail)
            return False, detail
        for leg, state in confirmed:
            status = LEG_CANCELLED if state.state == "cancelled" else LEG_CLOSED
            self.journal.update_leg(leg["id"], status=status, profit=state.profit,
                                    close_time=self.clock() if status == LEG_CLOSED else None)
        self.journal.update(setup_id, status=REJECTED, reason=f"Order ditolak broker: {error}")
        return False, error
