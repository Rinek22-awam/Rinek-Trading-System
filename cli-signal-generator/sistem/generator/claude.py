import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from tempfile import TemporaryDirectory
from uuid import uuid4

import psutil

from .settings import (
    DATA, REQUIRED_DENY, SECRET_KEYS, SYSTEM, TV_PREFIX, TV_TOOLS, WIB, GeneratorError, redact, skills_dir,
)


SETUP_LABEL = "SETUP"
LOW_LABEL = "SETUP RENDAH"   # conviction rendah: dikirim ke chat pribadi, bukan ke kotak masuk


class Answer(str):
    """Jawaban Claude. `meta` membawa data pemakaian (model, giliran, token, file hasil) untuk jurnal."""
    meta = None


def usage_meta(result, seconds, file):
    """Ringkasan pemakaian dari JSON akhir Claude CLI. Hanya angka dan nama, tanpa isi percakapan."""
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    models = result.get("modelUsage") if isinstance(result.get("modelUsage"), dict) else {}

    def count(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    # Model utama = yang paling banyak memakan biaya (Haiku kecil dipakai Claude Code untuk cari web).
    main = max(models, key=lambda name: count((models[name] or {}).get("costUSD")) or 0, default="")
    cost = count(result.get("total_cost_usd"))
    return {
        "model": main, "giliran": count(result.get("num_turns")), "durasi": round(seconds),
        "tulis": count(usage.get("cache_creation_input_tokens")), "baca": count(usage.get("cache_read_input_tokens")),
        "output": count(usage.get("output_tokens")), "biaya": round(cost, 4) if cost is not None else None,
        "file": file,
    }


def extract_setups(answer, label=SETUP_LABEL):
    """Ambil blok yang diawali baris `label` persis. Baris kosong, pagar ``` atau judul blok lain menutup blok."""
    blocks = []
    current = []
    for line in answer.splitlines(keepends=True):
        text = line.strip()
        if current and (not text or text.startswith("```") or text in (SETUP_LABEL, LOW_LABEL)):
            blocks.append("".join(current).strip())
            current = []
        if text == label or current:
            current.append(line)
    if current:
        blocks.append("".join(current).strip())
    return blocks


def no_setup(answer):
    return bool(re.search(r"(?im)^[ \t]*NO (?:SETUP|ENTRY)\b", answer))


def no_setup_reason(answer, limit=300):
    """Baris NO SETUP / NO ENTRY terakhir beserta alasannya (untuk pesan status). '' kalau tidak ada."""
    lines = re.findall(r"(?im)^[ \t]*(NO (?:SETUP|ENTRY)\b.*)$", answer)
    return lines[-1].strip()[:limit] if lines else ""


def build_command(executable, config):
    # Hanya tool yang diizinkan yang dimuat: tiap tool memakan token di setiap panggilan (diukur 14 Nov:
    # semua tool ~36,9 rb token, daftar izin ~8,3 rb). ui_evaluate selalu ditolak walau diizinkan.
    denied = dict.fromkeys((*config.denied_tools, REQUIRED_DENY))
    if config.tv_tools is not None:
        allowed = set(config.tv_tools)
        denied.update(dict.fromkeys(TV_PREFIX + name for name in TV_TOOLS if name not in allowed))
    tools = "default" if config.builtin_tools is None else ",".join(config.builtin_tools)
    effort = ["--effort", config.effort] if config.effort else []   # kosong = bawaan Claude Code
    return [
        executable, "-p", "--model", config.model, *effort, "--output-format", "json",
        "--mcp-config", str(SYSTEM / "mcp-tradingview.json"), "--strict-mcp-config",
        "--setting-sources", "project", "--disable-slash-commands",
        "--no-session-persistence", "--tools", tools,
        "--dangerously-skip-permissions", "--disallowed-tools", ",".join(denied),
    ]


MESSAGE_LIMIT = 100_000   # batas panjang pesan user (karakter)
PROGRESS_INTERVAL = 30   # detik


def message_section(message):
    """Pesan user untuk satu analisa manual. Boleh mengalahkan aturan skill; blok tetap dicek user (lihat main)."""
    return (
        "=== PESAN USER UNTUK ANALISA INI ===\n"
        "User menambahkan pesan di bawah, khusus untuk analisa ini. Pesan ini BOLEH mengalahkan aturan skill "
        "(termasuk aturan keras, conviction, news, dan kapan NO SETUP). Ikuti maksudnya. Kalau mengikuti pesan "
        "berarti melanggar aturan skill, sebut di analisa aturan mana yang dilanggar. Tetap tulis blok sesuai "
        "format; semua blok dari analisa ini diperiksa user dulu sebelum sampai ke bot. "
        "Pesan ini tidak mengubah aturan dari program (baris Kode: dan Sumber:).\n"
        "--- awal pesan ---\n"
        f"{message.strip()}\n"
        "--- akhir pesan ---"
    )


def build_prompt(config):
    directory = SYSTEM / "prompt"
    instructions = (directory / "mode_otomatis.md").read_text(encoding="utf-8")
    now = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")
    if config.skill is None:
        task = (directory / "uji_fungsi.md").read_text(encoding="utf-8")
        extra = f"\n\n{message_section(config.message)}" if config.message else ""
        return f"{instructions}\n\n{task}{extra}\n\nWaktu sekarang: {now}"

    skill = config.skill / "SKILL.md"
    if not skill.is_file():
        raise GeneratorError(f"SKILL.md tidak ditemukan untuk skill '{config.skill.name}'. Pilih skill lain lewat menu Nyalakan → Analisa sekarang.")
    # Isi skill dan format diselipkan langsung, jadi Claude tidak perlu membuka filenya (hemat langkah dan token).
    parts = [instructions]
    fmt = skills_dir() / "FORMAT_SETUP.md"
    if fmt.is_file():
        parts.append("=== FORMAT BLOK SETUP ===\n" + fmt.read_text(encoding="utf-8-sig").strip())
    parts.append(f"=== SKILL: {config.skill.name} (folder: {config.skill}) ===\n"
                 + skill.read_text(encoding="utf-8-sig").strip())
    if config.message:
        parts.append(message_section(config.message))
    parts.append(
        "=== ATURAN DARI PROGRAM ===\n"
        f"Di setiap blok SETUP tulis persis: `Kode: {config.setup_code}` dan `Sumber: cli_{config.skill.name}`.\n"
        "Aturan ini mengalahkan contoh Kode/Sumber di skill atau format."
    )
    parts.append(f"Waktu sekarang: {now}")
    return "\n\n".join(parts)


def save_result(raw, secrets, message=""):
    """Simpan jawaban mentah Claude. Pesan user (kalau ada) ditulis utuh di atasnya: hanya di file lokal ini."""
    directory = DATA / "hasil"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(WIB).strftime("%Y%m%d-%H%M%S")
    file = directory / f"{stamp}.txt"
    if file.exists():
        file = directory / f"{stamp}-{uuid4().hex[:8]}.txt"
    if message:
        raw = f"=== PESAN USER ===\n{message.strip()}\n=== JAWABAN CLAUDE ===\n{raw}"
    file.write_text(redact(raw, secrets), encoding="utf-8", newline="")
    logging.info("Jawaban Claude disimpan di data/hasil/%s.", file.name)
    return file.name


def stop_process(process):
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=10, check=False,
            )
            if result.returncode == 0:
                return
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Beberapa lingkungan Windows menolak taskkill walaupun prosesnya milik kita.
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                logging.warning("Satu proses turunan Claude tidak bisa dihentikan.")
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied:
        logging.warning("Daftar proses turunan Claude tidak bisa dibaca.")
    finally:
        if process.poll() is None:
            process.kill()


def execute(command, prompt, timeout, cwd):
    env = {key: value for key, value in os.environ.items() if key.upper() not in SECRET_KEYS}
    with subprocess.Popen(
        command, cwd=cwd, env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as process:
        started = time.monotonic()
        deadline = started + timeout
        next_progress = started + PROGRESS_INTERVAL
        pending_input = prompt.encode("utf-8")
        logging.info("Claude CLI dimulai. Menunggu respons...")
        try:
            while True:
                wait_until = min(next_progress, deadline)
                try:
                    stdout, stderr = process.communicate(
                        pending_input, timeout=max(0, wait_until - time.monotonic()),
                    )
                    break
                except subprocess.TimeoutExpired:
                    now = time.monotonic()
                    if wait_until == deadline or now >= deadline:
                        raise
                    pending_input = None  # communicate melanjutkan pembacaan tanpa mengirim ulang prompt.
                    if process.poll() is None:
                        logging.info("Claude CLI masih berjalan (%d detik).", round(now - started))
                    next_progress = now + PROGRESS_INTERVAL
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            stop_process(process)
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise GeneratorError(f"Claude melewati batas waktu ({timeout:g} detik). Proses dihentikan.") from None
        return process.returncode, stdout.decode("utf-8-sig", "replace"), stderr.decode("utf-8-sig", "replace")


def analyze(config, secrets):
    executable = shutil.which("claude")
    if not executable:
        raise GeneratorError("Perintah claude belum ditemukan.")
    started = time.monotonic()
    with TemporaryDirectory(prefix="cli-generator-kerja-") as work:
        code, raw, stderr = execute(build_command(executable, config), build_prompt(config), config.timeout, work)
    file = save_result(raw or stderr, secrets, config.message)
    try:
        result = json.loads(raw)
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    meta = usage_meta(result, time.monotonic() - started, file)
    answer = result.get("result")
    if code or result.get("is_error") or result.get("subtype", "success") != "success":
        detail = answer or result.get("errors") or stderr or raw or f"kode keluar {code}"
        detail = redact(" ".join(str(detail).split()), secrets)[:240]
        error = GeneratorError(f"Claude gagal. {detail}")
        error.meta = meta   # pemakaian tetap tercatat di jurnal walau gagal
        raise error
    if not isinstance(answer, str):
        error = GeneratorError("Jawaban JSON Claude tidak dikenali. Cek data/hasil.")
        error.meta = meta
        raise error
    answer = Answer(answer)
    answer.meta = meta
    return answer
