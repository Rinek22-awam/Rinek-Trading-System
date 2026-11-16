"""Jurnal analisa CLI.

Tiap analisa skill dicatat di dua tempat:
1. `sistem/data/jurnal.jsonl`: catatan asli. Hanya ditambah, jadi selalu berhasil ditulis.
2. `jurnal/Jurnal CLI.xlsx`: tampilan Excel untuk dibaca user dan dikirim ke AI.
   Baris baru ditambahkan tanpa menyentuh kolom yang diisi user (Hasil, Catatan kamu, dan R yang diketik sendiri).
   Kalau file sedang dibuka di Excel, file tidak bisa disimpan. Baris yang tertinggal masuk pada
   sinkron berikutnya (analisa berikutnya atau menu Jurnal).

Mode uji fungsi tidak dicatat. Jurnal tidak boleh menggagalkan analisa: semua error di sini
hanya dicatat sebagai peringatan di log.
"""
import json
import logging
import os
import re
from datetime import datetime
from tempfile import NamedTemporaryFile

from functools import cache
from types import SimpleNamespace

from .settings import DATA, SYSTEM, WIB

FOLDER = SYSTEM.parent / "jurnal"
XLSX = FOLDER / "Jurnal CLI.xlsx"
OLD_NAME = "Jurnal LCI.xlsx"   # nama lama (salah ketik LCI), dipindah otomatis
LOG = DATA / "jurnal.jsonl"
DELETED = DATA / "jurnal-terhapus.jsonl"   # cadangan analisa yang dihapus dari jurnal
HASIL = DATA / "hasil"                     # file jawaban mentah Claude per analisa

BOT = "Setup ke bot"
LOW = "Setup rendah"
NONE = "Tidak ada setup"
FAIL = "Gagal"
STATUSES = (BOT, LOW, NONE, FAIL)
MESSAGE_NOTE = "dengan pesan"   # cap analisa manual yang diberi pesan user (isi pesannya tidak dicatat di jurnal)
RESULTS = ("Tidak dieksekusi", "Tidak kebuka", "SL", "BE", "TP1", "TP2", "TP3", "TP4", "TP5", "Tutup manual")
DAYS = ("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min")
ANALYSIS_LIMIT = 32000          # batas isi satu sel Excel 32.767 karakter
FORMAT_ROWS = 10000             # jangkauan dropdown dan pewarnaan otomatis

# (kunci, judul kolom, lebar)
COLUMNS = [
    ("id", "ID", 18), ("tanggal", "Tanggal", 11), ("jam", "Jam", 7), ("hari", "Hari", 6),
    ("skill", "Skill", 14), ("model", "Model", 17), ("effort", "Effort", 8), ("status", "Status", 16),
    ("kode", "Kode", 7), ("pair", "Pair", 9), ("arah", "Arah", 7), ("order", "Order", 8),
    ("entry", "Entry", 10), ("entry2", "Entry2", 10), ("sl", "SL", 10),
    ("tp1", "TP1", 10), ("tp2", "TP2", 10), ("tp3", "TP3", 10), ("tp4", "TP4", 10), ("tp5", "TP5", 10),
    ("berlaku", "Berlaku", 19), ("batal", "Batal sebelum entry", 12),
    ("risk", "Risk (poin)", 10), ("rr1", "RR TP1", 8), ("rr_max", "RR TP terjauh", 9),
    ("conviction", "Conviction", 10), ("keterangan", "Keterangan", 48),
    ("durasi", "Durasi (detik)", 9), ("giliran", "Giliran", 8), ("tulis", "Token ditulis", 11),
    ("baca", "Token dibaca", 12), ("output", "Token output", 11), ("biaya", "Bobot API ($)", 10),
    ("file", "File hasil", 22),
    ("hasil", "Hasil", 15), ("r", "R didapat", 10), ("catatan", "Catatan kamu", 40),
]
USER_KEYS = ("hasil", "catatan")
def letter(number):
    """Nomor kolom jadi huruf Excel (1 = A, 27 = AA), tanpa perlu memuat openpyxl."""
    result = ""
    while number:
        number, rest = divmod(number - 1, 26)
        result = chr(65 + rest) + result
    return result


COL = {key: letter(index) for index, (key, _, _) in enumerate(COLUMNS, 1)}
FORMATS = {
    "tanggal": "yyyy-mm-dd", "jam": "hh:mm", "risk": "0.00", "rr1": "0.0", "rr_max": "0.0",
    "conviction": "0%", "durasi": "0", "giliran": "0", "tulis": "#,##0", "baca": "#,##0",
    "output": "#,##0", "biaya": "$0.00", "r": "0.00",
}
ANALYSIS_COLUMNS = [("ID", 18), ("Tanggal", 11), ("Jam", 7), ("Skill", 14), ("Model", 17),
                    ("Status", 16), ("Blok", 6), ("Teks analisa", 120)]


NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
FIELD = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ]*?)\s*:\s*(.*?)\s*$")
FIELD_KEYS = {
    "kode": "kode", "pair": "pair", "arah": "arah", "order": "order", "entry": "entry", "entry2": "entry2",
    "sl": "sl", "tp1": "tp1", "tp2": "tp2", "tp3": "tp3", "tp4": "tp4", "tp5": "tp5", "berlaku": "berlaku",
    "batal sebelum entry": "batal", "ke": "ke", "sumber": "sumber", "catatan": "catatan_blok",
}
PRICE_KEYS = ("entry", "entry2", "sl", "tp1", "tp2", "tp3", "tp4", "tp5")
CONVICTION = re.compile(r"(?i)conviction\b[^\n%]{0,60}?(\d{1,3})(?:[.,]\d+)?\s*%")


@cache
def _xl():
    """Pustaka Excel dan gaya sel. Dimuat sekali, dan hanya saat jurnal Excel dipakai
    (pesan rutin, pesan uji, dan cek kesiapan jadi tidak perlu menunggu openpyxl)."""
    from openpyxl import Workbook, load_workbook
    from openpyxl.comments import Comment
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    line = Side(style="thin", color="D9D9D9")
    return SimpleNamespace(
        Workbook=Workbook, load_workbook=load_workbook, Comment=Comment, FormulaRule=FormulaRule,
        Alignment=Alignment, Font=Font, PatternFill=PatternFill, DataValidation=DataValidation,
        FONT=Font(name="Arial", size=10),
        HEAD_FONT=Font(name="Arial", size=10, bold=True, color="FFFFFF"),
        USER_HEAD_FONT=Font(name="Arial", size=10, bold=True),
        HEAD_FILL=PatternFill("solid", fgColor="1F3864"),
        USER_HEAD_FILL=PatternFill("solid", fgColor="FFD966"),
        USER_FILL=PatternFill("solid", fgColor="FFF2CC"),
        BORDER=Border(left=line, right=line, top=line, bottom=line),
    )


class JournalLocked(Exception):
    """File Excel jurnal sedang dibuka program lain, jadi tidak bisa disimpan."""


class JournalError(Exception):
    """File Excel jurnal tidak bisa dibaca atau bentuknya sudah berubah."""


# ---------- membaca jawaban Claude ----------

def number(text):
    text = text.strip().replace(" ", "")
    text = text.replace(",", "") if "." in text else text.replace(",", ".")
    return float(text) if NUMBER.fullmatch(text) else None


def parse_block(block):
    """Isi blok SETUP jadi kamus. Harga jadi angka; tanpa baris Order berarti limit."""
    fields = {}
    for line in block.splitlines()[1:]:
        match = FIELD.match(line)
        key = FIELD_KEYS.get(match.group(1).lower()) if match else None
        if key:
            value = match.group(2)
            if key in PRICE_KEYS:
                value = number(value) if number(value) is not None else value
            fields[key] = value
    fields["arah"] = str(fields.get("arah", "")).upper()
    fields["order"] = str(fields.get("order") or "LIMIT").upper()
    return fields


def convictions(answer, blocks):
    """Angka conviction (persen) untuk tiap blok, dicari di teks analisa di luar blok.

    Dipasangkan urut kalau jumlahnya sama dengan jumlah blok. Satu blok: angka pertama.
    Selain itu kosong, karena tidak jelas angka mana milik blok mana.
    """
    text = answer
    for block in blocks:
        text = text.replace(block, "")
    found = [int(m.group(1)) for m in CONVICTION.finditer(text) if 0 < int(m.group(1)) <= 100]
    if len(found) == len(blocks):
        result = found
    elif len(blocks) == 1 and found:
        result = found[:1]
    else:
        result = [None] * len(blocks)
    # Cadangan: conviction yang (melanggar aturan) ditulis di dalam blok.
    for index, block in enumerate(blocks):
        if result[index] is None:
            inside = CONVICTION.search(block)
            result[index] = int(inside.group(1)) if inside and 0 < int(inside.group(1)) <= 100 else None
    return result


def build_entry(config, status, answer="", blocks=(), low=(), detail="", meta=None, started=None):
    meta = dict(meta or {})
    started = started or datetime.now(WIB)
    stem = os.path.splitext(meta.get("file") or "")[0]
    every = [*blocks, *low]
    scores = convictions(answer, every) if every else []
    items = [{"jenis": "bot" if index < len(blocks) else "rendah", "isi": parse_block(block),
              "conviction": scores[index]} for index, block in enumerate(every)]
    return {
        "id": stem or started.strftime("%Y%m%d-%H%M%S"),
        "waktu": started.isoformat(timespec="seconds"),
        "skill": config.skill_name,
        "model": meta.get("model") or config.model,
        "effort": config.effort or "bawaan",
        "kode": config.setup_code,
        "status": status,
        "keterangan": " ".join(str(detail).split())[:500],
        "pesan": bool(getattr(config, "message", "")),   # cap saja; isi pesan ada di file hasil
        "meta": meta,
        "blok": items,
        "analisa": answer or "",
    }


# ---------- catatan asli (jsonl) ----------

def append_log(entry):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_entries():
    if not LOG.is_file():
        return []
    entries = []
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue   # baris rusak (misal listrik mati saat menulis) dilewati
        if isinstance(entry, dict) and entry.get("id"):
            entries.append(entry)
    return entries


def record(config, status, answer="", blocks=(), low=(), detail="", meta=None, started=None):
    """Catat satu analisa. Tidak pernah melempar error ke pemanggil."""
    if not config.skill_name:
        return False   # mode uji fungsi tidak masuk jurnal
    try:
        meta = meta if meta is not None else getattr(answer, "meta", None)
        append_log(build_entry(config, status, str(answer or ""), blocks, low, detail, meta, started))
    except Exception as exc:
        logging.warning("Jurnal gagal dicatat (%s).", type(exc).__name__)
        return False
    try:
        sync()
    except JournalLocked:
        logging.warning("Jurnal Excel sedang dibuka. Analisa ini tetap tercatat dan masuk ke Excel "
                        "setelah file ditutup (analisa berikutnya atau menu Jurnal).")
    except Exception as exc:
        logging.warning("Jurnal Excel belum bisa diperbarui (%s). Catatan aslinya aman di data/jurnal.jsonl.",
                        exc if isinstance(exc, JournalError) else type(exc).__name__)
    return True


# ---------- baris Excel ----------

def rows_for(entry):
    """Satu baris per blok setup. Analisa tanpa blok tetap jadi satu baris."""
    when = datetime.fromisoformat(entry["waktu"])
    meta = entry.get("meta") or {}
    base = {
        "tanggal": when.date(), "jam": when.time().replace(second=0, microsecond=0),
        "hari": DAYS[when.weekday()], "skill": entry.get("skill", ""), "model": entry.get("model", ""),
        "effort": entry.get("effort", ""), "kode": entry.get("kode", ""),
    }
    usage = {key: meta.get(key) for key in ("durasi", "giliran", "tulis", "baca", "output", "biaya", "file")}
    items = entry.get("blok") or [None]
    rows = []
    for index, item in enumerate(items, 1):
        row = dict(base, id=f"{entry['id']}/{index}")
        if item is None:
            row.update(status=entry.get("status", ""), keterangan=entry.get("keterangan", ""))
        else:
            fields = item.get("isi") or {}
            row.update({key: fields.get(key) for key in ("kode", "pair", "arah", "order", *PRICE_KEYS,
                                                          "berlaku", "batal")})
            row["kode"] = fields.get("kode") or base["kode"]
            row["status"] = BOT if item.get("jenis") == "bot" else LOW
            score = item.get("conviction")
            row["conviction"] = score / 100 if isinstance(score, (int, float)) else None
            # Kalau analisa gagal di tengah jalan, blok yang sudah terkirim tetap dicatat beserta alasannya.
            row["keterangan"] = entry.get("keterangan", "") if entry.get("status") == FAIL else ""
        if entry.get("pesan"):
            row["keterangan"] = " · ".join(filter(None, (MESSAGE_NOTE, row.get("keterangan"))))
        if index == 1:
            row.update(usage)   # pemakaian token sekali per analisa, supaya tidak terhitung dobel
        rows.append(row)
    return rows


def formulas(row):
    c = COL
    return {
        "risk": f'=IF(AND(ISNUMBER({c["entry"]}{row}),ISNUMBER({c["sl"]}{row})),'
                f'ABS({c["entry"]}{row}-{c["sl"]}{row}),"")',
        "rr1": f'=IF(AND(N({c["risk"]}{row})>0,ISNUMBER({c["tp1"]}{row})),'
               f'ABS({c["tp1"]}{row}-{c["entry"]}{row})/{c["risk"]}{row},"")',
        "rr_max": f'=IF(AND(N({c["risk"]}{row})>0,COUNT({c["tp1"]}{row}:{c["tp5"]}{row})>0),'
                  f'MAX(ABS(MAX({c["tp1"]}{row}:{c["tp5"]}{row})-{c["entry"]}{row}),'
                  f'ABS(MIN({c["tp1"]}{row}:{c["tp5"]}{row})-{c["entry"]}{row}))/{c["risk"]}{row},"")',
        "r": r_formula(row),
    }


def r_formula(row):
    """R didapat dari Hasil: TPn = jarak Entry ke TPn dibagi Risk, SL = -1, BE = 0, lainnya kosong."""
    c = COL
    hasil, risk, entry = f'{c["hasil"]}{row}', f'{c["risk"]}{row}', f'{c["entry"]}{row}'
    target = f'INDEX({c["tp1"]}{row}:{c["tp5"]}{row},VALUE(MID({hasil},3,1)))'
    return (f'=IF({hasil}="SL",-1,IF({hasil}="BE",0,IF(AND(LEFT({hasil},2)="TP",N({risk})>0),'
            f'IFERROR(IF(ISNUMBER({target}),ABS({target}-{entry})/{risk},""),""),"")))')


def r_value(hasil, fields):
    """Hitungan yang sama dengan r_formula, untuk menu Ringkasan (openpyxl tidak menghitung rumus)."""
    hasil = str(hasil or "")
    if hasil == "SL":
        return -1.0
    if hasil == "BE":
        return 0.0
    entry, sl = fields.get("entry"), fields.get("sl")
    target = fields.get(hasil.lower()) if re.fullmatch(r"TP[1-5]", hasil) else None
    if not all(isinstance(v, (int, float)) for v in (entry, sl, target)) or entry == sl:
        return None
    return abs(target - entry) / abs(entry - sl)


def append_row(sheet, values):
    xl = _xl()
    row = sheet.max_row + 1
    values = {**values, **formulas(row)}
    for index, (key, _, _) in enumerate(COLUMNS, 1):
        cell = sheet.cell(row=row, column=index, value=values.get(key))
        cell.font = xl.FONT
        cell.border = xl.BORDER
        if key in FORMATS:
            cell.number_format = FORMATS[key]
        if key in USER_KEYS:
            cell.fill = xl.USER_FILL
        if key == "keterangan":
            cell.alignment = xl.Alignment(wrap_text=False, vertical="top")


def append_analysis(sheet, entry):
    xl = _xl()
    when = datetime.fromisoformat(entry["waktu"])
    text = entry.get("analisa") or entry.get("keterangan") or ""
    if entry.get("pesan"):
        text = f"[{MESSAGE_NOTE}]\n{text}"
    if len(text) > ANALYSIS_LIMIT:
        text = text[:ANALYSIS_LIMIT] + "\n[... dipotong, teks lengkap ada di data/hasil]"
    values = [entry["id"], when.date(), when.time().replace(second=0, microsecond=0), entry.get("skill", ""),
              entry.get("model", ""), entry.get("status", ""), len(entry.get("blok") or []), text]
    row = sheet.max_row + 1
    for index, value in enumerate(values, 1):
        cell = sheet.cell(row=row, column=index, value=value)
        cell.font = xl.FONT
        cell.border = xl.BORDER
        cell.alignment = xl.Alignment(vertical="top", wrap_text=index == len(values))
    sheet.cell(row=row, column=2).number_format = "yyyy-mm-dd"
    sheet.cell(row=row, column=3).number_format = "hh:mm"
    sheet.row_dimensions[row].height = 60


def header(sheet, titles, user=()):
    xl = _xl()
    for index, (title, width) in enumerate(titles, 1):
        cell = sheet.cell(row=1, column=index, value=title)
        mine = title in user
        cell.font = xl.USER_HEAD_FONT if mine else xl.HEAD_FONT
        cell.fill = xl.USER_HEAD_FILL if mine else xl.HEAD_FILL
        cell.border = xl.BORDER
        cell.alignment = xl.Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[letter(index)].width = width
    sheet.row_dimensions[1].height = 30


def new_workbook():
    xl = _xl()
    book = xl.Workbook()
    book.remove(book.active)
    book.create_sheet("Ringkasan")
    sheet = book.create_sheet("Jurnal")
    user_titles = {title for key, title, _ in COLUMNS if key in USER_KEYS}
    header(sheet, [(title, width) for _, title, width in COLUMNS], user_titles)
    for key in USER_KEYS:
        sheet[f"{COL[key]}1"].comment = xl.Comment("Diisi kamu. Kolom lain diisi program.", "CLI")
    sheet[f"{COL['r']}1"].comment = xl.Comment(R_NOTE, "CLI")
    sheet[f"{COL['biaya']}1"].comment = xl.Comment(
        "Perkiraan biaya kalau dibayar lewat API. Bukan tagihan: analisa memakai jatah langganan. "
        "Berguna untuk membandingkan berat-ringan analisa.", "CLI")
    sheet.freeze_panes = "E2"
    last = FORMAT_ROWS
    choices = xl.DataValidation(type="list", formula1='"' + ",".join(RESULTS) + '"', allow_blank=True,
                             showErrorMessage=True, errorTitle="Hasil",
                             error="Pilih dari daftar: " + ", ".join(RESULTS))
    choices.add(f"{COL['hasil']}2:{COL['hasil']}{last}")
    sheet.add_data_validation(choices)
    colors = {BOT: "E2EFDA", LOW: "FCE4D6", NONE: "EDEDED", FAIL: "F8CBAD"}
    status = COL["status"]
    for name, color in colors.items():
        sheet.conditional_formatting.add(
            f"{status}2:{status}{last}",
            xl.FormulaRule(formula=[f'${status}2="{name}"'], fill=xl.PatternFill("solid", fgColor=color)))
    side = COL["arah"]
    sheet.conditional_formatting.add(f"{side}2:{side}{last}", xl.FormulaRule(
        formula=[f'${side}2="BUY"'], font=xl.Font(name="Arial", size=10, bold=True, color="008000")))
    sheet.conditional_formatting.add(f"{side}2:{side}{last}", xl.FormulaRule(
        formula=[f'${side}2="SELL"'], font=xl.Font(name="Arial", size=10, bold=True, color="C00000")))
    result = COL["hasil"]
    sheet.conditional_formatting.add(f"{result}2:{result}{last}", xl.FormulaRule(
        formula=[f'LEFT(${result}2,2)="TP"'], fill=xl.PatternFill("solid", fgColor="C6EFCE")))
    sheet.conditional_formatting.add(f"{result}2:{result}{last}", xl.FormulaRule(
        formula=[f'${result}2="SL"'], fill=xl.PatternFill("solid", fgColor="FFC7CE")))
    analysis = book.create_sheet("Analisa")
    header(analysis, ANALYSIS_COLUMNS)
    analysis.freeze_panes = "B2"
    return book


# ---------- lembar Ringkasan (dibuat ulang tiap sinkron) ----------

R_NOTE = ("Otomatis dari Hasil: TPn = jarak Entry ke TPn dibagi Risk, SL = -1, BE = 0. Anggapan: seluruh posisi "
          "ditutup di TP itu. Kalau ada partial atau tutup manual, ketik angka R sendiri di atas rumusnya.")


def upgrade(sheet):
    """Pasang rumus R didapat di baris lama yang kolom R-nya masih kosong. Angka ketikan user tidak disentuh."""
    xl = _xl()
    column = [key for key, _, _ in COLUMNS].index("r") + 1
    head = sheet.cell(row=1, column=column)
    changed = 0
    if head.fill.fgColor.rgb != xl.HEAD_FILL.fgColor.rgb:
        head.font, head.fill = xl.HEAD_FONT, xl.HEAD_FILL
        head.comment = xl.Comment(R_NOTE, "CLI")
        changed += 1
    for row in range(2, sheet.max_row + 1):
        if sheet.cell(row=row, column=1).value is None:
            continue
        cell = sheet.cell(row=row, column=column)
        if cell.value is None:
            cell.value = r_formula(row)
            cell.fill = xl.PatternFill()
            cell.number_format = FORMATS["r"]
            changed += 1
    return changed


def summary_sheet(book, skills):
    xl = _xl()
    if "Ringkasan" in book.sheetnames:
        book.remove(book["Ringkasan"])
    sheet = book.create_sheet("Ringkasan", 0)
    book.active = 0
    j = "Jurnal!"
    status = f"{j}${COL['status']}:${COL['status']}"
    skill = f"{j}${COL['skill']}:${COL['skill']}"
    result = f"{j}${COL['hasil']}:${COL['hasil']}"
    gained = f"{j}${COL['r']}:${COL['r']}"
    bold = xl.Font(name="Arial", size=10, bold=True)
    sheet["A1"] = "Ringkasan Jurnal CLI"
    sheet["A1"].font = xl.Font(name="Arial", size=14, bold=True)
    sheet["A2"] = "Lembar ini dibuat ulang otomatis tiap jurnal diperbarui. Jangan diisi; isian kamu ada di lembar Jurnal."
    sheet["A2"].font = xl.Font(name="Arial", size=9, italic=True, color="595959")

    rows = [
        ("Analisa", None, None),
        ("Jumlah analisa", "=COUNTA(Analisa!$A:$A)-1", "0"),
        ("Blok setup ke bot", f'=COUNTIF({status},"{BOT}")', "0"),
        ("Setup rendah (ke chat pribadi)", f'=COUNTIF({status},"{LOW}")', "0"),
        ("Tidak ada setup", f'=COUNTIF({status},"{NONE}")', "0"),
        ("Gagal", f'=COUNTIF({status},"{FAIL}")', "0"),
        (None, None, None),
        ("Hasil trade (dari kolom Hasil yang kamu isi)", None, None),
        ("Kena TP (TP1 sampai TP5)", f'=COUNTIF({result},"TP*")', "0"),
        ("Kena SL", f'=COUNTIF({result},"SL")', "0"),
        ("BE", f'=COUNTIF({result},"BE")', "0"),
        ("Tutup manual", f'=COUNTIF({result},"Tutup manual")', "0"),
        ("Tidak kebuka / tidak dieksekusi",
         f'=COUNTIF({result},"Tidak kebuka")+COUNTIF({result},"Tidak dieksekusi")', "0"),
        ("Win rate (TP dibagi TP+SL+BE)", "=IFERROR(B12/(B12+B13+B14),\"\")", "0%"),
        ("Total R", f"=SUM({gained})", "0.00"),
        ("Rata-rata R per trade berisi R", f'=IFERROR(AVERAGE({gained}),"")', "0.00"),
    ]
    for offset, (label, formula, fmt) in enumerate(rows, 4):
        if label is None:
            continue
        cell = sheet.cell(row=offset, column=1, value=label)
        cell.font = bold if formula is None else xl.FONT
        if formula is not None:
            value = sheet.cell(row=offset, column=2, value=formula)
            value.font = xl.FONT
            value.number_format = fmt

    top = 4 + len(rows) + 1
    titles = ["Skill", "Analisa", "Setup ke bot", "Setup rendah", "Tidak ada setup", "Gagal", "TP", "SL", "Total R"]
    for index, title in enumerate(titles, 1):
        cell = sheet.cell(row=top, column=index, value=title)
        cell.font = xl.HEAD_FONT
        cell.fill = xl.HEAD_FILL
        cell.alignment = xl.Alignment(horizontal="center", wrap_text=True)
    for offset, name in enumerate(skills, top + 1):
        key = f"$A{offset}"
        values = [
            name,
            f"=COUNTIF(Analisa!$D:$D,{key})",
            f'=COUNTIFS({skill},{key},{status},"{BOT}")',
            f'=COUNTIFS({skill},{key},{status},"{LOW}")',
            f'=COUNTIFS({skill},{key},{status},"{NONE}")',
            f'=COUNTIFS({skill},{key},{status},"{FAIL}")',
            f'=COUNTIFS({skill},{key},{result},"TP*")',
            f'=COUNTIFS({skill},{key},{result},"SL")',
            f"=SUMIFS({gained},{skill},{key})",
        ]
        for index, value in enumerate(values, 1):
            cell = sheet.cell(row=offset, column=index, value=value)
            cell.font = xl.FONT
            cell.number_format = "0.00" if index == len(values) else "0"

    guide = top + len(skills) + 2
    notes = [
        ("Petunjuk", True),
        ("Kolom kuning di lembar Jurnal (Hasil, Catatan kamu) diisi kamu. Kolom lain diisi program, "
         "jangan diubah.", False),
        ("R didapat keluar otomatis begitu Hasil dipilih: TPn = jarak Entry ke TPn dibagi Risk, SL = -1, BE = 0.", False),
        ("Itu menganggap seluruh posisi ditutup di TP itu. Kalau ada partial atau tutup manual, ketik R sendiri "
         "di atas rumusnya, misal 1.3.", False),
        ("Contoh isian: Hasil = TP2 (R keluar sendiri) · Catatan kamu = TP1 kena, sisa kena BE.", False),
        ("Hasil dipilih dari daftar: " + ", ".join(RESULTS) + ".", False),
        ("Satu baris = satu blok setup. Analisa tanpa setup atau gagal tetap jadi satu baris.", False),
        ("Durasi, token, dan Bobot API hanya diisi di baris pertama tiap analisa, supaya tidak terhitung dobel.", False),
        ("Bobot API ($) = perkiraan biaya kalau lewat API, bukan tagihan. Dipakai untuk membandingkan berat analisa.", False),
        ("Teks analisa lengkap ada di lembar Analisa (cocokkan lewat ID). Kirim file ini ke AI untuk evaluasi.", False),
    ]
    for offset, (text, strong) in enumerate(notes, guide):
        cell = sheet.cell(row=offset, column=1, value=text)
        cell.font = bold if strong else xl.FONT
    sheet.column_dimensions["A"].width = 44
    for letter in "BCDEFGHI":
        sheet.column_dimensions[letter].width = 13
    return sheet


# ---------- sinkron ke Excel ----------

def open_book():
    xl = _xl()
    if not XLSX.exists():
        return new_workbook(), True
    try:
        book = xl.load_workbook(XLSX)
    except PermissionError:
        raise JournalLocked() from None
    except Exception:
        raise JournalError("File Excel jurnal tidak bisa dibaca. Ganti namanya atau pindahkan, "
                           "nanti dibuat ulang dari catatan asli.") from None
    if "Jurnal" not in book.sheetnames or "Analisa" not in book.sheetnames:
        raise JournalError("Lembar Jurnal atau Analisa di file Excel hilang atau diganti nama.")
    return book, False


def save_book(book):
    FOLDER.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=FOLDER, prefix="~simpan-", suffix=".xlsx", delete=False) as temp:
        name = temp.name
    try:
        book.save(name)
        os.replace(name, XLSX)
    except PermissionError:
        raise JournalLocked() from None
    finally:
        if os.path.exists(name):
            os.remove(name)


def existing(sheet):
    return {str(cell.value) for cell in sheet["A"][1:] if cell.value is not None}


def rename_old_file():
    """Jurnal dengan nama lama dipindah ke nama baru, isinya (termasuk isian user) tetap."""
    old = XLSX.with_name(OLD_NAME)
    if old.exists() and not XLSX.exists():
        try:
            old.rename(XLSX)
        except OSError:
            pass   # sedang dibuka Excel: dicoba lagi lain kali


def read_excel():
    """Satu kali baca Excel (tanpa mengubah apa pun).

    Mengembalikan ({id baris: (Hasil, R didapat)}, {ID analisa yang sudah masuk}). R berupa angka hanya kalau
    diketik user; selain itu rumus atau kosong. Set ID = None kalau file tidak bisa dibaca.
    """
    rename_old_file()
    if not XLSX.exists():
        return {}, set()
    xl = _xl()
    try:
        book = xl.load_workbook(XLSX, read_only=True)
        try:
            keys = [key for key, _, _ in COLUMNS]
            result, gained = keys.index("hasil"), keys.index("r")
            results = {}
            for row in book["Jurnal"].iter_rows(min_row=2, values_only=True):
                if row and row[0] is not None and len(row) > gained:
                    results[str(row[0])] = (row[result], row[gained])
            have = {str(row[0]) for row in book["Analisa"].iter_rows(min_row=2, max_col=1, values_only=True)
                    if row[0] is not None}
        finally:
            book.close()
    except Exception:
        return {}, None
    return results, have


def pending(snapshot=None):
    """Jumlah analisa di catatan asli yang belum ada di Excel (misal karena Excel sedang dibuka)."""
    entries = read_entries()
    if not entries:
        return 0
    have = (snapshot or read_excel())[1]
    if have is None:
        return len(entries)
    return sum(entry["id"] not in have for entry in entries)


def sync():
    """Tambahkan analisa yang belum ada ke Excel. Mengembalikan jumlah analisa baru."""
    rename_old_file()
    entries = read_entries()
    if not entries:
        return 0
    book, created = open_book()
    journal, analysis = book["Jurnal"], book["Analisa"]
    have_rows, have_analyses = existing(journal), existing(analysis)
    added = 0
    changed = 0 if created else upgrade(journal)
    for entry in entries:
        for row in rows_for(entry):
            if row["id"] not in have_rows:
                append_row(journal, row)
        if entry["id"] not in have_analyses:
            append_analysis(analysis, entry)
            added += 1
    if not added and not created and not changed:
        return 0
    finish(book)
    return added


def finish(book):
    """Rapikan filter, buat ulang Ringkasan, lalu simpan."""
    journal = book["Jurnal"]
    journal.auto_filter.ref = f"A1:{letter(len(COLUMNS))}{journal.max_row}"
    skill_col = [key for key, _, _ in COLUMNS].index("skill") + 1
    skills = sorted({str(row[0]) for row in journal.iter_rows(min_row=2, min_col=skill_col, max_col=skill_col,
                                                              values_only=True) if row[0]})
    summary_sheet(book, skills)
    book.calculation.fullCalcOnLoad = True   # rumus dihitung Excel saat file dibuka
    save_book(book)


# ---------- hapus analisa dan reset jurnal ----------

def ensure_closed():
    """Tolak kalau file Excel jurnal sedang dibuka (Excel mengunci file yang dibukanya)."""
    if not XLSX.exists():
        return
    try:
        with open(XLSX, "r+b"):
            pass
    except PermissionError:
        raise JournalLocked() from None


def result_file(entry):
    """File hasil analisa (jawaban mentah Claude) milik satu analisa, kalau masih ada."""
    name = (entry.get("meta") or {}).get("file")   # kosong = analisa gagal sebelum Claude menjawab
    path = HASIL / os.path.basename(name) if name else None
    return path if path and path.is_file() else None


def move_file(path, folder):
    """Pindahkan file ke folder (dibuat kalau belum ada) tanpa menimpa file yang sudah ada di sana."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    if target.exists():
        target = folder / f"{path.stem}-{datetime.now(WIB):%H%M%S%f}{path.suffix}"
    os.replace(path, target)
    return target


def write_entries(entries):
    """Tulis ulang catatan asli (lewat file sementara, supaya tidak pernah setengah tertulis)."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=LOG.parent, prefix="~jurnal-", suffix=".jsonl", delete=False,
                            encoding="utf-8", newline="\n") as temp:
        temp.writelines(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
    os.replace(temp.name, LOG)


def renumber_formulas(sheet):
    """Setelah baris dihapus, baris di bawahnya naik. Rumus (Risk, RR, R) ditulis ulang supaya menunjuk
    barisnya sendiri. R yang diketik user (angka) tidak disentuh."""
    keys = [key for key, _, _ in COLUMNS]
    for row in range(2, sheet.max_row + 1):
        if sheet.cell(row=row, column=1).value is None:
            continue
        for key, formula in formulas(row).items():
            cell = sheet.cell(row=row, column=keys.index(key) + 1)
            if key != "r" or cell.value is None or str(cell.value).startswith("="):
                cell.value = formula


def remove_rows(sheet, match):
    """Hapus baris (dari bawah) yang ID di kolom A-nya cocok. Mengembalikan jumlah baris terhapus."""
    rows = [row for row in range(2, sheet.max_row + 1)
            if sheet.cell(row=row, column=1).value is not None and match(str(sheet.cell(row=row, column=1).value))]
    for row in reversed(rows):
        sheet.delete_rows(row)
    return len(rows)


def delete_entries(ids):
    """Hapus analisa dari jurnal (catatan asli + Excel). Cadangan masuk ke jurnal-terhapus.jsonl dan file hasilnya
    dipindah ke data/hasil/terhapus. Mengembalikan daftar analisa yang dihapus."""
    ids = set(ids)
    entries = read_entries()
    chosen = [entry for entry in entries if entry["id"] in ids]
    if not chosen:
        raise JournalError("Analisa yang dipilih tidak ditemukan di jurnal.")
    ensure_closed()
    # 1. Cadangan dulu: kalau langkah berikutnya gagal, data tetap aman (paling banter tercatat dobel di cadangan).
    DELETED.parent.mkdir(parents=True, exist_ok=True)
    with DELETED.open("a", encoding="utf-8", newline="\n") as file:
        for entry in chosen:
            file.write(json.dumps(dict(entry, dihapus=datetime.now(WIB).isoformat(timespec="seconds")),
                                  ensure_ascii=False) + "\n")
    # 2. Excel. Gagal simpan (file dibuka) = berhenti di sini, catatan asli belum diubah.
    if XLSX.exists():
        book, _ = open_book()
        removed = remove_rows(book["Jurnal"], lambda value: value.split("/")[0] in ids)
        removed += remove_rows(book["Analisa"], lambda value: value in ids)
        if removed:
            renumber_formulas(book["Jurnal"])
            finish(book)
    # 3. Catatan asli, lalu 4. file hasil.
    write_entries([entry for entry in entries if entry["id"] not in ids])
    for entry in chosen:
        path = result_file(entry)
        if path:
            move_file(path, HASIL / "terhapus")
    return chosen


def reset_counts():
    """(jumlah analisa, jumlah baris yang Hasil-nya sudah diisi user) untuk layar konfirmasi reset."""
    results = user_results()
    return len(read_entries()), sum(1 for hasil, _ in results.values() if hasil)


def reset():
    """Jurnal mulai dari nol. Semua data lama dipindah ke jurnal/arsip/<tanggal-jam>/, tidak dihapus.
    Mengembalikan folder arsip (None kalau tidak ada yang perlu diarsipkan)."""
    ensure_closed()
    entries = read_entries()
    files = [path for path in (result_file(entry) for entry in entries) if path]
    old = [path for path in (XLSX, XLSX.with_name(OLD_NAME), LOG, DELETED) if path.exists()]
    trash = HASIL / "terhapus"
    if not old and not files and not trash.is_dir():
        return None
    archive = FOLDER / "arsip" / datetime.now(WIB).strftime("%Y%m%d-%H%M%S")
    if archive.exists():
        archive = archive.with_name(archive.name + datetime.now(WIB).strftime("-%f"))
    archive.mkdir(parents=True)
    for path in old:
        move_file(path, archive)
    for path in files:
        move_file(path, archive / "hasil")
    if trash.is_dir():
        os.replace(trash, archive / "hasil-terhapus")
    return archive


# ---------- teks untuk menu ----------

def user_results():
    """{id baris: (Hasil, R didapat)} dari Excel."""
    return read_excel()[0]


def describe(entry):
    when = entry.get("waktu", "")[:16].replace("T", " ")
    head = f"{when} · {entry.get('skill', '')} · {entry.get('status', '')}"
    if entry.get("pesan"):
        head += f" · {MESSAGE_NOTE}"
    items = entry.get("blok") or []
    if not items:
        note = entry.get("keterangan", "")
        return f"{head} · {note[:90]}" if note else head
    parts = []
    for item in items:
        fields = item.get("isi") or {}
        price = fields.get("entry", "market")
        text = f"{fields.get('arah', '?')} {fields.get('order', '')} {price} SL {fields.get('sl', '?')}"
        if item.get("conviction"):
            text += f" ({item['conviction']}%)"
        if item.get("jenis") == "rendah":
            text += " [rendah]"
        parts.append(text)
    return f"{head} · " + " | ".join(parts)


def recent_text(limit=15):
    entries = read_entries()
    if not entries:
        return "Jurnal masih kosong. Analisa skill berikutnya akan tercatat otomatis."
    lines = [describe(entry) for entry in entries[-limit:]]
    return "\n".join(reversed(lines))


def summary_numbers():
    """Angka ringkasan jurnal (dipakai menu Ringkasan dan Dashboard). None kalau jurnal kosong."""
    entries = read_entries()
    if not entries:
        return None
    rows = [row for entry in entries for row in rows_for(entry)]
    snapshot = read_excel()   # Excel dibaca sekali untuk hasil dan cek baris tertinggal
    results = snapshot[0]
    filled = {}
    for row in rows:
        if row["status"] in (BOT, LOW):
            hasil, typed = results.get(row["id"], (None, None))
            typed = typed if isinstance(typed, (int, float)) and not isinstance(typed, bool) else r_value(hasil, row)
            filled[row["id"]] = (hasil, typed)
    wins = sum(str(h or "").startswith("TP") for h, _ in filled.values())
    losses = sum(h == "SL" for h, _ in filled.values())
    even = sum(h == "BE" for h, _ in filled.values())
    skills = {}
    for skill in sorted({entry.get("skill", "") for entry in entries}):
        mine = [row for entry in entries if entry.get("skill") == skill for row in rows_for(entry)]
        skills[skill] = {
            "analisa": sum(entry.get("skill") == skill for entry in entries),
            **{name: sum(row["status"] == name for row in mine) for name in STATUSES},
        }
    return {
        "analisa": len(entries), "sejak": entries[0].get("waktu", "")[:10],
        **{name: sum(row["status"] == name for row in rows) for name in STATUSES},
        "tp": wins, "sl": losses, "be": even,
        "total_r": sum(r for _, r in filled.values() if isinstance(r, (int, float))),
        "winrate": wins / (wins + losses + even) if wins + losses + even else None,
        "kosong": sum(not h for h, _ in filled.values()),
        "tertinggal": pending(snapshot), "skill": skills, "hasil": filled,
    }


def summary_text():
    numbers = summary_numbers()
    if numbers is None:
        return "Jurnal masih kosong. Analisa skill berikutnya akan tercatat otomatis."
    lines = [f"{numbers['analisa']} analisa sejak {numbers['sejak']}",
             f"- {BOT}: {numbers[BOT]} blok · {LOW}: {numbers[LOW]} · {NONE}: {numbers[NONE]} · {FAIL}: {numbers[FAIL]}",
             "", "Per skill:"]
    for skill, mine in numbers["skill"].items():
        lines.append(f"- {skill}: {mine['analisa']} analisa · {mine[BOT]} setup ke bot · "
                     f"{mine[NONE]} tidak ada setup · {mine[FAIL]} gagal")
    lines += ["", "Hasil (dari kolom Hasil yang kamu isi di Excel):",
              f"- {numbers['tp']} TP · {numbers['sl']} SL · {numbers['be']} BE · total R {numbers['total_r']:+.2f}"]
    if numbers["winrate"] is not None:
        lines.append(f"- Win rate {numbers['winrate']:.0%} (TP dibagi TP+SL+BE)")
    lines.append(f"- {numbers['kosong']} setup belum diisi Hasil-nya")
    if numbers["tertinggal"]:
        lines.append(f"\n⚠️ {numbers['tertinggal']} analisa belum masuk Excel (file sempat dibuka). "
                     "Tutup Excel, lalu pilih Buka file Excel jurnal.")
    return "\n".join(lines)
