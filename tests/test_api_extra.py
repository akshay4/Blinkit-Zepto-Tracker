"""API and setup paths not reached by the end-to-end flows."""
import os
import runpy
import threading
import time

import pytest
from fastapi.testclient import TestClient

import api
import blinkit_tracker as blinkit
import tracker_core as core
import zepto_tracker as zepto
from fakes import FakeBrowser, FakeContext, FakePage, FakePlaywright

client = TestClient(api.app)
safe_client = TestClient(api.app, raise_server_exceptions=False)
URL = "https://blinkit.com/prn/m/prid/1"


def session(tracker=blinkit):
    with open(tracker.STATE_FILE, "w") as f:
        f.write('{"cookies":[],"origins":[]}')


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for s in list(api.active_setups.values()):
        if s.is_active:
            s.send("close", 5)
    api.active_setups.clear()
    for e in api.stop_events.values():
        e.set()
    for t in api.active_threads.values():
        t.join(5)
    api.active_threads.clear()
    api.stop_events.clear()
    api.thread_metadata.clear()


@pytest.mark.parametrize("method,path", [
    ("post", "/setup/nope"), ("get", "/setup/nope/status"), ("post", "/setup/nope/save"), ("post", "/setup/nope/cancel"),
    ("get", "/products/nope"), ("get", "/keywords/nope"), ("get", "/search/nope?q=a"), ("get", "/stock/nope?url=a"),
    ("get", "/tracker/nope/status"), ("post", "/tracker/nope/start"), ("post", "/tracker/nope/stop"),
])
def test_unknown_provider(method, path):
    assert getattr(client, method)(path).status_code == 400


@pytest.mark.parametrize("headers", [
    {"origin": "null"},
    {"host": "["},
    {"origin": "http://["},
    {"origin": "https://8.8.8.8"},
])
def test_guard_refuses_odd_requests(headers):
    assert safe_client.post("/tracker/all/stop", headers=headers).status_code == 403


def test_cors_allows_lan_and_not_others():
    ok = client.options("/health", headers={"origin": "http://192.168.1.9:3000", "access-control-request-method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://192.168.1.9:3000"
    assert "access-control-allow-credentials" not in ok.headers


def test_health_and_diagnostics():
    assert client.get("/health").json()["status"] == "online"
    core.record_incident("zepto", "no_results", "search")
    d = client.get("/diagnostics").json()
    assert d["incidents"][0]["kind"] == "no_results" and set(d["providers"]) == {"blinkit", "zepto"}


def test_history_without_tables():
    assert client.get("/history").json()["count"] == 0
    import sqlite3
    sqlite3.connect(blinkit.DB_FILE).close()
    assert client.get("/history").json()["count"] == 0


# --- products and keywords ---
def test_duplicate_product_is_ignored():
    client.post("/products/blinkit", json={"name": "m", "url": URL, "status": "IN_STOCK"})
    assert client.post("/products/blinkit", json={"name": "m", "url": URL}).json()["status"] == "ignored"


def test_name_with_brackets_can_be_added():
    r = safe_client.post("/products/blinkit", json={"name": "Milk [/500ml]", "url": URL, "status": "IN_STOCK"})
    assert r.status_code == 200 and client.get("/products/blinkit").json()["products"][0]["name"] == "Milk [/500ml]"


def test_invalid_initial_status_becomes_unknown(monkeypatch):
    called = []
    monkeypatch.setattr(api, "scrape_and_update_product_stock_bg", lambda *a: called.append(a))
    client.post("/products/blinkit", json={"name": "m", "url": URL, "status": "HACKED"})
    assert client.get("/products/blinkit").json()["products"][0]["status"] == "UNKNOWN" and called


def test_delete_product():
    client.post("/products/blinkit", json={"name": "m", "url": URL, "status": "IN_STOCK"})
    assert client.delete("/products/blinkit", params={"url": "https://blinkit.com/other"}).status_code == 404
    assert client.delete("/products/blinkit", params={"url": URL}).json()["status"] == "deleted"
    assert client.get("/products/blinkit").json()["products"] == []


def test_get_keywords():
    client.post("/keywords/zepto", json={"keywords": ["milk"]})
    assert client.get("/keywords/zepto").json()["keywords"] == ["milk"]
    assert client.post("/keywords/zepto", json={"keywords": ["x" * 101]}).status_code == 400


# --- background scrape after adding ---
def test_bg_scrape_skips_without_session_or_in_cooldown(monkeypatch):
    monkeypatch.setattr(core, "tracking_page", lambda s: pytest.fail("should not open a browser"))
    api.scrape_and_update_product_stock_bg("blinkit", "m", URL)
    session()
    core.note_block("blinkit")
    api.scrape_and_update_product_stock_bg("blinkit", "m", URL)


class FakePageContext:
    def __init__(self, page=None, error=None):
        self.page, self.error = page or FakePage(), error

    def __call__(self, state):
        return self

    def __enter__(self):
        if self.error:
            raise self.error
        return self.page

    def __exit__(self, *exc):
        return False


def test_bg_scrape_updates_status(monkeypatch):
    session()
    monkeypatch.setattr(core, "tracking_page", FakePageContext())
    monkeypatch.setattr(blinkit, "check_product_stock", lambda p, x: "OUT_OF_STOCK")
    api.scrape_and_update_product_stock_bg("blinkit", "m", URL)
    assert core.current_statuses(blinkit.DB_FILE)[URL] == "OUT_OF_STOCK"


@pytest.mark.parametrize("error", [core.BlockedError("blinkit", "blocked", "t"), RuntimeError("boom")])
def test_bg_scrape_survives_errors(monkeypatch, error):
    session()
    monkeypatch.setattr(core, "tracking_page", FakePageContext(error=error))
    api.scrape_and_update_product_stock_bg("blinkit", "m", URL)


# --- live routes ---
def test_live_routes_need_session():
    assert client.get("/search/blinkit", params={"q": "a"}).status_code == 400
    assert client.get("/stock/blinkit", params={"url": URL}).status_code == 400


def test_search_cooldown_and_rate_limit(monkeypatch):
    session()
    core.note_block("blinkit")
    assert client.get("/search/blinkit", params={"q": "a"}).status_code == 429
    core.clear_block("blinkit")
    monkeypatch.setattr(core, "tracking_page", FakePageContext(error=core.BlockedError("blinkit", "rate_limited", "t")))
    assert client.get("/search/blinkit", params={"q": "a"}).status_code == 429


def test_search_unexpected_error_is_scrubbed(monkeypatch):
    session()
    monkeypatch.setattr(core, "tracking_page", FakePageContext(error=RuntimeError("fail at 10.2.3.4")))
    r = client.get("/search/blinkit", params={"q": "a"})
    assert r.status_code == 500 and "10.2.3.4" not in r.json()["detail"]


def test_search_formats_zepto_urls(monkeypatch):
    session(zepto)
    monkeypatch.setattr(core, "tracking_page", FakePageContext())
    monkeypatch.setattr(zepto, "search_products", lambda p, q: [{"name": "m", "details": "d", "url": "https://www.zepto.com/p/1", "status": "IN_STOCK"}])
    assert client.get("/search/zepto", params={"q": "a"}).json()["results"][0]["url"].endswith("/p/1")


def test_stock_unexpected_error(monkeypatch):
    session()
    monkeypatch.setattr(core, "tracking_page", FakePageContext(error=RuntimeError("boom")))
    assert client.get("/stock/blinkit", params={"url": URL}).status_code == 500


def test_stock_uses_tracked_name(monkeypatch):
    session()
    client.post("/products/blinkit", json={"name": "My Milk", "url": URL, "status": "IN_STOCK"})
    seen = []
    monkeypatch.setattr(core, "tracking_page", FakePageContext())
    monkeypatch.setattr(blinkit, "check_product_stock", lambda p, x: seen.append(x["name"]) or "IN_STOCK")
    assert client.get("/stock/blinkit", params={"url": URL}).json()["status"] == "IN_STOCK" and seen == ["My Milk"]


def test_stock_check_with_brackets_in_name(monkeypatch):
    session()
    core.save_config(blinkit.CONFIG_FILE, dict(core.DEFAULT_CONFIG, products=[{"name": "Milk [/500ml]", "url": URL}]))
    monkeypatch.setattr(core, "tracking_page", FakePageContext(FakePage(text="Milk\nADD")))
    assert client.get("/stock/blinkit", params={"url": URL}).json()["status"] == "IN_STOCK"


# --- tracker controls ---
def test_start_needs_session():
    assert client.post("/tracker/blinkit/start").status_code == 400
    r = client.post("/tracker/all/start").json()
    assert r["blinkit"]["status"] == "error"


def test_start_all_reports_unexpected_error(monkeypatch):
    def boom(p):
        raise RuntimeError("bad")

    monkeypatch.setattr(api, "start_background_tracker", boom)
    monkeypatch.setattr(api, "stop_background_tracker", boom)
    assert client.post("/tracker/all/start").json()["zepto"]["status"] == "error"
    assert client.post("/tracker/all/stop").json()["zepto"]["status"] == "error"


def test_start_status_stop_cycle(monkeypatch):
    session()
    monkeypatch.setattr(api, "run_tracking_cycle", lambda *a: None)
    assert client.post("/tracker/blinkit/start").json()["status"] == "started"
    assert client.post("/tracker/blinkit/start").json()["status"] == "active"
    time.sleep(0.3)
    s = client.get("/tracker/blinkit/status").json()
    assert s["is_running"] and s["state"] == "waiting" and s["setup_saved"]
    assert "blinkit" in client.get("/health").json()["active_background_threads"]
    assert client.post("/tracker/blinkit/stop").json()["status"] == "stopped"
    assert client.post("/tracker/blinkit/stop").json()["status"] == "inactive"
    assert client.get("/tracker/blinkit/status").json()["state"] == "stopped"


def test_stop_reports_when_page_is_still_finishing(monkeypatch):
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True)
    t.start()
    monkeypatch.setitem(api.active_threads, "blinkit", t)
    monkeypatch.setitem(api.stop_events, "blinkit", threading.Event())
    monkeypatch.setattr(t, "join", lambda timeout=None: None)
    assert client.post("/tracker/blinkit/stop").json()["status"] == "stopping"
    assert "blinkit" in api.active_threads
    release.set()


def test_worker_checks_alerts_and_finds_keywords(monkeypatch):
    session()
    core.save_config(blinkit.CONFIG_FILE, dict(core.DEFAULT_CONFIG, products=[{"name": "m", "url": URL}], tracked_keywords=["milk"]))
    blinkit.init_db()
    blinkit.update_status(URL, "m", "OUT_OF_STOCK")
    monkeypatch.setattr(core, "tracking_page", FakePageContext())
    monkeypatch.setattr(blinkit, "check_product_stock", lambda p, x: "IN_STOCK")
    monkeypatch.setattr(blinkit, "search_products", lambda p, q: [{"name": "Cream", "details": "", "id": "2", "status": "IN_STOCK"}, {"name": "m", "details": "", "id": "1", "status": "IN_STOCK"}])
    alerts, desktop, telegram = [], [], []
    monkeypatch.setattr(blinkit, "trigger_notifications", lambda *a: alerts.append(a))
    monkeypatch.setattr(blinkit, "send_desktop_notification", lambda *a: desktop.append(a))
    monkeypatch.setattr(core, "send_telegram", lambda *a, **k: telegram.append(a))
    class Instant(threading.Event):
        def wait(self, timeout=None):
            return False

    notified = set()
    api.run_tracking_cycle("blinkit", blinkit, core.load_config(blinkit.CONFIG_FILE), Instant(), notified)
    assert len(alerts) == 1 and len(desktop) == 1 and len(telegram) == 1 and len(notified) == 1
    api.run_tracking_cycle("blinkit", blinkit, core.load_config(blinkit.CONFIG_FILE), Instant(), notified)
    assert len(desktop) == 1, "the same new product must not alert twice"


def test_cycle_handles_zepto_urls_and_stop(monkeypatch):
    session(zepto)
    core.save_config(zepto.CONFIG_FILE, dict(core.DEFAULT_CONFIG, products=[{"name": "m", "url": "https://www.zepto.com/pn/m/pvid/1"}], tracked_keywords=["milk"]))
    zepto.init_db()
    monkeypatch.setattr(core, "tracking_page", FakePageContext())
    monkeypatch.setattr(zepto, "check_product_stock", lambda p, x: "IN_STOCK")
    monkeypatch.setattr(zepto, "search_products", lambda p, q: [{"name": "c", "details": "", "url": "https://www.zepto.com/pn/c/pvid/2", "status": "IN_STOCK"}])
    notified = set()

    class StopAfterProducts(threading.Event):
        calls = 0

        def is_set(self):
            return False

        def wait(self, timeout=None):
            return False

    api.run_tracking_cycle("zepto", zepto, core.load_config(zepto.CONFIG_FILE), StopAfterProducts(), notified)
    assert notified == {"https://www.zepto.com/pn/c/pvid/2"}

    stopped = threading.Event()
    stopped.set()
    monkeypatch.setattr(zepto, "check_product_stock", lambda p, x: pytest.fail("stopped"))
    api.run_tracking_cycle("zepto", zepto, core.load_config(zepto.CONFIG_FILE), stopped, set())
    cfg = dict(core.load_config(zepto.CONFIG_FILE), products=[])
    monkeypatch.setattr(zepto, "search_products", lambda p, q: pytest.fail("stopped"))
    api.run_tracking_cycle("zepto", zepto, cfg, stopped, set())


def test_worker_keeps_running_if_blocked_while_config_is_damaged(monkeypatch):
    session()

    def blocked(*a):
        core.note_block("blinkit")
        with open(blinkit.CONFIG_FILE, "w") as f:
            f.write("{bad")
        raise core.BlockedError("blinkit", "blocked", "t")

    monkeypatch.setattr(api, "run_tracking_cycle", blocked)
    ev, meta = threading.Event(), {}
    t = threading.Thread(target=api.background_tracker_worker, args=("blinkit", ev, meta), daemon=True)
    t.start()
    time.sleep(0.6)
    alive = t.is_alive()
    ev.set()
    t.join(5)
    assert alive and meta["state"] == "stopped"


# --- location setup paths ---
def fake_setup(monkeypatch, page=None, context=None, browser=None):
    import playwright.sync_api as sp
    page = page or FakePage()
    context = context or FakeContext(page)
    browser = browser or FakeBrowser()
    monkeypatch.setattr(sp, "sync_playwright", FakePlaywright)
    monkeypatch.setattr(core, "launch_setup_browser", lambda p: (browser, context, "chrome"))
    monkeypatch.setattr(api, "SETUP_POLL_MS", 10)
    page.wait_for_timeout = lambda ms: time.sleep(0.02)
    return page, context, browser


def wait_for(session_obj, *states):
    for _ in range(300):
        if session_obj.info["state"] in states:
            return
        time.sleep(0.02)
    raise AssertionError(session_obj.info)


def test_setup_routes_without_a_window():
    assert client.get("/setup/blinkit/status").json()["state"] == "none"
    assert client.post("/setup/blinkit/cancel").json()["status"] == "inactive"
    assert client.post("/setup/blinkit/save").status_code == 400


def test_setup_slow_page_stays_open(monkeypatch):
    page, _, _ = fake_setup(monkeypatch)
    page.fail_goto = RuntimeError("Timeout")
    r = client.post("/setup/blinkit").json()
    assert r["state"] == "open" and "slow" in r["message"]


def test_setup_challenge_is_explained(monkeypatch):
    fake_setup(monkeypatch, page=FakePage(title="Just a moment...", text="Verifying"))
    r = client.post("/setup/blinkit").json()
    assert r["state"] == "challenge" and "security check" in r["message"]
    assert core.recent_incidents() == [] and core.cooldown_remaining("blinkit") == 0


def test_setup_notices_block_after_opening(monkeypatch):
    page, _, _ = fake_setup(monkeypatch)
    assert client.post("/setup/blinkit").json()["state"] == "open"
    page.text = "access denied"
    wait_for(api.active_setups["blinkit"], "blocked")
    page.text = "Home - 12 MG Road"
    wait_for(api.active_setups["blinkit"], "open")
    assert "block_message" not in api.active_setups["blinkit"].info


def test_setup_block_record_failure_still_shows_block(monkeypatch):
    fake_setup(monkeypatch, page=FakePage(text="access denied"))

    def fail(*a):
        raise RuntimeError("disk full")

    monkeypatch.setattr(core, "register_block", fail)
    assert client.post("/setup/blinkit").json()["state"] == "blocked"


def test_setup_detects_closed_window(monkeypatch):
    _, context, _ = fake_setup(monkeypatch)
    client.post("/setup/blinkit")
    s = api.active_setups["blinkit"]
    context.pages.clear()
    wait_for(s, "closed")
    s.thread.join(5)
    assert client.post("/setup/blinkit/save").status_code == 400


def test_setup_detects_browser_quit(monkeypatch):
    _, _, browser = fake_setup(monkeypatch)
    client.post("/setup/blinkit")
    s = api.active_setups["blinkit"]
    browser.closed = True
    wait_for(s, "closed")


def test_setup_times_out(monkeypatch):
    fake_setup(monkeypatch)
    monkeypatch.setattr(api, "SETUP_IDLE_LIMIT_SECONDS", 0.1)
    client.post("/setup/blinkit")
    wait_for(api.active_setups["blinkit"], "closed")
    assert "timed out" in api.active_setups["blinkit"].info["message"]


def test_setup_survives_tab_closing_mid_poll(monkeypatch):
    page, _, _ = fake_setup(monkeypatch)
    client.post("/setup/blinkit")
    calls = []

    def closing(ms):
        calls.append(ms)
        if len(calls) < 3:
            raise RuntimeError("Target closed")
        time.sleep(0.02)

    page.wait_for_timeout = closing
    time.sleep(0.4)
    assert len(calls) >= 3 and api.active_setups["blinkit"].is_active


def test_setup_save_failure_keeps_window_open(monkeypatch):
    fake_setup(monkeypatch)
    client.post("/setup/blinkit")

    def fail(*a):
        raise RuntimeError("disk full at /Users/someone/x")

    monkeypatch.setattr(core, "save_session", fail)
    r = client.post("/setup/blinkit/save")
    assert r.status_code == 500 and "someone" not in r.json()["detail"]
    assert api.get_active_setup("blinkit") is not None


def test_setup_save_after_all_tabs_gone(monkeypatch):
    _, context, _ = fake_setup(monkeypatch)
    s = api.SetupSession("blinkit")
    s.ready.wait(5)
    result = {}

    def handle():
        context.pages.clear()
        cmd = {"name": "save", "done": threading.Event(), "result": result}
        s._handle(cmd, context, "chrome")

    handle()
    assert result["status"] == 409
    wait_for(s, "closed")


def test_setup_navigation_tracking():
    s = api.SetupSession.__new__(api.SetupSession)
    s.last_status = None

    class Frame:
        pass

    frame = Frame()
    frame.page = Frame()
    frame.page.main_frame = frame

    class Req:
        def is_navigation_request(self):
            return True

    class Resp:
        request = Req()
        status = 403

    Resp.frame = frame
    s._track_navigation(Resp())
    assert s.last_status == 403
    s._track_navigation(object())
    assert s.last_status == 403


def test_setup_observe_with_no_pages_or_navigation():
    s = api.SetupSession.__new__(api.SetupSession)
    s.info = {"state": "open"}
    s._observe(FakeContext(FakePage()).__class__.__new__(type("E", (), {"pages": []})))

    class Gone:
        def evaluate(self, x):
            raise RuntimeError("navigating")

    s._observe(FakeContext(Gone()))
    assert s.info["state"] == "open"


def test_setup_send_times_out():
    s = api.SetupSession.__new__(api.SetupSession)
    import queue
    s.commands = queue.Queue()
    r = s.send("save", timeout=0.01)
    assert r["status"] == 504


def test_pending_commands_are_released_when_window_ends():
    s = api.SetupSession.__new__(api.SetupSession)
    import queue
    s.commands = queue.Queue()
    cmd = {"name": "save", "done": threading.Event(), "result": {}}
    s.commands.put(cmd)
    s._drain_commands()
    assert cmd["done"].is_set() and cmd["result"]["status"] == 409


# --- reset ---
def test_reset_stops_trackers_and_windows(monkeypatch):
    session()
    monkeypatch.setattr(api, "run_tracking_cycle", lambda *a: None)
    fake_setup(monkeypatch)
    client.post("/tracker/blinkit/start")
    client.post("/setup/zepto")
    assert client.post("/reset").status_code == 200
    assert api.active_threads == {} and api.active_setups == {}


def test_reset_with_locked_database(monkeypatch):
    blinkit.init_db()
    real = os.remove

    def locked(p):
        if p == blinkit.DB_FILE:
            raise PermissionError
        real(p)

    monkeypatch.setattr(api.os, "remove", locked)
    assert client.post("/reset").status_code == 200
    assert client.get("/history").json()["count"] == 0


def test_reset_with_locked_state_file(monkeypatch):
    session()

    def locked(p):
        raise PermissionError

    monkeypatch.setattr(api.os, "remove", locked)
    assert client.post("/reset").status_code == 200


# --- startup ---
def test_dashboard_opens_once_on_startup(monkeypatch):
    import webbrowser
    opened = threading.Event()
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.set())
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    monkeypatch.delenv("TRACKER_NO_BROWSER")
    monkeypatch.delenv("TRACKER_BROWSER_OPENED", raising=False)
    with TestClient(api.app):
        assert opened.wait(3)
    os.environ.pop("TRACKER_BROWSER_OPENED", None)


def test_startup_survives_browser_open_failure(monkeypatch, capsys):
    import webbrowser
    done = threading.Event()

    def fail(url):
        done.set()
        raise RuntimeError("no display")

    monkeypatch.setattr(webbrowser, "open", fail)
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    monkeypatch.delenv("TRACKER_NO_BROWSER")
    monkeypatch.delenv("TRACKER_BROWSER_OPENED", raising=False)
    with TestClient(api.app):
        assert done.wait(3)
    time.sleep(0.05)
    os.environ.pop("TRACKER_BROWSER_OPENED", None)


@pytest.mark.parametrize("network_ok", [True, False])
def test_server_entry_point(monkeypatch, capsys, network_ok):
    import socket
    import uvicorn
    runs = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: runs.append((a, k)))

    class S:
        def __init__(self, *a):
            pass

        def connect(self, addr):
            if not network_ok:
                raise OSError

        def getsockname(self):
            return ("192.168.1.2", 1)

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", S)
    runpy.run_path(api.__file__, run_name="__main__")
    assert runs and ("192.168.1.2" in capsys.readouterr().out) == network_ok
