"""Dashboard CLI Signal Generator (Tkinter). Dibuka dari MENU.bat → Buka Dashboard.

Isinya sama dengan MENU.bat, dalam tujuh tab. Semua perubahan memakai fungsi yang sama dengan menu
(skills, settings, heartbeat, journal, actions), jadi hasilnya selalu sama. Analisa dan generator
jalan terus tetap berjalan sebagai proses generator biasa, bukan di dalam jendela ini.
"""
import os
import queue
import re
import socket
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

import yaml

from . import actions, heartbeat, journal, main as runtime, settings, skills
from .settings import WIB, GeneratorError, redact

DASHBOARD_PORT = 47658
POLL_MS = 1000
STATUS_EVERY = 5   # setelan dan status skill dibaca ulang tiap 5 putaran (±5 detik); log tetap tiap detik
TITLE_FONT = ("Segoe UI", 11, "bold")
SMALL_FONT = ("Segoe UI", 9)
LOG_FONT = ("Consolas", 10)
COLORS = {"DEBUG": "#526074", "INFO": "#154bb5", "AKTIF": "#18703b", "WARNING": "#984600", "ERROR": "#b32328"}
LOG_LINE = re.compile(r"^\d{4}-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})[,.]\d+ (\w+) (.*)$")
LOG_KEEP = 2000
GLOBAL = "ikut global"
NATIVE_EFFORT = "bawaan Claude Code"


def format_log(line):
    """'2026-11-14 01:03:44,242 INFO pesan' → (warna, '14/11 01:03:44  [INFO]     pesan')."""
    match = LOG_LINE.match(line)
    if not match:
        return "DEBUG", line
    month, day, clock, level, message = match.groups()
    color = "AKTIF" if level == "INFO" and message.startswith("✅") else level
    color = color if color in COLORS else "INFO"
    return color, f"{day}/{month} {clock}  {'[' + level + ']':<10} {message}"


class LogTail:
    """Baca baris baru dari log generator. Pertama kali: `first` baris terakhir (bawaan 0 = log sesi sebelumnya
    tidak ditampilkan; Dashboard mulai bersih). Kalau log diputar (file baru lebih kecil): semua isi file baru."""

    def __init__(self, path, first=0):
        self.path = path
        self.position = None
        self.first = first

    def read(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            if self.position is None:
                self.position = 0   # log belum ada: semua yang ditulis nanti adalah baris baru
            return []
        if self.position is None or size < self.position:   # baru dibuka, atau log diputar
            opened = self.position is None
            with self.path.open("rb") as file:
                data = file.read()
            self.position = len(data)
            lines = data.decode("utf-8", "replace").splitlines()
            if not opened:
                return lines
            return lines[-self.first:] if self.first else []
        if size == self.position:
            return []
        with self.path.open("rb") as file:
            file.seek(self.position)
            data = file.read()
        self.position += len(data)
        return data.decode("utf-8", "replace").splitlines()


def secrets():
    try:
        return settings.read_secrets()
    except Exception:
        return {}


def clean(text):
    return redact(str(text), secrets())


def heading(parent, text, pady=(0, 6)):
    ttk.Label(parent, text=text, font=TITLE_FONT).pack(anchor="w", pady=pady)


def note(parent, text):
    label = ttk.Label(parent, text=text, font=SMALL_FONT, foreground=COLORS["DEBUG"], wraplength=820, justify="left")
    label.pack(anchor="w", pady=(0, 8))
    return label


def scroll_area(parent, height):
    box = ttk.Frame(parent)
    box.pack(fill="both", expand=True)
    canvas = tk.Canvas(box, highlightthickness=0, height=height)
    bar = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    inner = ttk.Frame(canvas)
    window = canvas.create_window(0, 0, window=inner, anchor="nw")
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
    inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
    return inner


def open_file(path, editor=False):
    if editor:
        subprocess.Popen(["notepad", str(path)])
    else:
        os.startfile(path)


class Page:
    def __init__(self, app, tabs, title):
        self.app = app
        self.frame = ttk.Frame(tabs, padding=12)
        tabs.add(self.frame, text=title)

    def safe(self, callback):
        """Bungkus aksi tombol: error ditampilkan sebagai pesan, tidak menutup dashboard."""
        def run(*_):
            try:
                callback()
            except Exception as error:
                self.app.error(error)
        return run

    def refresh(self):
        pass


class MessageDialog:
    """Popup "Analisa sekarang": kotak pesan opsional untuk Claude. on_run(teks) dipanggil saat Jalankan ditekan."""

    def __init__(self, root, skill, on_run, text=""):
        self.on_run = on_run
        self.window = window = tk.Toplevel(root)
        window.title(f"Analisa sekarang · {skill}")
        window.transient(root)
        window.minsize(520, 320)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Pesan untuk Claude · {skill}", font=TITLE_FONT).pack(anchor="w")
        ttk.Label(frame, font=SMALL_FONT, foreground=COLORS["DEBUG"], wraplength=640, justify="left", text=(
            "Opsional. Kosongkan untuk analisa biasa. Contoh: \"perhatikan juga level 4100\", atau tempel info panjang.\n"
            "Kalau diisi: pesan boleh mengalahkan aturan skill, dan SEMUA blok masuk ke chat pribadimu dulu "
            "(SETUP RENDAH), tidak langsung ke bot. Di Telegram dan jurnal cuma tertulis \"dengan pesan\"; "
            "isi lengkapnya disimpan di file hasil analisa.")).pack(anchor="w", pady=(2, 8))
        self.text = ScrolledText(frame, width=80, height=14, wrap="word", font=LOG_FONT, undo=True)
        self.text.pack(fill="both", expand=True)
        if text:   # pesan dari analisa yang tadi dilewati
            self.text.insert("1.0", text)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Batal", command=window.destroy).pack(side="right")
        ttk.Button(buttons, text="Jalankan", command=self.run).pack(side="right", padx=(0, 6))
        window.bind("<Escape>", lambda _: window.destroy())
        window.grab_set()
        self.text.focus_set()

    def run(self):
        text = self.text.get("1.0", "end").strip()
        self.window.destroy()
        self.on_run(text)


# ---------- tab 1: Dashboard ----------
class HomeTab(Page):
    def __init__(self, app, tabs):
        super().__init__(app, tabs, "Dashboard")
        frame = self.frame
        frame.columnconfigure(0, weight=1)
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Generator jalan terus", font=TITLE_FONT).pack(side="left")
        self.status = ttk.Label(top)
        self.status.pack(side="left", padx=10)
        buttons = ttk.Frame(top)
        buttons.pack(side="right")
        self.start = ttk.Button(buttons, text="Nyalakan", command=self.safe(self.turn_on))
        self.stop = ttk.Button(buttons, text="Stop", command=self.safe(self.turn_off))
        for button in (self.start, self.stop):
            button.pack(side="left", padx=(5, 0))
        self.info = ttk.Label(frame, font=SMALL_FONT, foreground=COLORS["DEBUG"])
        self.info.grid(row=1, column=0, sticky="w", pady=(4, 10))

        now = ttk.Frame(frame)
        now.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(now, text="Analisa sekarang", font=TITLE_FONT).pack(side="left")
        self.choice = ttk.Combobox(now, state="readonly", width=28)
        self.choice.pack(side="left", padx=10)
        self.run_button = ttk.Button(now, text="Jalankan", command=self.safe(self.analyze))
        self.run_button.pack(side="left")
        self.run_state = ttk.Label(now, font=SMALL_FONT, foreground=COLORS["DEBUG"])
        self.run_state.pack(side="left", padx=10)
        self.process = None
        self.process_message = ("", None)   # (teks, file) pesan untuk analisa yang sedang berjalan
        self.leftover = ""                   # pesan dari analisa yang dilewati, diisikan lagi ke jendela pesan

        ttk.Label(frame, text="Skill dan jadwal", font=TITLE_FONT).grid(row=3, column=0, sticky="w")
        self.tree = ttk.Treeview(frame, columns=("status", "jadwal", "berikutnya", "model", "kode"),
                                 show="tree headings", height=4)
        self.tree.heading("#0", text="Skill", anchor="w")
        self.tree.column("#0", width=120)
        for key, text, width in (("status", "Status", 70), ("jadwal", "Jadwal", 230), ("berikutnya", "Berikutnya", 95),
                                 ("model", "Model / effort", 170), ("kode", "Kode", 70)):
            self.tree.heading(key, text=text, anchor="w")
            self.tree.column(key, width=width, stretch=key == "jadwal")
        for tag, color in (("on", COLORS["AKTIF"]), ("off", COLORS["DEBUG"]),
                           ("bad", COLORS["ERROR"])):
            self.tree.tag_configure(tag, foreground=color)
        self.tree.grid(row=4, column=0, sticky="ew", pady=(4, 4))
        toggles = ttk.Frame(frame)
        toggles.grid(row=5, column=0, sticky="w", pady=(0, 10))
        ttk.Button(toggles, text="Nyalakan / matikan skill terpilih", command=self.safe(self.toggle)).pack(side="left")
        ttk.Label(toggles, text="Skill NYALA ikut jadwal saat generator jalan terus.", font=SMALL_FONT,
                  foreground=COLORS["DEBUG"]).pack(side="left", padx=10)

        head = ttk.Frame(frame)
        head.grid(row=6, column=0, sticky="ew")
        ttk.Label(head, text="Log generator", font=TITLE_FONT).pack(side="left")
        ttk.Button(head, text="Bersihkan log", command=self.clear).pack(side="right")
        frame.rowconfigure(7, weight=1)
        output = ttk.Frame(frame)
        output.grid(row=7, column=0, sticky="nsew", pady=(4, 0))
        output.columnconfigure(0, weight=1)
        output.rowconfigure(0, weight=1)
        self.log = tk.Text(output, height=8, wrap="word", state="disabled", font=LOG_FONT, padx=8, pady=5,
                           background="white", foreground="#222832")
        bar = ttk.Scrollbar(output, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        for level, color in COLORS.items():
            self.log.tag_configure(level, foreground=color)
        self.tail = LogTail(settings.LOGS / "generator.log")
        self.snapshot = None

    # generator
    def turn_on(self):
        # Tanpa jendela cmd: generator jalan di latar, kegiatannya terlihat di log di bawah.
        # Membawa nomor proses dashboard, supaya generator ikut berhenti kalau dashboard hilang.
        actions.start_generator(window=False, parent=os.getpid())
        self.app.toast("Generator nyala di latar. Kegiatannya terlihat di log; ikut mati kalau dashboard ditutup.")

    def turn_off(self):
        if not messagebox.askyesno("Stop generator", "Hentikan generator jalan terus?\n"
                                   "Analisa yang sedang berjalan ikut berhenti.", parent=self.app.root):
            return
        if actions.stop_generator():
            self.app.toast("Generator dihentikan.")

    # analisa sekarang
    def analyze(self):
        if self.process is not None and self.process.poll() is None:
            raise GeneratorError("Analisa dari dashboard masih berjalan.")
        if actions.analysis_running():
            raise GeneratorError("Generator sedang menganalisa. Tunggu sampai selesai.")
        names = settings.list_skills()
        index = self.choice.current()
        skill = names[index] if 0 <= index < len(names) else None
        MessageDialog(self.app.root, skill or "mode uji fungsi",
                      lambda text: self.safe(lambda: self.start_analysis(skill, text))(), self.leftover)

    def start_analysis(self, skill, text):
        if self.process is not None and self.process.poll() is None:
            raise GeneratorError("Analisa dari dashboard masih berjalan.")
        if actions.analysis_running():
            raise GeneratorError("Generator sedang menganalisa. Tunggu sampai selesai.")
        path = actions.save_message(text)
        self.leftover = ""
        self.process = actions.launch(*actions.analysis_args(skill, path))
        self.process_message = (text.strip(), path)
        self.run_state.configure(text=f"Analisa berjalan{' (dengan pesan)' if path else ''}… lihat log di bawah.",
                                 foreground=COLORS["INFO"])

    def finish_analysis(self, code):
        """Proses analisa selesai: tampilkan hasilnya. Dilewati = pesan disimpan untuk Jalankan berikutnya."""
        text, path = self.process_message
        self.process, self.process_message = None, ("", None)
        if code == runtime.EXIT_SKIPPED:
            self.leftover = text
            runtime.discard_message(path)   # teksnya sudah dipegang Dashboard
            note = " Pesanmu disimpan dan muncul lagi saat Jalankan." if text else ""
            self.run_state.configure(text="Analisa dilewati: generator sedang menganalisa." + note,
                                     foreground=COLORS["WARNING"])
            return
        self.run_state.configure(text="Analisa selesai." if code == 0 else "Analisa gagal, lihat log.",
                                 foreground=COLORS["AKTIF"] if code == 0 else COLORS["ERROR"])

    def toggle(self):
        selected = self.tree.selection()
        if not selected:
            raise GeneratorError("Pilih skill di tabel dulu.")
        skills.toggle_skill(self.tree.item(selected[0], "text"), settings.read_config())
        self.app.refresh_skills()

    # log
    def clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def append(self, lines):
        if not lines:
            return
        follow = self.log.yview()[1] >= 0.999
        self.log.configure(state="normal")
        hidden = secrets()   # sekali per kelompok baris, bukan per baris
        for line in lines:
            color, text = format_log(redact(line, hidden))
            self.log.insert("end", text + "\n", color)
        count = int(self.log.index("end-1c").split(".")[0])
        if count > LOG_KEEP:
            self.log.delete("1.0", f"{count - LOG_KEEP}.0")
        self.log.configure(state="disabled")
        if follow:
            self.log.see("end")

    def refresh(self):
        self.append(self.tail.read())
        running = actions.generator_running()
        busy = actions.analysis_running()
        label = ("● Jalan" if running else "○ Mati") + heartbeat.last_active_label(running)
        if busy:
            label += " · sedang analisa"
        self.status.configure(text=label, foreground=COLORS["AKTIF"] if running else COLORS["DEBUG"])
        self.start.configure(state="disabled" if running else "normal")
        self.stop.configure(state="normal" if running else "disabled")
        if self.process is not None and self.process.poll() is not None:
            self.finish_analysis(self.process.returncode)
        self.run_button.configure(state="disabled" if busy or self.process is not None else "normal")

    def render_skills(self, base, items):
        names = [name for name, _ in items]
        values = [*names, "Mode uji fungsi (TES)"]
        if list(self.choice["values"]) != values:
            current = self.choice.get()
            self.choice["values"] = values
            self.choice.set(current if current in values else values[0])
        now = datetime.now(WIB)
        rows = []
        for name, skill in items:
            if skill is None:
                rows.append((name, ("bad",), ("?", "setelan tidak valid", "—", "—", "—")))
                continue
            cfg = skill.config
            upcoming = heartbeat.next_run(skill, now) if skill.enabled else "—"
            tag = "on" if skill.enabled else "off"
            rows.append((name, (tag,), ("NYALA" if skill.enabled else "MATI", skill.describe(), upcoming or "—",
                                        f"{cfg.model} / {cfg.effort or 'bawaan'}",
                                        cfg.setup_code)))
        if rows == self.snapshot:
            return
        self.snapshot = rows
        selected = [self.tree.item(item, "text") for item in self.tree.selection()]
        self.tree.delete(*self.tree.get_children())
        for name, tags, values in rows:
            item = self.tree.insert("", "end", text=name, tags=tags, values=values)
            if name in selected:
                self.tree.selection_set(item)


# ---------- tab 2: Skill & analisa ----------
class SkillTab(Page):
    def __init__(self, app, tabs):
        super().__init__(app, tabs, "Skill & analisa")
        frame = self.frame
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text="Daftar skill", font=TITLE_FONT).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Setelan skill", font=TITLE_FONT).grid(row=0, column=1, sticky="w", padx=(16, 0))
        left = ttk.Frame(frame)
        left.grid(row=1, column=0, sticky="ns", pady=(6, 0))
        self.names = tk.Listbox(left, height=10, width=22, exportselection=False, font=("Segoe UI", 10),
                                activestyle="none")
        self.names.pack(fill="y", expand=True)
        self.names.bind("<<ListboxSelect>>", self.safe(self.load))
        ttk.Button(left, text="+ Tambah skill", command=self.safe(self.add)).pack(fill="x", pady=(8, 0))
        ttk.Button(left, text="Edit isi skill (SKILL.md)", command=self.safe(self.edit)).pack(fill="x", pady=(4, 0))
        ttk.Button(left, text="Hapus skill (ke arsip)", command=self.safe(self.remove)).pack(fill="x", pady=(4, 0))
        ttk.Button(left, text="Setelan global skill", command=self.safe(self.edit_global)).pack(fill="x", pady=(8, 0))

        right = ttk.Frame(frame)
        right.grid(row=1, column=1, sticky="nsew", padx=(16, 0), pady=(6, 0))
        right.columnconfigure(1, weight=1)
        self.on = tk.BooleanVar(right)
        ttk.Checkbutton(right, text="Skill NYALA (ikut jadwal)", variable=self.on).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(right, text="Mode jadwal").grid(row=1, column=0, sticky="w", pady=4)
        modes = ttk.Frame(right)
        modes.grid(row=1, column=1, sticky="w")
        self.mode = tk.StringVar(right, value="jam")
        ttk.Radiobutton(modes, text="Jam tertentu", value="jam", variable=self.mode,
                        command=self.show_mode).pack(side="left")
        ttk.Radiobutton(modes, text="Tiap sekian menit", value="interval", variable=self.mode,
                        command=self.show_mode).pack(side="left", padx=12)
        self.hours_label = ttk.Label(right, text="Jam (WIB, pisah koma)")
        self.hours_box = ttk.Frame(right)
        self.hours = ttk.Entry(self.hours_box)
        self.hours.grid(row=0, column=0, sticky="ew")
        self.hours_box.columnconfigure(0, weight=1)
        ttk.Button(self.hours_box, text="Salin jam global", command=self.safe(self.copy_global_times)).grid(
            row=0, column=1, padx=(6, 0))
        self.hours_hint = ttk.Label(self.hours_box, font=SMALL_FONT, foreground=COLORS["DEBUG"])
        self.hours_hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.every_label = ttk.Label(right, text="Tiap / mulai / sampai")
        self.every_box = ttk.Frame(right)
        self.every = ttk.Spinbox(self.every_box, from_=skills.MIN_INTERVAL, to=skills.MAX_INTERVAL, width=6)
        self.begin = ttk.Entry(self.every_box, width=7)
        self.end = ttk.Entry(self.every_box, width=7)
        self.every.pack(side="left")
        ttk.Label(self.every_box, text="menit, dari").pack(side="left", padx=4)
        self.begin.pack(side="left")
        ttk.Label(self.every_box, text="sampai").pack(side="left", padx=4)
        self.end.pack(side="left")
        ttk.Label(self.every_box, text="(boleh lewat tengah malam)", font=SMALL_FONT,
                  foreground=COLORS["DEBUG"]).pack(side="left", padx=6)
        ttk.Label(right, text="Hari aktif").grid(row=3, column=0, sticky="w", pady=4)
        days = ttk.Frame(right)
        days.grid(row=3, column=1, sticky="w")
        self.days = {}
        for day in skills.DAYS:
            var = self.days[day] = tk.BooleanVar(right)
            ttk.Checkbutton(days, text=day.capitalize(), variable=var).pack(side="left", padx=(0, 6))
        ttk.Label(right, text="Model").grid(row=4, column=0, sticky="w", pady=4)
        self.model = ttk.Combobox(right, width=30)
        self.model.grid(row=4, column=1, sticky="w", pady=4)
        ttk.Label(right, text="Effort").grid(row=5, column=0, sticky="w", pady=4)
        self.effort = ttk.Combobox(right, state="readonly", width=30)
        self.effort.grid(row=5, column=1, sticky="w", pady=4)
        ttk.Label(right, text="Kode SETUP").grid(row=6, column=0, sticky="w", pady=4)
        self.code = ttk.Entry(right, width=12)
        self.code.grid(row=6, column=1, sticky="w", pady=4)
        ttk.Label(right, text="Batas waktu (menit)").grid(row=7, column=0, sticky="w", pady=4)
        limit = ttk.Frame(right)
        limit.grid(row=7, column=1, sticky="w", pady=4)
        self.limit = ttk.Entry(limit, width=8)
        self.limit.pack(side="left")
        self.limit_hint = ttk.Label(limit, font=SMALL_FONT, foreground=COLORS["DEBUG"])
        self.limit_hint.pack(side="left", padx=8)
        ttk.Button(right, text="Simpan", command=self.safe(self.save)).grid(row=8, column=0, sticky="w", pady=(12, 0))
        self.saved = ttk.Label(right, text="")
        self.saved.grid(row=8, column=1, sticky="w", pady=(12, 0))
        ttk.Label(right, text="Kode selain TES minta ketik REAL karena setup bisa jadi order beneran di Automanage. "
                              "\"Ikut global\" memakai setelan bawaan di config.yaml.",
                  font=SMALL_FONT, foreground=COLORS["DEBUG"], wraplength=540, justify="left").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.loaded = None

    def selected(self):
        index = self.names.curselection()
        names = settings.list_skills()
        return names[index[0]] if index and index[0] < len(names) else None

    def render_list(self, items, select=None):
        labels = [f"{'●' if skill and skill.enabled else '○'} {name}" for name, skill in items]
        if list(self.names.get(0, "end")) == labels and select is None:
            return
        keep = select or self.selected()
        self.names.delete(0, "end")
        for label in labels:
            self.names.insert("end", label)
        names = [name for name, _ in items]
        if names:
            index = names.index(keep) if keep in names else 0
            self.names.selection_set(index)
            if names[index] != self.loaded or select:
                self.load()

    def load(self):
        name = self.selected()
        if not name:
            return
        base = settings.read_config()
        skill = skills.read_skill(name, base)
        raw = yaml.safe_load(skills.skill_file(name).read_text(encoding="utf-8-sig")) or {}
        self.loaded = name
        self.on.set(skill.enabled)
        self.mode.set(skill.mode)
        custom_times = () if skill.use_global_times else skill.times
        self.hours_hint.configure(text=f"Simpan kosong = ikut global ({', '.join(base.skill_times)} WIB).")
        for widget, value in ((self.hours, ", ".join(custom_times)),
                              (self.begin, skill.start), (self.end, skill.end),
                              (self.code, skill.config.setup_code)):
            widget.delete(0, "end")
            widget.insert(0, value)
        self.every.set(str(skill.every))
        for day, var in self.days.items():
            var.set(day in skill.days)
        global_model = f"{GLOBAL} ({base.model})"
        self.model["values"] = [global_model, *base.model_choices]
        self.model.set(raw.get("model") or global_model)
        global_effort = f"{GLOBAL} ({base.effort or 'bawaan'})"
        self.effort["values"] = [global_effort, NATIVE_EFFORT, *settings.EFFORTS]
        effort = raw.get("effort")
        self.effort.set(global_effort if effort is None else (effort or NATIVE_EFFORT))
        minutes = raw.get("batas_waktu_menit")
        self.limit.delete(0, "end")
        if minutes is not None:
            self.limit.insert(0, f"{float(minutes):g}")
        self.limit_hint.configure(text=f"kosong = ikut global ({base.timeout / 60:g} menit)")
        self.saved.configure(text="")
        self.show_mode()

    def show_mode(self):
        for widget in (self.hours_label, self.hours_box, self.every_label, self.every_box):
            widget.grid_remove()
        if self.mode.get() == "jam":
            self.hours_label.grid(row=2, column=0, sticky="w", pady=4)
            self.hours_box.grid(row=2, column=1, sticky="ew", pady=4)
        else:
            self.every_label.grid(row=2, column=0, sticky="w", pady=4)
            self.every_box.grid(row=2, column=1, sticky="w", pady=4)

    def save(self):
        name = self.selected()
        if not name:
            return
        changes = {("", "nyala"): self.on.get(), ("jadwal", "mode"): self.mode.get()}
        if self.mode.get() == "jam":
            changes["jadwal", "jam"] = skills.parse_times(self.hours.get()) or None
        else:
            every = skills.parse_interval(self.every.get())
            begin, end = self.begin.get().strip(), self.end.get().strip()
            if not skills.HOUR.fullmatch(begin) or not skills.HOUR.fullmatch(end):
                raise GeneratorError("Jam mulai dan sampai harus berbentuk HH:MM.")
            changes.update({("jadwal", "tiap_jam"): every / 60, ("jadwal", "mulai"): begin, ("jadwal", "sampai"): end})
        changes["jadwal", "hari"] = [day for day, var in self.days.items() if var.get()]
        model = self.model.get().strip()
        if model.startswith(GLOBAL):
            changes["", "model"] = None
        elif settings.MODEL_NAME.fullmatch(model):
            changes["", "model"] = model
        else:
            raise GeneratorError("Nama model tidak valid.")
        effort = self.effort.get()
        changes["", "effort"] = None if effort.startswith(GLOBAL) else ("" if effort == NATIVE_EFFORT else effort)
        limit = self.limit.get().strip().replace(",", ".")
        if limit:
            try:
                minutes = float(limit)
            except ValueError:
                minutes = 0
            if not 0 < minutes <= 240:
                raise GeneratorError("Batas waktu harus antara 1 dan 240 menit, atau kosong.")
            changes["", "batas_waktu_menit"] = minutes
        else:
            changes["", "batas_waktu_menit"] = None
        code = self.code.get().strip() or "TES"
        base = settings.read_config()
        if code != skills.read_skill(name, base).config.setup_code:
            confirm = lambda prompt: simpledialog.askstring("Konfirmasi kode", prompt, parent=self.app.root) or ""
            if not skills.change_code(name, code, confirm):
                self.saved.configure(text="Kode tidak diganti (konfirmasi REAL dibatalkan).",
                                     foreground=COLORS["WARNING"])
                return
        skills.change_skill(name, changes)
        updated = skills.read_skill(name, settings.read_config())
        self.saved.configure(text=f"Tersimpan: {updated.describe()}", foreground=COLORS["AKTIF"])
        self.app.refresh_skills(select=name)

    def copy_global_times(self):
        hours = ", ".join(settings.read_config().skill_times)
        self.hours.delete(0, "end")
        self.hours.insert(0, hours)

    def edit_global(self):
        base = settings.read_config()
        window = tk.Toplevel(self.app.root)
        window.title("Setelan global skill")
        window.transient(self.app.root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=14)
        body.pack(fill="both", expand=True)
        model = ttk.Combobox(body, values=base.model_choices, width=30)
        effort = ttk.Combobox(body, values=(NATIVE_EFFORT, *settings.EFFORTS), state="readonly")
        timeout = ttk.Entry(body)
        hours = ttk.Entry(body)
        fields = (
            ("Model", model, base.model),
            ("Effort", effort, base.effort or NATIVE_EFFORT),
            ("Batas waktu (menit)", timeout, str(base.timeout / 60).removesuffix(".0")),
            ("Jam tertentu (WIB)", hours, ", ".join(base.skill_times)),
        )
        for row, (label, widget, value) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
            widget.grid(row=row, column=1, sticky="ew")
            if isinstance(widget, ttk.Combobox):
                widget.set(value)
            else:
                widget.insert(0, value)
        ttk.Label(body, text="Pisahkan jam dengan koma. Berlaku untuk skill yang ikut global.",
                  font=SMALL_FONT).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        def save():
            selected_effort = effort.get()
            try:
                skills.save_global(model.get(), "" if selected_effort == NATIVE_EFFORT else selected_effort,
                                   timeout.get(), hours.get())
            except (GeneratorError, OSError) as exc:
                messagebox.showerror("Setelan global skill", str(exc), parent=window)
                return
            window.destroy()
            self.app.refresh_skills(select=self.selected())
            self.app.toast("Setelan global skill tersimpan.")

        ttk.Button(body, text="Batal", command=window.destroy).grid(row=5, column=0, sticky="w", pady=(12, 0))
        ttk.Button(body, text="Simpan", command=self.safe(save)).grid(row=5, column=1, sticky="e", pady=(12, 0))
        window.grab_set()

    def add(self):
        name = simpledialog.askstring("Tambah skill", "Nama skill (huruf kecil, angka, tanda -):",
                                      parent=self.app.root)
        if name:
            folder = skills.add_skill(name.strip())
            open_file(folder / "SKILL.md", editor=True)
            self.app.refresh_skills(select=name.strip())
            self.app.toast(f"Skill {name.strip()} dibuat dalam keadaan MATI. Lengkapi SKILL.md-nya.")

    def edit(self):
        name = self.selected()
        if name:
            open_file(settings.skills_dir() / name / "SKILL.md", editor=True)

    def remove(self):
        name = self.selected()
        if not name:
            return
        answer = simpledialog.askstring("Hapus skill", f"Pindahkan {name} ke arsip skill\\_terhapus? Ketik HAPUS:",
                                        parent=self.app.root)
        if answer == "HAPUS":
            skills.archive_skill(name)
            self.loaded = None
            self.app.refresh_skills()


# ---------- tab 3: Tool ----------
class ToolTab(Page):
    def __init__(self, app, tabs):
        super().__init__(app, tabs, "Tool")
        frame = self.frame
        heading(frame, "Tool yang diizinkan")
        note(frame, "Hanya tool yang dicentang yang dimuat saat analisa. Makin sedikit makin hemat jatah, dan Claude yang "
                    "jalan sendiri tidak bisa menjalankan perintah atau menulis file. Berlaku untuk semua skill. "
                    "Daftar lengkap dan kegunaannya ada di skill\\panduan-skill\\PANDUAN.md bagian 10.")
        self.count = ttk.Label(frame, font=SMALL_FONT)
        self.count.pack(anchor="w", pady=(0, 6))
        columns = ttk.Frame(frame)
        columns.pack(fill="both", expand=True)
        columns.columnconfigure(0, weight=1)
        columns.columnconfigure(1, weight=2)
        columns.rowconfigure(1, weight=1)
        ttk.Label(columns, text="Bawaan Claude Code", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(columns, text="TradingView", font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w",
                                                                                  padx=(16, 0))
        self.vars = {}
        self.holders = []
        for col in (0, 1):
            holder = ttk.Frame(columns)
            holder.grid(row=1, column=col, sticky="nsew", padx=(16 if col else 0, 0), pady=(4, 0))
            self.holders.append(scroll_area(holder, 330))
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Simpan", command=self.safe(self.save)).pack(side="left")
        ttk.Button(bottom, text="Kembalikan ke bawaan (15 tool)", command=self.reset).pack(side="left", padx=6)
        self.saved = ttk.Label(bottom, text="", foreground=COLORS["AKTIF"])
        self.saved.pack(side="left", padx=10)
        self.denied = note(frame, "")
        self.build()

    def build(self):
        base = settings.read_config()
        allowed_builtin = set(settings.BUILTIN_TOOLS if base.builtin_tools is None else base.builtin_tools)
        allowed_tv = set(settings.TV_TOOLS if base.tv_tools is None else base.tv_tools)
        for holder in self.holders:
            for child in holder.winfo_children():
                child.destroy()
        self.vars = {}
        groups = ((settings.BUILTIN_TOOLS, allowed_builtin, 1), (settings.TV_TOOLS, allowed_tv, 2))
        for col, (names, allowed, per_row) in enumerate(groups):
            ordered = sorted(names, key=lambda item: (item not in allowed, item))
            for index, name in enumerate(ordered):
                var = self.vars[(col, name)] = tk.BooleanVar(self.frame, value=name in allowed)
                locked = col == 1 and settings.TV_PREFIX + name == settings.REQUIRED_DENY
                check = ttk.Checkbutton(self.holders[col], text=name + ("  (terkunci)" if locked else ""),
                                        variable=var, command=self.render)
                if locked:
                    var.set(False)
                    check.state(["disabled"])
                check.grid(row=index // per_row, column=index % per_row, sticky="w", padx=(0, 12), pady=1)
        self.denied.configure(text="Selalu ditolak (juga kalau dicentang): " + ", ".join(base.denied_tools))
        self.render()

    def render(self):
        builtin = sum(var.get() for (col, _), var in self.vars.items() if col == 0)
        tv = sum(var.get() for (col, _), var in self.vars.items() if col == 1)
        self.count.configure(text=f"Diizinkan: {builtin} bawaan + {tv} TradingView = {builtin + tv} tool",
                             foreground=COLORS["AKTIF"] if builtin + tv <= 20 else COLORS["WARNING"])
        self.saved.configure(text="")

    def reset(self):
        defaults_builtin = {"WebSearch", "WebFetch"}
        defaults_tv = {"tv_health_check", "chart_get_state", "chart_set_symbol", "chart_set_timeframe",
                       "data_get_ohlcv", "quote_get", "data_get_pine_boxes", "data_get_pine_labels",
                       "data_get_pine_lines", "data_get_pine_tables", "tab_list", "tab_switch", "capture_screenshot"}
        for (col, name), var in self.vars.items():
            var.set(name in (defaults_builtin if col == 0 else defaults_tv))
        self.render()

    def save(self):
        builtin = [name for name in settings.BUILTIN_TOOLS if self.vars[(0, name)].get()]
        tv = [name for name in settings.TV_TOOLS if self.vars[(1, name)].get()]
        settings.update_yaml(settings.SYSTEM / "config.yaml", {("claude", "tools_bawaan"): builtin,
                                                               ("claude", "tools_tradingview"): tv})
        self.saved.configure(text=f"Tersimpan: {len(builtin) + len(tv)} tool.")


# ---------- tab 4: Telegram ----------
class TelegramTab(Page):
    def __init__(self, app, tabs):
        super().__init__(app, tabs, "Telegram")
        frame = self.frame
        frame.columnconfigure(1, weight=1)
        self.row = 0

        def section(text):
            ttk.Label(frame, text=text, font=TITLE_FONT).grid(row=self.row, column=0, columnspan=3, sticky="w",
                                                             pady=(10 if self.row else 0, 4))
            self.row += 1

        def field(label, widget, *buttons):
            ttk.Label(frame, text=label).grid(row=self.row, column=0, sticky="w", pady=4, padx=(0, 12))
            widget.grid(row=self.row, column=1, sticky="ew", pady=4)
            box = ttk.Frame(frame)
            box.grid(row=self.row, column=2, sticky="w", padx=(8, 0))
            for text, action in buttons:
                ttk.Button(box, text=text, command=self.safe(action)).pack(side="left", padx=(0, 4))
            self.row += 1

        section("Pengiriman")
        self.sender = ttk.Combobox(frame, values=("bot", "akun"), state="readonly", width=10)
        field("Cara kirim", self.sender, ("Simpan", self.save_sender))
        self.token = ttk.Entry(frame, show="*")
        field("Token bot", self.token, ("Simpan & cek", self.save_token))
        api = ttk.Frame(frame)
        self.api_id = ttk.Entry(api, width=14)
        self.api_hash = ttk.Entry(api, show="*")
        self.api_id.pack(side="left")
        ttk.Label(api, text="hash").pack(side="left", padx=6)
        self.api_hash.pack(side="left", fill="x", expand=True)
        field("API akun (mode akun)", api, ("Simpan", self.save_api))
        self.login_state = ttk.Label(frame, foreground=COLORS["DEBUG"])
        field("Login akun Telegram", self.login_state, ("Login…", self.login))
        self.secret_state = ttk.Label(frame, font=SMALL_FONT, foreground=COLORS["DEBUG"], wraplength=640,
                                      justify="left")
        self.secret_state.grid(row=self.row, column=1, columnspan=2, sticky="w")
        self.row += 1

        section("Tujuan")
        self.inbox = ttk.Label(frame)
        field("Channel kotak masuk", self.inbox, ("Ganti…", self.change_inbox))
        self.report = ttk.Label(frame)
        field("Chat pribadi laporan", self.report, ("Deteksi ulang", self.detect_report), ("Ketik ID…", self.type_report))
        self.full = tk.BooleanVar(frame)
        field("Analisa lengkap ke PM", ttk.Checkbutton(frame, text="Kirim analisa lengkap (plus blok SETUP)",
                                                      variable=self.full, command=self.safe(self.save_full)))

        section("Pesan rutin (tanda masih hidup)")
        routine = ttk.Frame(frame)
        self.routine_on = tk.BooleanVar(frame)
        ttk.Checkbutton(routine, text="Nyala", variable=self.routine_on).pack(side="left")
        ttk.Label(routine, text="jam").pack(side="left", padx=(12, 4))
        self.routine_hours = ttk.Entry(routine, width=24)
        self.routine_hours.pack(side="left")
        field("Pesan rutin", routine, ("Simpan", self.save_routine), ("Kirim contoh sekarang", self.sample))
        ttk.Label(frame, text="Dikirim selama generator jalan terus. Pesan tidak datang di jam biasanya = generator mati. "
                              "Jam boleh lebih dari satu, pisahkan koma.",
                  font=SMALL_FONT, foreground=COLORS["DEBUG"]).grid(row=self.row, column=0, columnspan=3, sticky="w",
                                                                    pady=(6, 0))
        self.load()

    def load(self):
        base = settings.read_config()
        keys = secrets()
        self.sender.set(base.sender)
        self.token.delete(0, "end")
        self.api_id.delete(0, "end")
        self.api_hash.delete(0, "end")
        self.secret_state.configure(text=(
            f"Token bot: {'terisi' if keys.get('GEN_TG_BOT_TOKEN') else 'kosong'} · "
            f"API ID: {'terisi' if keys.get('GEN_TG_API_ID') else 'kosong'} · "
            f"API hash: {'terisi' if keys.get('GEN_TG_API_HASH') else 'kosong'}. "
            "Kolom dibiarkan kosong = tetap. Isi rahasia tidak pernah ditampilkan."))
        session = actions.session_file().is_file()
        self.login_state.configure(text="Mode bot tidak perlu login akun." if base.sender == "bot" else
                                   ("Sesi akun tersimpan." if session else "Belum login."))
        self.show_inbox(base)
        self.report.configure(text=base.report or ("Saved Messages" if base.sender == "akun" else "belum terdeteksi"))
        self.full.set(base.send_analysis)
        routine = heartbeat.read()
        self.routine_on.set(routine.enabled)
        self.routine_hours.delete(0, "end")
        self.routine_hours.insert(0, ", ".join(routine.times))

    def show_inbox(self, base=None):
        base = base or settings.read_config()
        name = self.app.channel_name(base)
        self.inbox.configure(text=f"{name} ({base.inbox})" if base.inbox and name != base.inbox
                             else (base.inbox or "belum diatur"))

    def config_change(self, key, value):
        settings.update_yaml(settings.SYSTEM / "config.yaml", {("telegram", key): value})

    def save_sender(self):
        self.config_change("pengirim", self.sender.get())
        self.load()
        self.app.toast("Cara kirim disimpan.")

    def save_token(self):
        token = self.token.get().strip()
        if not token:
            raise GeneratorError("Tempel token dari BotFather dulu.")
        self.app.background(lambda: actions.save_token(token), lambda _: (
            self.app.forget_channel(), self.load(), self.app.toast("Token bot valid dan tersimpan.")))

    def save_api(self):
        updates = actions.api_changes(self.api_id.get(), self.api_hash.get())
        if not updates:
            raise GeneratorError("Tidak ada perubahan API.")
        reset = False
        if actions.session_file().is_file():
            reset = messagebox.askyesno("API berubah", "Hapus sesi akun lama dan login ulang nanti?",
                                        parent=self.app.root)
            if not reset:
                return
        actions.save_api(updates, reset)
        self.load()
        self.app.toast("API akun tersimpan.")

    def login(self):
        if settings.read_config().sender != "akun":
            raise GeneratorError("Mode bot tidak perlu login akun. Pilih cara kirim akun dulu.")
        actions.launch("login", window=True)
        self.app.toast("Login dibuka di jendela sendiri. Ketik kode dan password di sana.")

    def change_inbox(self):
        InboxDialog(self.app, self)

    def detect_report(self):
        base = settings.read_config()
        if base.sender == "akun":
            self.config_change("laporan_ke", "")
            self.load()
            self.app.toast("Laporan dikirim ke Saved Messages akunmu.")
            return
        if not messagebox.askokcancel("Deteksi chat pribadi", "Buka chat dengan bot generator, tekan Start atau "
                                      "kirim pesan apa saja, lalu tekan OK.", parent=self.app.root):
            return

        def work():
            chat_id = actions.bot().find_private_chat()
            if chat_id is None:
                raise GeneratorError("Chat belum terdeteksi. Tekan Start atau kirim pesan ke bot lalu coba lagi.")
            actions.save_report(chat_id, "bot")
            return chat_id

        self.app.background(work, lambda chat_id: (self.load(), self.app.toast(f"Chat pribadi {chat_id} tersimpan.")))

    def type_report(self):
        chat_id = simpledialog.askstring("Chat pribadi", "ID chat pribadi:", parent=self.app.root)
        if chat_id:
            sender = settings.read_config().sender
            self.app.background(lambda: actions.save_report(chat_id, sender),
                                lambda _: (self.load(), self.app.toast("Chat pribadi tersimpan.")))

    def save_full(self):
        self.config_change("kirim_analisa_ke_pm", bool(self.full.get()))
        self.app.toast("Setelan analisa lengkap ke PM disimpan.")

    def save_routine(self):
        times = [part.strip() for part in self.routine_hours.get().split(",") if part.strip()]
        if not times or any(not skills.HOUR.fullmatch(part) for part in times):
            raise GeneratorError("Jam harus berbentuk HH:MM, dari 00:00 sampai 23:59.")
        heartbeat.save(enabled=bool(self.routine_on.get()), times=sorted(dict.fromkeys(times)))
        self.load()
        self.app.toast(f"Pesan rutin: {heartbeat.read().describe()}")

    def sample(self):
        process = actions.launch("rutin", capture=True)

        def work():
            output = process.communicate(timeout=60)[0].decode("utf-8", "replace").strip()
            if process.returncode:
                last = output.splitlines()[-1] if output else "cek log generator"
                raise GeneratorError(f"Pesan rutin gagal dikirim. {last}")

        self.app.toast("Mengirim pesan rutin…")
        self.app.background(work, lambda _: self.app.toast("Pesan rutin terkirim ke chat pribadi."))


class InboxDialog:
    def __init__(self, app, page):
        self.app, self.page = app, page
        self.window = window = tk.Toplevel(app.root)
        window.title("Ganti channel kotak masuk")
        window.transient(app.root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Cari channel dari:", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        buttons = ttk.Frame(body)
        buttons.pack(anchor="w", pady=(4, 8))
        ttk.Button(buttons, text="Channel tempat bot jadi admin", command=page.safe(lambda: self.find(False))).pack(
            side="left")
        ttk.Button(buttons, text="Postingan yang diteruskan ke bot", command=page.safe(lambda: self.find(True))).pack(
            side="left", padx=6)
        note(body, "Telegram menyimpan update sekitar 24 jam. Kalau channel tidak muncul, jadikan ulang bot admin, "
                   "atau teruskan satu postingan channel ke chat bot generator, lalu cari lagi.")
        self.listbox = tk.Listbox(body, height=6, width=60, exportselection=False)
        self.listbox.pack(fill="x")
        manual = ttk.Frame(body)
        manual.pack(fill="x", pady=(8, 0))
        ttk.Label(manual, text="Atau ketik ID (-100...):").pack(side="left")
        self.manual = ttk.Entry(manual, width=24)
        self.manual.pack(side="left", padx=6)
        actions_row = ttk.Frame(body)
        actions_row.pack(anchor="e", pady=(12, 0))
        ttk.Button(actions_row, text="Batal", command=window.destroy).pack(side="left")
        ttk.Button(actions_row, text="Simpan", command=page.safe(self.save)).pack(side="left", padx=(6, 0))
        self.chats = []
        window.grab_set()

    def find(self, forwarded):
        from .menu import candidate_channels

        def work():
            return candidate_channels(actions.bot().updates(), forwarded=forwarded)

        def done(chats):
            self.chats = chats
            self.listbox.delete(0, "end")
            for chat in chats:
                self.listbox.insert("end", f"{chat.get('title', 'Channel')} ({chat['id']})")
            if not chats:
                self.listbox.insert("end", "Channel belum terdeteksi.")

        self.app.background(work, done)

    def save(self):
        chosen = self.listbox.curselection()
        chat_id = self.manual.get().strip() or (
            str(self.chats[chosen[0]]["id"]) if chosen and chosen[0] < len(self.chats) else "")
        if not chat_id:
            raise GeneratorError("Pilih channel dari daftar atau ketik ID-nya.")

        def done(result):
            title, can_post = result
            if not can_post and not messagebox.askyesno(
                    "Izin posting", f"{title}: izin posting bot belum terverifikasi.\n"
                                    "Jadikan bot admin dengan izin posting. Tetap simpan?", parent=self.window):
                return
            actions.save_inbox(chat_id)
            self.app.forget_channel()
            self.page.load()
            self.window.destroy()
            self.app.toast(f"Kotak masuk: {title}.")

        self.app.background(lambda: actions.check_inbox(chat_id), done)


# ---------- tab 5: TradingView ----------
class TradingViewTab(Page):
    def __init__(self, app, tabs):
        super().__init__(app, tabs, "TradingView")
        frame = self.frame
        heading(frame, "TradingView")
        status = ttk.Frame(frame)
        status.pack(fill="x", pady=3)
        ttk.Label(status, text="Chart (port 9222):").pack(side="left")
        self.state = ttk.Label(status)
        self.state.pack(side="left", padx=8)
        ttk.Button(frame, text="Buka TradingView sekarang", command=self.safe(self.open)).pack(
            anchor="w", pady=(6, 12))
        body = ttk.Frame(frame)
        body.pack(fill="x")
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Skrip peluncur").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 12))
        self.launcher = ttk.Entry(body)
        self.launcher.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(body, text="Pilih…", command=self.pick).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(body, text="Lama tunggu chart (detik)").grid(row=1, column=0, sticky="w", pady=4, padx=(0, 12))
        self.wait = ttk.Spinbox(body, from_=10, to=600, width=6)
        self.wait.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Button(frame, text="Simpan", command=self.safe(self.save)).pack(anchor="w", pady=(12, 0))
        self.load()

    def load(self):
        base = settings.read_config()
        self.launcher.delete(0, "end")
        self.launcher.insert(0, str(base.tv_launcher))
        self.wait.set(f"{base.tv_wait:g}")

    def refresh(self):
        ready = self.app.tv_ready
        self.state.configure(text="● siap" if ready else ("○ belum terbuka" if ready is False else "mengecek…"),
                             foreground=COLORS["AKTIF"] if ready else COLORS["DEBUG"])

    def pick(self):
        path = filedialog.askopenfilename(parent=self.app.root, title="Skrip peluncur TradingView",
                                          filetypes=[("Skrip", "*.bat *.cmd")])
        if path:
            self.launcher.delete(0, "end")
            self.launcher.insert(0, path)

    def save(self):
        actions.save_launcher(self.launcher.get())
        actions.save_tv_wait(self.wait.get())
        self.load()
        self.app.toast("Setelan TradingView disimpan.")

    def open(self):
        self.app.toast("Membuka TradingView…")
        self.app.background(actions.open_tradingview, lambda _: self.app.toast("Chart TradingView siap."))


# ---------- tab 6: Jurnal ----------
class JournalTab(Page):
    def __init__(self, app, tabs):
        super().__init__(app, tabs, "Jurnal")
        frame = self.frame
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Label(top, text="Ringkasan", font=TITLE_FONT).pack(side="left")
        ttk.Button(top, text="Muat ulang", command=self.safe(self.reload)).pack(side="right")
        self.cards = ttk.Frame(frame)
        self.cards.pack(fill="x", pady=(6, 10))
        self.warning = ttk.Label(frame, font=SMALL_FONT, foreground=COLORS["WARNING"])
        self.warning.pack(anchor="w")
        heading(frame, "Analisa terakhir")
        self.tree = ttk.Treeview(frame, columns=("skill", "status", "setup", "conv", "model", "bobot", "hasil"),
                                 show="tree headings", height=8, selectmode="extended")
        self.tree.heading("#0", text="Waktu", anchor="w")
        self.tree.column("#0", width=95)
        for key, text, width in (("skill", "Skill", 95), ("status", "Status", 110), ("setup", "Setup / keterangan", 230),
                                 ("conv", "Conv.", 50), ("model", "Model", 120), ("bobot", "Bobot API", 70),
                                 ("hasil", "Hasil", 80)):
            self.tree.heading(key, text=text, anchor="w")
            self.tree.column(key, width=width, stretch=key == "setup")
        for status, color in ((journal.BOT, COLORS["AKTIF"]), (journal.LOW, COLORS["WARNING"]),
                              (journal.NONE, COLORS["DEBUG"]), (journal.FAIL, COLORS["ERROR"])):
            self.tree.tag_configure(status, foreground=color)
        self.tree.pack(fill="both", expand=True)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Buka file Excel jurnal", command=self.safe(self.excel)).pack(side="left")
        ttk.Button(buttons, text="Buka folder jurnal", command=self.safe(self.folder)).pack(side="left", padx=6)
        ttk.Button(buttons, text="Hapus dari jurnal", command=self.safe(self.delete)).pack(side="right")
        ttk.Label(buttons, text="Hasil dan Catatan diisi di Excel; R keluar otomatis. "
                  "Hapus: pilih baris (Ctrl/Shift+klik untuk banyak).", font=SMALL_FONT,
                  foreground=COLORS["DEBUG"]).pack(side="left", padx=10)
        self.labels = {}   # ID analisa -> teks singkat untuk konfirmasi hapus

    def card(self, value, label):
        box = ttk.Frame(self.cards, padding=(10, 6), relief="groove")
        box.pack(side="left", padx=(0, 6))
        ttk.Label(box, text=value, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(box, text=label, font=SMALL_FONT, foreground=COLORS["DEBUG"]).pack(anchor="w")

    def reload(self):
        self.app.background(lambda: (journal.summary_numbers(), journal.read_entries()), self.render)

    def render(self, data):
        numbers, entries = data
        for child in self.cards.winfo_children():
            child.destroy()
        self.tree.delete(*self.tree.get_children())
        if numbers is None:
            self.card("0", "Analisa")
            self.warning.configure(text="Jurnal masih kosong. Analisa skill berikutnya akan tercatat otomatis.")
            return
        winrate = "—" if numbers["winrate"] is None else f"{numbers['winrate']:.0%}"
        for value, label in ((numbers["analisa"], "Analisa"), (numbers[journal.BOT], "Setup ke bot"),
                             (numbers[journal.NONE], "Tidak ada setup"), (numbers[journal.FAIL], "Gagal"),
                             (f"{numbers['tp']} / {numbers['sl']} / {numbers['be']}", "TP / SL / BE"),
                             (winrate, "Win rate"), (f"{numbers['total_r']:+.2f}", "Total R"),
                             (numbers["kosong"], "Belum diisi Hasil")):
            self.card(str(value), label)
        self.warning.configure(text=(f"⚠️ {numbers['tertinggal']} analisa belum masuk Excel (file sempat dibuka). "
                                     "Tutup Excel lalu tekan Buka file Excel jurnal.") if numbers["tertinggal"] else "")
        self.labels = {}
        for entry in reversed(entries[-50:]):
            self.labels[entry["id"]] = journal.describe(entry)
            for row in journal.rows_for(entry):
                fields = row
                if row["status"] in (journal.BOT, journal.LOW):
                    setup = f"{fields.get('arah') or '?'} {fields.get('order') or ''} {fields.get('entry') or 'market'}" \
                            f" · SL {fields.get('sl') or '?'}"
                    hasil, gained = numbers["hasil"].get(row["id"], (None, None))
                    result = hasil or "belum diisi"
                    if isinstance(gained, (int, float)):
                        result += f" ({gained:+.2f}R)"
                else:
                    setup, result = row.get("keterangan") or "", "—"
                conviction = f"{row['conviction']:.0%}" if isinstance(row.get("conviction"), (int, float)) else ""
                cost = f"${row['biaya']:.2f}" if isinstance(row.get("biaya"), (int, float)) else ""
                when = row["tanggal"].strftime("%d/%m") + " " + row["jam"].strftime("%H:%M")
                self.tree.insert("", "end", iid=row["id"], text=when, tags=(row["status"],), values=(
                    row.get("skill", ""), row["status"], setup, conviction, row.get("model") or "", cost, result))

    def delete(self):
        # Satu analisa bisa punya beberapa baris (satu per blok): yang dihapus selalu analisanya utuh.
        ids = list(dict.fromkeys(item.split("/")[0] for item in self.tree.selection()))
        if not ids:
            raise GeneratorError("Pilih analisa di tabel dulu (Ctrl/Shift+klik untuk memilih banyak).")
        listing = "\n".join(f"• {self.labels.get(entry_id, entry_id)}"[:160] for entry_id in ids[:12])
        if len(ids) > 12:
            listing += f"\n… dan {len(ids) - 12} lagi"
        if not messagebox.askyesno("Hapus dari jurnal", (
                f"Hapus {len(ids)} analisa dari jurnal (catatan asli dan Excel)?\n\n{listing}\n\n"
                "Semua baris setup milik analisa itu ikut terhapus. Cadangannya disimpan di "
                "data\\jurnal-terhapus.jsonl dan file hasilnya dipindah ke data\\hasil\\terhapus."),
                icon="warning", parent=self.app.root):
            return

        def done(removed):
            self.app.toast(f"{len(removed)} analisa dihapus dari jurnal.")
            self.reload()

        self.app.background(lambda: actions.delete_analyses(ids), done)

    def excel(self):
        def work():
            try:
                journal.sync()
                note_text = ""
            except journal.JournalLocked:
                note_text = "File Excel sedang dibuka; analisa baru masuk setelah ditutup."
            return note_text

        def done(note_text):
            if not journal.XLSX.exists():
                raise GeneratorError("Jurnal masih kosong. Analisa skill berikutnya akan tercatat otomatis.")
            open_file(journal.XLSX)
            self.app.toast(note_text or "Membuka jurnal Excel. Isi kolom kuning (Hasil, Catatan kamu), lalu simpan.")

        self.app.background(work, done)

    def folder(self):
        journal.FOLDER.mkdir(exist_ok=True)
        open_file(journal.FOLDER)


# ---------- tab 7: Utilitas ----------
class UtilityTab(Page):
    def __init__(self, app, tabs):
        super().__init__(app, tabs, "Utilitas")
        frame = self.frame
        heading(frame, "Utilitas")
        ttk.Style(app.root).configure("Menu.TButton", anchor="w", padding=(8, 4))
        for label, action in (("Cek kesiapan", self.ready), ("Kirim pesan uji ke kotak masuk (Kode TES)", self.test),
                              ("Lihat log terakhir", self.logs), ("Riwayat analisa", self.history),
                              ("Tes logika program", self.tests),
                              ("Buka config.yaml (mode lanjut)",
                               lambda: open_file(settings.SYSTEM / "config.yaml", editor=True))):
            ttk.Button(frame, text=label, style="Menu.TButton", command=self.safe(action)).pack(fill="x", pady=(0, 4))
        head = ttk.Frame(frame)
        head.pack(fill="x", pady=(10, 4))
        self.title = ttk.Label(head, text="Hasil", font=TITLE_FONT)
        self.title.pack(side="left")
        self.extra = ttk.Frame(head)
        self.extra.pack(side="right")
        self.output = ScrolledText(frame, state="disabled", wrap="word", height=10, font=LOG_FONT, padx=8, pady=6)
        self.output.pack(fill="both", expand=True)
        self.process = None
        self.lines = queue.Queue()

    def show(self, title, text=""):
        self.title.configure(text=title)
        for child in self.extra.winfo_children():
            child.destroy()
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("end", text)
        self.output.configure(state="disabled")

    def write(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def stream(self, title, args):
        if self.process is not None and self.process.poll() is None:
            raise GeneratorError("Utilitas sebelumnya masih berjalan.")
        self.show(title + " (berjalan…)")
        options = dict(cwd=settings.SYSTEM, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, creationflags=actions.NO_WINDOW,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1"))
        self.process = process = subprocess.Popen(args, **options)

        def pump():
            for raw in process.stdout:
                self.lines.put(raw.decode("utf-8", "replace"))
            process.wait()
            self.lines.put(None)

        threading.Thread(target=pump, daemon=True).start()

        def poll():
            batch, finished = [], False
            try:
                while True:
                    item = self.lines.get_nowait()
                    if item is None:
                        finished = True
                        break
                    batch.append(item)
            except queue.Empty:
                pass
            if batch:
                self.write(clean("".join(batch)))
            if not finished:
                self.app.root.after(100, poll)
                return
            result = {0: " (selesai)", runtime.EXIT_SKIPPED: " (dilewati, generator sedang menganalisa)"}
            self.title.configure(text=title + result.get(process.returncode, " (gagal)"))

        self.app.root.after(100, poll)

    def ready(self):
        self.stream("Cek kesiapan", actions.command("ready"))

    def test(self):
        if messagebox.askyesno("Kirim pesan uji", "Kirim blok SETUP Kode: TES ke channel kotak masuk?",
                               parent=self.app.root):
            self.stream("Kirim pesan uji", actions.command("test"))

    def tests(self):
        self.stream("Tes logika program", [actions.python_exe(), "-m", "pytest", "-q"])

    def logs(self):
        path = settings.LOGS / "generator.log"
        text = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]) if path.is_file() \
            else "Log belum ada."
        self.show("Log terakhir (30 baris)", clean(text))

    def history(self):
        paths = sorted((settings.DATA / "hasil").glob("*.txt"), reverse=True)
        self.show("Riwayat analisa", "\n".join(path.name for path in paths) or "Riwayat masih kosong.")
        if paths:
            ttk.Button(self.extra, text="Buka hasil terbaru",
                       command=self.safe(lambda: open_file(paths[0], editor=True))).pack(side="right")


# ---------- jendela utama ----------
class App:
    def __init__(self, root, listener=None):
        self.root = root
        self.listener = listener
        self.channel_names = {}
        self.tv_ready = None
        self.tv_check = None
        self.tv_checked = 0
        self.tick = 0
        self.jobs = queue.Queue()
        root.title("Dashboard CLI")
        root.geometry("900x740")
        root.minsize(720, 560)
        self.notice = tk.Label(root, text="", anchor="w", font=SMALL_FONT, padx=10, pady=3)
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True)
        self.home = HomeTab(self, self.tabs)
        self.skill = SkillTab(self, self.tabs)
        self.tool = ToolTab(self, self.tabs)
        self.telegram = TelegramTab(self, self.tabs)
        self.tradingview = TradingViewTab(self, self.tabs)
        self.journal = JournalTab(self, self.tabs)
        self.utility = UtilityTab(self, self.tabs)
        self.tabs.enable_traversal()
        self.tabs.bind("<<NotebookTabChanged>>", self.tab_changed)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_skills()
        self.poll()

    def close(self):
        """Generator ikut mati kalau dashboard ditutup (keputusan user). Peringatan dulu."""
        if not actions.generator_running():
            self.root.destroy()
            return
        if not messagebox.askokcancel(
                "Tutup dashboard?", "Generator akan mati kalau dashboard ditutup.\n"
                "Analisa yang sedang berjalan ikut berhenti, dan jadwal tidak jalan sampai generator "
                "dinyalakan lagi.\n\nTutup sekarang?", icon="warning", parent=self.root):
            return
        try:
            actions.stop_generator()
        except Exception as error:
            self.error(error)
            return
        self.root.destroy()

    # pesan
    def error(self, error):
        message = str(error) if isinstance(error, GeneratorError) else (
            "Operasi file atau program gagal. Cek akses dan coba lagi." if isinstance(error, OSError)
            else f"Terjadi gangguan ({type(error).__name__}).")
        messagebox.showerror("Gagal", clean(message), parent=self.root)

    def toast(self, text):
        self.notice.configure(text=text, background="#e8f2e8", foreground=COLORS["AKTIF"])
        self.notice.pack(fill="x", before=self.tabs)
        self.root.after(6000, lambda: self.notice.pack_forget() if self.notice.cget("text") == text else None)

    def background(self, work, done):
        """Kerjakan di belakang (jaringan, file besar), lalu jalankan done(hasil) di jendela."""
        def run():
            try:
                self.jobs.put((done, True, work()))
            except Exception as error:
                self.jobs.put((done, False, error))
        threading.Thread(target=run, daemon=True).start()

    # status bersama
    def channel_name(self, base):
        token = secrets().get("GEN_TG_BOT_TOKEN")
        if not settings.valid_inbox(base) or not token:
            return base.inbox or "belum diatur"
        if base.inbox not in self.channel_names:
            self.channel_names[base.inbox] = base.inbox

            def lookup():
                try:
                    chat = actions.BotSender(token).call("getChat", chat_id=int(base.inbox))
                    self.channel_names[base.inbox] = chat.get("title") or base.inbox
                    # Perbarui label channel di tab Telegram begitu judulnya diketahui.
                    self.jobs.put((lambda _: self.telegram.show_inbox(), True, None))
                except Exception:
                    pass
            threading.Thread(target=lookup, daemon=True).start()
        return self.channel_names[base.inbox]

    def forget_channel(self):
        self.channel_names = {}

    def status_line(self, base=None, items=None):
        """Baris status di tab Dashboard. base/items dari refresh_skills supaya setelan tidak dibaca dua kali."""
        if base is None or items is None:
            base, items = self.read_skills()
        active = [name for name, skill in items if skill is not None and skill.enabled]
        pm = settings.valid_report(base) if base.sender == "bot" else actions.session_file().is_file()
        tv = "siap" if self.tv_ready else ("belum" if self.tv_ready is False else "mengecek")
        try:
            routine = heartbeat.read().describe()
        except GeneratorError:
            routine = "setelan tidak valid"
        return (f"Skill nyala: {', '.join(active) or '—'}   ·   Kirim lewat: {base.sender}   ·   "
                f"Kotak masuk: {clean(self.channel_name(base))}   ·   PM: {'✅' if pm else '❌'}   ·   "
                f"TradingView: {tv}   ·   Pesan rutin: {routine}")

    @staticmethod
    def read_skills():
        base = settings.read_config()
        items = []
        for name in settings.list_skills():
            try:
                items.append((name, skills.read_skill(name, base)))
            except GeneratorError:
                items.append((name, None))
        return base, items

    def refresh_skills(self, select=None):
        """Baca setelan dan skill sekali, lalu perbarui tabel skill dan baris status."""
        base, items = self.read_skills()
        self.home.render_skills(base, items)
        self.skill.render_list(items, select)
        self.home.info.configure(text=self.status_line(base, items))

    def probe_tv(self):
        if self.tv_check is not None and self.tv_check.is_alive():
            return

        def probe():
            self.tv_ready = runtime.tradingview_ready()
        self.tv_check = threading.Thread(target=probe, daemon=True)
        self.tv_check.start()

    def tab_changed(self, _=None):
        index = self.tabs.index("current")
        page = (self.home, self.skill, self.tool, self.telegram, self.tradingview, self.journal, self.utility)[index]
        try:
            if page is self.journal:
                self.journal.reload()
            elif page is self.telegram:
                self.telegram.load()
            elif page is self.tradingview:
                self.tradingview.load()
        except Exception as error:
            self.error(error)

    def poll(self):
        try:
            while True:
                done, ok, value = self.jobs.get_nowait()
                try:
                    if ok:
                        done(value)
                    else:
                        self.error(value)
                except Exception as error:
                    self.error(error)
        except queue.Empty:
            pass
        if self.listener is not None:
            try:
                connection, _ = self.listener.accept()
            except (BlockingIOError, OSError):
                pass
            else:
                connection.close()
                self.root.deiconify()
                self.root.lift()
                self.root.attributes("-topmost", True)
                self.root.after(250, lambda: self.root.attributes("-topmost", False))
        try:
            self.tick += 1
            if self.tick % 5 == 1:
                self.probe_tv()
            if self.tick % STATUS_EVERY == 0:
                self.refresh_skills()
            self.home.refresh()
            self.tradingview.refresh()
        except Exception as error:   # setelan rusak dll: tampilkan sekali per menit, dashboard tetap jalan
            if self.tick % 60 == 1:
                self.error(error)
        self.root.after(POLL_MS, self.poll)


def claim():
    """Satu dashboard saja. Kalau sudah terbuka, jendela lama dimunculkan."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        listener.bind(("127.0.0.1", DASHBOARD_PORT))
    except OSError:
        listener.close()
        try:
            with socket.create_connection(("127.0.0.1", DASHBOARD_PORT), timeout=2):
                pass
        except OSError:
            pass
        return None
    listener.listen(1)
    listener.setblocking(False)
    return listener


def main():
    os.chdir(settings.SYSTEM)
    listener = claim()
    if listener is None:
        return
    with listener:
        root = tk.Tk()
        try:
            App(root, listener)
        except GeneratorError as error:
            root.withdraw()
            messagebox.showerror("Dashboard CLI", clean(str(error)))
            root.destroy()
            return
        root.mainloop()


if __name__ == "__main__":
    main()
