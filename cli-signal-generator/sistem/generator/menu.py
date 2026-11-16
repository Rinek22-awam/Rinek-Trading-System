
import math
import os
import re
import subprocess
import sys
import time

from functools import partial
from threading import Thread

from . import actions, heartbeat, journal, main as runtime, settings, skills
from .settings import GeneratorError, REQUIRED_DENY, redact
from .telegram import AccountSender, BotSender


def ask(prompt):
    return input(prompt).strip()


def choose(options, back="Kembali"):
    for index, label in enumerate(options, 1):
        print(f"  {index}. {label}")
    print(f"  0. {back}")
    answer = ask("Pilih: ")
    if answer in ("", "0"):
        return None
    if not re.fullmatch(r"[0-9]{1,6}", answer) or not 1 <= int(answer) <= len(options):
        raise GeneratorError("Nomor tidak ada di daftar.")
    return int(answer) - 1


def open_path(path, editor=False):
    if editor:
        subprocess.Popen(["notepad", str(path)])
    else:
        os.startfile(path)


def action(*args, new_window=False):
    command = [sys.executable, "-m", "generator.main", *args]
    if new_window:
        subprocess.Popen(command, cwd=settings.SYSTEM, creationflags=subprocess.CREATE_NEW_CONSOLE)
        return None
    return subprocess.run(command, cwd=settings.SYSTEM, check=False).returncode


def change(section, **values):
    settings.update_yaml(settings.SYSTEM / "config.yaml", {(section, key): value for key, value in values.items()})
    print("Tersimpan.")


def number(prompt):
    value = ask(prompt)
    if value in ("", "0"):
        return None
    try:
        result = float(value)
    except ValueError:
        result = 0
    if not math.isfinite(result) or result <= 0:
        raise GeneratorError("Masukkan angka lebih besar dari nol.")
    return result


DELETE_LIST = 30   # jumlah analisa terakhir yang ditampilkan di menu hapus


def parse_numbers(text, limit):
    """'1,3' / '2-5' / '1, 4-6' jadi daftar nomor urut tanpa dobel. Salah ketik = error, tidak ada yang dihapus."""
    numbers = []
    for part in text.replace(" ", "").split(","):
        match = re.fullmatch(r"(\d{1,4})(?:-(\d{1,4}))?", part)
        if not match:
            raise GeneratorError("Tulis nomor seperti 1,3 atau 2-5.")
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= limit:
            raise GeneratorError(f"Nomor harus antara 1 dan {limit}, dan rentang dari kecil ke besar.")
        numbers += range(start, end + 1)
    return list(dict.fromkeys(numbers))


def active_label(skill):
    return "NYALA" if skill.enabled else "MATI"


class Menu:
    def __init__(self):
        self.channel_names = {}
        self.chart_status = "mengecek"
        self.chart_checked = 0
        self.chart_check = None
        self.leftover = None   # file pesan dari analisa yang dilewati (generator sedang sibuk)

    def bot(self):
        return BotSender(settings.read_secrets()["GEN_TG_BOT_TOKEN"])

    def channel_name(self, config):
        token = settings.read_secrets()["GEN_TG_BOT_TOKEN"]
        if not settings.valid_inbox(config) or not token:
            return config.inbox or "belum diatur"
        cache = self.channel_names
        if config.inbox not in cache:
            cache[config.inbox] = config.inbox

            def lookup():
                try:
                    chat = BotSender(token).call("getChat", chat_id=int(config.inbox))
                    cache[config.inbox] = chat.get("title") or config.inbox
                except (GeneratorError, AttributeError, ValueError):
                    pass

            Thread(target=lookup, daemon=True).start()
        return cache[config.inbox]

    def chart_label(self):
        if time.monotonic() - self.chart_checked >= 2 and not (
            self.chart_check and self.chart_check.is_alive()
        ):
            def probe():
                self.chart_status = "siap" if runtime.tradingview_ready() else "belum"
                self.chart_checked = time.monotonic()

            self.chart_check = Thread(target=probe, daemon=True)
            self.chart_check.start()
        return self.chart_status

    def header(self, title):
        if sys.stdout.isatty():
            os.system("cls")
        base = settings.read_config()
        with runtime.local_lock(runtime.DAEMON_PORT) as available:
            daemon = "MATI" if available else "NYALA"
        daemon += heartbeat.last_active_label(running=not available)
        active = []
        for name in settings.list_skills():
            try:
                if skills.read_skill(name, base).enabled:
                    active.append(name)
            except GeneratorError:
                continue
        pm = (settings.valid_report(base) if base.sender == "bot" else
              (settings.SYSTEM / "data" / "generator.session").is_file())
        tv = self.chart_label()
        print("CLI Signal Generator")
        print(f"Generator: {daemon} · Skill nyala: {', '.join(active) or '—'}")
        channel = redact(self.channel_name(base), settings.read_secrets())
        print(f"Kirim lewat: {base.sender} · Kotak masuk: {channel} · PM: {'✅' if pm else '❌'} · TradingView: {tv}")
        print(f"\n{title}\n")
        return base

    def screen(self, title, entries, root=False):
        while True:
            try:
                self.header(title)
                if root:
                    print("Tekan 0 atau Enter untuk keluar.")
                selected = choose([label for label, _ in entries], "Keluar" if root else "Kembali")
                if selected is None:
                    return
                entries[selected][1]()
            except (GeneratorError, OSError) as exc:
                self.error(exc)
                input("\nEnter untuk kembali.")

    def error(self, exc):
        message = str(exc) if isinstance(exc, GeneratorError) else "Operasi file atau program gagal. Cek akses dan coba lagi."
        print(redact(message, settings.read_secrets()))

    def leaf(self, title, run):
        base = self.header(title)
        run(base)
        input("\n0 atau Enter untuk kembali.")

    def start(self):
        self.screen("Nyalakan", [
            ("Generator jalan terus", lambda: self.leaf("Generator jalan terus", self.start_daemon)),
            ("Analisa sekarang", self.manual),
        ])

    def start_daemon(self, _):
        with runtime.local_lock(runtime.DAEMON_PORT) as available:
            if not available:
                print("Generator sudah nyala.")
                return
        action("daemon", new_window=True)
        print("Generator dibuka di jendela sendiri. Tutup jendelanya untuk berhenti.")

    def manual(self):
        self.header("Analisa sekarang")
        names = settings.list_skills()
        selected = choose([*names, "Mode uji fungsi (TES)"])
        if selected is None:
            return
        skill = names[selected] if selected < len(names) else None
        print("\nPesan untuk Claude (opsional), misal \"perhatikan juga level 4100\".")
        print("Pesan boleh mengalahkan aturan skill. Kalau diisi, semua blok masuk ke chat pribadimu dulu, tidak ke bot.")
        path = self.take_leftover()
        if path is None and ask("Ketik P lalu Enter untuk menulis pesan di Notepad, "
                                "atau Enter saja untuk tanpa pesan: ").upper() == "P":
            print("Notepad dibuka. Tulis atau tempel pesannya, simpan (Ctrl+S), lalu tutup Notepad.")
            path, length = actions.message_from_notepad()
            print(f"Pesan terbaca ({length:,} karakter)." if path else "Pesan kosong. Analisa jalan tanpa pesan.")
        if action(*actions.analysis_args(skill, path)) == runtime.EXIT_SKIPPED:
            print("\n⚠️ Analisa dilewati: generator sedang menganalisa. Jalankan lagi setelah selesai.")
            if path:
                self.leftover = path
                print("Pesanmu disimpan dan bisa dipakai lagi di Analisa sekarang berikutnya.")
        input("\nEnter untuk kembali.")

    def take_leftover(self):
        """Tawarkan pesan dari analisa yang tadi dilewati. Mengembalikan file pesannya, atau None."""
        path, self.leftover = self.leftover, None
        if path is None or not path.exists():
            return None
        if ask("Ada pesan dari analisa yang tadi dilewati. Pakai lagi? Ketik Y lalu Enter (Enter saja = tidak): "
               ).upper() == "Y":
            return path
        runtime.discard_message(path)
        return None

    def skill_menu(self):
        self.screen("Skill & analisa", [
            ("Pilih skill aktif", self.toggle),
            ("+ Tambah skill", lambda: self.leaf("Tambah skill", self.add)),
            ("Daftar skill", self.skill_list),
            ("Tool yang diizinkan", self.tools),
            ("Setelan global skill", lambda: self.leaf("Setelan global skill", self.global_skill)),
        ])

    def global_skill(self, base):
        print("Enter mempertahankan nilai sekarang. Ketik 0 untuk batal.")
        current_hours = ", ".join(base.skill_times)
        model = ask(f"Model ({base.model}): ")
        if model == "0":
            return
        effort = ask(f"Effort ({base.effort or 'bawaan Claude Code'}, - = bawaan): ")
        if effort == "0":
            return
        timeout = ask(f"Batas waktu dalam menit ({base.timeout / 60:g}): ")
        if timeout == "0":
            return
        hours = ask(f"Jam tertentu WIB, pisahkan dengan koma ({current_hours}): ")
        if hours == "0":
            return
        skills.save_global(model or base.model, "" if effort == "-" else (effort or base.effort),
                           timeout or str(base.timeout / 60), hours or current_hours)
        print("Setelan global skill tersimpan.")

    def toggle(self):
        while True:
            base = self.header("Pilih skill aktif")
            names = settings.list_skills()
            selected = choose([f"{name} [{active_label(skills.read_skill(name, base))}]" for name in names])
            if selected is None:
                return
            skills.toggle_skill(names[selected], base)

    def add(self, _):
        name = ask("Nama skill (huruf kecil, angka, tanda -): ")
        if name not in ("", "0"):
            open_path(skills.add_skill(name))
            print("Skill dibuat dalam keadaan MATI.")

    def skill_list(self):
        while True:
            base = self.header("Daftar skill")
            names = settings.list_skills()
            selected = choose([self.skill_summary(skills.read_skill(name, base)) for name in names])
            if selected is None:
                return
            self.skill_detail(names[selected])

    @staticmethod
    def skill_summary(skill):
        cfg = skill.config
        return (f"{cfg.skill_name} [{active_label(skill)}] · {skill.describe()} · "
                f"{cfg.model}/{cfg.effort or 'bawaan'} · kode {cfg.setup_code} · {cfg.timeout / 60:g} menit")

    def skill_detail(self, name):
        while True:
            base = self.header(name)
            skill = skills.read_skill(name, base)
            print(self.skill_summary(skill))
            selected = choose(["Jadwal analisa", "Model & effort", "Kode SETUP",
                               "Batas waktu analisa (menit)", "Edit isi skill", "HAPUS skill"])
            if selected is None:
                return
            if selected == 5:
                if ask(f"Pindahkan {name} ke arsip? Ketik HAPUS: ") == "HAPUS":
                    skills.archive_skill(name)
                    return
                continue
            handlers = [self.schedule, self.model, self.code, self.timeout,
                        lambda current: open_path(current.config.skill / "SKILL.md", editor=True)]
            try:
                handlers[selected](skill)
            except GeneratorError as exc:
                self.error(exc)
            if selected != 4:
                input("\nEnter untuk kembali.")

    def schedule(self, skill):
        name = skill.config.skill_name
        print(f"Sekarang: {skill.describe()}\n")
        print("Mode jadwal:")
        selected = choose(["Jam tertentu (misal 15:00, 20:30)",
                           "Tiap sekian menit (misal tiap 60 menit dari 20:00 sampai 04:00)"], "Batal")
        if selected is None:
            return
        changes = {("jadwal", "mode"): skills.MODES[selected]}
        if selected == 0:
            global_hours = ", ".join(skill.config.skill_times)
            print(f"\nJam WIB, pisahkan dengan koma. Kosong = ikut global ({global_hours} WIB).")
            hour_text = ask("Jam (Enter = ikut global, + = salin jam global, 0 = batal): ")
            if hour_text == "0":
                return
            if hour_text == "+":
                hour_text = global_hours
            changes["jadwal", "jam"] = skills.parse_times("" if hour_text == "-" else hour_text) or None
        else:
            every_text = ask(f"\nTiap berapa menit? (Enter = {skill.every}): ")
            if every_text == "0":
                return
            every = skills.parse_interval(every_text) if every_text else skill.every
            start = ask(f"Mulai jam (WIB, Enter = {skill.start}): ") or skill.start
            end = ask(f"Sampai jam (WIB, boleh lewat tengah malam, Enter = {skill.end}): ") or skill.end
            if not skills.HOUR.fullmatch(start) or not skills.HOUR.fullmatch(end):
                raise GeneratorError("Jam harus berbentuk HH:MM, dari 00:00 sampai 23:59.")
            changes.update({("jadwal", "tiap_jam"): every / 60, ("jadwal", "mulai"): start, ("jadwal", "sampai"): end})
        days_text = ask("Hari (sen, sel, rab, kam, jum, sab, min; Enter = tetap): ").lower()
        if days_text == "0":
            return
        days = [] if days_text == "-" else [part.strip() for part in days_text.split(",")] if days_text else list(skill.days)
        if any(part not in skills.DAYS for part in days):
            raise GeneratorError("Hari harus sen, sel, rab, kam, jum, sab, atau min.")
        changes["jadwal", "hari"] = list(dict.fromkeys(days))
        skills.change_skill(name, changes)
        updated = skills.read_skill(name, settings.read_config())
        print(f"Tersimpan: {updated.describe()}")
        if updated.mode == "interval":
            print("Jam jalan: " + (", ".join(updated.slots()) or "—"))

    def model(self, skill):
        runtime.pick_claude_settings(skill.config, ask=ask, save=lambda model, effort: skills.change_skill(
            skill.config.skill_name, {("", "model"): model, ("", "effort"): effort}
        ))

    def code(self, skill):
        code = ask("Kode sumber (TES untuk uji): ")
        if code not in ("", "0"):
            print("Tersimpan." if skills.change_code(skill.config.skill_name, code, ask) else "Dibatalkan.")

    def timeout(self, skill):
        value = number("Batas waktu (menit): ")
        if value is not None:
            if not math.isfinite(value * 60):
                raise GeneratorError("Batas waktu terlalu besar.")
            skills.change_skill(skill.config.skill_name, {("", "batas_waktu_menit"): value})
            print("Tersimpan.")

    def tools(self):
        self.screen("Tool yang diizinkan", [
            ("Lihat tool yang diizinkan", lambda: self.leaf("Tool yang diizinkan", self.show_allowed)),
            ("Izinkan tool TradingView", lambda: self.leaf("Izinkan tool TradingView",
                                                           partial(self.edit_allowed, tv=True, add=True))),
            ("Tutup tool TradingView", lambda: self.leaf("Tutup tool TradingView",
                                                         partial(self.edit_allowed, tv=True, add=False))),
            ("Izinkan tool bawaan Claude", lambda: self.leaf("Izinkan tool bawaan Claude",
                                                             partial(self.edit_allowed, tv=False, add=True))),
            ("Tutup tool bawaan Claude", lambda: self.leaf("Tutup tool bawaan Claude",
                                                           partial(self.edit_allowed, tv=False, add=False))),
            ("Daftar tolak tambahan (lanjutan)", self.deny),
        ])

    @staticmethod
    def show_allowed(base):
        print("Hanya tool di daftar ini yang dimuat saat analisa. Makin sedikit, makin hemat token.\n")
        builtin = "semua" if base.builtin_tools is None else ", ".join(base.builtin_tools) or "tidak ada"
        print(f"Tool bawaan Claude: {builtin}")
        if base.tv_tools is None:
            print("Tool TradingView: semua")
        else:
            print(f"Tool TradingView ({len(base.tv_tools)}):")
            for name in base.tv_tools:
                print(f"  - {name}" + ("  (tetap ditolak: terkunci)" if settings.TV_PREFIX + name == REQUIRED_DENY else ""))
        print(f"\nSelalu ditolak: {', '.join(base.denied_tools)}")

    @staticmethod
    def edit_allowed(base, tv, add):
        known = settings.TV_TOOLS if tv else settings.BUILTIN_TOOLS
        current = base.tv_tools if tv else base.builtin_tools
        if current is None:
            current = known   # belum dibatasi = semua diizinkan
        options = [name for name in known if name not in current] if add else list(current)
        if not options:
            print("Tidak ada pilihan." if add else "Daftar sudah kosong.")
            return
        selected = choose(options)
        if selected is None:
            return
        name = options[selected]
        updated = [*current, name] if add else [item for item in current if item != name]
        change("claude", **{"tools_tradingview" if tv else "tools_bawaan": updated})
        if add and tv and settings.TV_PREFIX + name == REQUIRED_DENY:
            print("Catatan: ui_evaluate terkunci, tetap ditolak walau diizinkan.")

    def deny(self):
        self.screen("Daftar tolak tambahan", [
            ("Lihat daftar", lambda: self.leaf("Daftar tolak", self.show_denied)),
            ("Tambah tool", lambda: self.leaf("Tambah tool", self.add_deny)),
            ("Hapus tool", lambda: self.leaf("Hapus tool", self.remove_deny)),
        ])

    def show_denied(self, base):
        for tool in base.denied_tools:
            print(tool + (" (terkunci)" if tool == REQUIRED_DENY else ""))

    def add_deny(self, base):
        tool = ask("Nama tool: ")
        if tool in ("", "0"):
            return
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_*.-]*", tool):
            raise GeneratorError("Nama tool tidak valid.")
        change("claude", tools_ditolak=list(dict.fromkeys((*base.denied_tools, tool))))

    def remove_deny(self, base):
        selected = choose([tool + (" (terkunci)" if tool == REQUIRED_DENY else "") for tool in base.denied_tools])
        if selected is not None:
            tool = base.denied_tools[selected]
            if tool == REQUIRED_DENY:
                raise GeneratorError("ui_evaluate terkunci dan tidak bisa dihapus.")
            change("claude", tools_ditolak=[item for item in base.denied_tools if item != tool])

    def telegram(self):
        self.screen("Telegram", [
            ("Cara kirim: bot / akun", lambda: self.leaf("Cara kirim", self.sender)),
            ("Isi / ganti token bot", lambda: self.leaf("Token bot", self.token)),
            ("Isi / ganti API akun Telegram", lambda: self.leaf("API akun Telegram", self.api)),
            ("Login akun Telegram", lambda: self.leaf("Login akun Telegram", self.login)),
            ("Ganti channel kotak masuk", lambda: self.leaf("Channel kotak masuk", self.inbox)),
            ("Chat pribadi laporan", lambda: self.leaf("Chat pribadi laporan", self.report)),
            ("Kirim analisa lengkap ke PM", lambda: self.leaf("Analisa lengkap ke PM", self.toggle_pm)),
            ("Pesan rutin (tanda masih hidup)", self.routine),
        ])

    def routine(self):
        def toggle(_):
            current = heartbeat.read()
            heartbeat.save(enabled=not current.enabled, times=list(current.times or heartbeat.DEFAULT_TIMES))
            print(f"Pesan rutin sekarang: {heartbeat.read().describe()}")

        def hours(_):
            current = heartbeat.read()
            print(f"Sekarang: {', '.join(current.times) or '—'}")
            text = ask("Jam kirim WIB, pisahkan koma (contoh 07:00, 19:00; Enter = tetap): ")
            if text in ("", "0"):
                return
            times = [part.strip() for part in text.split(",") if part.strip()]
            if not times or any(not skills.HOUR.fullmatch(part) for part in times):
                raise GeneratorError("Jam harus berbentuk HH:MM, dari 00:00 sampai 23:59.")
            heartbeat.save(enabled=current.enabled, times=sorted(dict.fromkeys(times)))
            print(f"Tersimpan: {heartbeat.read().describe()}")

        def sample(_):
            print("Mengirim pesan rutin ke chat pribadi...")
            action("rutin")

        while True:
            self.header("Pesan rutin")
            print(f"Sekarang: {heartbeat.read().describe()}")
            print("Dikirim ke chat pribadi selama generator jalan terus. "
                  "Pesan tidak datang di jam biasanya = generator mati.\n")
            selected = choose(["Nyalakan / matikan", "Atur jam kirim", "Kirim contoh sekarang"])
            if selected is None:
                return
            run = (toggle, hours, sample)[selected]
            try:
                run(None)
            except GeneratorError as exc:
                self.error(exc)
            input("\nEnter untuk kembali.")

    def sender(self, base):
        print(f"Sekarang: {base.sender}")
        selected = choose(["bot", "akun"])
        if selected is not None:
            change("telegram", pengirim=("bot", "akun")[selected])

    def token(self, _):
        token = ask("Token dari BotFather: ")
        if token in ("", "0"):
            return
        actions.save_token(token)
        self.channel_names = {}
        print("Tersimpan.")

    def api(self, _):
        print("Ambil dari my.telegram.org → API development tools. Boleh pakai API Pembaca Automanage.")
        api_id = ask("API ID (Enter = tetap): ")
        if api_id == "0":
            return
        api_hash = ask("API hash (Enter = tetap): ")
        if api_hash == "0":
            return
        updates = actions.api_changes(api_id, api_hash)
        if not updates:
            return
        reset = False
        if actions.session_file().is_file():
            reset = ask("API berubah. Hapus sesi lama dan login ulang? Ketik YA: ").upper() == "YA"
            if not reset:
                print("Dibatalkan.")
                return
        actions.save_api(updates, reset)
        print("Tersimpan.")

    def login(self, base):
        if base.sender != "akun":
            print("Mode bot tidak perlu login akun.")
            return
        secrets = settings.read_secrets()
        if not secrets["GEN_TG_API_ID"] or not secrets["GEN_TG_API_HASH"]:
            raise GeneratorError("Isi API ID dan API hash lewat Telegram → 3 dulu.")
        with runtime.local_lock(runtime.WORK_PORT) as available:
            if not available:
                raise GeneratorError("Generator sedang bekerja. Coba lagi setelah selesai.")
            sender = AccountSender(secrets)
            try:
                if sender.authorized():
                    print("Akun Telegram sudah login.")
                    return
                print("Akun Telegram belum login.")
                sender.login()
                print("Akun Telegram sudah login.")
            finally:
                sender.close()

    def inbox(self, _):
        selected = choose(["Channel tempat bot dijadikan admin", "Teruskan postingan channel ke bot", "Ketik ID manual"])
        if selected is None:
            return
        bot = self.bot()
        if selected == 2:
            chat_id = ask("ID channel (-100...): ")
        else:
            if selected == 0:
                print("Telegram menyimpan update sekitar 24 jam. Kalau channel hilang, jadikan ulang bot admin atau teruskan postingan.")
            else:
                print("Teruskan satu postingan channel ke chat bot generator.")
                if ask("Ketik CEK setelah diteruskan, atau Enter untuk kembali: ").upper() != "CEK":
                    return
            chats = candidate_channels(bot.updates(), forwarded=selected == 1)
            if not chats:
                print("Channel belum terdeteksi. Coba pilihan lain atau ulangi setelah ada update baru.")
                return
            chosen = choose([f"{chat.get('title', 'Channel')} ({chat['id']})" for chat in chats])
            if chosen is None:
                return
            chat_id = str(chats[chosen]["id"])
        if chat_id in ("", "0"):
            return
        title, can_post = actions.check_inbox(chat_id)
        print(f"Channel: {title} ({chat_id})")
        if not can_post:
            print("Izin posting belum terverifikasi. Jadikan bot admin dan nyalakan izin posting di channel.")
            if ask("Tetap simpan? Ketik YA: ").upper() != "YA":
                return
        actions.save_inbox(chat_id)
        print("Tersimpan.")
        self.channel_names[chat_id] = title

    def report(self, base):
        print(f"Chat laporan: {base.report or ('Saved Messages' if base.sender == 'akun' else 'belum terdeteksi')}")
        selected = choose(["Deteksi ulang otomatis", "Ketik ID manual"])
        if selected is None:
            return
        if selected == 0 and base.sender == "akun":
            change("telegram", laporan_ke="")
            return
        if selected == 0:
            print("Tekan Start atau kirim pesan di chat pribadi bot.")
            if ask("Ketik CEK setelah siap, atau Enter untuk kembali: ").upper() != "CEK":
                return
            chat_id = self.bot().find_private_chat()
            if chat_id is None:
                raise GeneratorError("Chat belum terdeteksi. Tekan Start atau kirim pesan ke bot lalu coba lagi.")
        else:
            chat_id = ask("ID chat pribadi: ")
            if chat_id in ("", "0"):
                return
        actions.save_report(chat_id, base.sender)
        print("Tersimpan.")

    def toggle_pm(self, base):
        print(f"Sekarang: {'NYALA' if base.send_analysis else 'MATI'}")
        selected = choose(["Nyala", "Mati"])
        if selected is not None:
            change("telegram", kirim_analisa_ke_pm=selected == 0)

    def tradingview(self):
        self.screen("TradingView", [
            ("Buka TradingView sekarang", lambda: self.leaf("Buka TradingView", self.open_tv)),
            ("Letak skrip peluncur", lambda: self.leaf("Letak skrip peluncur", self.launcher)),
            ("Lama tunggu chart (detik)", lambda: self.leaf("Lama tunggu chart", self.tv_wait)),
        ])

    def open_tv(self, _):
        actions.open_tradingview()
        print("Chart TradingView siap.")

    def launcher(self, base):
        print(f"Sekarang: {base.tv_launcher}")
        value = ask("Lokasi skrip .bat atau .cmd: ").strip('"')
        if value in ("", "0"):
            return
        actions.save_launcher(value)
        print("Tersimpan.")

    def tv_wait(self, base):
        print(f"Sekarang: {base.tv_wait:g} detik")
        value = number("Lama tunggu (detik): ")
        if value is not None:
            change("tradingview", tunggu_detik=value)

    def journal(self):
        journal.FOLDER.mkdir(exist_ok=True)
        self.screen("Jurnal", [
            ("Ringkasan", lambda: self.leaf("Ringkasan jurnal", lambda _: print(journal.summary_text()))),
            ("Daftar analisa terakhir", lambda: self.leaf("Daftar analisa terakhir",
                                                          lambda _: print(journal.recent_text()))),
            ("Buka file Excel jurnal", lambda: self.leaf("Buka file Excel jurnal", self.journal_excel)),
            ("Buka folder jurnal", partial(open_path, journal.FOLDER)),
            ("Hapus analisa dari jurnal", lambda: self.leaf("Hapus analisa dari jurnal", self.journal_delete)),
            ("Reset jurnal (mulai dari nol)", lambda: self.leaf("Reset jurnal", self.journal_reset)),
        ])

    @staticmethod
    def journal_delete(_):
        entries = list(reversed(journal.read_entries()))[:DELETE_LIST]
        if not entries:
            print("Jurnal masih kosong.")
            return
        print(f"{len(entries)} analisa terakhir (terbaru di atas):")
        for number, entry in enumerate(entries, 1):
            print(f"  {number:>2}. {journal.describe(entry)[:150]}")
        answer = ask("\nNomor yang dihapus (contoh 1,3 atau 2-5; Enter = batal): ")
        if answer in ("", "0"):
            return
        picked = parse_numbers(answer, len(entries))
        chosen = [entries[number - 1] for number in picked]
        print(f"\nAkan dihapus {len(chosen)} analisa (semua baris setup-nya ikut):")
        for entry in chosen:
            print(f"  • {journal.describe(entry)[:150]}")
        if ask("Ketik YA untuk menghapus: ").upper() != "YA":
            print("Dibatalkan.")
            return
        removed = actions.delete_analyses([entry["id"] for entry in chosen])
        print(f"{len(removed)} analisa dihapus. Cadangan: sistem\\data\\jurnal-terhapus.jsonl · "
              "file hasilnya: sistem\\data\\hasil\\terhapus.")

    @staticmethod
    def journal_reset(_):
        analyses, filled = journal.reset_counts()
        if not analyses and not journal.XLSX.exists():
            print("Jurnal sudah kosong.")
            return
        print(f"Jurnal berisi {analyses} analisa; {filled} baris sudah kamu isi Hasil-nya.")
        print("Reset = jurnal mulai dari nol. Data lama TIDAK dihapus, tapi dipindah ke jurnal\\arsip\\<tanggal-jam>\\")
        print("(catatan asli, file Excel, cadangan hapusan, dan file hasil analisanya).")
        print("Arsip bisa kamu hapus manual kapan saja lewat Buka folder jurnal.")
        if ask("\nKetik RESET untuk melanjutkan: ") != "RESET":
            print("Dibatalkan.")
            return
        archive = actions.reset_journal()
        print(f"Jurnal direset. Data lama ada di {archive}." if archive else "Tidak ada data untuk diarsipkan.")

    @staticmethod
    def journal_excel(_):
        try:
            added = journal.sync()
        except journal.JournalLocked:
            print("File Excel jurnal sedang dibuka. Analisa baru masuk setelah Excel ditutup "
                  "dan menu ini dipilih lagi.")
        except journal.JournalError as exc:
            print(exc)
            return
        else:
            if added:
                print(f"{added} analisa baru ditambahkan ke Excel.")
        if not journal.XLSX.exists():
            print("Jurnal masih kosong. Analisa skill berikutnya akan tercatat otomatis.")
            return
        open_path(journal.XLSX)
        print(f"Membuka {journal.XLSX.name}. Isi kolom kuning (Hasil, R didapat, Catatan kamu), lalu simpan.")

    def utilities(self):
        self.screen("Utilitas", [
            ("Cek kesiapan", lambda: self.leaf("Cek kesiapan", lambda _: action("ready"))),
            ("Kirim pesan uji ke kotak masuk (Kode TES)", lambda: self.leaf("Kirim pesan uji", lambda _: action("test"))),
            ("Lihat log terakhir", lambda: self.leaf("Log terakhir", self.logs)),
            ("Riwayat analisa", lambda: self.leaf("Riwayat analisa", self.history)),
            ("Buka config.yaml (mode lanjut)", partial(open_path, settings.SYSTEM / "config.yaml", editor=True)),
            ("Tes logika program", lambda: self.leaf("Tes logika program", self.tests)),
        ])

    def logs(self, _):
        path = settings.SYSTEM / "logs" / "generator.log"
        if path.is_file():
            text = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])
            print(redact(text, settings.read_secrets()))
        else:
            print("Log belum ada.")

    def history(self, _):
        paths = sorted((settings.SYSTEM / "data" / "hasil").glob("*.txt"), reverse=True)
        if not paths:
            print("Riwayat masih kosong.")
            return
        for path in paths:
            print(path.name)
        if choose(["Buka hasil terbaru"]) is not None:
            open_path(paths[0], editor=True)

    def tests(self, _):
        subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=settings.SYSTEM, check=False)

    @staticmethod
    def open_dashboard(_):
        actions.open_dashboard()
        print("Dashboard dibuka di jendela sendiri. Kalau sudah terbuka, jendelanya dimunculkan.")

    def run(self):
        self.screen("Menu utama", [
            ("Nyalakan", self.start),
            ("Buka Dashboard", lambda: self.leaf("Dashboard", self.open_dashboard)),
            ("Skill & analisa", self.skill_menu),
            ("Telegram", self.telegram),
            ("TradingView", self.tradingview),
            ("Jurnal", self.journal),
            ("Utilitas", self.utilities),
        ], root=True)


def candidate_channels(updates, forwarded):
    channels = {}
    for update in updates:
        if forwarded:
            message = update.get("message") or {}
            origin = message.get("forward_origin") or {}
            if message.get("chat", {}).get("type") != "private":
                continue
            chat = origin.get("chat") or message.get("forward_from_chat") or {}
        else:
            member = update.get("my_chat_member") or {}
            chat = member.get("chat") or {}
            if (member.get("new_chat_member") or {}).get("status") != "administrator":
                channels.pop(chat.get("id"), None)
                continue
        if chat.get("type") == "channel" and isinstance(chat.get("id"), int):
            channels[chat["id"]] = chat
    return list(channels.values())


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        skills.migrate()
        base = settings.read_config()
        config_path = settings.SYSTEM / "config.yaml"
        raw = settings.yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
        if REQUIRED_DENY not in raw["claude"]["tools_ditolak"]:
            settings.update_yaml(config_path, {("claude", "tools_ditolak"): list(base.denied_tools)})
        (settings.SYSTEM.parent / "jurnal").mkdir(exist_ok=True)
        Menu().run()
    except (EOFError, KeyboardInterrupt):
        return
    except (GeneratorError, OSError) as exc:
        Menu().error(exc)
        input("Enter untuk menutup menu.")


if __name__ == "__main__":
    main()
