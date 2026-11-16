"""Source editing and Telegram discovery in the dashboard."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from copier import accounts, accounts_mt5, sopfiles
from copier.config import load_config
from copier.single import READER_PORT, claim
from reader import sources, wizard

from . import sources_menu
from .page_widgets import Page, checklist
from .page_settings import edit_section, platform_settings


TOPIC_MODES = ("Semua topik", "Semua topik kecuali yang dicentang", "Hanya topik yang dicentang")


@asynccontextmanager
async def discovery():
    from reader.main import telegram_client

    lock = claim(READER_PORT)
    if lock is None:
        raise ValueError("Sesi Telegram sedang dipakai. Hentikan Pembaca sinyal sebelum deteksi.")
    with lock:
        try:
            client = telegram_client()
        except SystemExit as error:
            raise ValueError(str(error)) from error
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise ValueError("Login Telegram Pembaca dulu lewat MENU.bat → Nyalakan.")
            yield client
        finally:
            await client.disconnect()


async def detect_dialogs(accepts):
    async with discovery() as client:
        return [(str(d.id), d.name, bool(getattr(d.entity, "forum", False)))
                async for d in client.iter_dialogs() if accepts(d)]


async def detect_topics(channel):
    async with discovery() as client:
        return await wizard.list_topics(client, await client.get_input_entity(int(channel)))


def account_checklist(parent, platform, selected=()):
    items = accounts.load() if platform == "bybit" else accounts_mt5.load()
    names = [a.nama for a in items]
    selected = {name.casefold() for name in selected}
    if not names:
        text = ("Terminal MT5 lama akan dipakai." if platform == "mt5" and not accounts_mt5.FILE.exists()
                else "Tambah akun lewat Setelan universal.")
        ttk.Label(parent, text=text).pack(anchor="w")
    return checklist(parent, [(name, name) for name in names],
                     [name for name in names if name.casefold() in selected])


def target_accounts(platform, selected):
    if selected:
        return selected
    if platform == "mt5" and not accounts_mt5.FILE.exists():
        return None
    raise ValueError("Centang minimal satu akun tujuan.")


class SourcePage(Page):
    def __init__(self, root, tabs):
        super().__init__(root, tabs, "Sumber sinyal")
        self.frame.bind("<Visibility>", lambda _: self.action(self.open_once)(), add="+")
        self.opened = False

    def open_once(self):
        if self.opened:
            return
        self.opened = True
        self.list_sources()

    def list_sources(self):
        self.heading("Sumber sinyal")
        try:
            items = sources.load(cfg=load_config(sopfiles.UNIVERSAL["mt5"]))
            if any(not s.kode or not Path(sopfiles.sop_path(s.kode)).exists() for s in items):
                sopfiles.migrate()
                items = sources.load()
        except Exception:
            self.button("Muat ulang", self.list_sources)
            raise
        self.button("+ Tambah sumber", self.channel_picker)
        self.button("Muat ulang", self.list_sources)
        table = ttk.Treeview(self.frame, show="tree", selectmode="browse")
        scroll = ttk.Scrollbar(self.frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        table.pack(fill="both", expand=True)
        for source in items:
            table.insert("", "end", iid=source.kode, text=sources_menu.row(".", source))

        def selected(action):
            if table.selection():
                action(table.selection()[0])

        self.button("Edit sumber", lambda: selected(self.edit_source), pady=(8, 0))
        self.button("Hapus sumber", lambda: selected(self.remove))
        table.bind("<Double-1>", lambda _: self.action(lambda: selected(self.edit_source))())

    @staticmethod
    def lookup(code):
        items = sources.load()
        source = sources.by_code(items, code)
        if source is None:
            raise ValueError(f"Sumber {code} sudah tidak tersedia.")
        return items, source

    def update(self, code, **changes):
        items, source = self.lookup(code)
        for field, value in changes.items():
            setattr(source, field, value)
        sources.save(items)
        self.edit_source(code)

    def remove(self, code):
        items, source = self.lookup(code)
        self.check_busy(code)
        if messagebox.askyesno("Hapus sumber", f"Hapus {code} {source.nama}? SOP akan diarsipkan.",
                               parent=self.root):
            self.check_busy(code)
            sources.save([s for s in items if s.kode != code])
            sopfiles.archive_source(code)
            self.list_sources()

    @staticmethod
    def check_busy(code):
        busy = sources_menu.busy_setups(".", code)
        if busy:
            raise ValueError(f"{code} masih punya setup aktif atau menunggu konfirmasi: {', '.join(busy)}.")

    def edit_source(self, code):
        _, source = self.lookup(code)
        back = lambda: self.edit_source(code)
        settings = platform_settings(source.ke, code)

        def rename(raw):
            items, current = self.lookup(code)
            name = sources.slug(raw)
            if not raw or any(s.nama == name and s is not current for s in items):
                raise ValueError("Isi nama sumber yang belum dipakai.")
            self.update(code, nama=name)

        rows = [
            (f"Channel & topik · {source.judul or source.channel} · {source.topic_text()}",
             lambda: self.channel_picker(code)),
            (f"Diolah oleh · {sources.OLAH[source.olah]}",
             lambda: self.form("Diolah oleh", [("Pengolah", sources.OLAH[source.olah], tuple(sources.OLAH.values()))],
                               lambda label: self.update(code, olah=next(k for k, v in sources.OLAH.items()
                                                                         if v == label)), back)),
            (f"Tujuan · {sources.TUJUAN[source.ke]}", lambda: self.destination(source)),
            ("Akun tujuan · " + (", ".join(source.account_names()) or "–"), lambda: self.destination(source, True)),
            (f"Nama · {source.nama}", lambda: self.form("Nama", [("Nama", source.nama, ())], rename, back)),
            (f"Ganti kode · {code}", lambda: self.form(
                "Ganti kode", [("Kode baru", code, ())],
                lambda raw: self.edit_source(sources_menu.change_code(code, raw)), back)),
        ]
        rows += [(f"SOP · {title}", lambda title=title, items=items: edit_section(self, settings, title, items, back))
                 for title, items in settings.sections]
        self.menu(f"{code} {source.nama}", rows, self.list_sources)

    def destination(self, source, accounts_only=False):
        back = lambda: self.edit_source(source.kode)
        self.heading("Akun tujuan" if accounts_only else "Tujuan", back)
        platform = tk.StringVar(value=source.ke)
        box = ttk.LabelFrame(self.frame, text="Akun tujuan", padding=8)

        def render():
            nonlocal selected
            for child in box.winfo_children():
                child.destroy()
            saved = source.account_names() if platform.get() == source.ke else ()
            selected = account_checklist(box, platform.get(), saved)

        selected = None
        if not accounts_only:
            for value, label in sources.TUJUAN.items():
                ttk.Radiobutton(self.frame, text=label, value=value, variable=platform,
                                command=self.action(render)).pack(anchor="w")
        box.pack(fill="x")
        render()

        def save():
            ke = platform.get()
            names = target_accounts(ke, selected())
            items, current = self.lookup(source.kode)
            if ke != current.ke:
                self.check_busy(current.kode)
                cfg = sopfiles.effective(sopfiles.UNIVERSAL[current.ke], current.kode)
                sopfiles.write_source(current.kode, ke, current.nama, sopfiles.sop_values(cfg))
            current.ke, current.akun_tujuan = ke, names
            current.akun = names[0] if names and ke == "bybit" else ""
            sources.save(items)
            back()

        self.button("Simpan", save, pady=12)

    def channel_picker(self, code=None):
        original = self.lookup(code)[1] if code else None
        back = (lambda: self.edit_source(code)) if code else self.list_sources
        self.heading("Channel & topik" if code else "Tambah sumber sinyal", back)
        bar = ttk.Frame(self.frame)
        bar.pack(fill="x")
        combo = ttk.Combobox(bar, state="disabled", width=46)
        combo.pack(side="left", padx=(0, 8))
        if original:
            combo.set(original.judul or original.channel)
        status = ttk.Label(self.frame, wraplength=620)
        status.pack(anchor="w", pady=4)
        topic_box = ttk.LabelFrame(self.frame, text="Topik yang dibaca", padding=8)
        topic_box.pack(fill="x")
        dialogs = []
        selection = None
        mode = tk.IntVar()
        get_topics = lambda: []

        def show_topics(_event=None):
            nonlocal selection
            selection = dialogs[combo.current()]
            mode.set(0)
            for child in topic_box.winfo_children():
                child.destroy()
            submit.state(["disabled"])
            channel, _, forum = selection
            if not forum:
                ttk.Label(topic_box, text="Semua pesan dibaca. Channel atau grup ini tidak memakai topik.").pack(anchor="w")
                submit.state(["!disabled"])
                return
            combo.state(["disabled"])
            detect.state(["disabled"])
            self.background(status, lambda: asyncio.run(detect_topics(channel)), render_topics)

        def render_topics(ok, topics):
            nonlocal get_topics
            combo.state(["!disabled"])
            detect.state(["!disabled"])
            if not ok:
                return
            saved = []
            if original and selection[0] == original.channel:
                if original.topik != "semua":
                    mode.set(2)
                    saved = original.topik
                elif original.abaikan:
                    mode.set(1)
                    saved = original.abaikan
            for index, label in enumerate(TOPIC_MODES):
                ttk.Radiobutton(topic_box, text=label, variable=mode, value=index,
                                command=lambda: choices.pack(fill="x") if mode.get()
                                else choices.pack_forget()).pack(anchor="w")
            choices = ttk.Frame(topic_box)
            choices.pack(fill="x")
            labels = dict(topics)
            labels.update({number: "Topik tersimpan" for number in saved if number not in topics})
            get_topics = checklist(choices, [(n, f"{n} · {title}") for n, title in labels.items()], saved)
            if not mode.get():
                choices.pack_forget()
            submit.state(["!disabled"])

        def discovered(ok, value):
            detect.state(["!disabled"])
            if not ok:
                return
            dialogs[:] = value
            if not dialogs:
                status.configure(text="Tidak ada channel atau grup yang ditemukan.")
                return
            combo.configure(state="readonly", values=[f"{name} · {channel}" for channel, name, _ in dialogs])
            combo.current(next((i for i, row in enumerate(dialogs) if original and row[0] == original.channel), 0))
            show_topics()

        def find():
            detect.state(["disabled"])
            combo.state(["disabled"])
            submit.state(["disabled"])
            self.background(status, lambda: asyncio.run(detect_dialogs(wizard.source_dialog)), discovered)

        def save():
            channel, title, _ = selection
            checked = get_topics() if mode.get() else []
            if mode.get() and not checked:
                raise ValueError("Centang minimal satu topik.")
            picked = dict(channel=channel, judul=title, topik=checked if mode.get() == 2 else "semua",
                          abaikan=checked if mode.get() == 1 else [])
            if code:
                self.update(code, **picked)
            else:
                self.details(picked)

        detect = self.button("Deteksi channel & grup", find, parent=bar)
        submit = self.button("Simpan" if code else "Lanjut", save, pady=12)
        submit.state(["disabled"])
        combo.bind("<<ComboboxSelected>>", lambda _: self.action(show_topics)())

    def details(self, picked):
        self.heading("Tambah sumber sinyal", self.channel_picker)
        items = sources.load()
        olah, platform, sop, name = self.fields([
            ("Diolah oleh", "", tuple(sources.OLAH.values())),
            ("Tujuan", "", tuple(sources.TUJUAN.values())),
            ("SOP awal", "Bawaan", ("Bawaan",)),
            ("Nama", sources.slug(picked["judul"]), ()),
        ])
        box = ttk.LabelFrame(self.frame, text="Akun tujuan", padding=8)
        box.pack(fill="x")
        choices = {}
        selected = None

        def render():
            nonlocal selected
            ke = platform.get().lower()
            for child in box.winfo_children():
                child.destroy()
            selected = account_checklist(box, ke)
            choices.clear()
            choices.update({f"{s.kode} {s.nama}": s.kode for s in items if s.ke == ke})
            sop.configure(values=("Bawaan", *choices))
            sop.set("Bawaan")

        def save():
            existing = sources.load()
            nama = sources.slug(name.get())
            if any(s.nama == nama for s in existing):
                raise ValueError("Nama sumber sudah dipakai.")
            ke = platform.get().lower()
            names = target_accounts(ke, selected())
            processor = next(key for key, label in sources.OLAH.items() if label == olah.get())
            code = sopfiles.new_code(existing)
            template = choices.get(sop.get())
            values = sopfiles.sop_values(sopfiles.effective(sopfiles.UNIVERSAL[ke], template)) if template else None
            source = sources.Source(nama=nama, kode=code, ke=ke, olah=processor, akun_tujuan=names,
                                    akun=names[0] if names and ke == "bybit" else "", **picked)
            sopfiles.write_source(code, ke, nama, values)
            sources.save(existing + [source])
            self.edit_source(code)

        platform.bind("<<ComboboxSelected>>", lambda _: self.action(render)())
        render()
        self.button("Simpan", save, pady=12)
