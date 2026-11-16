"""Universal and per-source configuration forms."""
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from copier import accounts, accounts_mt5, sopfiles
from reader import sources

from . import mt5_accounts, sources_menu, symbols
from .main import bot_on, bybit_on
from .page_widgets import Page
from .settings import (
    DEFAULTS, SOURCE_SECTIONS, SOURCE_SECTIONS_BYBIT, SPLIT_SECTIONS,
    UNIVERSAL_SECTIONS, UNIVERSAL_SECTIONS_BYBIT, Settings, _get, parse,
)


def platform_settings(platform, code=None):
    bybit = platform == "bybit"
    if code:
        path = sopfiles.sop_path(code)
        sections = SOURCE_SECTIONS_BYBIT if bybit else SOURCE_SECTIONS
    else:
        path = sopfiles.UNIVERSAL[platform]
        sections = UNIVERSAL_SECTIONS_BYBIT if bybit else UNIVERSAL_SECTIONS
    return Settings(path, bot_running=bybit_on if bybit else bot_on, bybit=bybit,
                    sections=sections, live=bool(code))


def edit_section(page, settings, title, items, back):
    again = lambda: edit_section(page, settings, title, items, back)
    cfg = settings.cfg()
    rows = []
    for item in items:
        path, label, *_ = item
        value = settings._display_value(path, _get(cfg, path))
        rows.append((f"{label}    {value}", lambda item=item: edit_value(page, settings, item, again)))
    if title in SPLIT_SECTIONS:
        def reset():
            if messagebox.askyesno(title, "Kembalikan semua pembagian partial ke bawaan?", parent=page.root):
                for path, _, kind, *_ in items:
                    if kind == "split":
                        settings.set(path, _get(DEFAULTS, path))
                again()
        rows.append(("Kembalikan semua pembagian partial ke bawaan", reset))
    page.menu(title, rows, back)


def edit_value(page, settings, item, back):
    path, label, kind, options, note = item
    current = _get(settings.cfg(), path)
    display = lambda v: settings._display_value(path, v)
    values = {display(v): v for v in options} if kind == "choice" else {}
    if kind == "bool":
        values = {"ya": True, "tidak": False}

    def save(raw):
        value = values[raw] if values else parse(kind, options, raw)
        if path == ("akun", "hanya_demo") and value is False:
            confirm = simpledialog.askstring("Akun REAL", "Ketik REAL untuk mengizinkan trading akun real.",
                                             parent=page.root)
            if confirm != "REAL":
                return
        settings.set(path, value)
        if not settings.live and settings.bot_running():
            messagebox.showinfo("Setelan disimpan", "Nyalakan ulang bot supaya perubahan berlaku.",
                                parent=page.root)
        back()

    page.form(label, [(label, display(current), tuple(values))], save, back)
    if note:
        ttk.Label(page.frame, text=note, wraplength=590).pack(anchor="w")


class SettingsPage(Page):
    def __init__(self, root, tabs):
        super().__init__(root, tabs, "Setelan universal")
        self.home()

    def home(self):
        self.menu("Setelan universal (MT5 / Bybit)", [
            ("MT5 (Exness)", lambda: self.platform("mt5")),
            ("Bybit", lambda: self.platform("bybit")),
        ])

    def platform(self, platform):
        settings = platform_settings(platform)
        back = lambda: self.platform(platform)
        bybit = platform == "bybit"
        rows = [(label, lambda label=label, items=items: edit_section(self, settings, label, items, back))
                for label, items in settings.sections]
        rows.append(("Akun Bybit (API key per akun)" if bybit else "Akun MT5",
                     self.bybit_accounts if bybit else self.mt5_list))
        self.menu(f"Setelan universal {settings.name}", rows, self.home)

    def mt5_list(self):
        rows = [(a.nama, lambda name=a.nama: self.mt5_account(name)) for a in accounts_mt5.load()]
        self.menu("Akun MT5", rows + [("+ Tambah akun", self.mt5_path)], lambda: self.platform("mt5"))

    @staticmethod
    def mt5_index(name):
        items = accounts_mt5.load()
        for index, item in enumerate(items):
            if item.nama == name:
                return items, index
        raise ValueError("Akun MT5 sudah tidak tersedia.")

    def mt5_account(self, name):
        items, index = self.mt5_index(name)
        self.menu(name, [
            (f"Ganti direktori terminal · {Path(items[index].path).parent}", lambda: self.mt5_path(name)),
            ("Pakai akun yang sedang login", lambda: self.mt5_current_login(items[index])),
            ("Nama simbol", lambda: self.mt5_symbols(name)),
            ("Cocokkan otomatis dengan broker di MT5", lambda: self.mt5_match(name)),
        ], self.mt5_list)

    def mt5_current_login(self, account):
        self.mt5_account_preview(account, existing=True)

    def mt5_account_preview(self, account, *, existing):
        settings = platform_settings("mt5")
        back = (lambda: self.mt5_account(account.nama)) if existing else self.mt5_path
        self.heading("Pakai akun yang sedang login" if existing else "Periksa akun MT5 baru", back)
        status = ttk.Label(self.frame, wraplength=620)
        status.pack(anchor="w")

        def detected(ok, result):
            if not ok:
                return
            identity, mapping, choices, missing = result
            ttk.Label(self.frame, text=f"Login {identity[0]} · Server {identity[1]}").pack(anchor="w")
            multiple = []
            automatic = []
            skipped = "Pilih nanti"
            for pair, names in choices.items():
                if len(names) == 1:
                    mapping[pair] = names[0]
                    automatic.append(f"{pair} → {names[0]}")
                else:
                    multiple.append((pair, skipped, (skipped, *names)))
            if automatic:
                ttk.Label(self.frame, text="Usulan otomatis · " + ", ".join(automatic),
                          wraplength=620).pack(anchor="w")
            if missing:
                ttk.Label(self.frame, text="Simbol belum ditemukan · " + ", ".join(missing),
                          wraplength=620).pack(anchor="w")
            controls = self.fields(multiple) if multiple else []

            def save():
                selected = {pair: field.get() for (pair, _, _), field in zip(multiple, controls)
                            if field.get() != skipped}
                save_account = mt5_accounts.use_current_login if existing else mt5_accounts.add_account
                button.state(["disabled"])
                self.background(status, lambda: save_account(settings, account, identity, {**mapping, **selected}),
                                saved)

            def saved(ok, _):
                if ok:
                    self.mt5_account(account.nama)
                else:
                    button.state(["!disabled"])

            button = self.button("Simpan", save, pady=10)

        self.background(status, lambda: mt5_accounts.preview_account(settings, account, existing=existing),
                        detected)

    def mt5_path(self, name=None):
        settings = platform_settings("mt5")
        folder = ""
        if name:
            items, index = self.mt5_index(name)
            folder = str(Path(items[index].path).parent)
        back = (lambda: self.mt5_account(name)) if name else self.mt5_list
        self.heading("Ganti direktori terminal" if name else "Tambah akun MT5", back)
        specs = [] if name else [("Nama akun", "", ())]
        controls = self.fields(specs + [("Folder instalasi MT5", folder, ())])
        path_entry = controls[-1]

        def browse():
            chosen = filedialog.askdirectory(parent=self.root)
            if chosen:
                path_entry.delete(0, "end")
                path_entry.insert(0, chosen)

        def save():
            folder = path_entry.get().strip().strip('"')
            if not folder:
                raise ValueError("Isi folder instalasi MT5.")
            new_name = name or controls[0].get().strip()
            if not name:
                items = accounts_mt5.load()
                if not accounts.valid_name(new_name) or any(a.nama.casefold() == new_name.casefold() for a in items):
                    raise ValueError("Nama akun harus valid dan unik.")
                path = mt5_accounts.validate_terminal_path(folder, items)
                self.mt5_account_preview(accounts_mt5.Account(new_name, path), existing=False)
                return

            def work():
                items, index = self.mt5_index(name)
                path = mt5_accounts.validate_terminal_path(folder, items, items[index])
                mt5_accounts.change_path(settings, items, index, path)

            buttons = [child for child in self.frame.winfo_children() if isinstance(child, ttk.Button)]
            for button in buttons:
                button.state(["disabled"])

            def done(ok, _):
                if ok:
                    self.mt5_account(new_name)
                else:
                    for button in buttons:
                        button.state(["!disabled"])

            self.background(status, work, done)

        self.button("Pilih folder…", browse)
        self.button("Simpan" if name else "Periksa akun dan pair", save, pady=10)
        status = ttk.Label(self.frame, wraplength=620)
        status.pack(anchor="w")

    def mt5_symbols(self, name):
        scoped = mt5_accounts.AccountSymbols(name, platform_settings("mt5"))
        back = lambda: self.mt5_symbols(name)
        self.heading("Nama simbol", lambda: self.mt5_account(name))
        mapping = scoped.cfg()["simbol"]
        self.button("+ Tambah simbol", lambda: edit())
        table = ttk.Treeview(self.frame, columns=("broker",), show="tree headings", selectmode="browse")
        table.heading("#0", text="Simbol di sinyal")
        table.heading("broker", text="Simbol di broker")
        bar = ttk.Scrollbar(self.frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        table.pack(fill="both", expand=True)
        for key, value in sorted(mapping.items()):
            table.insert("", "end", iid=key, text=key, values=(value,))

        def edit(key=""):
            def save(signal, broker):
                if not signal or not broker:
                    raise ValueError("Isi nama simbol sinyal dan broker.")
                self.mt5_index(name)
                scoped.set(("simbol", key or signal.upper()), broker)
                back()
            if key:
                self.form("Nama simbol", [("Simbol di broker", mapping[key], ())],
                          lambda broker: save(key, broker), back)
            else:
                self.form("Tambah simbol", [("Simbol di sinyal", "", ()), ("Simbol di broker", "", ())],
                          save, back)

        def edit_selected():
            if table.selection():
                edit(table.selection()[0])

        self.button("Edit simbol", edit_selected)
        table.bind("<Double-1>", lambda _: self.action(edit_selected)())

    def mt5_match(self, name):
        items, index = self.mt5_index(name)
        settings = platform_settings("mt5")
        scoped = mt5_accounts.AccountSymbols(name, settings)
        account = items[index]
        identity = accounts_mt5.metadata().get(name.casefold(), {}).get("identity")
        back = lambda: self.mt5_account(name)
        self.heading("Cocokkan nama pair dengan broker", back)
        status = ttk.Label(self.frame, wraplength=620)
        status.pack(anchor="w")

        def render(ok, result):
            if not ok:
                return
            info, broker_symbols = result
            status.configure(text=f"{info.login} · {info.server} · {len(broker_symbols)} simbol")
            mapping = scoped.cfg()["simbol"]
            changes = []
            missing = []
            for key, current in mapping.items():
                candidates = [s.name for s in symbols.candidates(key, broker_symbols)]
                if not candidates:
                    missing.append(key)
                elif current not in candidates:
                    changes.append((key, current, candidates))
            if missing:
                ttk.Label(self.frame, text="Simbol belum ditemukan: " + ", ".join(missing),
                          wraplength=620).pack(anchor="w")
            if not changes:
                ttk.Label(self.frame, text="Tidak ada usulan perubahan simbol.").pack(anchor="w")
                return
            skipped = "Pertahankan simbol sekarang"
            controls = self.fields([(f"{key} · {current}", names[0], (*names, skipped))
                                    for key, current, names in changes])

            def save():
                self.mt5_index(name)
                updated = scoped.cfg()["simbol"]
                updated.update({key: control.get() for (key, _, _), control in zip(changes, controls)
                                if control.get() != skipped})
                accounts_mt5.set_metadata(name, simbol=updated)
                back()

            self.button("Simpan", save, pady=12)

        self.background(status, lambda: mt5_accounts._probe(settings, account.path, identity, symbols=True), render)

    def bybit_accounts(self):
        rows = [(a.describe(), lambda name=a.nama: self.bybit_account(name)) for a in accounts.load()]
        self.menu("Akun Bybit", rows + [("+ Tambah akun (API key baru)", self.bybit_add),
                                        ("Hapus akun", self.bybit_remove)], lambda: self.platform("bybit"))

    def bybit_add(self):
        def save(name, mode, key, secret):
            items = accounts.load()
            if not accounts.valid_name(name) or accounts.by_name(items, name):
                raise ValueError("Nama akun harus valid dan unik.")
            self.check_credentials(key, secret)
            accounts.save(items + [accounts.Account(name, mode, key, secret)])
            self.bybit_accounts()

        self.form("Tambah akun Bybit", [
            ("Nama akun", "", ()), ("Mode", "demo", accounts.MODES),
            ("API key", "", ()), ("API secret", "", ()),
        ], save, self.bybit_accounts)

    @staticmethod
    def check_credentials(key, secret):
        if not key or not secret:
            raise ValueError("Isi API key dan API secret.")

    def bybit_account(self, name):
        account = accounts.by_name(accounts.load(), name)
        if account is None:
            raise ValueError("Akun Bybit sudah tidak tersedia.")
        back = lambda: self.bybit_account(name)

        def update(**fields):
            items = accounts.load()
            current = accounts.by_name(items, name)
            if current is None:
                raise ValueError("Akun Bybit sudah tidak tersedia.")
            for field, value in fields.items():
                setattr(current, field, value)
            accounts.save(items)
            if bybit_on():
                messagebox.showinfo("Akun disimpan", "Nyalakan ulang bot Bybit supaya perubahan berlaku.",
                                    parent=self.root)
            back()

        def keys(key, secret):
            self.check_credentials(key, secret)
            update(key=key, secret=secret)

        self.menu(account.describe(), [
            ("Ganti API key + secret", lambda: self.form("Ganti API key + secret",
                                                        [("API key", "", ()), ("API secret", "", ())], keys, back)),
            ("Ganti mode", lambda: self.form("Ganti mode", [("Mode", account.mode, accounts.MODES)],
                                            lambda mode: update(mode=mode), back)),
        ], self.bybit_accounts)

    def bybit_remove(self):
        def remove(name):
            items = accounts.load()
            if any(s.ke == "bybit" and sources_menu.uses_account(s, name) for s in sources.load()):
                raise ValueError("Akun masih dipakai sumber Bybit. Ganti akun sumber itu dulu.")
            if sources_menu._bybit_account_busy(".", name):
                raise ValueError("Akun masih punya setup yang perlu dikelola.")
            accounts.save([a for a in items if a.nama != name])
            self.bybit_accounts()

        names = tuple(a.nama for a in accounts.load())
        self.heading("Hapus akun Bybit", self.bybit_accounts)
        if not names:
            ttk.Label(self.frame, text="Belum ada akun Bybit.").pack(anchor="w")
            return
        (account,) = self.fields([("Akun", "", names)])
        self.button("Hapus akun", lambda: remove(account.get()) if messagebox.askyesno(
            "Hapus akun Bybit", f"Hapus akun {account.get()}?", parent=self.root) else None)
