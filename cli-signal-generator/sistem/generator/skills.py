
import math
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import uuid4

import yaml

from . import settings
from .settings import EFFORTS, HOUR, MODEL_NAME, SKILL_NAME, WIB, GeneratorError

DAYS = ("sen", "sel", "rab", "kam", "jum", "sab", "min")
CODE = re.compile(r"[A-Za-z0-9_-]{1,20}")
MODES = ("jam", "interval")
MIN_INTERVAL = 15   # menit; satu analisa makan 2–5 menit, jadi jarak di bawah ini bikin antrean menumpuk
MAX_INTERVAL = 1440  # menit
TEMPLATE = """nyala: false  # Saklar untuk jadwal. Analisa manual tetap bisa.
jadwal:
  mode: jam  # jam = jam tertentu; interval = tiap sekian jam di antara mulai dan sampai.
  jam: []  # Mode jam. WIB, misal ["15:00", "20:30"]. null = ikut global.
  tiap_jam: 1  # Mode interval. Boleh pecahan, misal 0.5 = tiap 30 menit (minimal 15 menit).
  mulai: "20:00"  # Mode interval. Jam WIB pertama.
  sampai: "04:00"  # Mode interval. Boleh lewat tengah malam (20:00 sampai 04:00).
  hari: [sen, sel, rab, kam, jum]  # sen, sel, rab, kam, jum, sab, min. Mode interval: hari jam mulai.
model: null  # Kosong/null = model global. Nama lengkap mengunci versi.
effort: null  # null = setelan global; "" = bawaan Claude Code.
kode: TES  # Kode sumber di blok SETUP.
batas_waktu_menit: null  # Kosong/null = batas waktu global.
"""

def minutes(text):
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


@dataclass(frozen=True)
class Skill:
    config: settings.Config
    enabled: bool
    times: tuple[str, ...]
    days: tuple[str, ...]
    mode: str = "jam"
    every: int = 60          # menit, mode interval
    start: str = "00:00"     # mode interval
    end: str = "23:59"       # mode interval; lebih kecil dari start = lewat tengah malam
    use_global_times: bool = False

    def due(self, now):
        return self.enabled and now.strftime("%H:%M") in self.today(now)

    def today(self, now):
        """Jam-jam jalan (HH:MM) untuk tanggal `now`. Mode interval ikut menghitung jendela yang mulai kemarin."""
        if self.mode != "interval":
            return list(self.times) if DAYS[now.weekday()] in self.days else []
        start, end = minutes(self.start), minutes(self.end)
        length = (end - start) % 1440   # panjang jendela dalam menit (lewat tengah malam tetap benar)
        result = set()
        for shift, day in ((0, now), (-1440, now - timedelta(days=1))):
            if DAYS[day.weekday()] not in self.days:
                continue
            for offset in range(0, length + 1, self.every):
                moment = shift + start + offset
                if 0 <= moment < 1440:
                    result.add(f"{moment // 60:02d}:{moment % 60:02d}")
        return sorted(result)

    def slots(self):
        """Semua jam jalan dalam satu jendela, urut dari jam mulai (untuk ditampilkan)."""
        if self.mode != "interval":
            return list(self.times)
        start = minutes(self.start)
        length = (minutes(self.end) - start) % 1440
        return [f"{(start + offset) % 1440 // 60:02d}:{(start + offset) % 60:02d}"
                for offset in range(0, length + 1, self.every)]

    def describe(self):
        days = "Sen–Jum" if self.days == DAYS[:5] else ", ".join(self.days) or "tanpa hari"
        if self.mode == "interval":
            return f"tiap {self.every} menit {self.start}–{self.end} {days}"
        hours = ", ".join(self.times) or "tanpa jam"
        if self.use_global_times:
            hours += " (global)"
        return f"{hours} {days}"


def skill_file(name):
    if name not in settings.list_skills():
        raise GeneratorError("Skill tidak ditemukan.")
    path = settings.skills_dir() / name / "setelan.yaml"
    if not path.exists():
        settings.atomic_write(path, TEMPLATE)
    return path


def read_skill(name, base):
    path = skill_file(name)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        schedule = raw.get("jadwal", {})
        times = schedule.get("jam", [])
        use_global_times = times is None
        if use_global_times:
            times = list(base.skill_times)
        days = schedule.get("hari", list(DAYS[:5]))
        mode = schedule.get("mode") or "jam"
        every = interval_minutes(schedule.get("tiap_jam", 1))
        start = str(schedule.get("mulai") or "00:00")
        end = str(schedule.get("sampai") or "23:59")
        enabled = raw.get("nyala", False)
        model = raw.get("model") or base.model
        effort = raw.get("effort")
        effort = base.effort if effort is None else effort
        code = raw.get("kode") or settings.DEFAULT_SETUP_CODE
        minutes = raw.get("batas_waktu_menit")
        timeout = base.timeout if minutes is None else float(minutes) * 60
        if not isinstance(enabled, bool) or not isinstance(times, list) or not isinstance(days, list):
            raise ValueError
        if any(not HOUR.fullmatch(value) for value in times) or any(day not in DAYS for day in days):
            raise ValueError
        if mode not in MODES or every is None or not HOUR.fullmatch(start) or not HOUR.fullmatch(end):
            raise ValueError
        if not MODEL_NAME.fullmatch(model) or effort not in ("", *EFFORTS) or not CODE.fullmatch(code):
            raise ValueError
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
    except (OSError, TypeError, ValueError, AttributeError, yaml.YAMLError):
        raise GeneratorError(f"Setelan skill {name} tidak valid.") from None
    config = replace(base, skill=path.parent, skill_name=name, model=model, effort=effort,
                     setup_code=code, timeout=timeout)
    return Skill(config, enabled, tuple(dict.fromkeys(times)), tuple(dict.fromkeys(days)), mode, every, start, end,
                 use_global_times)


def parse_times(text):
    times = [part.strip() for part in text.split(",") if part.strip()]
    if any(not HOUR.fullmatch(part) for part in times):
        raise GeneratorError("Jam harus berbentuk HH:MM, dari 00:00 sampai 23:59.")
    return list(dict.fromkeys(times))


def save_global(model, effort, timeout, hours):
    model, effort = model.strip(), effort.strip().lower()
    if not MODEL_NAME.fullmatch(model):
        raise GeneratorError("Nama model tidak valid.")
    if effort not in ("", *EFFORTS):
        raise GeneratorError("Effort harus kosong atau salah satu dari " + ", ".join(EFFORTS) + ".")
    try:
        minutes = float(timeout.strip().replace(",", "."))
    except ValueError:
        minutes = 0
    if not math.isfinite(minutes * 60) or minutes <= 0:
        raise GeneratorError("Batas waktu harus berupa angka menit lebih besar dari nol.")
    times = parse_times(hours)
    if not times:
        raise GeneratorError("Isi minimal satu jam analisa global.")
    values = {"model": model, "effort": effort, "batas_waktu_menit": minutes, "jam_skill": times}
    settings.update_yaml(settings.SYSTEM / "config.yaml", {("claude", key): value for key, value in values.items()})


def interval_minutes(hours):
    """tiap_jam (boleh pecahan) jadi menit bulat. None kalau tidak valid atau di bawah batas minimal."""
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours):
        return None
    value = round(hours * 60)
    return value if MIN_INTERVAL <= value <= MAX_INTERVAL else None


def parse_interval(text):
    """Baca interval dalam menit bulat dari formulir jadwal."""
    if re.fullmatch(r"[0-9]{1,4}", text.strip()):
        value = int(text)
        if MIN_INTERVAL <= value <= MAX_INTERVAL:
            return value
    raise GeneratorError(f"Jarak harus {MIN_INTERVAL}–{MAX_INTERVAL} menit dalam angka bulat.")


def change_skill(name, changes):
    settings.update_yaml(skill_file(name), changes)


def toggle_skill(name, base):
    enabled = not read_skill(name, base).enabled
    change_skill(name, {("", "nyala"): enabled})
    return enabled


def change_code(name, code, confirm):
    if not CODE.fullmatch(code):
        raise GeneratorError("Kode harus 1–20 huruf, angka, garis bawah, atau tanda -.")
    if code != "TES" and confirm("Setup bisa jadi order beneran di Automanage. Ketik REAL: ") != "REAL":
        return False
    change_skill(name, {("", "kode"): code})
    return True


def add_skill(name):
    if not SKILL_NAME.fullmatch(name):
        raise GeneratorError("Nama hanya boleh huruf kecil, angka, dan tanda -.")
    root = settings.skills_dir()
    template = (settings.SYSTEM / "prompt" / "kerangka_skill.md").read_text(encoding="utf-8-sig")
    folder = root / name
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise GeneratorError("Nama itu sudah dipakai.") from None
    settings.atomic_write(folder / "SKILL.md", template.replace("nama-skill", name))
    settings.atomic_write(folder / "setelan.yaml", TEMPLATE)
    return folder


def archive_skill(name):
    if name not in settings.list_skills():
        raise GeneratorError("Skill tidak ditemukan.")
    folder = settings.skills_dir() / name
    archive = settings.skills_dir() / "_terhapus"
    archive.mkdir(exist_ok=True)
    if archive.resolve().parent != settings.skills_dir().resolve() or folder.resolve().parent != archive.resolve().parent:
        raise GeneratorError("Folder skill atau arsip berada di luar perpustakaan.")
    target = archive / f"{name}-{datetime.now(WIB):%Y%m%d-%H%M%S}"
    if target.exists():
        target = target.with_name(f"{target.name}-{uuid4().hex[:6]}")
    folder.rename(target)
    return target


def migrate():
    path = settings.SYSTEM / "config.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        old = raw.get("skill") or {}
    except (OSError, AttributeError, yaml.YAMLError):
        raise GeneratorError("Setelan tidak bisa dibaca. Cek config.yaml.") from None
    if not old and "uji_coba_jam" not in raw:
        return
    active = old.get("aktif", old.get("folder", ""))
    settings.read_config()
    name = settings.resolve_skill(active).name if active else ""
    if active and (name not in settings.list_skills() or
                   settings.resolve_skill(active).resolve() != (settings.skills_dir() / name).resolve()):
        raise GeneratorError("Skill aktif lama tidak ditemukan di folder skill. Pindahkan foldernya ke perpustakaan skill agar migrasi bisa berjalan.")
    backup = settings.SYSTEM / "data" / f"cadangan-setelan-{datetime.now(WIB):%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
    backup.mkdir(parents=True)
    shutil.copy2(path, backup / path.name)
    if name:
        changes = {("", "nyala"): True, ("", "kode"): str(old.get("kode") or "TES")}
        schedule = raw.get("uji_coba_jam") or {}
        if schedule.get("nyala"):
            changes["jadwal", "jam"] = schedule["jam"]
            changes["jadwal", "hari"] = list(DAYS)
        change_skill(name, changes)
    text = path.read_text(encoding="utf-8-sig")
    # Simpan komentar lama juga di file aktif, sebagai catatan migrasi.
    text = re.sub(r"(?m)^(?:skill|uji_coba_jam):[^\n]*(?:\n[ \t]+[^\n]*)*",
                  lambda match: "\n".join(f"# {line}" for line in match[0].splitlines()), text)
    settings.atomic_write(path, text)
