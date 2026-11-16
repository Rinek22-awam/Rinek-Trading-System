"""Letak file .env. User mengisinya di folder DEPAN (di samping MENU.bat); program ada di
folder sistem/. Dicari di depan dulu, lalu di folder sistem (susunan lama)."""
import os

SYSTEM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../sistem
FRONT = os.path.dirname(SYSTEM)                                         # folder MENU.bat


def env_path() -> str:
    for folder in (FRONT, SYSTEM):
        path = os.path.join(folder, ".env")
        if os.path.exists(path):
            return path
    return os.path.join(FRONT, ".env")


def load():
    from dotenv import load_dotenv
    load_dotenv(env_path())


def save_setting(key: str, value: str):
    """Simpan satu setelan dan pakai nilainya untuk program yang diluncurkan dari menu ini."""
    from dotenv import set_key

    set_key(env_path(), key, value, quote_mode="never", encoding="utf-8")
    os.environ[key] = value
