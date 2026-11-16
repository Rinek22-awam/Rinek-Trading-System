import json
from dataclasses import replace

import pytest

from generator import claude, menu, settings
from generator.settings import GeneratorError, REQUIRED_DENY

from test_generator import CONFIG
from test_menu import project  # noqa: F401  (fixture folder proyek sementara)

TV = settings.TV_PREFIX


def denied(command):
    return set(command[command.index("--disallowed-tools") + 1].split(","))


def test_no_allowlist_keeps_old_behaviour():
    command = claude.build_command("claude", CONFIG)
    assert command[command.index("--tools") + 1] == "default"
    assert TV + "draw_shape" not in denied(command)


def test_allowlist_limits_builtin_and_tradingview():
    config = replace(CONFIG, builtin_tools=("WebSearch", "WebFetch"),
                     tv_tools=("tv_health_check", "data_get_ohlcv", "ui_evaluate"))
    command = claude.build_command("claude", config)
    assert command[command.index("--tools") + 1] == "WebSearch,WebFetch"
    blocked = denied(command)
    assert TV + "data_get_ohlcv" not in blocked and TV + "tv_health_check" not in blocked
    assert {TV + "draw_shape", TV + "data_get_study_values", TV + "pine_set_source"} <= blocked
    assert REQUIRED_DENY in blocked          # terkunci walau ada di daftar izin
    assert len(blocked) == len(settings.TV_TOOLS) - 2 + 0   # semua kecuali dua yang diizinkan


def test_empty_builtin_list_disables_all_builtin():
    command = claude.build_command("claude", replace(CONFIG, builtin_tools=()))
    assert command[command.index("--tools") + 1] == ""


def test_example_config_allowlist(tmp_path, monkeypatch):
    example = settings.SYSTEM / "config.example.yaml"
    (tmp_path / example.name).write_bytes(example.read_bytes())
    monkeypatch.setattr(settings, "SYSTEM", tmp_path)
    config = settings.read_config()
    assert config.builtin_tools == ("WebSearch", "WebFetch")
    assert {"data_get_pine_boxes", "data_get_pine_tables", "capture_screenshot"} <= set(config.tv_tools)
    assert "data_get_study_values" not in config.tv_tools


def test_config_rejects_bad_tool_names(project):
    path = project / "config.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "telegram:", "  tools_tradingview: [\"Bad Name\"]\ntelegram:"), encoding="utf-8")
    with pytest.raises(GeneratorError):
        settings.read_config()


def test_menu_adds_and_removes_tools(project, monkeypatch, capsys):
    instance = menu.Menu()
    base = settings.read_config()
    assert base.tv_tools is None and base.builtin_tools is None
    # Belum dibatasi: menutup satu tool = daftar izin berisi semua kecuali tool itu.
    names = list(settings.TV_TOOLS)
    monkeypatch.setattr(menu, "ask", lambda _: str(names.index("draw_shape") + 1))
    instance.edit_allowed(base, tv=True, add=False)
    base = settings.read_config()
    assert "draw_shape" not in base.tv_tools and len(base.tv_tools) == len(names) - 1
    monkeypatch.setattr(menu, "ask", lambda _: "1")
    instance.edit_allowed(base, tv=True, add=True)
    assert "draw_shape" in settings.read_config().tv_tools
    builtin = list(settings.BUILTIN_TOOLS)
    monkeypatch.setattr(menu, "ask", lambda _: str(builtin.index("Bash") + 1))
    instance.edit_allowed(settings.read_config(), tv=False, add=False)
    assert "Bash" not in settings.read_config().builtin_tools
    instance.show_allowed(settings.read_config())
    assert "Tool TradingView (84)" in capsys.readouterr().out


def test_fresh_clone_creates_private_files(tmp_path, monkeypatch):
    system = tmp_path / "sistem"
    system.mkdir()
    (system / "config.example.yaml").write_bytes((settings.SYSTEM / "config.example.yaml").read_bytes())
    monkeypatch.setattr(settings, "SYSTEM", system)
    config = settings.read_config()
    assert (system / "config.yaml").is_file() and (system / "mcp-tradingview.json").is_file()
    assert config.inbox == "" and config.report == "" and config.tv_launcher == settings.TV_LAUNCHER
    assert config.builtin_tools == ("WebSearch", "WebFetch") and "capture_screenshot" in config.tv_tools
    mcp = json.loads((system / "mcp-tradingview.json").read_text())
    assert mcp["mcpServers"]["tradingview"]["args"] == [
        str(settings.default_mcp_folder() / "src" / "server.js")
    ]
    example = (settings.SYSTEM / "config.example.yaml").read_text(encoding="utf-8")
    assert '"-100' not in example and 'laporan_ke: ""' in example
