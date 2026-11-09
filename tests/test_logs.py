"""The log file and the zip a user sends for debugging."""
import io
import json
import os
import runpy
import sys
import zipfile

from fastapi.testclient import TestClient

import api
import blinkit_tracker as blinkit
import tracker_core as core

client = TestClient(api.app)
SECRET_TOKEN = "123456:SECRET-token"


def read_log():
    with open(core.LOG_FILE, encoding="utf-8") as f:
        return f.read()


def open_zip(data):
    return zipfile.ZipFile(io.BytesIO(data))


def everything_in(archive):
    return "\n".join(archive.read(n).decode("utf-8", "replace") for n in archive.namelist())


def png(folder, name, mtime=None):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, name)
    with open(p, "wb") as f:
        f.write(b"\x89PNG")
    if mtime is not None:
        os.utime(p, (mtime, mtime))


class Stream(io.StringIO):
    def isatty(self):
        return True


def test_tee_passes_through_and_logs_cleaned_lines():
    stream = Stream()
    tee = core.LogTee(stream)
    tee.write("\x1b[1;31mfailed\x1b[0m at 10.1.2.3 with bot123:abc\n")
    assert "\x1b[1;31m" in stream.getvalue()
    log = read_log()
    assert "failed at <ip> with bot<token>" in log and "\x1b" not in log and "10.1.2.3" not in log


def test_tee_joins_partial_writes_and_skips_blank_lines():
    tee = core.LogTee(Stream())
    tee.write("hel")
    assert not os.path.exists(core.LOG_FILE)
    tee.write("lo\n\n   \nnext\n")
    assert [l.split(" ", 1)[1] for l in read_log().splitlines()] == ["hello", "next"]


def test_tee_acts_like_the_real_stream():
    stream = Stream()
    tee = core.LogTee(stream)
    assert tee.isatty() is True and tee.write("abc") == 3
    tee.flush()


def test_tee_never_breaks_the_app(monkeypatch):
    def full_disk(line):
        raise OSError("disk full")

    monkeypatch.setattr(core, "write_log_line", full_disk)
    stream = Stream()
    assert core.LogTee(stream).write("hello\n") == 6
    assert stream.getvalue() == "hello\n"


def test_search_terms_are_removed_from_logs():
    core.write_log_line('"GET /search/blinkit?q=private+thing HTTP/1.1" 200')
    assert "private" not in read_log() and "q=<query>" in read_log()


def test_log_rotates_and_keeps_one_old_file(monkeypatch):
    monkeypatch.setattr(core, "MAX_LOG_BYTES", 100)
    for i in range(30):
        core.write_log_line(f"line {i} " + "x" * 20)
    assert os.path.getsize(core.LOG_FILE) < 200 and os.path.exists(f"{core.LOG_FILE}.1")
    assert [f for f in os.listdir(core.DIAG_DIR) if f.startswith("app.log")].__len__() == 2


def test_install_captures_prints_once():
    core.install_log_capture()
    first = sys.stdout
    core.install_log_capture()
    assert sys.stdout is first
    print("hello from the app")
    print("oops", file=sys.stderr)
    log = read_log()
    assert "Started app" in log and "hello from the app" in log and "oops" in log and log.count("Started app") == 1


def test_rich_tracker_output_reaches_the_log():
    core.install_log_capture()
    blinkit.log_plain("Checking Milk [/500ml]...")
    assert "Checking Milk [/500ml]..." in read_log()


def test_bundle_has_report_context_logs_and_screenshots():
    core.write_log_line("something happened")
    png(core.DIAG_DIR, "blinkit-blocked-1.png")
    png(blinkit.SCREENSHOT_DIR, "Milk_latest.png")
    archive = open_zip(core.build_log_bundle("It broke", "dashboard"))
    names = archive.namelist()
    assert {"report.txt", "app-context.json", "logs/app.log", "screenshots/blocks/blinkit-blocked-1.png", "screenshots/products/Milk_latest.png"} <= set(names)
    assert "It broke" in archive.read("report.txt").decode()
    assert "something happened" in archive.read("logs/app.log").decode()


def test_bundle_includes_rotated_log(monkeypatch):
    monkeypatch.setattr(core, "MAX_LOG_BYTES", 10)
    core.write_log_line("first " * 10)
    core.write_log_line("second")
    names = open_zip(core.build_log_bundle()).namelist()
    assert "logs/app.log" in names and "logs/app.log.1" in names


def test_bundle_without_screenshots():
    png(core.DIAG_DIR, "a.png")
    assert not [n for n in open_zip(core.build_log_bundle(include_screenshots=False)).namelist() if n.endswith(".png")]


def test_bundle_works_on_a_fresh_install():
    names = open_zip(core.build_log_bundle()).namelist()
    assert names == ["report.txt", "app-context.json"]


def test_bundle_keeps_only_newest_screenshots():
    for i in range(core.MAX_DIAG_SCREENSHOTS + 5):
        png(blinkit.SCREENSHOT_DIR, f"p{i}.png", mtime=1000 + i)
    shots = [n for n in open_zip(core.build_log_bundle()).namelist() if n.endswith(".png")]
    assert len(shots) == core.MAX_DIAG_SCREENSHOTS and "screenshots/products/p0.png" not in shots
    assert f"screenshots/products/p{core.MAX_DIAG_SCREENSHOTS + 4}.png" in shots


def test_bundle_never_contains_cookies_or_tokens():
    with open(blinkit.STATE_FILE, "w") as f:
        json.dump({"cookies": [{"name": "gr_1_lat", "value": "COOKIE-VALUE-28.61"}], "origins": []}, f)
    core.save_config(blinkit.CONFIG_FILE, dict(core.DEFAULT_CONFIG, telegram={"enabled": True, "bot_token": SECRET_TOKEN, "chat_id": "CHAT-ID-77"}))
    core.install_log_capture()
    print(f"sending to https://api.telegram.org/bot{SECRET_TOKEN}/sendMessage from 203.0.113.5")
    core.record_incident("blinkit", "page_error", "search", snippet=f"bot{SECRET_TOKEN} 203.0.113.5")
    archive = open_zip(core.build_log_bundle("my ip is 203.0.113.5"))
    text = everything_in(archive)
    for secret in ("COOKIE-VALUE", SECRET_TOKEN, "CHAT-ID-77", "203.0.113.5", "SECRET"):
        assert secret not in text, secret
    assert not [n for n in archive.namelist() if "state" in n or "config.json" in n]


def test_app_context_contents():
    blinkit.init_db()
    blinkit.update_status("https://blinkit.com/prn/a/prid/1", "a", "IN_STOCK")
    blinkit.update_status("https://www.zepto.com/pn/b/pvid/2", "b", "OUT_OF_STOCK")
    with open(blinkit.STATE_FILE, "w") as f:
        f.write("{}")
    with open(f"{blinkit.STATE_FILE}.meta.json", "w") as f:
        json.dump({"channel": "chrome", "user_agent": "UA", "saved_at": "2026-01-01"}, f)
    core.save_config(blinkit.CONFIG_FILE, dict(core.DEFAULT_CONFIG, products=[{"name": "a", "url": "https://blinkit.com/a"}], tracked_keywords=["m"]))
    core.record_incident("blinkit", "blocked", "setup")
    c = core.app_context()
    b = c["providers"]["blinkit"]
    assert b["products"] == 1 and b["keywords"] == 1 and b["user_agent"] == "UA" and b["setup_browser"] == "chrome"
    assert b["status_counts"] == {"IN_STOCK": 1} and c["providers"]["zepto"]["status_counts"] == {"OUT_OF_STOCK": 1}
    assert c["incidents"][0]["kind"] == "blocked" and "playwright" in c["packages"]


def test_bundle_still_builds_with_a_damaged_config():
    with open(blinkit.CONFIG_FILE, "w") as f:
        f.write("{bad")
    context = json.loads(open_zip(core.build_log_bundle()).read("app-context.json"))
    assert "not valid JSON" in context["providers"]["blinkit"]["config_error"]


def test_download_route():
    core.write_log_line("hello")
    png(core.DIAG_DIR, "a.png")
    r = client.post("/report/logs", json={"note": "help", "context": "dashboard"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="tracker-logs-' in r.headers["content-disposition"]
    archive = open_zip(r.content)
    assert "logs/app.log" in archive.namelist() and "screenshots/blocks/a.png" in archive.namelist()
    assert "help" in archive.read("report.txt").decode()


def test_download_route_can_leave_out_screenshots():
    png(core.DIAG_DIR, "a.png")
    r = client.post("/report/logs", json={"include_screenshots": False})
    assert not [n for n in open_zip(r.content).namelist() if n.endswith(".png")]


def test_other_websites_cannot_take_the_logs():
    assert client.post("/report/logs", json={}, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/report/logs", json={}, headers={"origin": "null"}).status_code == 403


def test_reset_deletes_logs_and_logging_continues():
    core.write_log_line("before")
    assert client.post("/reset").status_code == 200
    assert not os.path.exists(core.LOG_FILE)
    core.write_log_line("after")
    assert "after" in read_log()


def test_report_command_creates_the_zip(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["tracker_core.py", "--report", "it", "broke"])
    runpy.run_path(core.__file__, run_name="__main__")
    target = os.path.join(core.DIAG_DIR, "logs-to-send.zip")
    assert os.path.exists(target) and "logs-to-send.zip" in capsys.readouterr().out
    with zipfile.ZipFile(target) as archive:
        assert "it broke" in archive.read("report.txt").decode()


def test_old_bundles_are_not_packed_into_new_ones():
    os.makedirs(core.DIAG_DIR, exist_ok=True)
    with open(os.path.join(core.DIAG_DIR, "logs-to-send.zip"), "wb") as f:
        f.write(b"old")
    assert not [n for n in open_zip(core.build_log_bundle()).namelist() if n.endswith(".zip")]
