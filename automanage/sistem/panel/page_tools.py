"""Utility screens and streamed output for existing command-line tools."""
import asyncio
import codecs
import os
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
import sys
import threading
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from copier import envfile
from reader import sources, wizard

from .page_sources import detect_dialogs
from .page_widgets import Page


class ToolsPage(Page):
    def __init__(self, root, tabs):
        super().__init__(root, tabs, "Utilitas")
        self.process = None
        self.frame.bind("<Destroy>", self.close_process, add="+")
        self.home()

    def home(self):
        self.menu("Utilitas", [
            ("Coba baca gambar sinyal (tanpa Telegram)", self.image),
            ("Cek kesiapan (MT5, Bybit, Telegram, Claude)",
             lambda: self.utility("Cek kesiapan", "readiness")),
            ("Tes manual Bybit (coba order satu per satu, demo saja)",
             lambda: self.run("Tes manual Bybit", ("-m", "panel.bybit_tes"), interactive=True)),
            ("Riwayat setup", lambda: self.utility("Riwayat setup", "history")),
            ("Reset jurnal MT5 (hapus riwayat, ada cadangan)",
             lambda: self.utility("Reset jurnal MT5", "reset-mt5", interactive=True)),
            ("Reset jurnal Bybit (mulai dari #1, ada cadangan)",
             lambda: self.utility("Reset jurnal Bybit", "reset-bybit", interactive=True)),
            ("Isi/ganti token bot Telegram (MT5 / Bybit / Pengawas)", self.token),
            ("Ganti channel kotak masuk sinyal", self.inbox),
            ("Tes logika program", lambda: self.run("Tes logika program", ("-m", "pytest", "-q"))),
        ])

    def utility(self, title, name, interactive=False):
        self.run(title, ("-m", "panel.utility_worker", name), interactive=interactive)

    def close_process(self, event=None):
        if event is not None and event.widget != self.frame:
            return
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()

    def run(self, title, args, interactive=False):
        self.heading(title)
        back = self.button("Kembali", self.home)
        back.state(["disabled"])
        status = ttk.Label(self.frame, text="Sedang berjalan…")
        status.pack(anchor="w", pady=4)
        output = ScrolledText(self.frame, state="disabled", wrap="word", height=12,
                              font=("Consolas", 10), padx=8, pady=6)
        output.pack(fill="both", expand=True)
        options = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        try:
            child = self.process = subprocess.Popen(
                [sys.executable, "-u", *args], env=options, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, stdin=subprocess.PIPE if interactive else subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            back.state(["!disabled"])
            status.configure(text="Gagal menjalankan utilitas.")
            raise

        entry = send = None
        if interactive:
            (entry,) = self.fields([("Jawaban", "", ())])

            def respond():
                if child.poll() is not None:
                    return
                try:
                    child.stdin.write((entry.get() + "\n").encode("utf-8"))
                    child.stdin.flush()
                except BrokenPipeError:
                    return
                entry.delete(0, "end")

            send = self.button("Kirim jawaban", respond)
            entry.bind("<Return>", lambda _: self.action(respond)())
        cancel = self.button("Hentikan", self.close_process)
        messages = Queue()

        def read():
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            with child.stdout:
                for chunk in iter(lambda: child.stdout.read1(4096), b""):
                    messages.put(decoder.decode(chunk))
                messages.put(decoder.decode(b"", final=True))
            messages.put(child.wait())

        def poll():
            for _ in range(50):
                try:
                    item = messages.get_nowait()
                except Empty:
                    break
                if isinstance(item, int):
                    self.process = None
                    if child.stdin is not None:
                        child.stdin.close()
                    status.configure(text="Selesai" if item == 0 else f"Berhenti dengan kode {item}")
                    back.state(["!disabled"])
                    cancel.destroy()
                    if interactive:
                        entry.state(["disabled"])
                        send.state(["disabled"])
                    return
                output.configure(state="normal")
                follow = output.yview()[1] >= 0.999
                output.insert("end", item)
                if follow:
                    output.see("end")
                output.configure(state="disabled")
            self.root.after(70, poll)

        threading.Thread(target=read, daemon=True).start()
        self.root.after(70, poll)

    def image(self):
        candidates = {f"{s.kode} {s.nama}": s.kode for s in sources.load() if s.olah == "claude"}
        self.heading("Coba baca gambar sinyal", self.home)
        path, text, source = self.fields([("File gambar", "", ()), ("Teks pesan", "", ()),
                                          ("Sumber SOP", "", ("Bawaan", *candidates))])

        def browse():
            result = filedialog.askopenfilename(parent=self.root, filetypes=[
                ("Gambar", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"), ("Semua file", "*.*"),
            ])
            if result:
                path.delete(0, "end")
                path.insert(0, result)

        def run():
            file = path.get().strip().strip('"')
            if not Path(file).is_file():
                raise ValueError("Pilih file gambar yang tersedia.")
            self.run("Coba baca gambar sinyal", ("-m", "reader.main", "--coba-gambar", file,
                                                f"--teks={text.get()}", "--sumber", candidates.get(source.get(), "")))

        self.button("Pilih gambar…", browse)
        self.button("Coba baca", run)

    def token(self):
        bots = {"MT5": "TG_BOT_TOKEN", "Bybit": "BYBIT_TG_BOT_TOKEN", "Pengawas": "SUPERVISOR_TG_BOT_TOKEN"}

        def save(bot, token):
            if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
                raise ValueError("Format token Telegram salah. Salin token lengkap dari BotFather.")
            envfile.save_setting(bots[bot], token)
            messagebox.showinfo("Token disimpan", f"Nyalakan ulang bot {bot} jika sedang berjalan.", parent=self.root)
            self.home()

        self.form("Isi/ganti token bot Telegram", [("Bot", "", tuple(bots)), ("Token bot", "", ())],
                  save, self.home)

    def inbox(self):
        self.heading("Ganti channel kotak masuk sinyal", self.home)
        status = ttk.Label(self.frame, wraplength=620)
        status.pack(anchor="w")

        def render(ok, result):
            if not ok:
                self.button("Deteksi ulang", self.inbox)
                return
            if not result:
                status.configure(text="Tidak ada channel tempat akun Pembaca bisa posting.")
                return
            channels = {f"{title} · {channel}": channel for channel, title, _ in result}
            current = os.getenv("TG_INBOX_CHANNEL_ID")
            selected = next((label for label, channel in channels.items() if channel == current), "")
            (choice,) = self.fields([("Channel kotak masuk", selected, tuple(channels))])

            def save():
                channel = channels[choice.get()]
                if messagebox.askyesno("Simpan kotak masuk",
                                       "Pastikan bot eksekutor sudah menjadi anggota channel ini. Simpan?",
                                       parent=self.root):
                    envfile.save_setting("TG_INBOX_CHANNEL_ID", channel)
                    messagebox.showinfo("Kotak masuk disimpan",
                                        "Nyalakan ulang Pembaca dan eksekutor yang sedang berjalan.",
                                        parent=self.root)
                    self.home()

            self.button("Simpan", save)

        self.background(status, lambda: asyncio.run(detect_dialogs(wizard.inbox_dialog)), render)
