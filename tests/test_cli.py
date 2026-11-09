"""The terminal modes of both trackers, and the reset script."""
import json
import os
import runpy

import pytest

import blinkit_tracker as blinkit
import reset_tracker
import tracker_core as core
import zepto_tracker as zepto
from fakes import FakeBrowser, FakeContext, FakePage, FakePlaywright

TRACKERS = [blinkit, zepto]
PRODUCT_URL = {blinkit: "https://blinkit.com/prn/milk/prid/1", zepto: "https://www.zepto.com/pn/milk/pvid/1"}


class StopLoop(Exception):
    pass


@pytest.fixture(params=TRACKERS, ids=["blinkit", "zepto"])
def tracker(request):
    return request.param


@pytest.fixture
def playwright_fakes(monkeypatch):
    import playwright.sync_api as api

    browser = FakeBrowser()
    page = FakePage()
    monkeypatch.setattr(api, "sync_playwright", FakePlaywright)
    monkeypatch.setattr(core, "open_tracking_page", lambda p, s: (browser, None, page))
    monkeypatch.setattr(core, "launch_setup_browser", lambda p: (browser, FakeContext(page), "chrome"))
    monkeypatch.setattr(blinkit.time, "sleep", lambda s: None)
    return browser, page


def answers(monkeypatch, *values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))


def session(tracker):
    with open(tracker.STATE_FILE, "w") as f:
        json.dump({"cookies": [], "origins": []}, f)


def products(tracker):
    return core.load_config(tracker.CONFIG_FILE)["products"]


def found(tracker, name="Milk", status="IN_STOCK"):
    item = {"name": name, "details": "500 ml | Rs. 30", "status": status}
    if tracker is blinkit:
        item["id"] = "1"
    else:
        item["url"] = PRODUCT_URL[zepto]
    return item


@pytest.mark.parametrize("text,count,expected", [
    ("2", 5, [1]), ("1,3,5", 5, [0, 2, 4]), ("1-3", 5, [0, 1, 2]), ("all", 3, [0, 1, 2]), ("", 5, []),
    ("9", 5, []), ("x", 5, []), ("a-b", 5, []), ("1,,2", 5, [0, 1]), ("4-9", 5, [3, 4]),
])
def test_parse_selection(tracker, text, count, expected):
    assert tracker.parse_selection_input(text, count) == expected


def test_slugify():
    assert blinkit.slugify("  Hot Wheels: 8-Crate! ") == "hot-wheels-8-crate"


def test_add_to_config_ignores_duplicates(tracker):
    tracker.add_to_config("a", "https://x/1")
    tracker.add_to_config("a", "https://x/1")
    assert len(products(tracker)) == 1


@pytest.mark.parametrize("status", ["IN_STOCK", "OUT_OF_STOCK", "OTHER"])
def test_desktop_notification_wording(tracker, status, monkeypatch):
    got = []
    monkeypatch.setattr(core, "send_desktop", lambda t, m, a, l: got.append(m))
    tracker.send_desktop_notification("Milk", status)
    assert "Milk" in got[0] and ("OUT OF STOCK" in got[0]) == (status == "OUT_OF_STOCK")


def test_trigger_notifications(tracker, monkeypatch):
    sent = []
    monkeypatch.setattr(tracker, "send_desktop_notification", lambda *a: sent.append("desktop"))
    monkeypatch.setattr(tracker, "send_telegram_notification", lambda *a: sent.append("telegram"))
    tracker.trigger_notifications("Milk", None, "IN_STOCK", "u", {"desktop_notifications": True, "telegram": {"enabled": True}})
    assert sent == ["desktop", "telegram"]
    sent.clear()
    tracker.trigger_notifications("Milk", "OUT_OF_STOCK", "IN_STOCK", "u", {})
    assert sent == []


# --- add wizard ---
def test_add_wizard_needs_setup(tracker, capsys):
    tracker.run_add_item()
    assert "not found" in capsys.readouterr().out


def test_add_wizard_paste_url(tracker, monkeypatch):
    session(tracker)
    answers(monkeypatch, "2", PRODUCT_URL[tracker], "My Milk")
    tracker.run_add_item()
    assert products(tracker) == [{"name": "My Milk", "url": PRODUCT_URL[tracker]}]


def test_add_wizard_paste_url_generates_name(tracker, monkeypatch):
    session(tracker)
    answers(monkeypatch, "2", PRODUCT_URL[tracker], "")
    tracker.run_add_item()
    assert products(tracker)[0]["name"]


def test_add_wizard_rejects_bad_url(tracker, monkeypatch, capsys):
    session(tracker)
    answers(monkeypatch, "2", "https://example.com/nope")
    tracker.run_add_item()
    assert products(tracker) == [] and "Invalid" in capsys.readouterr().out


@pytest.mark.parametrize("inputs,message", [(("7",), "Invalid choice"), (("1", ""), "cannot be empty")])
def test_add_wizard_bad_input(tracker, monkeypatch, capsys, inputs, message):
    session(tracker)
    answers(monkeypatch, *inputs)
    tracker.run_add_item()
    assert message in capsys.readouterr().out


def test_add_wizard_no_results(tracker, monkeypatch, capsys, playwright_fakes):
    session(tracker)
    monkeypatch.setattr(tracker, "search_products", lambda p, q: [])
    answers(monkeypatch, "1", "milk")
    tracker.run_add_item()
    assert "No products found" in capsys.readouterr().out


@pytest.mark.parametrize("selection,count,message", [("", 0, "Cancelled"), ("99", 0, "No valid"), ("1", 1, "Success")])
def test_add_wizard_selection(tracker, monkeypatch, capsys, playwright_fakes, selection, count, message):
    session(tracker)
    monkeypatch.setattr(tracker, "search_products", lambda p, q: [found(tracker)])
    answers(monkeypatch, "1", "milk", selection)
    tracker.run_add_item()
    assert len(products(tracker)) == count and message in capsys.readouterr().out


def test_add_wizard_saved_name_has_details(tracker, monkeypatch, playwright_fakes):
    session(tracker)
    monkeypatch.setattr(tracker, "search_products", lambda p, q: [found(tracker)])
    answers(monkeypatch, "1", "milk", "all")
    tracker.run_add_item()
    assert products(tracker)[0]["name"] == "Milk (500 ml | Rs. 30)"


@pytest.mark.parametrize("name", ["Milk [/500ml]", "Soap [pack of 3]"])
def test_add_wizard_handles_brackets_in_names(tracker, monkeypatch, capsys, playwright_fakes, name):
    session(tracker)
    monkeypatch.setattr(tracker, "search_products", lambda p, q: [found(tracker, name=name)])
    answers(monkeypatch, "1", "milk", "1")
    tracker.run_add_item()
    assert name in products(tracker)[0]["name"]
    assert name in capsys.readouterr().out.replace("\n", "")


# --- keyword wizard ---
def with_keywords(tracker, kw=("milk",)):
    core.save_config(tracker.CONFIG_FILE, dict(core.DEFAULT_CONFIG, tracked_keywords=list(kw)))


def test_keyword_wizard_needs_setup(tracker, capsys):
    tracker.run_keyword_check_wizard()
    assert "not found" in capsys.readouterr().out


def test_keyword_wizard_needs_keywords(tracker, capsys):
    session(tracker)
    tracker.run_keyword_check_wizard()
    assert "No keywords" in capsys.readouterr().out


def test_keyword_wizard_nothing_new(tracker, monkeypatch, capsys, playwright_fakes):
    session(tracker)
    with_keywords(tracker)
    monkeypatch.setattr(tracker, "search_products", lambda p, q: [])
    tracker.run_keyword_check_wizard()
    assert "No new products" in capsys.readouterr().out


@pytest.mark.parametrize("selection,count,message", [("", 0, "Cancelled"), ("99", 0, "No valid"), ("1", 1, "Success")])
def test_keyword_wizard_selection(tracker, monkeypatch, capsys, playwright_fakes, selection, count, message):
    session(tracker)
    with_keywords(tracker)
    monkeypatch.setattr(tracker, "search_products", lambda p, q: [found(tracker)])
    answers(monkeypatch, selection)
    tracker.run_keyword_check_wizard()
    assert len(products(tracker)) == count and message in capsys.readouterr().out


def test_keyword_wizard_recognises_tracked_product_by_id(monkeypatch, capsys, playwright_fakes):
    session(blinkit)
    core.save_config(blinkit.CONFIG_FILE, dict(core.DEFAULT_CONFIG, tracked_keywords=["milk"], products=[
        {"name": "Mine", "url": "https://blinkit.com/prn/a-different-slug/prid/1"}]))
    monkeypatch.setattr(blinkit, "search_products", lambda p, q: [found(blinkit)])
    blinkit.run_keyword_check_wizard()
    assert "No new products" in capsys.readouterr().out


# --- setup ---
def test_setup_saves_session(tracker, monkeypatch, capsys, playwright_fakes):
    answers(monkeypatch, "", "")
    tracker.run_setup()
    assert os.path.exists(tracker.STATE_FILE)
    assert core.read_state_meta(tracker.STATE_FILE)["channel"] == "chrome"


def test_setup_stops_on_block(tracker, monkeypatch, capsys, playwright_fakes):
    _browser, page = playwright_fakes
    page.status, page.title_text, page.text = 403, "Just a moment...", "access denied"
    answers(monkeypatch, "")
    with pytest.raises(SystemExit) as e:
        tracker.run_setup()
    assert e.value.code == 1 and "Access Denied" in capsys.readouterr().out.replace("\n", " ")
    assert not os.path.exists(tracker.STATE_FILE) and core.recent_incidents()[0]["kind"] == "blocked"


def test_setup_asks_again_until_location_is_set(tracker, monkeypatch, capsys, playwright_fakes):
    _browser, page = playwright_fakes
    page.text = "Please provide your delivery location"
    calls = []

    def fake_input(*a):
        calls.append(1)
        if len(calls) == 3:
            page.text = "Home - 12 MG Road"
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    tracker.run_setup()
    assert len(calls) == 3 and os.path.exists(tracker.STATE_FILE)


def test_setup_survives_slow_page(tracker, monkeypatch, capsys, playwright_fakes):
    _browser, page = playwright_fakes
    page.fail_goto = RuntimeError("Timeout 45000ms")
    answers(monkeypatch, "", "")
    tracker.run_setup()
    assert "did not finish loading" in capsys.readouterr().out.replace("\n", " ")


def test_setup_saves_from_the_tab_in_use(tracker, monkeypatch, playwright_fakes):
    browser, first = playwright_fakes
    first.text = "Please provide your delivery location"
    context = FakeContext(first)
    second = FakePage(text="Home - 12 MG Road")
    context.pages.append(second)
    monkeypatch.setattr(core, "launch_setup_browser", lambda p: (browser, context, "chrome"))
    answers(monkeypatch, "", "")
    tracker.run_setup()
    assert os.path.exists(tracker.STATE_FILE)


def test_setup_ends_when_window_is_closed(tracker, monkeypatch, capsys, playwright_fakes):
    browser, page = playwright_fakes
    context = FakeContext(page)
    monkeypatch.setattr(core, "launch_setup_browser", lambda p: (browser, context, "chrome"))

    def close_then_enter(*a):
        context.pages.clear()
        return ""

    monkeypatch.setattr("builtins.input", close_then_enter)
    tracker.run_setup()
    assert not os.path.exists(tracker.STATE_FILE) and "closed" in capsys.readouterr().out


# --- tracking loop ---
def tracked(tracker, keywords=(), **extra):
    session(tracker)
    core.save_config(tracker.CONFIG_FILE, dict(
        core.DEFAULT_CONFIG, products=[{"name": "Milk", "url": PRODUCT_URL[tracker]}], tracked_keywords=list(keywords), **extra))


def test_tracker_needs_setup(tracker):
    with pytest.raises(SystemExit) as e:
        tracker._tracker_loop(True)
    assert e.value.code == 1


def test_tracker_with_no_products_exits_after_wizard(tracker, monkeypatch):
    session(tracker)
    answers(monkeypatch, "7")
    with pytest.raises(SystemExit) as e:
        tracker._tracker_loop(True)
    assert e.value.code == 0


def test_tracker_check_once_alerts_on_change(tracker, monkeypatch, capsys, playwright_fakes):
    tracked(tracker)
    tracker.init_db()
    tracker.update_status(PRODUCT_URL[tracker], "Milk", "OUT_OF_STOCK")
    monkeypatch.setattr(tracker, "check_product_stock", lambda p, x: "IN_STOCK")
    alerts = []
    monkeypatch.setattr(tracker, "trigger_notifications", lambda *a: alerts.append(a))
    tracker.run_tracker(check_once=True)
    assert len(alerts) == 1 and core.current_statuses(tracker.DB_FILE)[PRODUCT_URL[tracker]] == "IN_STOCK"
    assert "Stock Tracking Status" in capsys.readouterr().out


def test_tracker_no_alert_for_unknown(tracker, monkeypatch, playwright_fakes):
    tracked(tracker)
    tracker.init_db()
    tracker.update_status(PRODUCT_URL[tracker], "Milk", "IN_STOCK")
    monkeypatch.setattr(tracker, "check_product_stock", lambda p, x: "UNKNOWN")
    monkeypatch.setattr(tracker, "trigger_notifications", lambda *a: pytest.fail("no alert expected"))
    tracker.run_tracker(check_once=True)


def test_tracker_plain_text_without_rich(tracker, monkeypatch, capsys, playwright_fakes):
    tracked(tracker)
    monkeypatch.setattr(tracker, "check_product_stock", lambda p, x: "IN_STOCK")
    monkeypatch.delitem(tracker.__dict__, "Table")
    tracker.run_tracker(check_once=True)
    assert "Tracking Results" in capsys.readouterr().out


def test_tracker_keyword_discovery_alerts(tracker, monkeypatch, playwright_fakes):
    tracked(tracker, keywords=["milk"], )
    new = found(tracker, name="Fresh Cream")
    if tracker is blinkit:
        new["id"] = "2"
    else:
        new["url"] = "https://www.zepto.com/pn/cream/pvid/2"
    monkeypatch.setattr(tracker, "check_product_stock", lambda p, x: "IN_STOCK")
    monkeypatch.setattr(tracker, "search_products", lambda p, q: [new])
    desktop, telegram = [], []
    monkeypatch.setattr(tracker.notification, "notify", lambda **k: desktop.append(k))
    monkeypatch.setattr(core, "send_telegram", lambda *a: telegram.append(a))
    tracker.run_tracker(check_once=True)
    assert len(desktop) == 1 and len(telegram) == 1


def test_tracker_keyword_alert_failure_is_not_fatal(tracker, monkeypatch, playwright_fakes):
    tracked(tracker, keywords=["milk"])
    new = found(tracker, name="Fresh Cream")
    if tracker is blinkit:
        new["id"] = "2"
    else:
        new["url"] = "https://www.zepto.com/pn/cream/pvid/2"
    monkeypatch.setattr(tracker, "check_product_stock", lambda p, x: "IN_STOCK")
    monkeypatch.setattr(tracker, "search_products", lambda p, q: [new])

    def fail(**k):
        raise RuntimeError("no backend")

    monkeypatch.setattr(tracker.notification, "notify", fail)
    monkeypatch.setattr(core, "send_telegram", lambda *a: None)
    tracker.run_tracker(check_once=True)


def test_tracker_does_not_alert_for_tracked_keyword_results(tracker, monkeypatch, playwright_fakes):
    tracked(tracker, keywords=["milk"])
    monkeypatch.setattr(tracker, "check_product_stock", lambda p, x: "IN_STOCK")
    monkeypatch.setattr(tracker, "search_products", lambda p, q: [found(tracker)])
    monkeypatch.setattr(tracker.notification, "notify", lambda **k: pytest.fail("already tracked"))
    monkeypatch.setattr(core, "send_telegram", lambda *a: pytest.fail("already tracked"))
    tracker.run_tracker(check_once=True)


def test_tracker_waits_at_least_the_minimum_interval(tracker, monkeypatch, playwright_fakes):
    session(tracker)
    core.save_config(tracker.CONFIG_FILE, dict(core.DEFAULT_CONFIG, check_interval_minutes=0, products=[{"name": "Milk", "url": PRODUCT_URL[tracker]}]))
    monkeypatch.setattr(tracker, "check_product_stock", lambda p, x: "IN_STOCK")
    sleeps = []

    def stop_on_long_sleep(seconds):
        sleeps.append(seconds)
        if seconds >= 60:
            raise StopLoop

    monkeypatch.setattr(tracker.time, "sleep", stop_on_long_sleep)
    with pytest.raises(StopLoop):
        tracker.run_tracker()
    assert sleeps[-1] >= core.MIN_CHECK_INTERVAL_MINUTES * 60 - 30


def test_check_once_fails_during_cooldown(tracker, capsys):
    core.note_block(tracker.PROVIDER)
    with pytest.raises(SystemExit) as e:
        tracker.run_tracker(check_once=True)
    assert e.value.code == 1 and "Pausing" in capsys.readouterr().out


def test_tracker_waits_out_cooldown_then_runs(tracker, monkeypatch, playwright_fakes):
    core.note_block(tracker.PROVIDER)
    waits = []

    def clear_on_sleep(seconds):
        waits.append(seconds)
        core.clear_block(tracker.PROVIDER)

    monkeypatch.setattr(tracker.time, "sleep", clear_on_sleep)
    ran = []
    monkeypatch.setattr(tracker, "_tracker_loop", lambda once: ran.append(once))
    tracker.run_tracker()
    assert waits == [60] and ran == [False]


def test_tracker_pauses_after_block_and_alerts(tracker, monkeypatch, capsys):
    error = core.BlockedError(tracker.PROVIDER, "blocked", "t")
    runs, alerts = [], []

    def loop(once):
        runs.append(1)
        if len(runs) == 1:
            raise error

    monkeypatch.setattr(tracker, "_tracker_loop", loop)
    monkeypatch.setattr(core, "alert_blocked", lambda *a: alerts.append(a))
    tracker.run_tracker()
    assert len(runs) == 2 and len(alerts) == 1 and "report" in capsys.readouterr().out


def test_check_once_raises_block(tracker, monkeypatch):
    def loop(once):
        raise core.BlockedError(tracker.PROVIDER, "blocked", "t")

    monkeypatch.setattr(tracker, "_tracker_loop", loop)
    with pytest.raises(core.BlockedError):
        tracker.run_tracker(check_once=True)


# --- search failures and names ---
def test_search_page_error_returns_empty_and_records(tracker):
    page = FakePage()
    page.fail_goto = RuntimeError("net error at 10.0.0.1")
    assert tracker.search_products(page, "milk") == []
    i = core.recent_incidents()[0]
    assert i["kind"] == "page_error" and "10.0.0.1" not in i["snippet"]


def test_search_block_is_raised(tracker):
    with pytest.raises(core.BlockedError):
        tracker.search_products(FakePage(status=403, title="x", text="access denied"), "milk")


def test_search_handles_brackets_in_query(tracker):
    tracker.search_products(FakePage(text="x"), "milk [/pack]")


def test_blinkit_card_without_name():
    assert blinkit.parse_search_card("1", "8 MINS\nADD") is None


def test_zepto_anchor_without_name():
    assert zepto.parse_search_anchor("u", "₹30\nADD") is None


def test_zepto_broad_link_fallback():
    class P(FakePage):
        def evaluate(self, script):
            if "querySelectorAll" in script:
                return [{"href": "/cn/fruits", "text": "Fruits"},
                        {"href": "/pn/milk/p/1", "text": "Milk\n500 ml\n₹30"},
                        {"href": "/pn/milk/p/1", "text": "Milk\n500 ml\n₹30"},
                        {"href": None, "text": "x"}]
            return super().evaluate(script)

    items = zepto.search_products(P(), "milk")
    assert len(items) == 1 and items[0]["url"] == "https://www.zepto.com/pn/milk/p/1"


def test_zepto_out_of_stock_card():
    assert zepto.parse_search_anchor("u", "Milk\n500 ml\n₹30\nSold out")["status"] == "OUT_OF_STOCK"


# --- command line entry points ---
@pytest.mark.parametrize("module", TRACKERS, ids=["blinkit", "zepto"])
@pytest.mark.parametrize("flags,message", [(["--add"], "not found"), (["--keywords"], "not found")])
def test_main_commands(module, flags, message, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["x"] + flags)
    runpy.run_path(module.__file__, run_name="__main__")
    assert message in capsys.readouterr().out


@pytest.mark.parametrize("module", TRACKERS, ids=["blinkit", "zepto"])
def test_main_check_once_needs_setup(module, monkeypatch):
    monkeypatch.setattr("sys.argv", ["x", "--check-once"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(module.__file__, run_name="__main__")
    assert e.value.code == 1


@pytest.mark.parametrize("module", TRACKERS, ids=["blinkit", "zepto"])
def test_main_reports_block_cleanly(module, monkeypatch, capsys):
    import playwright.sync_api as api
    monkeypatch.setattr(api, "sync_playwright", FakePlaywright)
    page = FakePage(status=403, title="x", text="access denied")
    browser = FakeBrowser()
    monkeypatch.setattr(core, "launch_setup_browser", lambda p: (browser, FakeContext(page), "chrome"))
    monkeypatch.setattr("builtins.input", lambda *a: "")
    monkeypatch.setattr("sys.argv", ["x", "--setup"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(module.__file__, run_name="__main__")
    assert e.value.code == 1


@pytest.mark.parametrize("module", TRACKERS, ids=["blinkit", "zepto"])
def test_runs_without_rich_installed(module, monkeypatch, capsys):
    import sys
    monkeypatch.setitem(sys.modules, "rich", None)
    monkeypatch.setitem(sys.modules, "rich.console", None)
    g = runpy.run_path(module.__file__, run_name="not_main")
    g["console"].log("hello")
    g["console"].print("world")
    out = capsys.readouterr().out
    assert "hello" in out and "world" in out


# --- reset script ---
@pytest.fixture
def reset_dir(monkeypatch):
    monkeypatch.chdir(core.DATA_DIR)
    monkeypatch.setattr(reset_tracker.os, "chdir", lambda p: None)
    return core.DATA_DIR


def test_reset_cancelled_keeps_files(reset_dir, monkeypatch, capsys):
    session(blinkit)
    answers(monkeypatch, "no")
    reset_tracker.reset_all()
    assert os.path.exists(blinkit.STATE_FILE) and "cancelled" in capsys.readouterr().out


def test_reset_clears_files(reset_dir, monkeypatch, capsys):
    session(blinkit)
    with open(f"{blinkit.STATE_FILE}.meta.json", "w") as f:
        f.write("{}")
    blinkit.init_db()
    core.record_incident("blinkit", "blocked", "t")
    core.save_config(blinkit.CONFIG_FILE, dict(core.DEFAULT_CONFIG, tracked_keywords=["x"]))
    answers(monkeypatch, "yes")
    reset_tracker.reset_all()
    assert not os.path.exists(blinkit.STATE_FILE) and not os.path.exists(f"{blinkit.STATE_FILE}.meta.json")
    assert not os.path.exists(blinkit.DB_FILE) and not os.path.exists(core.DIAG_DIR)
    assert core.load_config(blinkit.CONFIG_FILE)["tracked_keywords"] == []


def test_reset_reports_locked_files(reset_dir, monkeypatch, capsys):
    session(blinkit)
    blinkit.init_db()

    def locked(p):
        raise PermissionError("in use")

    monkeypatch.setattr(reset_tracker.os, "remove", locked)
    monkeypatch.setattr(reset_tracker.shutil, "rmtree", locked)
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(PermissionError("in use")))
    monkeypatch.setattr("builtins.input", lambda *a: "yes")
    reset_tracker.reset_all()
    out = capsys.readouterr().out
    assert out.count("❌") >= 4


@pytest.mark.parametrize("module", TRACKERS, ids=["blinkit", "zepto"])
def test_main_explains_damaged_config(module, monkeypatch, capsys):
    session(module)
    with open(module.CONFIG_FILE, "w") as f:
        f.write("{bad")
    monkeypatch.setattr("sys.argv", ["x", "--check-once"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(module.__file__, run_name="__main__")
    assert e.value.code == 1 and "not valid JSON" in capsys.readouterr().out.replace("\n", " ")
