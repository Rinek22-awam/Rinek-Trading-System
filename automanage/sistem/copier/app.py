"""Wires the inbox channel, gate, executor and guard together."""
import logging
import os
import time
from concurrent.futures import Future
from datetime import datetime

from .config import EXPIRY_MANUAL, EXPIRY_SIGNAL_TIME_PRICE
from .executor import Executor
from .gate import Gate
from .guard import Guard
from .journal import backup_path
from .models import (ACTIVE, AWAITING, CANCELLED, DECLINED, EXPIRED, LEG_PENDING, REJECTED, RUNNING,
                     Setup, price_limit_reason)
from .profiles import Profile, Profiles, SingleProfile
from .setup_format import dest_of, parse_setup, parse_update, price_limit_from_text, valid_from_text

log = logging.getLogger("copier")

HELP = ("Perintah:\n"
        "/status  setup aktif, loss hari ini\n"
        "/stop  hentikan setup baru + batalkan semua limit\n"
        "/lanjut  terima setup baru lagi\n"
        "/limit  daftar limit yang menunggu, dengan tombol batal\n"
        "/hapuslimit  hapus SEMUA limit yang belum ke-fill (ada konfirmasi)\n"
        "/tutupsemua  tutup SEMUA posisi bot (ada konfirmasi)\n"
        "/resetjurnal  hapus riwayat setup, ada cadangan (ada konfirmasi)")


def setup_from_row(r: dict) -> Setup:
    return Setup(symbol=r["symbol"], side=r["side"], entry=r["entry"], sl=r["sl"], tps=r["tps"],
                 order_type=r["order_type"], entry2=r["entry2"], sl2=r["sl2"],
                 risk_mode=r["risk_mode"], risk_value=r["risk_value"], source=r["source"] or "",
                 note=r["note"] or "", raw=r["raw"] or "", valid_until=valid_from_text(r["raw"] or ""),
                 code=(r.get("kode") or "").upper(), cancel_before_entry=price_limit_from_text(r["raw"]))


class App:
    DEST = "mt5"     # hanya postingan berlabel "Ke: mt5" yang dikerjakan bot ini
    AUTO_SOURCE_CANCEL = False
    LOSS_CHECK_SEC = 60   # loss harian per sumber dicek paling sering segini

    def __init__(self, cfg, broker, journal, bot, inbox_chat: int, admin_id: int, clock=time.time,
                 sources_root=None, universal_path=None):
        """sources_root: folder sumber.yaml + sumber/ -> SOP per sumber (setup wajib berkode).
        Tanpa itu: satu SOP (cfg) untuk semua setup, seperti susunan lama (dipakai tes)."""
        self.cfg, self.broker, self.journal, self.bot = cfg, broker, journal, bot
        self.inbox, self.admin, self.clock = int(inbox_chat), int(admin_id), clock
        self._loss_checked = {}
        self._confirmation_sends = {}
        if sources_root is None:
            prof = self.build_profile("", None, cfg, None)
            self.profiles = SingleProfile(prof)
        else:
            self.profiles = Profiles(self.DEST, universal_path or "config.yaml", self.build_profile,
                                     sources_root, extra_paths=self.extra_setting_paths)
            self.profiles.refresh()
            prof = self.profiles.fallback
        # dipakai kode lama dan dashboard: komponen profil cadangan / satu-satunya
        self.gate, self.executor, self.guard = prof.gate, prof.executor, prof.guard

    # --- SOP per sumber -------------------------------------------------------
    def extra_setting_paths(self) -> list:
        return []

    def build_profile(self, code, src, cfg, accept) -> Profile:
        name = f"{src.nama} ({code})" if src is not None else ""
        return Profile(code, name, cfg, broker=self.broker, src=src,
                       gate=Gate(cfg, self.broker, self.journal, self.clock, accept=accept, label=name),
                       executor=Executor(cfg, self.broker, self.journal, self.clock),
                       guard=Guard(cfg, self.broker, self.journal, self.notify, self.clock, accept=accept,
                                   activity=self.activity))

    def _cancel_pending(self, setup_id=None) -> int:
        if setup_id is not None:
            row = self.journal.get(setup_id) or {}
            prof = self.profiles.for_row(row)
            if prof.guard is None:
                return 0
            count = prof.guard.cancel_pending(setup_id)
            self.activity(row, f"Batal limit | {count} permintaan diterima", account=prof.extra.get("akun", ""))
            return count
        count = sum(p.guard.cancel_pending() for p in self.profiles.all())
        self.activity({}, f"Batal semua limit | {count} permintaan diterima")
        return count

    def _close_setup(self, setup_id: int) -> int:
        row = self.journal.get(setup_id) or {}
        prof = self.profiles.for_row(row)
        if prof.guard is None:
            return 0
        count = prof.guard.close_setup(setup_id)
        self.activity(row, f"Tutup setup | {count} permintaan diterima", account=prof.extra.get("akun", ""))
        return count

    def _close_all(self) -> int:
        count = sum(p.guard.close_all() for p in self.profiles.all())
        self.activity({}, f"Tutup semua setup | {count} permintaan diterima")
        return count

    # --- output -------------------------------------------------------------
    def activity(self, row, message, *, account=""):
        context = [self.DEST.upper(), row.get("kode"), account,
                   f"#{row['id']}" if row.get("id") is not None else "",
                   row.get("broker_symbol") or row.get("symbol")]
        log.info("[%s] %s", " | ".join(str(part) for part in context if part), message)

    def log_startup(self):
        rows = self.journal.by_status(*ACTIVE)
        self.activity({}, f"Eksekutor aktif | {len(rows)} setup aktif dimuat dari jurnal")
        for row in rows:
            prof = self.profiles.for_row(row)
            self.activity(row, f"Status jurnal: {row['status']} | menunggu cek broker",
                          account=prof.extra.get("akun", ""))

    def notify(self, text, buttons=None):
        try:
            if buttons:
                generation = self.journal.get_kv("button_generation", 0)
                if generation:
                    buttons = [[(label, f"{data}|{generation}") for label, data in row] for row in buttons]
            return self.bot.send(self.admin, text, buttons=buttons)
        except Exception:
            log.exception("cannot send Telegram message")
            log.info("message was: %s", text)
            return None

    # --- startup ------------------------------------------------------------
    def startup_checks(self) -> bool:
        acc = self.broker.account()
        if self.cfg["akun"]["hanya_demo"] and not self.broker.is_demo():
            log.error("MT5 login ke akun REAL. Bot berhenti (akun.hanya_demo: true).")
            return False
        if not self.broker.is_hedging():
            log.error("Akun MT5 bukan tipe hedging. TP bertahap butuh akun hedging.")
            return False
        missing = [m for m in set(self.cfg["simbol"].values()) if self.broker.symbol_info(m) is None]
        orphan = self.broker.bot_tickets() - self.journal.all_tickets()
        lines = [f"🤖 Bot nyala. Akun {acc.login} ({'demo' if self.broker.is_demo() else 'REAL'}), "
                 f"saldo {acc.balance:.2f} {acc.currency}",
                 f"Setup aktif di jurnal: {len(self.journal.by_status(*ACTIVE))}"]
        if missing:
            lines.append("⚠️ Simbol tidak ada di MT5: " + ", ".join(sorted(missing)) +
                         ". Setup untuk pair itu akan ditolak. Perbaiki lewat MENU > Utilitas > "
                         "Cocokkan nama pair dengan broker.")
        if orphan:
            lines.append(f"⚠️ {len(orphan)} order/posisi ber-magic bot tidak ada di jurnal "
                         f"(tidak dijaga): {sorted(orphan)}")
        if self.journal.get_kv("paused", False):
            lines.append("⏸ Masih dalam mode stop. /lanjut untuk menerima setup.")
        self.notify("\n".join(lines))
        return True

    # --- main loop ----------------------------------------------------------
    def run(self, stopping=lambda: False):
        interval = float(self.cfg["bot"]["interval_detik"])
        while not stopping():
            started = self.clock()
            try:
                offset = self.journal.get_kv("tg_offset", 0)
                for u in self.bot.get_updates(offset, wait=1):
                    if stopping():
                        return
                    self.journal.set_kv("tg_offset", u["update_id"] + 1)
                    self.handle_update(u)
            except OSError as e:   # timeouts and dropped connections: just try again
                log.warning("Telegram lambat/putus, dicoba lagi: %s", e)
            except Exception:
                log.exception("telegram polling failed")
            if stopping():
                return
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")
            time.sleep(max(0.0, interval - (self.clock() - started)))

    def tick(self):
        self._check_waiting_confirmations()
        for prof in self.profiles.all():
            prof.guard.run_once()
        self._watch_daily_loss()

    def run_telethon(self, stopping):
        """Run the guard and handle queued Telegram events on the trading thread."""
        interval = float(self.cfg["bot"]["interval_detik"])
        next_tick = time.monotonic()
        while not stopping():
            now = time.monotonic()
            if now >= next_tick:
                next_tick = now + interval
                try:
                    self.tick()
                except Exception:
                    log.exception("tick failed")
            try:
                self._settle_confirmations()
            except Exception:
                log.exception("Telegram confirmation delivery update failed")
            if stopping():
                return
            try:
                update = self.bot.pop_update()
            except Exception:
                log.exception("Telegram update queue failed")
                update = None
            if update is not None:
                self.handle_update(update)
            else:
                time.sleep(min(0.1, max(0.0, next_tick - time.monotonic())))

    def _settle_confirmations(self):
        if not self._confirmation_sends:
            return
        generation = self.journal.get_kv("button_generation", 0)
        for sid, (original_generation, delivery) in list(self._confirmation_sends.items()):
            row = self.journal.get(sid) if generation == original_generation else None
            awaiting = bool(row and row["status"] == AWAITING)
            if not awaiting:
                delivery.cancel()
            if not delivery.done():
                continue
            if not delivery.cancelled() and delivery.exception() is None:
                msg_id = delivery.result()["message_id"]
                if awaiting:
                    self.journal.update(sid, confirm_msg_id=msg_id)
                else:
                    self.bot.edit(self.admin, msg_id, f"Setup #{sid} sudah tidak menunggu konfirmasi.")
            del self._confirmation_sends[sid]

    def handle_update(self, u: dict):
        try:
            if "channel_post" in u:
                self.on_channel_post(u["channel_post"])
            elif "callback_query" in u:
                self.on_button(u["callback_query"])
            elif "message" in u:
                self.on_message(u["message"])
        except Exception:
            log.exception("update failed: %s", u.get("update_id"))

    # --- inbox --------------------------------------------------------------
    def on_channel_post(self, msg: dict):
        chat = msg["chat"]["id"]
        if chat != self.inbox or self.journal.message_seen(chat, msg["message_id"]):
            return
        text = msg.get("text") or msg.get("caption") or ""
        if dest_of(text) != self.DEST:
            return   # bukan untuk bot ini, atau tanpa label tujuan: diam
        upd = parse_update(text)
        if upd:
            self.activity({"symbol": upd.get("pair")}, f"Update sumber {upd['source']}: {upd['action']}")
            self.on_source_update(upd)
            return
        setup, errors = parse_setup(text)
        if setup is None:
            if errors:
                self.activity({}, "Format setup ditolak: " + "; ".join(errors))
                self.notify("❌ Format setup salah:\n- " + "\n- ".join(errors))
            return
        meta = dict(msg_chat=chat, msg_id=msg["message_id"], posted=float(msg["date"]))
        prof, why = self.profiles.get(setup.code)
        self._prepare(setup, meta, prof, why)

    def _prepare(self, setup, meta, prof, why="", group=None):
        account = prof.extra.get("akun", "") if prof else ""
        context = {"kode": setup.code, "symbol": setup.symbol}
        self.activity(context, f"Sinyal diterima dari {setup.source or 'kotak masuk'} | {setup.side}", account=account)
        if prof is None:
            sid = self.journal.add_setup(setup, REJECTED, reason=why, **meta)
            self.activity(dict(context, id=sid), f"Setup ditolak: {why}", account=account)
            self.notify(f"🚫 Setup #{sid} {setup.symbol} {setup.side} ditolak:\n- {why}")
            return
        v = prof.gate.check(setup, posted=meta["posted"])
        plan = prof.executor.plan(setup, v.market) if v.ok else None
        if plan and plan.error:
            v.ok, v.reasons = False, [plan.error]
        if not v.ok:
            sid = self.journal.add_setup(setup, REJECTED, reason="; ".join(v.reasons), **meta)
            self.activity(dict(context, id=sid), "Setup ditolak: " + "; ".join(v.reasons), account=account)
            self.notify(f"🚫 Setup #{sid} {setup.symbol} {setup.side} ditolak:\n- " + "\n- ".join(v.reasons)
                        + (f"\nSumber: {prof.name}" if prof.name else ""))
            return
        sid = self.journal.add_setup(setup, AWAITING, broker_symbol=v.market.symbol,
                                     warning="; ".join(v.warnings), **meta)
        gerbang = prof.cfg["gerbang"]
        context.update(id=sid, broker_symbol=v.market.symbol)
        needs_warning_confirmation = gerbang["konfirmasi_peringatan"] and any(
            warning != v.doubt_warning or not gerbang["izinkan_tebakan_otomatis"] for warning in v.warnings
        )
        requires_confirmation = gerbang["konfirmasi_wajib"] or needs_warning_confirmation
        if group is None and not requires_confirmation:
            self.activity(context, "Lolos gerbang | eksekusi otomatis", account=account)
            for warning in v.warnings:
                self.activity(context, f"Peringatan: {warning}", account=account)
            result = self._execute(sid)
            if v.warnings:
                result += "\n" + "\n".join(f"⚠️ {warning}" for warning in v.warnings)
            self.notify(result)
            return
        minutes = prof.cfg["gerbang"]["konfirmasi_kedaluwarsa_menit"]
        deadline = self.clock() + minutes * 60
        if prof.cfg["entry"]["kedaluwarsa"] != EXPIRY_MANUAL and setup.valid_until:
            deadline = min(deadline, setup.valid_until)
        reason = "; ".join(v.warnings) or "sesuai SOP sumber"
        self.activity(context, f"Menunggu konfirmasi: {reason}", account=account)
        text = self._summary(sid, setup, v, plan, prof) + \
            f"\nBerlaku sampai {datetime.fromtimestamp(deadline):%H:%M}"
        self.journal.update(sid, confirm_deadline=deadline)
        if group is not None:
            group.append((sid, text, requires_confirmation))
            return
        sent = self.notify(text, buttons=[[("✅ Ya, eksekusi", f"ya:{sid}"), ("❌ Tidak", f"tidak:{sid}")]])
        if isinstance(sent, Future):
            self._confirmation_sends[sid] = self.journal.get_kv("button_generation", 0), sent
        elif sent:
            self.journal.update(sid, confirm_msg_id=sent["message_id"])

    def on_source_update(self, upd: dict, *, ref=None):
        """Route source updates to the referenced setup or the latest matching setup."""
        head = f"📣 Update channel{' ' + upd['source'] if upd['source'] else ''}"
        auto_cancel = self.AUTO_SOURCE_CANCEL and upd["action"] == "CANCEL"
        if ref is None and upd.get("ref"):
            ref = self.journal.by_message(self.inbox, upd["ref"])
        if auto_cancel and upd.get("ref") and ref is None:
            self.notify(f"{head}: CANCEL\n{upd['text']}\nSetup referensi tidak ditemukan.")
            return
        if ref is not None:
            live = ref["status"] in (AWAITING, *ACTIVE)
            if upd["action"] == "INFO" or not live:
                about = f"Setup terkait: #{ref['id']} {ref['broker_symbol'] or ref['symbol']} {ref['side']}"
                state = "" if live else f" (sudah {ref['status']}, tidak ada yang perlu ditindak)"
                kind = "" if upd["action"] == "INFO" else f" {upd['action']}"
                self.notify(f"{head}:{kind}\n{upd['text']}\n{about}{state}")
                return
            r = ref
        else:
            if upd["action"] == "INFO":
                self.notify(f"{head}:\n{upd['text']}")
                return
            src, pair = upd["source"].lower(), upd.get("pair", "")
            rows = [r for r in self.journal.by_status(AWAITING, *ACTIVE)
                    if (not src or (r["source"] or "").lower() == src)
                    and (not pair or pair in (r["symbol"], r["broker_symbol"]))]
            if not rows:
                what = f"{pair} dari sumber ini" if pair else "dari sumber ini"
                self.notify(f"{head}: {upd['action']}\n{upd['text']}\nTidak ada setup aktif {what}.")
                return
            r = max(rows, key=lambda x: x["id"])
        sid = r["id"]
        if auto_cancel:
            if r["status"] == AWAITING:
                reason = "Dibatalkan oleh sumber sinyal"
                self.journal.update(sid, status=CANCELLED, reason=reason)
                self.activity(r, reason)
                self._edit(r, f"🚫 Setup #{sid} dibatalkan oleh sumber sinyal.")
                return
            count = self._cancel_pending(sid)
            result = ("Permintaan pembatalan order entry diterima Bybit." if count else
                      "Tidak ada pembatalan order entry yang diterima Bybit.")
            self.notify(f"{head}: CANCEL\n{upd['text']}\nSetup #{sid}: {result}")
            return
        if r["status"] == AWAITING:
            act = ("❌ Tolak setup #%d" % sid, f"tidak:{sid}")
        elif upd["action"] == "CANCEL":
            act = (f"🚫 Batalkan limit #{sid}", f"batal:{sid}")
        else:
            act = (f"🧯 Tutup semua posisi #{sid}", f"tutup:{sid}")
        self.notify(f"{head}: {upd['action']}\n{upd['text']}\n"
                    f"Setup terkait: #{sid} {r['broker_symbol'] or r['symbol']} {r['side']} ({r['status']})\n"
                    f"Bot tidak bertindak sebelum kamu tekan tombol.",
                    buttons=[[act, ("Abaikan", "abaikan:0")]])

    def _summary(self, sid, s: Setup, v, plan, prof=None) -> str:
        prof = prof or self.profiles.for_row({"kode": s.code})
        m = v.market
        r = prof.cfg["risiko"]
        mode = s.risk_mode or r["mode"]
        value = s.risk_value if s.risk_mode else r[mode]
        risk_txt = f"{value}% saldo" if mode == "persen" else f"tetap ${value}"
        if s.risk_mode:
            risk_txt += ", dari setup"
        lines = [f"🆕 Setup #{sid} perlu konfirmasi",
                 f"{m.symbol} {s.side} {m.kind} @ {plan.entry}",
                 f"SL {s.sl}"]
        for i, tp in enumerate(s.tps):
            vol = plan.legs[i][0] if i < len(plan.legs) else 0
            if vol:
                lot = f"{vol} lot"
            elif i < len(plan.legs) or prof.cfg["tp"]["mode"] == "satu":
                lot = "tanpa lot"
            else:
                lot = "tidak dipakai, lot kurang"
            lines.append(f"TP{i + 1} {tp} ({lot})")
        cur = prof.broker.account().currency
        lines += [f"Lot total {plan.total_lot} · Risiko {plan.actual_risk:.2f} {cur} ({risk_txt})",
                  f"Harga sekarang {m.bid} / {m.ask}"]
        if m.kind != "MARKET":
            until, why = prof.executor.expiry_for(s)
            lines.append(f"Batas limit: {datetime.fromtimestamp(until):%d/%m %H:%M} ({why})")
        if prof.cfg["entry"]["kedaluwarsa"] == EXPIRY_SIGNAL_TIME_PRICE and s.cancel_before_entry:
            op, level = s.cancel_before_entry
            lines.append(f"Batal sebelum entry jika harga {op} {level:g}")
        if s.entry2 or s.sl2:
            lines.append(f"Entry2/SL2 dicatat, tidak dipakai: {s.entry2 or '-'} / {s.sl2 or '-'}")
        if prof.name:
            lines.append(f"Sumber: {prof.name}")
        elif s.source:
            lines.append(f"Sumber: {s.source}")
        lines += [f"ℹ️ {n}" for n in v.notes]
        lines += [f"⚠️ {w}" for w in v.warnings]
        return "\n".join(lines)

    def _execute(self, sid) -> str:
        row = self.journal.get(sid)
        s = setup_from_row(row)
        if row.get("account") and hasattr(self.profiles, "for_account"):
            prof = self.profiles.for_account(row)
            why = prof.blocked if prof else "Akun tujuan tidak tersedia"
            if why:
                prof = None
        else:
            prof, why = self.profiles.get(s.code)
        if prof is None:
            self.journal.update(sid, status=REJECTED, reason=why)
            self.activity(row, f"Eksekusi ditolak: {why}")
            return f"🚫 #{sid} batal dieksekusi:\n- {why}"
        account = prof.extra.get("akun", "")
        v = prof.gate.check(s, recheck=True)
        plan = prof.executor.plan(s, v.market) if v.ok else None
        if plan and plan.error:
            v.ok, v.reasons = False, [plan.error]
        if not v.ok:
            self.journal.update(sid, status=REJECTED, reason="; ".join(v.reasons))
            self.activity(row, "Eksekusi ditolak: " + "; ".join(v.reasons), account=account)
            return f"🚫 #{sid} batal dieksekusi:\n- " + "\n- ".join(v.reasons)
        self.activity(row, f"Mengirim {s.side} {v.market.kind} qty {plan.total_lot:g} @ {plan.entry:g}", account=account)
        ok, err = prof.executor.execute(sid, s, v.market, plan)
        if not ok:
            self.activity(row, f"Pemasangan order gagal: {err}", account=account)
            if self.journal.get(sid)["status"] in ACTIVE:
                return f"⚠️ #{sid} order terpasang sebagian: {err}"
            return f"🚫 #{sid} ditolak broker: {err}"
        self.activity(row, f"{s.side} {v.market.kind} diterima broker | qty {plan.total_lot:g} | SL {s.sl:g}",
                      account=account)
        if self.DEST == "mt5":
            for leg in self.journal.legs(sid):
                self.activity(row, f"Tiket {leg['ticket']} | qty {leg['volume']:g} | TP{leg['idx'] + 1} {leg['tp']:g}")
        what = "masuk market" if v.market.kind == "MARKET" else f"{v.market.kind} terpasang"
        return (f"📌 #{sid} {v.market.symbol} {s.side} {what}, lot {plan.total_lot}, "
                f"risiko {plan.actual_risk:.2f} {prof.broker.account().currency}")

    def _check_waiting_confirmations(self):
        for r in self.journal.by_status(AWAITING):
            if r["confirm_deadline"] and self.clock() > r["confirm_deadline"]:
                self.journal.update(r["id"], status=EXPIRED, reason="Tidak dikonfirmasi")
                self.activity(r, "Konfirmasi kedaluwarsa")
                self._edit(r, f"⌛ Setup #{r['id']} {r['symbol']} {r['side']} kedaluwarsa (tidak dijawab)")
                continue
            limit = price_limit_from_text(r["raw"])
            if limit:
                prof = self.profiles.for_row(r)
                if prof and prof.broker and prof.cfg["entry"]["kedaluwarsa"] == EXPIRY_SIGNAL_TIME_PRICE:
                    price = prof.broker.signal_price(r["broker_symbol"])
                    reason = price_limit_reason(limit, price)
                    if reason:
                        self.journal.update(r["id"], status=CANCELLED, reason=reason)
                        self.activity(r, reason)
                        self._edit(r, f"🚫 Setup #{r['id']} dibatalkan. {reason}")

    def _edit(self, row, text):
        if row.get("confirm_msg_id"):
            try:
                self.bot.edit(self.admin, row["confirm_msg_id"], text)
                return
            except Exception:
                log.exception("edit failed")
        self.notify(text)

    def _reply(self, msg_id, text):
        """Replace the button message with the result; send a new message if that fails."""
        try:
            self.bot.edit(self.admin, msg_id, text)
        except Exception:
            log.warning("edit failed, sending a new message", exc_info=True)
            self.notify(text)

    def _watch_daily_loss(self):
        if not self.journal.by_status(*ACTIVE):
            return
        today = datetime.now().strftime("%Y-%m-%d")
        for prof in self.profiles.sources():
            if prof.code:   # per sumber: hanya kalau sumber itu punya setup aktif, tidak tiap tick
                if not prof.guard.mine(*ACTIVE):
                    continue
                label = (prof.code, prof.extra.get("key", ""))
                last = self._loss_checked.get(label, 0)
                if self.clock() - last < self.LOSS_CHECK_SEC:
                    continue
                self._loss_checked[label] = self.clock()
            try:
                hit = prof.gate.daily_loss_hit()
            except Exception:
                log.exception("Gagal memeriksa batas loss untuk %s", prof.name)
                continue
            key = f"loss_warned:{prof.code}" if prof.code else "loss_warned"
            if prof.extra.get("key"):
                key += f":{prof.extra['key']}"
            if hit and self.journal.get_kv(key, "") != today:
                self.journal.set_kv(key, today)
                who = f" Setup baru dari {prof.name}" if prof.name else " Setup baru"
                self.notify(f"🛑 {hit}.{who} ditolak sampai besok. Posisi yang jalan tetap dijaga.")

    # --- buttons and commands (admin only) -----------------------------------
    def on_button(self, q: dict):
        if q["from"]["id"] != self.admin:
            return
        data, _, generation = q.get("data", "").partition("|")
        action, _, arg = data.partition(":")
        try:
            self.bot.answer(q["id"])   # only stops the button spinner; may fail if the tap arrived late
        except Exception:
            log.warning("answerCallbackQuery failed, continuing", exc_info=True)
        msg_id = q.get("message", {}).get("message_id")
        if (generation or "0") != str(self.journal.get_kv("button_generation", 0)):
            self._reply(msg_id, "Tombol lama sudah tidak berlaku setelah jurnal di-reset.")
            return
        if action == "tutupsemua":
            text = "Dibatalkan, tidak ada yang ditutup."
            if arg == "ya":
                n = self._close_all()
                text = f"🧯 {n} order/posisi ditutup atau dibatalkan."
            self._reply(msg_id, text)
            return
        if action == "hapuslimit":
            text = "Dibatalkan, limit tetap terpasang."
            if arg == "ya":
                n = self._cancel_pending()
                text = f"🚫 {n} limit dihapus. Posisi yang sudah jalan tetap dijaga."
            self._reply(msg_id, text)
            return
        if action == "resetjurnal":
            text = "Dibatalkan, jurnal tidak diubah."
            if arg == "ya":
                text = self._reset_journal()
            self._reply(msg_id, text)
            return
        if action == "abaikan":
            self._reply(msg_id, "Diabaikan. SOP bot tetap berjalan.")
            return
        if action in ("batal", "tutup") and arg.isdigit():
            if action == "batal":
                n = self._cancel_pending(int(arg))
                self._reply(msg_id, f"🚫 {n} limit setup #{arg} dibatalkan.")
            else:
                n = self._close_setup(int(arg))
                self._reply(msg_id, f"🧯 Setup #{arg}: {n} order/posisi ditutup atau dibatalkan.")
            return
        if action not in ("ya", "tidak") or not arg.isdigit():
            return
        row = self.journal.get(int(arg))
        if row is None or row["status"] != AWAITING:
            self._reply(msg_id, f"Setup #{arg} sudah tidak menunggu konfirmasi.")
            return
        if row["confirm_deadline"] and self.clock() > row["confirm_deadline"]:
            reason = "Tidak dikonfirmasi"
            valid_until = valid_from_text(row["raw"])
            prof, _ = self.profiles.get(row["kode"])
            if (prof and prof.cfg["entry"]["kedaluwarsa"] != EXPIRY_MANUAL
                    and valid_until and self.clock() >= valid_until):
                reason = "Masa berlaku sinyal sudah lewat"
            self.journal.update(row["id"], status=EXPIRED, reason=reason)
            self.activity(row, f"Konfirmasi kedaluwarsa: {reason}")
            self._reply(msg_id, f"⌛ Setup #{arg} kedaluwarsa. {reason}.")
            return
        if action == "tidak":
            self.journal.update(row["id"], status=DECLINED, reason="Ditolak user")
            self.activity(row, "Konfirmasi ditolak pengguna")
            self._reply(msg_id, f"❌ Setup #{arg} kamu tolak.")
            return
        self.activity(row, "Konfirmasi diterima")
        self._reply(msg_id, self._execute(row["id"]))

    def on_message(self, msg: dict):
        if msg.get("from", {}).get("id") != self.admin or msg["chat"]["id"] != self.admin:
            return
        parts = (msg.get("text") or "").strip().split()
        cmd = parts[0].lower().split("@")[0] if parts else ""
        if cmd == "/status":
            self.notify(self._status())
        elif cmd == "/stop":
            self.journal.set_kv("paused", True)
            self.activity({}, "Penerimaan setup dihentikan lewat /stop")
            n = self._cancel_pending()
            self.notify(f"⏸ Bot stop. {n} limit dibatalkan. Posisi yang sudah jalan tetap dijaga. "
                        f"/lanjut untuk menyalakan lagi.")
        elif cmd == "/lanjut":
            self.journal.set_kv("paused", False)
            self.activity({}, "Penerimaan setup dilanjutkan lewat /lanjut")
            self.notify("▶️ Bot menerima setup lagi.")
        elif cmd == "/batal" and len(parts) > 1 and parts[1].lstrip("#").isdigit():   # not in HELP: /limit has buttons
            n = self._cancel_pending(int(parts[1].lstrip("#")))
            self.notify(f"{n} limit setup #{parts[1].lstrip('#')} dibatalkan.")
        elif cmd == "/limit":
            self._list_limits()
        elif cmd == "/hapuslimit":
            ids = [r["id"] for r in self._with_limits()]
            if not ids:
                self.notify("Tidak ada limit yang terpasang.")
            else:
                self.notify("Yakin hapus semua limit yang belum ke-fill? Setup: " +
                            ", ".join(f"#{i}" for i in ids) + ". Posisi yang sudah jalan tidak disentuh.",
                            buttons=[[("🚫 Ya, hapus limit", "hapuslimit:ya"), ("Batal", "hapuslimit:tidak")]])
        elif cmd == "/tutupsemua":
            self.notify("Yakin tutup SEMUA posisi dan limit bot di harga pasar?",
                        buttons=[[("🧯 Ya, tutup semua", "tutupsemua:ya"), ("Batal", "tutupsemua:tidak")]])
        elif cmd == "/resetjurnal":
            busy = self._reset_blocked()
            if busy:
                self.notify(busy)
            else:
                self.notify(f"Yakin reset jurnal? Riwayat {len(self.journal.since(0))} setup dihapus "
                            f"(cek dobel ikut mulai dari nol, nomor setup mulai lagi dari #1), "
                            f"cadangan disimpan di folder data. "
                            f"Batas loss harian tidak ikut ter-reset (dihitung dari MT5).",
                            buttons=[[("🗑 Ya, reset", "resetjurnal:ya"), ("Batal", "resetjurnal:tidak")]])
        else:
            self.notify(HELP)

    def _with_limits(self):
        """Active setups that still have an unfilled order."""
        return [r for r in self.journal.by_status(*ACTIVE)
                if any(l["status"] == LEG_PENDING for l in self.journal.legs(r["id"]))]

    def _list_limits(self):
        rows = self._with_limits()
        if not rows:
            self.notify("Tidak ada limit yang terpasang.")
            return
        lines = ["📋 Limit yang menunggu:"]
        for r in rows:
            if r["status"] == RUNNING:
                when = "sebagian jalan"
            elif r["expires"]:
                end = datetime.fromtimestamp(r["expires"])
                when = "habis " + end.strftime("%H:%M" if end.date() == datetime.now().date() else "%d/%m %H:%M")
            else:
                when = ""
            lines.append(f"#{r['id']} {r['broker_symbol']} {r['side']} @ {r['entry']} · SL {r['sl']}"
                         + (f" · {when}" if when else ""))
        self.notify("\n".join(lines), buttons=[[(f"🚫 Batalkan #{r['id']}", f"batal:{r['id']}")] for r in rows])

    def _reset_blocked(self) -> str:
        """Why the journal cannot be reset now, or "" if it can."""
        active = self.journal.by_status(*ACTIVE)
        if not active:
            return ""
        return ("Belum bisa reset: masih ada setup aktif " + ", ".join(f"#{r['id']}" for r in active) +
                ". Tanpa jurnal, bot tidak bisa menjaganya (BE, trailing). "
                "Tunggu selesai, atau /hapuslimit / /tutupsemua dulu.")

    def _reset_journal(self) -> str:
        busy = self._reset_blocked()   # checked again: something may have been placed since the question
        if busy:
            return busy
        backup = backup_path(self.journal.path)
        n = self.journal.reset(backup)
        self.activity({}, f"Jurnal di-reset | {n} setup dihapus")
        return f"🗑 Jurnal di-reset, {n} setup dihapus. Cadangan: {os.path.basename(backup)}"

    def _status(self) -> str:
        acc = self.broker.account()
        lines = [f"Saldo {acc.balance:.2f} · Equity {acc.equity:.2f} {acc.currency}",
                 "⏸ Mode stop" if self.journal.get_kv("paused", False) else "▶️ Menerima setup"]
        active = self.journal.by_status(*ACTIVE)
        if not active:
            lines.append("Tidak ada setup aktif.")
        for r in active:
            src = f" [{r['kode']}]" if r.get("kode") else ""
            lines.append(f"#{r['id']}{src} {r['broker_symbol']} {r['side']} {r['status']}, "
                         f"TP kena: {r['tp_level'] or 0}/{len(r['tps'])}, lot {r['total_lot']}")
        waiting = self.journal.by_status(AWAITING)
        if waiting:
            lines.append("Menunggu konfirmasi: " + ", ".join(f"#{r['id']}" for r in waiting))
        for prof in self.profiles.sources():
            if prof.code and not prof.guard.mine(*ACTIVE):
                continue   # loss sumber tanpa posisi aktif hanya dari kaki tertutup: tetap dicek di gerbang
            hit = prof.gate.daily_loss_hit()
            if hit:
                lines.append(f"🛑 {hit}")
        return "\n".join(lines)
