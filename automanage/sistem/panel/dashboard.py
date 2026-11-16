"""Tkinter monitor for the same three processes launched by MENU.bat."""
import json
import os
from pathlib import Path
import re
import socket
import time
import tkinter as tk
from tkinter import messagebox, ttk

from copier import envfile
from copier.log_tail import LogTail
from copier.process_control import WORKER_PROGRAMS, launch_background, request_stop, running_session
from copier.single import (
    BOT_PORT, BYBIT_BOT_PORT, DASHBOARD_PORT, READER_PORT, SUPERVISOR_PORT, claim, is_running,
)
from reader import sources


PROGRAMS = (*WORKER_PROGRAMS, ("Bot Pengawas", SUPERVISOR_PORT, "pengawas.log", ("copier.supervisor",)))
POLL_MS = 400
STOP_TIMEOUT = 300  # The Reader can be waiting on a Claude call (up to 240 seconds).
LOG_MAX_LINES = 2500
LOG_LINE = re.compile(
    r"^\d{4}-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})[,.]\d+ "
    r"(DEBUG|INFO|AKTIF|WARNING|ERROR|CRITICAL) \S+ (.*)$"
)
LOG_COLORS = {
    "DEBUG": "#526074", "INFO": "#154bb5", "AKTIF": "#18703b",
    "WARNING": "#984600", "ERROR": "#b32328", "CRITICAL": "#b32328",
}


class BotPanel:
    def __init__(self, parent, name, port, log_name, command, check):
        self.name = name
        self.port = port
        self.command = command
        self.check = check
        self.tail = LogTail(Path("logs") / log_name)
        self.child = None
        self.session = None
        self.active = False
        self.pending = None
        self.stop_session = None
        self.stop_deadline = 0
        self.error = False
        self.level = "DEBUG"
        self.locked = False

        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)
        header = ttk.Frame(self.frame)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Label(header, text=name, font=("Segoe UI", 11, "bold")).pack(side="left")
        self.status = ttk.Label(header)
        self.status.pack(side="left", padx=10)
        actions = ttk.Frame(header)
        actions.pack(side="right")
        self.start_button = ttk.Button(actions, text="Nyalakan", command=self.start)
        self.stop_button = ttk.Button(actions, text="Stop", command=lambda: self.stop("stop"))
        self.restart_button = ttk.Button(actions, text="Restart", command=lambda: self.stop("restart"))
        clear_button = ttk.Button(actions, text="Bersihkan log", command=self.clear_log)
        for button in (self.start_button, self.stop_button, self.restart_button, clear_button):
            button.pack(side="left", padx=(5, 0))

        output = ttk.Frame(self.frame)
        output.grid(row=1, column=0, sticky="nsew")
        output.columnconfigure(0, weight=1)
        output.rowconfigure(0, weight=1)
        self.log = tk.Text(output, height=5, wrap="word", state="disabled",
                           font=("Consolas", 10), padx=8, pady=5,
                           background="white", foreground="#222832")
        scroll = ttk.Scrollbar(output, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        for level, color in LOG_COLORS.items():
            self.log.tag_configure(level, foreground=color)

    def clear_log(self):
        self.tail.skip_existing()
        self.level = "DEBUG"
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def append(self, lines):
        if not lines:
            return
        follow = self.log.yview()[1] >= 0.999
        self.log.configure(state="normal")
        for line in lines:
            match = LOG_LINE.match(line)
            if match:
                month, day, clock, self.level, message = match.groups()
                if self.port == BYBIT_BOT_PORT and self.level == "INFO" and message.startswith("Trafik HTTP Bybit:"):
                    continue
                line = f"{day}/{month} {clock}  {'[' + self.level + ']':<10} {message}"
            self.log.insert("end", line + "\n", self.level)
        line_count = int(self.log.index("end-1c").split(".")[0]) - 1
        if line_count > LOG_MAX_LINES:
            self.log.delete("1.0", f"{line_count - LOG_MAX_LINES + 1}.0")
        self.log.configure(state="disabled")
        if follow:
            self.log.see("end")

    def report_error(self, message):
        self.error = True
        self.level = "ERROR"
        self.append([f"[ERROR]    {message}"])

    def start(self):
        if self.child is not None or is_running(self.port):
            return
        self.error = False
        try:
            reason = self.check(None)
            if reason:
                raise ValueError(reason)
            if self.port == READER_PORT and not Path("pembaca.session").is_file():
                raise ValueError("Login Telegram Pembaca dulu lewat MENU.bat → Nyalakan.")
            self.child = launch_background(self.command)
        except Exception as exc:
            self.report_error(f"Gagal menyalakan {self.name}: {exc}")
            messagebox.showerror(self.name, str(exc), parent=self.frame)
        self.render()

    def stop(self, action):
        self.pending = action
        self.stop_session = None
        self.stop_deadline = time.monotonic() + STOP_TIMEOUT
        self.refresh()

    def refresh(self):
        self.append(self.tail.read())
        if self.child is not None:
            exit_code = self.child.poll()
            if exit_code is not None:
                self.child = None
                if exit_code:
                    self.report_error(f"{self.name} keluar dengan kode {exit_code}.")
        self.active = is_running(self.port)
        self.session = running_session(self.port) if self.active else None
        if self.pending:
            self.advance_stop()
        self.render()

    def advance_stop(self):
        if not self.active and self.child is None:
            action, self.pending = self.pending, None
            if action == "restart":
                self.start()
        elif self.stop_session and self.session and self.session != self.stop_session:
            self.pending = None
            self.report_error("Proses sudah diganti dari luar dashboard.")
        elif time.monotonic() >= self.stop_deadline:
            self.pending = None
            self.report_error(f"{self.name} belum berhenti dalam {STOP_TIMEOUT} detik.")
        elif self.session and self.stop_session is None:
            try:
                request_stop(self.port, self.session)
            except OSError as exc:
                self.pending = None
                self.report_error(f"Gagal mengirim permintaan stop: {exc}")
            else:
                self.stop_session = self.session

    def render(self):
        busy = self.locked or self.pending is not None
        can_stop = bool(self.session)
        if self.pending:
            status = "Merestart…" if self.pending == "restart" else "Menghentikan…"
        elif self.active:
            status = "● Jalan" if can_stop else "● Dipakai (lihat menu)"
        elif self.child is not None:
            status = "Menyalakan…"
        else:
            status = "● Error" if self.error else "○ Mati"
        color = LOG_COLORS["ERROR"] if status == "● Error" else (
            LOG_COLORS["AKTIF"] if status == "● Jalan" else LOG_COLORS["DEBUG"]
        )
        self.status.configure(text=status, foreground=color)
        self.start_button.configure(state="disabled" if busy or self.active or self.child else "normal")
        for button in (self.stop_button, self.restart_button):
            button.configure(state="normal" if can_stop and not busy else "disabled")

    @property
    def running(self):
        return self.active or self.child is not None


class Dashboard:
    def __init__(self, root, listener=None):
        from .main import PROGRAMS as menu_programs
        from .pages import add_pages

        self.root = root
        self.listener = listener
        self.poll_id = None
        self.restore_id = None
        self.close_after_stop = False
        self.prompt = None
        root.title("Dashboard Bot")
        root.geometry("860x720")
        root.minsize(650, 540)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Destroy>", self.cancel_poll, add="+")
        icon = Path(envfile.FRONT) / "logo" / "logo.ico"
        if os.name == "nt" and icon.is_file():
            root.iconbitmap(default=str(icon))
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True)
        body = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(body, text="Dashboard")
        add_pages(root, self.tabs)
        self.tabs.enable_traversal()
        body.columnconfigure(0, weight=1)
        checks = {module: check for _, _, module, _, check, _ in menu_programs}
        self.panels = [BotPanel(body, name, port, log, command, checks[command[0]])
                       for name, port, log, command in PROGRAMS]
        for row, panel in enumerate(self.panels):
            body.rowconfigure(row, weight=1, uniform="logs")
            panel.frame.grid(row=row, column=0, sticky="nsew",
                             pady=(0, 10 if row < len(self.panels) - 1 else 0))
        account_panel = ttk.Frame(body)
        account_panel.grid(row=len(self.panels), column=0, sticky="ew", pady=(10, 0))
        ttk.Label(account_panel, text="Sumber sinyal dan akun", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.accounts = ttk.Treeview(account_panel, columns=("state",), show="tree headings", height=5)
        self.accounts.heading("#0", text="Sumber / akun tujuan", anchor="w")
        self.accounts.heading("state", text="Status koneksi", anchor="w")
        self.accounts.column("state", width=350)
        self.accounts.tag_configure("ready", foreground=LOG_COLORS["AKTIF"])
        self.accounts.tag_configure("error", foreground=LOG_COLORS["ERROR"])
        self.accounts.tag_configure("idle", foreground=LOG_COLORS["DEBUG"])
        scroll = ttk.Scrollbar(account_panel, orient="vertical", command=self.accounts.yview)
        self.accounts.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.accounts.pack(fill="x")
        self.account_snapshot = None
        self.poll()

    def poll(self):
        self.poll_id = None
        if self.listener is not None:
            try:
                connection, _ = self.listener.accept()
            except BlockingIOError:
                pass
            else:
                connection.close()
                self.root.deiconify()
                self.root.lift()
                self.root.attributes("-topmost", True)
                self.root.focus_force()
                if self.restore_id is not None:
                    self.root.after_cancel(self.restore_id)
                self.restore_id = self.root.after(250, self.clear_topmost)
                self.tabs.select(0)
        for panel in self.panels:
            panel.refresh()
        self.refresh_accounts()
        if self.close_after_stop and not any(panel.pending for panel in self.panels):
            if not any(panel.running for panel in self.panels):
                self.root.destroy()
                return
            self.close_after_stop = False
            for panel in self.panels:
                panel.locked = False
                panel.render()
            messagebox.showwarning("Bot masih aktif",
                                   "Sebagian proses belum berhenti. Lihat log masing-masing bot.",
                                   parent=self.root)
        self.poll_id = self.root.after(POLL_MS, self.poll)

    def clear_topmost(self):
        self.restore_id = None
        self.root.attributes("-topmost", False)

    def cancel_poll(self, event):
        if event.widget is self.root:
            for callback in (self.poll_id, self.restore_id):
                if callback is not None:
                    self.root.after_cancel(callback)

    def refresh_accounts(self):
        active = {panel.port: panel.active for panel in self.panels}
        records = {}
        for platform, port in (("mt5", BOT_PORT), ("bybit", BYBIT_BOT_PORT)):
            path = Path("data") / f"koneksi-{platform}.json"
            try:
                age = time.time() - path.stat().st_mtime
                records[platform] = json.loads(path.read_text(encoding="utf-8")) if age < 60 or not active.get(port) else []
            except (OSError, ValueError):
                records[platform] = []
        try:
            items = sources.load()
        except Exception:
            return
        tree = {}
        for source in items:
            names = source.account_names()
            if source.ke == "mt5" and source.akun_tujuan is None and not names:
                names = ["MT5 lama"]
            tree[source.ke, source.kode, source.nama] = {name: False for name in names}
        for platform, rows in records.items():
            for row in rows:
                if row["old"]:
                    key = next((key for key in tree if key[:2] == (platform, row["code"])),
                               (platform, row["code"], row["source"] or "Setup lama"))
                    tree.setdefault(key, {}).setdefault(row["name"], True)
        snapshot = (active, records, tree)
        if snapshot == self.account_snapshot:
            return
        self.account_snapshot = snapshot
        self.accounts.delete(*self.accounts.get_children())
        for (platform, code, source), names in tree.items():
            if not names:
                continue
            port = BOT_PORT if platform == "mt5" else BYBIT_BOT_PORT
            parent = self.accounts.insert("", "end", text=f"{code} {source} · {sources.TUJUAN[platform]}", open=True)
            for name, old in names.items():
                record = next((row for row in records[platform] if row["code"] == code
                               and row["name"].casefold() == name.casefold()), None)
                if not active.get(port):
                    state, tag = "○ Bot mati", "idle"
                elif record is None:
                    state, tag = "○ Menunggu status koneksi", "idle"
                elif record["error"]:
                    state, tag = f"● Gagal terhubung · {record['error']}", "error"
                else:
                    state, tag = "● Terhubung", "ready"
                label = name + (" · Mengelola setup lama" if old else "")
                self.accounts.insert(parent, "end", text=label, values=(state,), tags=(tag,))

    def close(self):
        if self.close_after_stop:
            return
        if self.prompt is not None:
            self.prompt.lift()
            return
        for panel in self.panels:
            panel.refresh()
        running = [panel.name for panel in self.panels if panel.running]
        if not running:
            self.root.destroy()
            return
        dialog = self.prompt = tk.Toplevel(self.root)
        dialog.title("Tutup dashboard?")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", self.dismiss_prompt)
        dialog.bind("<Escape>", lambda _: self.dismiss_prompt())
        content = ttk.Frame(dialog, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Masih berjalan: " + ", ".join(running)).pack(anchor="w", pady=(0, 12))
        buttons = ttk.Frame(content)
        buttons.pack(anchor="e")
        cancel = ttk.Button(buttons, text="Batal", command=self.dismiss_prompt)
        cancel.pack(side="left")
        ttk.Button(buttons, text="Tutup dashboard saja", command=self.root.destroy).pack(side="left", padx=6)
        ttk.Button(buttons, text="Matikan semua & tutup", command=self.stop_all).pack(side="left")
        dialog.update_idletasks()
        left = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2
        top = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_reqheight()) // 2
        dialog.geometry(f"+{left}+{top}")
        dialog.grab_set()
        cancel.focus_set()

    def dismiss_prompt(self):
        self.prompt.destroy()
        self.prompt = None

    def stop_all(self):
        self.dismiss_prompt()
        for panel in self.panels:
            panel.refresh()
        unmanaged = [panel.name for panel in self.panels
                     if panel.active and not panel.session and panel.child is None]
        if unmanaged:
            messagebox.showwarning("Proses dipakai di luar dashboard",
                                   "Selesaikan dari jendela asal: " + ", ".join(unmanaged),
                                   parent=self.root)
            return
        self.close_after_stop = True
        for panel in self.panels:
            panel.locked = True
            if panel.running:
                panel.stop("stop")
            else:
                panel.render()


def main():
    os.chdir(Path(__file__).resolve().parents[1])
    lock = claim(DASHBOARD_PORT)
    if lock is None:
        with socket.create_connection(("127.0.0.1", DASHBOARD_PORT), timeout=2):
            pass
        return
    with lock:
        lock.setblocking(False)
        envfile.load()
        root = tk.Tk()
        Dashboard(root, lock)
        root.mainloop()


if __name__ == "__main__":
    main()
