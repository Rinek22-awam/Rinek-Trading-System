"""Named terminal paths and local metadata for MT5 accounts."""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import accounts, envfile

FILE = Path(envfile.FRONT) / "akun-mt5.env"
METADATA = Path(envfile.SYSTEM) / "data" / "akun-mt5.json"


@dataclass(frozen=True)
class Account:
    nama: str
    path: str


def load():
    rows = {}
    for key, value in accounts._values(str(FILE)).items():
        match = re.fullmatch(r"MT5_(\d+)_(NAMA|PATH)", key)
        if match:
            rows.setdefault(int(match[1]), {})[match[2]] = value
    result, names, paths = [], set(), set()
    for _, row in sorted(rows.items()):
        name, path = row.get("NAMA", ""), row.get("PATH", "")
        canonical = os.path.normcase(os.path.abspath(path))
        if not accounts.valid_name(name) or not path or name.casefold() in names or canonical in paths:
            raise ValueError("akun-mt5.env: setiap akun perlu nama dan path terminal yang unik")
        names.add(name.casefold())
        paths.add(canonical)
        result.append(Account(name, path))
    return result


def save(items):
    lines = ["# Login dilakukan manual di setiap terminal MT5."]
    for index, item in enumerate(items, 1):
        path = item.path.replace("\\", "/")
        lines += [f"MT5_{index}_NAMA={item.nama}", f'MT5_{index}_PATH="{path}"', ""]
    atomic_write(FILE, "\n".join(lines))


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def metadata():
    return json.loads(METADATA.read_text(encoding="utf-8")) if METADATA.exists() else {}


def set_metadata(name, **fields):
    data = metadata()
    data.setdefault(name.casefold(), {}).update(fields)
    atomic_write(METADATA, json.dumps(data, ensure_ascii=False, indent=2))
