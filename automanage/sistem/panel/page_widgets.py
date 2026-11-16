"""Shared controls for pages inside the Tkinter notebook."""
from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk


class Page:
    def __init__(self, root, tabs, title):
        self.root = root
        self.frame = ttk.Frame(tabs, padding=12)
        tabs.add(self.frame, text=title)

    def action(self, callback):
        def run():
            try:
                callback()
            except Exception as error:
                self.error(error)
        return run

    def error(self, error):
        messagebox.showerror("Gagal", str(error), parent=self.root)

    def heading(self, title, back=None):
        for child in self.frame.winfo_children():
            child.destroy()
        ttk.Label(self.frame, text=title, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 10))
        if back:
            self.button("Kembali", back, pady=(0, 10))

    def button(self, label, callback, *, parent=None, wide=False, pady=4):
        widget = ttk.Button(parent or self.frame, text=label, command=self.action(callback),
                            style="Menu.TButton" if wide else "TButton")
        widget.pack(anchor="w", fill="x" if wide else "none", pady=pady)
        return widget

    def menu(self, title, rows, back=None):
        self.heading(title, back)
        parent = scroll_area(self.frame, 340, expand=True) if len(rows) > 10 else self.frame
        for label, action in rows:
            self.button(label, action, parent=parent, wide=True, pady=(0, 4))

    def fields(self, specs):
        parent = scroll_area(self.frame, 300, expand=True) if len(specs) > 9 else self.frame
        body = ttk.Frame(parent)
        body.pack(fill="x")
        body.columnconfigure(1, weight=1)
        controls = []
        for row, (label, value, choices) in enumerate(specs):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            if choices:
                control = ttk.Combobox(body, values=choices, state="readonly")
                control.set(value or choices[0])
            else:
                control = ttk.Entry(body, show="*" if label in ("API key", "API secret", "Token bot") else "")
                control.insert(0, value)
            control.grid(row=row, column=1, sticky="ew", pady=4)
            controls.append(control)
        return controls

    def form(self, title, specs, save, back):
        self.heading(title, back)
        controls = self.fields(specs)
        self.button("Simpan", lambda: save(*(control.get().strip() for control in controls)), pady=12)
        return controls

    def background(self, status, work, done):
        result = Queue()
        status.configure(text="Sedang memproses…")

        def run():
            try:
                result.put((True, work()))
            except Exception as error:
                result.put((False, str(error)))

        def poll():
            if not status.winfo_exists():
                return
            try:
                ok, value = result.get_nowait()
            except Empty:
                self.root.after(75, poll)
                return
            status.configure(text="" if ok else value)
            self.action(lambda: done(ok, value))()

        threading.Thread(target=run, daemon=True).start()
        self.root.after(75, poll)


def scroll_area(parent, height, *, expand=False):
    box = ttk.Frame(parent)
    box.pack(fill="both", expand=expand)
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


def checklist(parent, labels, selected=()):
    """Return a getter, keeping every item reachable in long lists."""
    inner = scroll_area(parent, min(175, max(32, 26 * len(labels))))
    variables = {}
    for key, label in labels:
        var = variables[key] = tk.BooleanVar(inner, key in selected)
        ttk.Checkbutton(inner, text=label, variable=var).pack(anchor="w", pady=2)
    return lambda: [key for key, var in variables.items() if var.get()]
