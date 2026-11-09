import json
import os
import runpy
import sqlite3
from datetime import datetime

import pytest

import blinkit_tracker as blinkit
import tracker_core as core
from fakes import FakePage, FakeContext, Response


def test_load_config_creates_missing_file():
    os.remove(blinkit.CONFIG_FILE)
    assert core.load_config(blinkit.CONFIG_FILE)["products"] == []
    assert os.path.exists(blinkit.CONFIG_FILE)


def test_load_config_rejects_non_object():
    with open(blinkit.CONFIG_FILE, "w") as f:
        f.write("[1]")
    with pytest.raises(core.ConfigError):
        core.load_config(blinkit.CONFIG_FILE)


def test_default_config_is_not_shared_between_callers():
    os.remove(blinkit.CONFIG_FILE)
    core.load_config(blinkit.CONFIG_FILE)["products"].append({"name": "x", "url": "y"})
    assert core.DEFAULT_CONFIG["products"] == []


def test_atomic_write_retries_when_windows_holds_the_file(monkeypatch):
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError
        real(a, b)

    monkeypatch.setattr(core.os, "replace", flaky)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    target = core.path("x.txt")
    core.atomic_write_text(target, "hello", mode=0o600)
    assert open(target).read() == "hello" and len(calls) == 3


def test_atomic_write_gives_up_eventually(monkeypatch):
    def locked(a, b):
        raise PermissionError

    monkeypatch.setattr(core.os, "replace", locked)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        core.atomic_write_text(core.path("y.txt"), "x")


@pytest.mark.parametrize("url,key", [
    ("https://blinkit.com/prn/a-b/prid/123", "123"),
    ("https://blinkit.com/prn/other-slug/prid/123?x=1", "123"),
    ("https://www.zepto.com/pn/a/pvid/abc-d", "abc-d"),
    ("https://blinkit.com/plain", "https://blinkit.com/plain"),
])
def test_product_key(url, key):
    assert core.product_key(url) == key


@pytest.mark.parametrize("url,ok", [
    ("https://blinkit.com/x", True), ("https://www.blinkit.com/x", True), ("https://[bad", False),
    (None, False), ("", False), ("HTTPS://blinkit.com/x", True),
])
def test_is_allowed_url(url, ok):
    assert core.is_allowed_url("blinkit", url) is ok


def test_current_statuses_without_db_or_tables():
    assert core.current_statuses(core.path("missing.db")) == {}
    empty = core.path("empty.db")
    sqlite3.connect(empty).close()
    assert core.current_statuses(empty) == {}


def test_read_state_meta_handles_bad_files():
    assert core.read_state_meta(core.path("none.json")) == {}
    with open(core.path("a.json.meta.json"), "w") as f:
        f.write("{bad")
    assert core.read_state_meta(core.path("a.json")) == {}
    with open(core.path("b.json.meta.json"), "w") as f:
        f.write("[1]")
    assert core.read_state_meta(core.path("b.json")) == {}


def test_launch_browser_explains_when_none_start():
    class C:
        def launch(self, **k):
            raise RuntimeError("missing at /Users/someone/cache")

    class P:
        chromium = C()

    with pytest.raises(RuntimeError) as e:
        core.launch_browser(P(), True, (None,))
    assert "No browser could be started" in str(e.value) and "someone" not in str(e.value)


def _pw(calls, fail_channels=()):
    class Ctx:
        def new_page(self):
            return "page"

    class B:
        def new_context(self, **k):
            calls.append(("context", k))
            return Ctx()

    class C:
        def launch(self, **k):
            calls.append(("launch", k))
            if k.get("channel") in fail_channels:
                raise RuntimeError("no")
            return B()

    class P:
        chromium = C()

    return P()


def test_old_sessions_keep_their_original_identity():
    calls = []
    state = core.path("state.json")
    core.open_tracking_page(_pw(calls), state)
    assert calls[0] == ("launch", {"headless": True})
    assert calls[1][1]["user_agent"] == core.LEGACY_USER_AGENT


def test_tracking_reuses_saved_browser_and_identity():
    calls = []
    state = core.path("state.json")
    with open(f"{state}.meta.json", "w") as f:
        json.dump({"channel": "chrome", "user_agent": "Saved UA"}, f)
    core.open_tracking_page(_pw(calls), state)
    assert calls[0][1]["channel"] == "chrome" and calls[1][1]["user_agent"] == "Saved UA"


def test_tracking_falls_back_when_saved_browser_is_gone():
    calls = []
    state = core.path("state.json")
    with open(f"{state}.meta.json", "w") as f:
        json.dump({"channel": "chrome", "user_agent": "Saved UA"}, f)
    core.open_tracking_page(_pw(calls, fail_channels=("chrome",)), state)
    assert [c[1].get("channel") for c in calls if c[0] == "launch"] == ["chrome", None]


def test_setup_browser_prefers_real_browser_without_fake_identity():
    calls = []
    core.launch_setup_browser(_pw(calls))
    assert calls[0][1] == {"headless": False, "channel": "chrome"}
    assert "user_agent" not in calls[1][1]


def test_inspect_page_while_navigating():
    class P:
        def evaluate(self, s):
            raise RuntimeError("Execution context was destroyed")

    assert core.inspect_page(P()) is None
    assert core.classify_block(None) is None


def test_challenge_that_clears_is_not_a_block():
    page = FakePage(title="Just a moment...", text="Verifying")
    seen = []

    def clear(ms):
        seen.append(ms)
        page.title_text, page.text = "Blinkit", "Home " * 20

    page.wait_for_timeout = clear
    assert core.verify_access(page, "blinkit", "t", Response(200))["title"] == "Blinkit"
    assert seen and core.cooldown_remaining("blinkit") == 0


def test_challenge_that_never_clears_is_a_block():
    page = FakePage(title="Just a moment...", text="Verifying")
    with pytest.raises(core.BlockedError) as e:
        core.verify_access(page, "blinkit", "t", None, wait_seconds=0)
    assert e.value.kind == "challenge" and "security check" in e.value.user_message


def test_empty_202_that_fills_in_is_not_a_block():
    page = FakePage(title="", text="")

    def fill(ms):
        page.text = "Real content " * 10

    page.wait_for_timeout = fill
    assert core.verify_access(page, "zepto", "t", Response(202))["length"] > 0


def test_block_message_for_rate_limit():
    assert "rate limiting" in core.block_message("zepto", "rate_limited")


def test_save_session_refusals():
    state = core.path("state.json")
    cases = [
        (FakePage(title="Just a moment...", text="x"), "security check"),
        (FakePage(title="x", text="access denied"), "Access Denied"),
        (FakePage(title="x", text=""), "finished loading"),
        (FakePage(title="x", text="Please provide your delivery location"), "delivery location"),
    ]
    for page, expected in cases:
        with pytest.raises(core.SetupRefused) as e:
            core.save_session(FakeContext(page), page, "blinkit", state, "chrome")
        assert expected in str(e.value)
        assert not os.path.exists(state)


def test_save_session_when_page_is_gone():
    class Gone:
        def evaluate(self, s):
            raise RuntimeError("Target closed")

    with pytest.raises(core.SetupRefused):
        core.save_session(None, Gone(), "blinkit", core.path("state.json"), "chrome")


def test_stock_ignores_foreign_urls():
    logs = []
    page = FakePage()
    result = core.check_stock(page, "blinkit", {"name": "a", "url": "https://evil.example/x"}, ["x"], ".", logs.append)
    assert result == "UNKNOWN" and "not a Blinkit" in logs[0]


def test_stock_load_failure_is_unknown_and_recorded():
    page = FakePage()
    page.fail_goto = RuntimeError("net::ERR_INTERNET_DISCONNECTED at 10.0.0.9")
    result = core.check_stock(page, "blinkit", {"name": "a", "url": "https://blinkit.com/x"}, ["x"], ".", lambda m: None)
    i = core.recent_incidents()[0]
    assert result == "UNKNOWN" and i["kind"] == "page_error" and "10.0.0.9" not in i["snippet"]


def test_stock_success_clears_a_previous_block():
    core.note_block("blinkit")
    page = FakePage(text="Milk\nADD\n" + "x" * 50)
    product = {"name": "a", "url": "https://blinkit.com/x"}
    assert core.check_stock(page, "blinkit", product, ["out of stock"], ".", lambda m: None) == "IN_STOCK"
    assert core.cooldown_remaining("blinkit") == 0


def test_stock_times_out_as_unknown_with_screenshot(tmp_path):
    page = FakePage(text="Nothing useful here " * 20)
    product = {"name": "A/B: long " * 30, "url": "https://blinkit.com/x"}
    # The long, odd product name must not stop the screenshot from saving.
    original = core.wait_for_stock_signal
    result = None
    try:
        core.wait_for_stock_signal = lambda *a, **k: original(*a, timeout_seconds=0)
        result = core.check_stock(page, "blinkit", product, ["out of stock"], str(tmp_path), lambda m: None)
    finally:
        core.wait_for_stock_signal = original
    assert result == "UNKNOWN" and len(os.listdir(tmp_path)) == 1


def test_stock_signal_survives_navigation():
    class P:
        n = 0

        def evaluate(self, s):
            P.n += 1
            if P.n == 1:
                raise RuntimeError("navigating")
            return {"title": "", "text": "", "length": 0} if s == core.PAGE_INFO_JS else "Milk\nADD"

        def wait_for_timeout(self, ms):
            pass

    assert core.wait_for_stock_signal(P(), ["out of stock"], timeout_seconds=3) == "IN_STOCK"


def test_screenshot_failure_is_harmless():
    class P:
        def screenshot(self, **k):
            raise RuntimeError("closed")

        def title(self):
            raise RuntimeError("closed")

    assert core.capture_diagnostic(P(), "blinkit", "blocked") == ""
    core._save_product_screenshot(P(), "x", core.path("."))
    assert core._safe_title(P()) == ""


def test_open_search_detects_late_block():
    page = FakePage(title="x", text="fine " * 10)

    def late(*a, **k):
        page.text = "access denied"
        raise TimeoutError

    page.wait_for_selector = late
    with pytest.raises(core.BlockedError):
        core.open_search(page, "blinkit", "https://blinkit.com/s", "x")


@pytest.mark.parametrize("text,kind", [("Please provide your delivery location", "location_missing"), ("nothing", "no_results")])
def test_finish_search_records_why_nothing_was_found(text, kind):
    core.finish_search(FakePage(text=text), "blinkit", "https://blinkit.com/s/?q=secret", 0)
    i = core.recent_incidents()[0]
    assert i["kind"] == kind and "secret" not in i["url"]


def test_finish_search_success_clears_block():
    core.note_block("blinkit")
    core.finish_search(FakePage(), "blinkit", "u", 3)
    assert core.cooldown_remaining("blinkit") == 0


def test_block_state_survives_bad_file():
    os.makedirs(core.DIAG_DIR, exist_ok=True)
    with open(core.BLOCK_FILE, "w") as f:
        f.write("{bad")
    assert core.cooldown_remaining("blinkit") == 0
    with open(core.BLOCK_FILE, "w") as f:
        f.write("[1]")
    assert core.cooldown_remaining("blinkit") == 0
    with open(core.BLOCK_FILE, "w") as f:
        json.dump({"blinkit": {"count": 1, "until": "not a date"}}, f)
    assert core.cooldown_remaining("blinkit") == 0


def test_cooldown_is_capped():
    for _ in range(12):
        until, _first = core.note_block("zepto")
    minutes = (until - datetime.now()).total_seconds() / 60
    assert 355 < minutes <= core.MAX_BLOCK_COOLDOWN_MINUTES


@pytest.mark.parametrize("config", [
    {"enabled": False, "bot_token": "1:a", "chat_id": "2"},
    {"enabled": True, "bot_token": "YOUR_BOT_TOKEN", "chat_id": "2"},
    {"enabled": True, "bot_token": "", "chat_id": "2"},
])
def test_telegram_not_sent_without_credentials(config, monkeypatch):
    def fail(*a, **k):
        raise AssertionError("should not send")

    monkeypatch.setattr(core.requests, "post", fail)
    assert core.send_telegram(config, "t", lambda m: None) is False


def test_telegram_rejected_message_is_logged(monkeypatch):
    class R:
        status_code = 400
        text = "bad request from bot1:abc"

    monkeypatch.setattr(core.requests, "post", lambda *a, **k: R())
    logs = []
    assert core.send_telegram({"enabled": True, "bot_token": "1:a", "chat_id": "2"}, "t", logs.append) is False
    assert "400" in logs[0] and "1:abc" not in logs[0]


def test_desktop_notification_is_shortened_and_failure_logged(monkeypatch):
    from plyer import notification
    got = {}
    monkeypatch.setattr(notification, "notify", lambda **k: got.update(k))
    core.send_desktop("t" * 100, "m" * 500, "a" * 100, lambda m: None)
    assert len(got["title"]) <= 60 and len(got["message"]) <= 240 and len(got["app_name"]) <= 60

    def fail(**k):
        raise RuntimeError("no backend")

    monkeypatch.setattr(notification, "notify", fail)
    logs = []
    core.send_desktop("t", "m", "a", logs.append)
    assert "no backend" in logs[0]


def _block_error(first=True, until=None):
    return core.BlockedError("blinkit", "blocked", "t", cooldown_until=until, first=first)


def test_alert_blocked_only_once_per_episode(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "send_desktop", lambda *a: sent.append("desktop"))
    monkeypatch.setattr(core, "send_telegram", lambda *a: sent.append("telegram"))
    core.alert_blocked("blinkit", {"desktop_notifications": True}, _block_error(first=False))
    assert sent == []
    core.alert_blocked("blinkit", {"desktop_notifications": True}, _block_error(until=None))
    assert sent == ["desktop", "telegram"]


def test_safe_url():
    assert core.safe_url("https://a.com/b?c=d#e") == "https://a.com/b"
    assert core.safe_url("") == "" and core.safe_url(None) == ""
    assert core.safe_url("https://[bad") == ""


def test_diagnostic_screenshots_are_pruned(monkeypatch):
    page = FakePage()
    os.makedirs(core.DIAG_DIR, exist_ok=True)
    for i in range(core.MAX_DIAG_SCREENSHOTS + 3):
        open(os.path.join(core.DIAG_DIR, f"old{i}.png"), "w").close()
        os.utime(os.path.join(core.DIAG_DIR, f"old{i}.png"), (i, i))
    assert core.capture_diagnostic(page, "blinkit", "blocked")
    assert len([f for f in os.listdir(core.DIAG_DIR) if f.endswith(".png")]) == core.MAX_DIAG_SCREENSHOTS


def test_recent_incidents_skip_damaged_lines():
    assert core.recent_incidents() == []
    core.record_incident("zepto", "no_results", "search")
    with open(core.INCIDENT_FILE, "a") as f:
        f.write("{damaged\n")
    assert [i["kind"] for i in core.recent_incidents()] == ["no_results"]


def test_recent_incidents_limit():
    for _ in range(5):
        core.record_incident("zepto", "no_results", "search")
    assert len(core.recent_incidents(limit=2)) == 2


def test_report_without_incidents_or_note():
    r = core.build_report()
    assert r["title"] == "[Report] Problem report" and "None recorded." in r["text"] and "no description" in r["text"]


def test_report_shows_older_setup_and_missing_package(monkeypatch):
    with open(core.path("state.json"), "w") as f:
        f.write("{}")
    assert core.provider_summary("blinkit")["setup_browser"] == "older setup"
    assert core.provider_summary("zepto")["setup_browser"] == "none"

    def missing(name):
        raise core.importlib_metadata.PackageNotFoundError

    monkeypatch.setattr(core.importlib_metadata, "version", missing)
    assert core._package_version("x") == "not installed"


def test_report_command(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["tracker_core.py", "--report", "it", "broke"])
    runpy.run_path(core.__file__, run_name="__main__")
    out = capsys.readouterr().out
    assert "it broke" in out and "issues/new" in out
    assert os.path.exists(os.path.join(core.DIAG_DIR, "report.txt"))


def test_report_command_usage(monkeypatch):
    monkeypatch.setattr("sys.argv", ["tracker_core.py"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(core.__file__, run_name="__main__")
    assert e.value.code == 2
