"""Menu tes eksekutor Bybit: coba setiap fungsi dasar satu per satu di akun DEMO/TESTNET.

Buka dari MENU.bat > Utilitas > Tes manual Bybit, atau:  python -m panel.bybit_tes
Mode LIVE sengaja ditolak di menu ini. Setiap order ditanya dulu (y/n) sebelum dikirim.
"""
import os
import sys

from copier import envfile

from copier.broker_bybit import BybitBroker

from .ui import ask, choose, clear, pause, title

MENU = ["Cek koneksi, saldo, dan izin API key",
        "Lihat harga dan aturan simbol",
        "Ganti simbol",
        "Atur leverage",
        "Buka posisi MARKET (SL/TP opsional)",
        "Pasang order LIMIT (SL/TP opsional)",
        "Pasang / geser SL-TP posisi",
        "Ubah order LIMIT (harga, qty, SL, TP)",
        "Batalkan satu order LIMIT",
        "Batalkan semua order simbol ini",
        "Tutup posisi (penuh atau sebagian)",
        "Lihat posisi dan order terbuka"]


def num(prompt: str, required: bool = False):
    """Minta angka. Kosong = None (kecuali wajib). Koma boleh dipakai sebagai desimal."""
    while True:
        raw = ask(prompt).replace(",", ".")
        if raw == "":
            if not required:
                return None
            print("  Wajib diisi.")
            continue
        try:
            return float(raw)
        except ValueError:
            print("  Harus angka, contoh 65000 atau 0.01")


def side_input():
    while True:
        s = ask("  Arah (B = BUY/long, S = SELL/short): ").upper()
        if s in ("B", "BUY", "L", "LONG"):
            return "BUY"
        if s in ("S", "SELL", "SHORT"):
            return "SELL"
        print("  Ketik B atau S.")


def yes(prompt="  Kirim ke Bybit? (y/n): ") -> bool:
    return ask(prompt).lower() in ("y", "ya", "yes")


def report(ok, err, done="Berhasil."):
    print(f"\n  [OK] {done}" if ok else f"\n  [X] Gagal: {err}")


def show_rules(b, symbol):
    sp, err = b.spec(symbol)
    if err:
        print(f"  [X] {err}")
        return None
    t, err = b.tick(symbol)
    if t:
        print(f"  {symbol}  bid {t['bid']}  ask {t['ask']}  last {t['last']}  mark {t['mark']}")
    print(f"  Kelipatan harga {sp.tick_size.normalize():f} | qty min {sp.min_qty.normalize():f}, "
          f"kelipatan {sp.qty_step.normalize():f} | nilai min {sp.min_notional.normalize():f} USDT"
          f" | leverage maks {sp.max_leverage.normalize():f}x")
    return sp


def show_state(b, symbol=None):
    pos, err = b.positions(symbol)
    print("  POSISI:")
    if err:
        print(f"    [X] {err}")
    elif not pos:
        print("    (tidak ada)")
    for p in pos:
        print(f"    {p.symbol} {p.side} {p.size} @ {p.entry}  SL {p.sl or '-'}  TP {p.tp or '-'}"
              f"  PnL {p.pnl:+.2f}  lev {p.leverage}x  likuidasi {p.liq or '-'}")
    orders, err = b.open_orders(symbol)
    print("  ORDER TERBUKA:")
    if err:
        print(f"    [X] {err}")
    elif not orders:
        print("    (tidak ada)")
    for i, o in enumerate(orders, 1):
        harga = o.price or o.trigger
        extra = f"  SL {o.sl or '-'}  TP {o.tp or '-'}" if o.kind == "Limit" else ""
        ro = "  reduce-only" if o.reduce_only else ""
        print(f"    {i}. {o.symbol} {o.kind} {o.side} {o.qty} @ {harga}{extra}{ro}  [{o.status}]"
              f"  id {o.order_id[:8]}..")
    return pos, orders


def pick_limit(b, symbol):
    orders, err = b.open_orders(symbol)
    limits = [o for o in orders if o.kind == "Limit"]
    if err:
        print(f"  [X] {err}")
        return None
    if not limits:
        print(f"  Tidak ada order LIMIT {symbol} yang terbuka.")
        return None
    i = choose([f"{o.side} {o.qty} @ {o.price}  SL {o.sl or '-'}  TP {o.tp or '-'}" for o in limits])
    return None if i is None else limits[i]


def sltp_inputs(label_sl="  SL (kosong = tanpa SL): ", label_tp="  TP (kosong = tanpa TP): "):
    return num(label_sl), num(label_tp)


def run(b: BybitBroker):
    symbol = os.getenv("BYBIT_SIMBOL_TES", "BTCUSDT").upper()
    while True:
        clear()
        title(f"TES EKSEKUTOR BYBIT  |  mode {b.mode.upper()}  |  simbol {symbol}")
        i = choose(MENU, back="Keluar")
        if i is None:
            return
        clear()
        title(MENU[i])
        if i == 0:
            ok, msg = b.connect()
            print(f"  [OK] {msg}" if ok else f"  [X] {msg}")
            if ok:
                bal, _ = b.balance()
                print(f"  Saldo wallet {bal['wallet']:.2f} USD, bisa dipakai {bal['available']:.2f}"
                      f" USD, USDT {bal['usdt']:.2f}")
                info, err = b.key_info()
                if info:
                    print(f"  API key: {'HANYA BACA' if info['read_only'] else 'boleh trading'},"
                          f" izin {info['permissions']}")
                    if info["withdraw"]:
                        print("  [!] API key ini punya izin WITHDRAW. Sebaiknya buat ulang tanpa izin itu.")
                else:
                    print(f"  (izin API key tidak bisa dibaca di mode ini: {err})")
        elif i == 1:
            show_rules(b, symbol)
        elif i == 2:
            new = ask(f"  Simbol baru (sekarang {symbol}, contoh ETHUSDT): ").upper()
            if new:
                b_sp, err = b.spec(new)
                if err:
                    print(f"  [X] {err}")
                else:
                    symbol = new
                    print(f"  [OK] Simbol sekarang {symbol}")
        elif i == 3:
            show_rules(b, symbol)
            lev = num("  Leverage (contoh 10): ", required=True)
            if yes(f"  Set leverage {symbol} ke {lev:g}x? (y/n): "):
                report(*b.set_leverage(symbol, lev))
        elif i in (4, 5):
            kind = "MARKET" if i == 4 else "LIMIT"
            if not show_rules(b, symbol):
                pause()
                continue
            side = side_input()
            qty = num("  Qty dalam koin (contoh 0.001): ", required=True)
            price = num("  Harga limit: ", required=True) if kind == "LIMIT" else None
            sl, tp = sltp_inputs()
            print(f"\n  Akan dikirim: {kind} {side} {qty:g} {symbol}"
                  f"{f' @ {price:g}' if price else ' harga pasar'}  SL {sl or '-'}  TP {tp or '-'}")
            if yes():
                oid, err = b.place(symbol, side, kind, qty, price, sl, tp)
                report(oid is not None, err, f"Order terkirim, id {oid}")
        elif i == 6:
            pos, _ = show_state(b, symbol)
            if not pos:
                print(f"\n  Tidak ada posisi {symbol}. Buka posisi dulu (menu 5 atau limit yang terisi).")
            else:
                print("\n  Kosong = tidak diubah, 0 = hapus.")
                sl, tp = sltp_inputs("  SL baru: ", "  TP baru: ")
                if (sl is not None or tp is not None) and yes():
                    report(*b.set_sl_tp(symbol, sl, tp))
        elif i == 7:
            o = pick_limit(b, symbol)
            if o:
                print("\n  Kosong = tidak diubah. Untuk SL/TP, 0 = hapus.")
                price = num(f"  Harga baru (sekarang {o.price}): ")
                qty = num(f"  Qty baru (sekarang {o.qty}): ")
                sl, tp = sltp_inputs(f"  SL baru (sekarang {o.sl or '-'}): ",
                                     f"  TP baru (sekarang {o.tp or '-'}): ")
                if yes():
                    report(*b.amend(symbol, o.order_id, price, qty, sl, tp))
        elif i == 8:
            o = pick_limit(b, symbol)
            if o and yes(f"  Batalkan {o.side} {o.qty} @ {o.price}? (y/n): "):
                report(*b.cancel(symbol, o.order_id))
        elif i == 9:
            if yes(f"  Batalkan SEMUA order {symbol}? (y/n): "):
                report(*b.cancel_all(symbol))
        elif i == 10:
            pos, _ = show_state(b, symbol)
            if pos:
                p = pos[0]
                qty = num(f"\n  Qty yang ditutup (kosong = semua {p.size}): ")
                if yes(f"  Tutup {qty or p.size} dari posisi {p.side} {symbol}? (y/n): "):
                    report(*b.close(symbol, qty), done="Perintah tutup terkirim.")
        elif i == 11:
            print("  Semua simbol:\n")
            show_state(b)
        pause()


def main():
    from copier import accounts
    envfile.load()
    accs = [a for a in accounts.load() if a.mode in ("demo", "testnet")]
    if not accs:
        sys.exit("[X] Belum ada akun Bybit demo/testnet (Setelan universal > Bybit > Akun Bybit). "
                 "Menu tes ini tidak dipakai untuk akun live.")
    acc = accs[0]
    if len(accs) > 1:
        print("  Akun mana yang dites?\n")
        for i, a in enumerate(accs, 1):
            print(f"  {i}. {a.describe()}")
        raw = input("\n  Nomor (Enter = 1): ").strip()
        acc = accs[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(accs) else accs[0]
    print(f"\n  Akun: {acc.describe()}\n")
    try:
        b = BybitBroker(acc.mode, acc.key, acc.secret)
    except ValueError as e:
        sys.exit(f"[X] {e}")
    run(b)


if __name__ == "__main__":
    main()
