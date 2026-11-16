"""Tanda masih hidup.

1. Pesan rutin ke chat pribadi di jam yang diatur (bawaan 07:00). Cara bacanya terbalik:
   pesan tidak datang = generator mati (PC sleep, jendela tertutup, dll).
2. Detak "terakhir aktif": generator jalan terus menulis jamnya tiap menit, ditampilkan di menu.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

import yaml

from . import journal, settings, skills
from .settings import DATA, WIB, GeneratorError

DEFAULT_TIMES = ("07:00",)
PULSE = DATA / "detak.txt"
DAY_NAMES = ("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min")


@dataclass(frozen=True)
class Routine:
    enabled: bool
    times: tuple[str, ...]

    def describe(self):
        return f"NYALA · jam {', '.join(self.times)}" if self.enabled and self.times else "MATI"

    def due(self, now):
        return self.enabled and now.strftime("%H:%M") in self.times


def read():
    """Setelan `pesan_rutin` di config.yaml. Belum ada = nyala jam 07:00."""
    try:
        raw = yaml.safe_load((settings.SYSTEM / "config.yaml").read_text(encoding="utf-8-sig")) or {}
        section = raw.get("pesan_rutin")
        if section is None:
            return Routine(True, DEFAULT_TIMES)
        enabled = section.get("nyala", True)
        times = section.get("jam", list(DEFAULT_TIMES))
        if not isinstance(enabled, bool) or not isinstance(times, list):
            raise ValueError
        if any(not isinstance(value, str) or not skills.HOUR.fullmatch(value) for value in times):
            raise ValueError
    except (OSError, AttributeError, TypeError, ValueError, yaml.YAMLError):
        raise GeneratorError("Setelan pesan_rutin tidak valid. Cek config.yaml.") from None
    return Routine(enabled, tuple(sorted(dict.fromkeys(times))))


def save(**values):
    keys = {"enabled": "nyala", "times": "jam"}
    changes = {("pesan_rutin", keys[name]): value for name, value in values.items()}
    settings.update_yaml(settings.SYSTEM / "config.yaml", changes)


def next_run(skill, now):
    """Jam jalan berikutnya dalam 24 jam ke depan, sebagai teks ('20:00' atau 'besok 15:00'). '' kalau tidak ada."""
    current = now.strftime("%H:%M")
    later = [slot for slot in skill.today(now) if slot > current]
    if later:
        return later[0]
    tomorrow = skill.today(now + timedelta(days=1))
    return f"besok {tomorrow[0]}" if tomorrow else ""


def message(base, now):
    lines = [f"🟢 CLI masih nyala · {DAY_NAMES[now.weekday()]} {now:%d/%m %H:%M} WIB", ""]
    active = []
    for name in settings.list_skills():
        try:
            skill = skills.read_skill(name, base)
        except GeneratorError:
            lines.append(f"⚠️ Setelan skill {name} tidak valid.")
            continue
        if skill.enabled:
            active.append(skill)
    if active:
        lines.append("Jadwal analisa:")
        for skill in active:
            upcoming = next_run(skill, now)
            line = f"- {skill.config.skill_name} (kode {skill.config.setup_code}): {skill.describe()}"
            lines.append(line + (f" · berikutnya {upcoming}" if upcoming else " · tidak ada jadwal 24 jam ke depan"))
    else:
        lines.append("Belum ada skill NYALA, jadi tidak ada analisa terjadwal.")

    since = now - timedelta(hours=24)
    recent = []
    for entry in journal.read_entries():
        try:
            if datetime.fromisoformat(entry["waktu"]) >= since:
                recent.append(entry)
        except (KeyError, TypeError, ValueError):
            continue
    if recent:
        rows = [row for entry in recent for row in journal.rows_for(entry)]
        count = {name: sum(row["status"] == name for row in rows) for name in journal.STATUSES}
        lines += ["", f"24 jam terakhir: {len(recent)} analisa · {count[journal.BOT]} setup ke bot · "
                      f"{count[journal.NONE]} tidak ada setup · {count[journal.FAIL]} gagal"]
    else:
        lines += ["", "24 jam terakhir: belum ada analisa."]
    lines += ["", "Kalau pesan ini tidak datang di jam biasanya, berarti generator mati."]
    return "\n".join(lines)


def pulse(now):
    DATA.mkdir(parents=True, exist_ok=True)
    settings.atomic_write(PULSE, now.isoformat(timespec="seconds"))


def last_pulse():
    try:
        return datetime.fromisoformat(PULSE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def last_active_label(running, now=None):
    """Teks untuk baris status menu."""
    seen = last_pulse()
    if seen is None:
        return ""
    now = now or datetime.now(WIB)
    when = seen.strftime("%H:%M") if seen.date() == now.date() else seen.strftime("%d/%m %H:%M")
    if running and now - seen > timedelta(minutes=3):
        return f" (⚠️ terakhir aktif {when}, tidak berdetak)"
    return f" (terakhir aktif {when})"
