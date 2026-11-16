import logging
import math
import re
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

import yaml
from dotenv import dotenv_values

SYSTEM = Path(__file__).resolve().parents[1]
DATA = SYSTEM / "data"
LOGS = SYSTEM / "logs"
WIB = timezone(timedelta(hours=7), "WIB")
SECRET_KEYS = ("GEN_TG_BOT_TOKEN", "GEN_TG_API_ID", "GEN_TG_API_HASH")
REQUIRED_DENY = "mcp__tradingview__ui_evaluate"
TV_PREFIX = "mcp__tradingview__"
# Semua tool MCP TradingView (dicek 14 Nov 2026). Yang tidak ada di tools_tradingview ditolak supaya tidak
# dimuat ke Claude (tiap tool memakan token di setiap panggilan). Tool baru dari update MCP belum ada di sini,
# jadi tetap terbuka sampai daftar ini diperbarui.
TV_TOOLS = tuple("""
alert_create alert_delete alert_list batch_run capture_screenshot chart_get_state chart_get_visible_range
chart_manage_indicator chart_scroll_to_date chart_set_symbol chart_set_timeframe chart_set_type chart_set_visible_range
data_get_equity data_get_indicator data_get_ohlcv data_get_pine_boxes data_get_pine_labels data_get_pine_lines
data_get_pine_tables data_get_strategy_results data_get_study_values data_get_trades depth_get draw_clear
draw_get_properties draw_list draw_remove_one draw_shape indicator_add indicator_search indicator_set_inputs
indicator_toggle_visibility layout_list layout_new layout_switch pane_focus pane_list pane_set_layout pane_set_symbol
pine_analyze pine_check pine_compile pine_get_console pine_get_errors pine_get_source pine_list_scripts pine_new
pine_open pine_save pine_set_source pine_smart_compile quote_get replay_autoplay replay_start replay_status replay_step
replay_stop replay_trade symbol_info symbol_search tab_close tab_list tab_new tab_switch tv_discover tv_health_check
tv_launch tv_ui_state tv_update ui_click ui_evaluate ui_find_element ui_fullscreen ui_hover ui_keyboard
ui_mouse_click ui_open_panel ui_scroll ui_type_text watchlist_add watchlist_add_bulk watchlist_get watchlist_remove
""".split())
# Tool bawaan Claude Code yang terlihat dengan setelan CLI (Claude Code 2.1.323), untuk pilihan di menu.
BUILTIN_TOOLS = tuple("""
WebSearch WebFetch Read Glob Grep Write Edit NotebookEdit Bash PowerShell Task SendMessage ListAgents TaskStop
Monitor CronCreate CronDelete CronList ScheduleWakeup RemoteTrigger PushNotification ToolSearch Artifact
ArtifactComments ArtifactData DesignSync EnterWorktree ExitWorktree ReportFindings
""".split())
TV_LAUNCHER = Path.home() / "Documents" / "tradingview-mcp" / "scripts" / "launch_tv_debug.bat"
TV_WAIT_SECONDS = 90
DEFAULT_SETUP_CODE = "TES"   # tidak terdaftar di Automanage, jadi setup uji pasti ditolak bot
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MODELS = ("fable", "opus", "sonnet", "haiku")
DEFAULT_SKILL_TIMES = ("09:00", "14:00", "20:00")
HOUR = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
MODEL_NAME = re.compile(r"[A-Za-z0-9][\w.\[\]-]*")
SKILL_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


class GeneratorError(Exception):
    """Pesan untuk pengguna yang aman ditampilkan."""


def skills_dir():
    """Folder perpustakaan skill: <folder proyek>\\skill. Satu subfolder = satu skill."""
    return SYSTEM.parent / "skill"


def list_skills():
    """Skill yang bisa dipilih: subfolder berisi SKILL.md, nama tidak diawali '_'. Urut nama."""
    folder = skills_dir()
    if not folder.is_dir():
        return []
    return sorted(
        item.name for item in folder.iterdir()
        if item.is_dir() and not item.name.startswith("_") and (item / "SKILL.md").is_file()
    )


def resolve_skill(value):
    """'' -> None (mode uji). Nama -> skill\\<nama>. Path (ada / \\ :) -> folder itu."""
    value = value.strip()
    if not value:
        return None
    if any(sep in value for sep in ("/", "\\", ":")):
        return (SYSTEM.parent / value).resolve()
    return skills_dir() / value



def atomic_write(path, text):
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, newline="") as stream:
        temporary = Path(stream.name)
        stream.write(text)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def update_yaml(path, changes):
    import json

    text = path.read_text(encoding="utf-8-sig")
    for (section, key), value in changes.items():
        root = yaml.compose(text)
        fields = {node.value: val for node, val in root.value}
        if section and section not in fields:
            text = text.rstrip("\n") + f"\n{section}:\n  {key}: {json.dumps(value, ensure_ascii=False)}\n"
            continue
        mapping = fields[section] if section else root
        if not isinstance(mapping, yaml.MappingNode):
            raise GeneratorError(f"Bagian {section} harus berisi setelan YAML.")
        if section and not mapping.value and mapping.flow_style:
            position = mapping.start_mark.index
            text = text[:position] + json.dumps({key: value}, ensure_ascii=False) + text[mapping.end_mark.index:]
            continue
        fields = {node.value: (node, val) for node, val in mapping.value}
        encoded = json.dumps(value, ensure_ascii=False)
        if key not in fields:
            if section and not fields:
                raise GeneratorError(f"Bagian {section} harus berisi setelan YAML.")
            if section and mapping.flow_style:
                position = mapping.start_mark.index + 1
                text = text[:position] + f"{key}: {encoded}, " + text[position:]
                continue
            if section:
                position = text.rfind("\n", 0, mapping.start_mark.index) + 1
                indent = " " * mapping.start_mark.column
                text = text[:position] + f"{indent}{key}: {encoded}\n" + text[position:]
            else:
                text = text.rstrip("\n") + f"\n{key}: {encoded}\n"
            continue
        key_node, node = fields[key]
        start, end = node.start_mark.index, node.end_mark.index
        if isinstance(node, yaml.SequenceNode) and not node.flow_style:
            start = text.rfind("\n", 0, start) + 1
            end = text.find("\n", node.value[-1].end_mark.index)
            end = len(text) if end < 0 else end
            indent = " " * key_node.start_mark.column
            old_lines = text[start:end].splitlines()
            comments = "\n".join(
                f"{indent}#{line.partition('#')[2]}" for line in old_lines if "#" in line
            )
            key_end = text.find("\n", key_node.end_mark.index)
            header = text[key_node.end_mark.index:key_end]
            header_comment = header.partition("#")[2]
            note = f"  #{header_comment}" if header_comment else ""
            notes = f"\n{comments}" if comments else ""
            text = text[:key_node.end_mark.index] + f": {encoded}{note}{notes}" + text[end:]
        elif start == end:
            text = text[:start] + " " + encoded + text[end:]
        else:
            text = text[:start] + encoded + text[end:]
    try:
        yaml.safe_load(text)
    except yaml.YAMLError:
        raise GeneratorError("Perubahan tidak bisa disimpan karena format YAML tidak valid.") from None
    atomic_write(path, text)


def save_secrets(changes):
    import io
    import json
    from dotenv.parser import parse_stream

    path = SYSTEM.parent / ".env"
    text = path.read_bytes().decode("utf-8-sig") if path.exists() else ""
    newline = "\r\n" if "\r\n" in text else "\n"
    for key, value in changes.items():
        if key not in SECRET_KEYS or not value or "\n" in value or "\r" in value:
            raise GeneratorError("Isian Telegram tidak valid.")
        line = f"{key}={json.dumps(value, ensure_ascii=False)}"
        pieces = []
        updated = False
        for binding in parse_stream(io.StringIO(text)):
            original = binding.original.string
            if binding.key != key:
                pieces.append(original)
                continue
            # Parser dotenv juga mengenali nilai yang melintasi beberapa baris.
            head = re.match(r"\s*", original).group()
            tail = re.search(r"(?:\r\n|\n|\r)$", original)
            pieces.append(head + line + (tail[0] if tail else ""))
            updated = True
        if updated:
            text = "".join(pieces)
        else:
            if text and not text.endswith("\n"):
                text += newline
            text += line + newline
    atomic_write(path, text)


def set_config_value(section, key, value, old_keys=()):
    """Tulis satu nilai teks di config.yaml (bagian `section`) tanpa merusak komentar."""
    path = SYSTEM / "config.yaml"
    text = path.read_text(encoding="utf-8-sig")
    keys = "|".join(re.escape(k) for k in (key, *old_keys))
    pattern = re.compile(
        rf'(?m)^(?P<lead>[ \t]+)(?:{keys}):[ \t]*(?:"[^"\n]*"|\'[^\'\n]*\'|[^#\n]*?)(?P<tail>[ \t]*(?:#.*)?)$')
    start = re.search(rf"(?m)^{re.escape(section)}:[ \t]*$", text)
    end = re.search(r"(?m)^\S", text[start.end():]) if start else None
    limit = start.end() + end.start() if end else len(text)
    match = pattern.search(text, start.end(), limit) if start else None
    if not match:
        raise GeneratorError(f"Baris {section}.{key} tidak ditemukan di config.yaml.")
    line = f'{match["lead"]}{key}: "{value}"{match["tail"] or ""}'
    atomic_write(path, text[:match.start()] + line + text[match.end():])


def set_report_chat(chat_id):
    """Simpan ID chat pribadi user (hasil deteksi otomatis) ke telegram.laporan_ke."""
    if not re.fullmatch(r"[1-9][0-9]*", str(chat_id)):
        raise GeneratorError("ID chat pribadi tidak valid.")
    set_config_value("telegram", "laporan_ke", str(chat_id))


@dataclass(frozen=True)
class Config:
    skill: Path | None
    model: str
    timeout: float
    denied_tools: tuple[str, ...]
    sender: str
    inbox: str
    report: str
    schedule_enabled: bool
    times: frozenset[str]
    tv_launcher: Path = TV_LAUNCHER   # skrip pembuka TradingView (CDP 9222)
    tv_wait: float = TV_WAIT_SECONDS  # lama menunggu chart siap setelah TradingView dibuka
    skill_name: str = ""              # nama skill aktif ('' = mode uji fungsi)
    setup_code: str = DEFAULT_SETUP_CODE  # isi baris Kode: di blok SETUP
    send_analysis: bool = True        # kirim jawaban analisa lengkap ke chat pribadi
    effort: str = ""                  # '' = bawaan Claude Code
    model_choices: tuple[str, ...] = DEFAULT_MODELS
    builtin_tools: tuple[str, ...] | None = None   # tool bawaan Claude Code yang diizinkan; None = semua
    tv_tools: tuple[str, ...] | None = None        # tool TradingView yang diizinkan (nama pendek); None = semua
    message: str = ""   # pesan user untuk satu analisa manual ('' = tanpa pesan)
    skill_times: tuple[str, ...] = DEFAULT_SKILL_TIMES


def tool_list(value, pattern):
    """Daftar nama tool dari config.yaml. None (kunci tidak ada) = semua diizinkan."""
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) or not re.fullmatch(pattern, item) for item in value):
        raise ValueError
    return tuple(dict.fromkeys(value))


def default_mcp_folder():
    return Path.home() / "Documents" / "tradingview-mcp"


def ensure_local_files():
    """PC baru / hasil clone: buat config.yaml dan mcp-tradingview.json pribadi dari contoh.

    Dua file itu berisi ID dan path milik pemakai, jadi tidak ikut repo (lihat .gitignore).
    """
    config = SYSTEM / "config.yaml"
    example = SYSTEM / "config.example.yaml"
    if not config.exists() and example.exists():
        atomic_write(config, example.read_text(encoding="utf-8-sig"))
    mcp = SYSTEM / "mcp-tradingview.json"
    if not mcp.exists():
        import json

        server = {"command": "node", "args": [str(default_mcp_folder() / "src" / "server.js")],
                  "env": {"TV_CDP_HOST": "127.0.0.1", "TV_CDP_PORT": "9222"}}
        atomic_write(mcp, json.dumps({"mcpServers": {"tradingview": server}}, indent=2) + "\n")


def read_config():
    ensure_local_files()
    try:
        raw = yaml.safe_load((SYSTEM / "config.yaml").read_text(encoding="utf-8-sig"))
        skill_cfg = raw.get("skill") or {}
        folder = skill_cfg.get("aktif", skill_cfg.get("folder", ""))  # 'folder' = nama kunci lama
        setup_code = str(skill_cfg.get("kode") or DEFAULT_SETUP_CODE).strip()
        claude = raw["claude"]
        skill_times = claude.get("jam_skill", list(DEFAULT_SKILL_TIMES))
        if not isinstance(skill_times, list) or not skill_times or any(not HOUR.fullmatch(t) for t in skill_times):
            raise ValueError
        telegram = raw["telegram"]
        schedule = raw.get("uji_coba_jam") or {}
        model = str(claude["model"]).strip()
        effort = str(claude.get("effort") or "").strip().lower()
        choices = claude.get("pilihan_model") or list(DEFAULT_MODELS)
        if effort and effort not in EFFORTS:
            raise ValueError
        if not isinstance(choices, list) or not all(isinstance(c, str) and MODEL_NAME.fullmatch(c) for c in choices):
            raise ValueError
        timeout = float(claude["batas_waktu_menit"]) * 60
        denied = claude["tools_ditolak"]
        builtin_tools = tool_list(claude.get("tools_bawaan"), r"[A-Za-z][A-Za-z0-9_]*")
        tv_tools = tool_list(claude.get("tools_tradingview"), r"[a-z][a-z0-9_]*")
        times = schedule.get("jam", [])
        enabled = schedule.get("nyala", False)
        sender = telegram["pengirim"]
        inbox = str(telegram["kotak_masuk"]).strip()
        report = str(telegram.get("laporan_ke") or "").strip()   # kosong = dideteksi otomatis
        send_analysis = telegram.get("kirim_analisa_ke_pm", True)
        if not isinstance(send_analysis, bool):
            raise ValueError
        if not isinstance(folder, str) or not MODEL_NAME.fullmatch(model):
            raise ValueError
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
        if not isinstance(denied, list) or any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_*.-]*", tool) for tool in denied
        ):
            raise ValueError
        if not isinstance(times, list) or any(
            not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time) for time in times
        ) or not isinstance(enabled, bool):
            raise ValueError
        if sender not in ("bot", "akun"):
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", setup_code):
            raise ValueError
        skill = resolve_skill(folder)
        tv = raw.get("tradingview") or {}
        launcher = str(tv.get("peluncur") or TV_LAUNCHER).strip()
        tv_wait = float(tv.get("tunggu_detik", TV_WAIT_SECONDS))
        if not launcher or not math.isfinite(tv_wait) or tv_wait <= 0:
            raise ValueError
        tv_launcher = Path(launcher)
    except (OSError, RuntimeError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise GeneratorError("Setelan tidak bisa dibaca. Cek config.yaml.") from None

    if REQUIRED_DENY not in denied:
        logging.warning("ui_evaluate hilang dari setelan dan otomatis ditambahkan ke daftar tolak.")
        denied.append(REQUIRED_DENY)
    return Config(skill, model, timeout, tuple(denied), sender, inbox, report, enabled, frozenset(times),
                  tv_launcher, tv_wait, skill.name if skill else "", setup_code, send_analysis,
                  effort, tuple(choices), builtin_tools, tv_tools, skill_times=tuple(dict.fromkeys(skill_times)))


def read_secrets():
    values = dotenv_values(SYSTEM.parent / ".env", encoding="utf-8-sig", interpolate=False)
    return {key: (values.get(key) or "").strip() for key in SECRET_KEYS}


def redact(text, secrets):
    values = sorted({value for value in secrets.values() if value}, key=len, reverse=True)
    for value in values:
        text = text.replace(value, "[disamarkan]")
    return text


def valid_inbox(config):
    return bool(re.fullmatch(r"-100[0-9]+", config.inbox))


def valid_report(config):
    return bool(re.fullmatch(r"[1-9][0-9]*", config.report))
