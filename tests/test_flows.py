import json, os, threading, time
import pytest

import tracker_core as core
import blinkit_tracker as blinkit
import zepto_tracker as zepto
import api
from fastapi.testclient import TestClient

BLOCK_HTML = "<title>Just a moment...</title><body>access denied<br><br>sorry, you have been blocked!<br>The page you are trying to access has blocked you</body>"
HOME_NOLOC = "<title>Blinkit</title><body>Delivery in 26 minutes<br>Welcome to blinkit<br>Please provide your delivery location to see products at nearby store<br>Detect my location</body>"
HOME_OK = "<title>Blinkit</title><body>Delivery in 12 minutes<br>Home - 12 MG Road<p>Fresh vegetables</p></body>"
IN_STOCK = "<title>p</title><body><h1>Milk</h1><div>ADD</div></body>"
OOS = "<title>p</title><body><h1>Milk</h1><div>Out of Stock</div><h2>Similar</h2><div>ADD</div></body>"
ERROR_PAGE = "<title>Oops</title><style>.x{padding:1px}</style><body>Something broke, address this later</body>"


class Site:
    def __init__(self):
        self.reset()

    def reset(self):
        self.mode = "ok"
        self.hits = 0
        self.body = {"status": 200, "html": HOME_OK}

    def handler(self, route):
        self.hits += 1
        if route.request.resource_type != "document":
            return route.abort()
        if self.mode == "blocked":
            return route.fulfill(status=403, content_type="text/html", body=BLOCK_HTML)
        if self.mode == "abort":
            return route.abort()
        if self.mode == "zepto202":
            return route.fulfill(status=202, content_type="text/html", body="")
        return route.fulfill(status=self.body["status"], content_type="text/html", body=self.body["html"])


site = Site()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    site.reset()
    orig_open = core.open_tracking_page

    def open_routed(p, state):
        b, c, pg = orig_open(p, state)
        c.route("**/*", site.handler)
        return b, c, pg

    def setup_routed(p):
        b = p.chromium.launch(headless=True)
        c = b.new_context()
        c.route("**/*", site.handler)
        return b, c, "chrome"

    monkeypatch.setattr(core, "open_tracking_page", open_routed)
    monkeypatch.setattr(core, "launch_setup_browser", setup_routed)
    monkeypatch.setattr(core, "MIN_PAGE_GAP_SECONDS", 0.0)
    monkeypatch.setattr(api, "SETUP_POLL_MS", 200)
    for p in ("tracker.db", "state.json", "zepto_state.json", "state.json.meta.json"):
        try:
            os.remove(core.path(p))
        except OSError:
            pass
    import shutil
    shutil.rmtree(core.DIAG_DIR, ignore_errors=True)
    core.save_config(blinkit.CONFIG_FILE, core.DEFAULT_CONFIG)
    core.save_config(zepto.CONFIG_FILE, core.DEFAULT_CONFIG)
    yield
    for s in list(api.active_setups.values()):
        if s.is_active:
            s.send("close", 10)
    api.active_setups.clear()


client = TestClient(api.app)


def make_session():
    with open(blinkit.STATE_FILE, "w") as f:
        json.dump({"cookies": [], "origins": []}, f)


def wait_state(provider, wanted, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/setup/{provider}/status").json()
        if r["state"] in wanted:
            return r
        time.sleep(0.2)
    raise AssertionError(f"state {r}")


# --- detection, on text captured from the real sites ---
@pytest.mark.parametrize("info,status,kind", [
    ({"title": "Just a moment...", "text": "access denied\n\nsorry, you have been blocked!", "length": 1312}, 403, "blocked"),
    ({"title": "", "text": "", "length": 0}, 202, "challenge"),
    ({"title": "www.zepto.com", "text": "This page isn’t working\n\nHTTP ERROR 429\nReload", "length": 97}, 202, "rate_limited"),
    ({"title": "Just a moment...", "text": "Verify you are human", "length": 40}, 200, "challenge"),
    ({"title": "30,000+ products", "text": "Delivery in 26 minutes", "length": 8535}, 200, None),
    ({"title": "Book", "text": "access denied " * 10, "length": 9000}, 200, None),
    ({"title": "", "text": "", "length": 0}, 200, None),
    ({"title": "x", "text": "hello", "length": 5}, 403, "blocked"),
])
def test_classify(info, status, kind):
    assert core.classify_block(info, status) == kind


# --- status storage ---
def test_unknown_never_replaces_known():
    db = blinkit.DB_FILE
    core.init_db(db)
    assert core.update_status(db, "u", "n", "IN_STOCK") is None
    assert core.update_status(db, "u", "n", "UNKNOWN") == "IN_STOCK"
    assert core.current_statuses(db)["u"] == "IN_STOCK"
    assert core.update_status(db, "u", "n", "IN_STOCK") == "IN_STOCK"  # so no false alert
    assert client.get("/history").json()["count"] == 1
    assert core.update_status(db, "u", "n", "OUT_OF_STOCK") == "IN_STOCK"
    assert client.get("/history").json()["count"] == 2


# --- stock pages through the real API and browser ---
def stock(url="https://blinkit.com/prn/milk/prid/1"):
    return client.get("/stock/blinkit", params={"url": url})


@pytest.mark.parametrize("html,expected", [(IN_STOCK, "IN_STOCK"), (OOS, "OUT_OF_STOCK")])
def test_stock_status(html, expected):
    make_session()
    site.body = {"status": 200, "html": html}
    t = time.time()
    r = stock()
    assert r.status_code == 200 and r.json()["status"] == expected
    assert time.time() - t < 8


def test_error_page_is_not_in_stock(monkeypatch):
    make_session()
    monkeypatch.setattr(core, "wait_for_stock_signal", lambda *a, **k: core.read_stock_signal(*a[:2]))
    site.body = {"status": 200, "html": ERROR_PAGE}
    assert stock().json()["status"] == "UNKNOWN"
    assert core.recent_incidents()[0]["kind"] == "unknown_status"


def test_location_prompt_is_reported():
    make_session()
    site.body = {"status": 200, "html": HOME_NOLOC}
    assert stock().json()["status"] == "UNKNOWN"
    assert core.recent_incidents()[0]["kind"] == "location_missing"


def test_block_on_stock_check_pauses_everything():
    make_session()
    site.mode = "blocked"
    r = stock()
    assert r.status_code == 403 and "Access Denied" in r.json()["detail"]
    assert core.cooldown_remaining("blinkit") > 1700
    i = core.recent_incidents()[0]
    assert i["kind"] == "blocked" and i["http_status"] == 403 and i["screenshot"]
    assert os.path.exists(os.path.join(core.DIAG_DIR, i["screenshot"]))
    hits = site.hits
    r2 = stock()
    assert r2.status_code == 429 and site.hits == hits
    s = client.get("/tracker/blinkit/status").json()
    assert s["cooldown_seconds"] > 0 and "Access Denied" in s["block_message"]
    assert core.current_statuses(blinkit.DB_FILE) == {}


def test_cooldown_grows():
    a, first = core.note_block("zepto")
    b, second = core.note_block("zepto")
    assert first and not second and (b - a).total_seconds() > 1500
    core.clear_block("zepto")
    assert core.cooldown_remaining("zepto") == 0


def test_cycle_stops_at_first_block_and_alerts_once(monkeypatch):
    make_session()
    sent = []
    monkeypatch.setattr(core, "send_desktop", lambda *a, **k: sent.append(a))
    cfg = dict(core.DEFAULT_CONFIG, products=[
        {"name": "a", "url": "https://blinkit.com/prn/a/prid/1"},
        {"name": "b", "url": "https://blinkit.com/prn/b/prid/2"},
    ])
    core.save_config(blinkit.CONFIG_FILE, cfg)
    blinkit.init_db()
    site.mode = "blocked"
    ev = threading.Event()
    meta = {}
    t = threading.Thread(target=api.background_tracker_worker, args=("blinkit", ev, meta))
    t.start()
    for _ in range(100):
        if meta.get("state") == "paused":
            break
        time.sleep(0.2)
    assert meta["state"] == "paused"
    assert site.hits == 1, "kept loading products after a block"
    assert len(sent) == 1
    ev.set(); t.join(10)
    assert not t.is_alive() and meta["state"] == "stopped"


def test_worker_survives_unexpected_error(monkeypatch):
    make_session()
    calls = []

    def boom(*a):
        calls.append(1)
        raise RuntimeError("bot123:abc at 10.1.2.3")

    monkeypatch.setattr(api, "run_tracking_cycle", boom)
    ev = threading.Event()
    meta = {}
    t = threading.Thread(target=api.background_tracker_worker, args=("blinkit", ev, meta))
    t.start()
    time.sleep(0.7)
    assert t.is_alive() and calls and meta["state"] == "waiting"
    ev.set(); t.join(10)
    i = core.recent_incidents()[0]
    assert i["kind"] == "worker_error" and "123:abc" not in i["snippet"] and "10.1.2.3" not in i["snippet"]


# --- location setup across threads ---
def test_setup_save_from_other_threads():
    site.body = {"status": 200, "html": HOME_OK}
    r = client.post("/setup/blinkit")
    assert r.status_code == 200 and r.json()["state"] == "open" and r.json()["browser"] == "chrome"
    result = {}
    t = threading.Thread(target=lambda: result.update(r=client.post("/setup/blinkit/save")))
    t.start(); t.join(35)
    assert result["r"].status_code == 200, result["r"].text
    assert json.load(open(blinkit.STATE_FILE))["cookies"] == []
    meta = core.read_state_meta(blinkit.STATE_FILE)
    assert meta["channel"] == "chrome" and "Mozilla" in meta["user_agent"]
    assert oct(os.stat(blinkit.STATE_FILE).st_mode)[-3:] == "600"
    assert client.get("/setup/blinkit/status").json()["state"] in ("saved", "none")


def test_setup_blocked_is_visible_and_not_saved():
    with open(blinkit.STATE_FILE, "w") as f:
        f.write('{"cookies":[{"keep":1}],"origins":[]}')
    site.mode = "blocked"
    r = client.post("/setup/blinkit")
    assert r.status_code == 200 and r.json()["state"] == "blocked"
    assert "Access Denied" in r.json()["message"]
    s = client.post("/setup/blinkit/save")
    assert s.status_code == 409 and "Access Denied" in s.json()["detail"]
    assert "keep" in open(blinkit.STATE_FILE).read()
    assert [i["kind"] for i in core.recent_incidents()] == ["blocked"], "one incident per episode"
    report = client.post("/report", json={"note": "cannot set location"}).json()
    assert "blocked" in report["title"] and "location setup" in report["text"]


def test_setup_refuses_when_location_not_set():
    site.body = {"status": 200, "html": HOME_NOLOC}
    client.post("/setup/blinkit")
    s = client.post("/setup/blinkit/save")
    assert s.status_code == 409 and "delivery location" in s.json()["detail"]
    assert not os.path.exists(blinkit.STATE_FILE)


def test_cancel_then_retry_after_block():
    site.mode = "blocked"
    assert client.post("/setup/blinkit").json()["state"] == "blocked"
    assert client.post("/setup/blinkit/cancel").json()["status"] == "cancelled"
    site.mode = "ok"
    r = client.post("/setup/blinkit")
    assert r.status_code == 200 and r.json()["state"] == "open" and r.json()["status"] == "browser_launched"


def test_second_open_reuses_window():
    client.post("/setup/blinkit")
    r = client.post("/setup/blinkit")
    assert r.status_code == 200 and r.json()["status"] == "browser_already_open"


def test_user_closing_window_is_noticed(monkeypatch):
    client.post("/setup/blinkit")
    session = api.active_setups["blinkit"]
    session.commands.put({"name": "close", "done": threading.Event(), "result": {}})
    wait_state("blinkit", ("closed",))
    assert api.get_active_setup("blinkit") is None
    assert client.post("/setup/blinkit").status_code == 200


def test_successful_save_clears_cooldown():
    core.note_block("blinkit")
    client.post("/setup/blinkit")
    assert client.post("/setup/blinkit/save").status_code == 200
    assert core.cooldown_remaining("blinkit") == 0


def test_launch_failure_message(monkeypatch):
    def bad(p):
        raise RuntimeError("No browser could be started at /Users/someone/x")
    monkeypatch.setattr(core, "launch_setup_browser", bad)
    r = client.post("/setup/zepto")
    assert r.status_code == 500 and "No browser" in r.json()["detail"] and "someone" not in r.json()["detail"]


def test_real_browser_preference_order():
    calls = []

    class C:
        def launch(self, **k):
            calls.append(k.get("channel"))
            if k.get("channel") == "chrome":
                raise RuntimeError("not found")
            return "b"

    class P:
        chromium = C()

    assert core.launch_browser(P(), False, ("chrome", "msedge", None)) == ("b", "msedge")
    assert calls == ["chrome", "msedge"]


# --- search parsing ---
def test_blinkit_names_with_min_and_off():
    assert blinkit.parse_search_card("1", "8 MINS\nMint Chutney\n200 g\n₹50\nADD")["name"] == "Mint Chutney"
    item = blinkit.parse_search_card("1", "20% OFF\n9 mins\nNescafe Coffee\n50 g\n₹99\nADD")
    assert item["name"] == "Nescafe Coffee" and item["details"] == "50 g | Rs. 99" and item["status"] == "IN_STOCK"
    assert blinkit.parse_search_card("1", "Milk\n₹5\nOut of Stock")["status"] == "OUT_OF_STOCK"


def test_zepto_names():
    assert zepto.parse_search_anchor("u", "20% Off\nFilter Coffee\n4.4\n(7.7k)\n250 g\n₹99\n₹120\nADD")["name"] == "Filter Coffee"
    item = zepto.parse_search_anchor("u", "Milk (Toned)\n500 ml\n₹30\nADD")
    assert item["name"] == "Milk (Toned)" and item["details"] == "500 ml | Rs. 30"


def test_search_blocked_gives_403():
    make_session()
    site.mode = "blocked"
    r = client.get("/search/blinkit", params={"q": "milk"})
    assert r.status_code == 403


def test_search_results():
    make_session()
    site.body = {"status": 200, "html": "<body><div role='button' id='12'>8 MINS\n<br>Haldiram's Mix<br>200 g<br>₹50<br>ADD</div><div role='button' id='nav'>Cart</div></body>"}
    r = client.get("/search/blinkit", params={"q": "mix"}).json()
    assert r["count"] == 1 and r["results"][0]["url"].endswith("/prid/12") and "Haldiram" in r["results"][0]["name"]


# --- API protection and validation ---
@pytest.mark.parametrize("headers,code", [
    ({"origin": "https://evil.example"}, 403),
    ({"host": "evil.example"}, 403),
    ({"host": "evil.example", "origin": "http://evil.example"}, 403),
    ({"origin": "http://192.168.1.5:8000", "host": "192.168.1.5:8000"}, 200),
    ({"origin": "http://localhost:53000"}, 200),
    ({"host": "192.168.1.5:8000"}, 200),
    ({"host": "my-mac.local:8000"}, 200),
])
def test_origin_guard(headers, code):
    assert client.get("/health", headers=headers).status_code == code


def test_foreign_site_cannot_reset():
    core.save_config(blinkit.CONFIG_FILE, dict(core.DEFAULT_CONFIG, tracked_keywords=["keep"]))
    assert client.post("/reset", headers={"origin": "https://evil.example"}).status_code == 403
    assert core.load_config(blinkit.CONFIG_FILE)["tracked_keywords"] == ["keep"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://blinkit.com/x", "https://evil.example/prid/1", "https://blinkit.com.evil.example/x", "http://169.254.169.254/"])
def test_bad_urls(url):
    make_session()
    assert client.post("/products/blinkit", json={"name": "x", "url": url}).status_code == 400
    assert stock(url).status_code == 400


def test_products_include_status():
    client.post("/products/blinkit", json={"name": "m", "url": "https://blinkit.com/prn/m/prid/1", "status": "IN_STOCK"})
    assert client.get("/products/blinkit").json()["products"][0]["status"] == "IN_STOCK"


def test_keywords_limits():
    assert client.post("/keywords/blinkit", json={"keywords": [str(i) for i in range(30)]}).status_code == 400
    assert client.post("/keywords/blinkit", json={"keywords": [" a ", "a", ""]}).json()["keywords"] == ["a"]


def test_interval_floor():
    for v, want in [(0, 5), (1, 5), ("x", 10), (None, 10), (15, 15), (-3, 5)]:
        assert core.check_interval_minutes({"check_interval_minutes": v}) == want


def test_config_writes_survive_threads():
    def add(i):
        blinkit.add_to_config(f"p{i}", f"https://blinkit.com/prn/p/prid/{i}")
    ts = [threading.Thread(target=add, args=(i,)) for i in range(40)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(core.load_config(blinkit.CONFIG_FILE)["products"]) == 40


def test_corrupt_config_message():
    with open(blinkit.CONFIG_FILE, "w") as f:
        f.write("{oops")
    c = TestClient(api.app, raise_server_exceptions=False)
    r = c.get("/products/blinkit")
    assert r.status_code == 500 and "not valid JSON" in r.json()["detail"]


def test_bad_config_rows_are_skipped():
    core.save_config(blinkit.CONFIG_FILE, dict(core.DEFAULT_CONFIG, products=[{"name": "no url"}, "x", {"name": "a", "url": "https://blinkit.com/a"}]))
    assert len(client.get("/products/blinkit").json()["products"]) == 1


def test_reset_clears_everything():
    make_session()
    core.note_block("blinkit")
    core.record_incident("blinkit", "blocked", "x")
    assert client.post("/reset").status_code == 200
    assert not os.path.exists(blinkit.STATE_FILE) and core.recent_incidents() == [] and core.cooldown_remaining("blinkit") == 0


# --- reports and notifications ---
def test_report_has_no_private_data():
    core.record_incident("blinkit", "page_error", "search", url="https://blinkit.com/s/?q=secretquery",
                         title="from 203.0.113.9", snippet="POST https://api.telegram.org/bot123456:ABCdef-x/sendMessage me@mail.com /Users/akshay/x 2001:db8:0:0:0:0:0:1")
    r = client.post("/report", json={"note": "my ip 203.0.113.9"}).json()
    for bad in ("secretquery", "203.0.113.9", "123456:ABC", "me@mail.com", "akshay", "2001:db8"):
        assert bad not in r["text"] and bad not in r["github_url"].split("?",1)[1], bad
    assert r["github_url"].startswith("https://github.com/akshay4/Blinkit-Zepto-Tracker/issues/new?")


def test_report_url_fits():
    for i in range(30):
        core.record_incident("blinkit", "page_error", "search", snippet="x" * 300, title="y" * 120, url="https://blinkit.com/" + "a" * 200)
    r = core.build_report("n" * 2000)
    assert len(r["github_url"]) <= core.MAX_REPORT_URL_LENGTH


def test_incidents_capped():
    for i in range(core.MAX_INCIDENTS + 20):
        core.record_incident("zepto", "no_results", "search")
    with open(core.INCIDENT_FILE) as f:
        assert len(f.readlines()) == core.MAX_INCIDENTS


def test_telegram_escape_and_token_scrub(monkeypatch):
    seen = {}

    class R:
        status_code = 200
        text = ""

    monkeypatch.setattr(core.requests, "post", lambda url, json, timeout: seen.update(json) or R())
    blinkit.send_telegram_notification("Tom_and*Jerry", "OUT_OF_STOCK", "IN_STOCK", "https://blinkit.com/x", {"enabled": True, "bot_token": "1:a", "chat_id": "2"})
    assert r"Tom\_and\*Jerry" in seen["text"]

    logs = []

    def fail(*a, **k):
        raise RuntimeError("cannot reach https://api.telegram.org/bot99:SECRET-x/sendMessage")

    monkeypatch.setattr(core.requests, "post", fail)
    core.send_telegram({"enabled": True, "bot_token": "99:SECRET-x", "chat_id": "2"}, "t", logs.append)
    assert logs and "SECRET" not in logs[0]


def test_discovery_alert_text(monkeypatch):
    got = []
    monkeypatch.setattr(core, "send_desktop", lambda t, m, a, l: got.append((t, m)))
    blinkit.send_desktop_notification("Keyword 'x'", "DISCOVERED_NEW_ITEMS")
    assert "OUT OF STOCK" not in got[0][1]


def test_pace_spaces_requests(monkeypatch):
    monkeypatch.setattr(core, "MIN_PAGE_GAP_SECONDS", 0.3)
    monkeypatch.setattr(core.random, "uniform", lambda a, b: 0)
    core._next_page_time.clear()
    t = time.time()
    [core.pace("zepto") for _ in range(3)]
    assert time.time() - t >= 0.55


def test_dashboard_served():
    r = client.get("/")
    assert r.status_code == 200 and "Report a problem" in r.text
