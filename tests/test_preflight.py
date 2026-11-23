"""The pre-flight check that the launchers run before starting the tracker."""
import json
import os
import socket
import sys

import pytest

import preflight


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(preflight, "browser_probe", lambda: {"chromium": None, "chrome": None, "msedge": "not installed"})
    return tmp_path


def run(argv=("--offline",)):
    return preflight.main(list(argv))


def results(capsys):
    return capsys.readouterr().out


def write(folder, name, value):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write(value if isinstance(value, str) else json.dumps(value))


@pytest.mark.parametrize("marker,platform,expected", [
    ('sys_platform == "darwin"', "darwin", True),
    ('sys_platform == "darwin"', "win32", False),
    ('sys_platform == "darwin" and python_version < "3.10"', "darwin", sys.version_info[:2] < (3, 10)),
    ('sys_platform == "darwin" and python_version >= "3.10"', "darwin", sys.version_info[:2] >= (3, 10)),
    ('platform_machine == "arm64"', "darwin", True),  # Unknown markers are checked, not skipped.
])
def test_markers(monkeypatch, marker, platform, expected):
    monkeypatch.setattr(sys, "platform", platform)
    assert preflight.marker_applies(marker) is expected


def test_only_one_pyobjus_line_applies_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    names = [name for name, _, _ in preflight.read_requirements()]
    assert names.count("pyobjus") == 1 and "playwright" in names


def test_pyobjus_is_not_needed_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert "pyobjus" not in [name for name, _, _ in preflight.read_requirements()]


def test_missing_or_old_packages_fail(data, monkeypatch, capsys):
    versions = {"playwright": None, "rich": "12.0.0"}
    monkeypatch.setattr(preflight, "installed_version", lambda n: versions.get(n, "99.0"))
    assert run() == 1
    out = results(capsys)
    assert "Missing or outdated: playwright, rich" in out and "rich 12.0.0 installed, 13.7.1 or newer needed" in out
    assert "preflight.py --fix" in out


def test_fresh_install_is_ready_with_setup_warnings(data, monkeypatch, capsys):
    monkeypatch.setattr(preflight, "installed_version", lambda n: "99.0")
    monkeypatch.setattr(preflight, "check_notifications", lambda r, configs: None)
    assert run() == 0
    out = results(capsys)
    assert "Delivery location not set up" in out and "blinkit_tracker.py --setup" in out
    assert "not created yet" in out and "Ready to run" in out


def test_broken_config_fails_without_suggesting_fix(data, monkeypatch, capsys):
    monkeypatch.setattr(preflight, "installed_version", lambda n: "99.0")
    monkeypatch.setattr(preflight, "check_notifications", lambda r, configs: None)
    write(data, "config.json", '{"products": [')
    assert run() == 1
    out = results(capsys)
    assert "config.json is broken" in out and "preflight.py --fix" not in out


def test_session_saved_with_a_missing_browser_warns(data, monkeypatch, capsys):
    monkeypatch.setattr(preflight, "installed_version", lambda n: "99.0")
    monkeypatch.setattr(preflight, "check_notifications", lambda r, configs: None)
    write(data, "zepto_state.json", {"cookies": [], "origins": []})
    write(data, "zepto_state.json.meta.json", {"channel": "msedge"})
    write(data, "zepto_config.json", {"products": [{"name": "Milk", "url": "https://www.zepto.com/pn/x"}],
                                      "telegram": {"enabled": True, "bot_token": "YOUR_BOT_TOKEN", "chat_id": "1"}})
    assert run() == 0
    out = results(capsys)
    assert "saved with Microsoft Edge, which can no longer start" in out
    assert "Tracking 1 product(s)" in out and "bot token or chat ID is not filled in" in out


def test_missing_chromium_fails(data, monkeypatch, capsys):
    monkeypatch.setattr(preflight, "installed_version", lambda n: "99.0")
    monkeypatch.setattr(preflight, "check_notifications", lambda r, configs: None)
    monkeypatch.setattr(preflight, "browser_probe", lambda: {"chromium": "Executable doesn't exist", "chrome": "no", "msedge": "no"})
    assert run() == 1
    out = results(capsys)
    assert "Chromium cannot start" in out and "playwright install chromium" in out and "Neither Google Chrome" in out


def test_busy_dashboard_port_fails(data, monkeypatch, capsys):
    monkeypatch.setattr(preflight, "installed_version", lambda n: "99.0")
    monkeypatch.setattr(preflight, "check_notifications", lambda r, configs: None)
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    s.listen()
    monkeypatch.setattr(preflight, "DASHBOARD_PORT", s.getsockname()[1])
    try:
        assert run(["--offline", "--dashboard"]) == 1
    finally:
        s.close()
    assert "is already in use" in results(capsys)


def test_dns_blocked_site_fails(data, monkeypatch, capsys):
    monkeypatch.setattr(preflight, "installed_version", lambda n: "99.0")
    monkeypatch.setattr(preflight, "check_notifications", lambda r, configs: None)
    monkeypatch.setattr(preflight.socket, "gethostbyname", lambda host: "0.0.0.0")
    assert run([]) == 1
    assert "blinkit.com is blocked by your DNS" in results(capsys)


def test_old_python_stops_early(monkeypatch, capsys):
    monkeypatch.setattr(preflight, "MIN_PYTHON", (99, 0))
    assert run() == 1
    out = results(capsys)
    assert "is too old" in out and "packages" not in out
