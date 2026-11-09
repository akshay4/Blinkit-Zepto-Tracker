"""Every test runs against a temporary data folder, never the real settings or cookies."""
import os
import shutil
import sys
import tempfile

DATA_DIR = tempfile.mkdtemp(prefix="tracker-tests-")
os.environ["TRACKER_DATA_DIR"] = DATA_DIR
os.environ["TRACKER_NO_BROWSER"] = "true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import blinkit_tracker
import tracker_core as core
import zepto_tracker


@pytest.fixture(autouse=True)
def fresh_data():
    for name in os.listdir(DATA_DIR):
        target = os.path.join(DATA_DIR, name)
        shutil.rmtree(target) if os.path.isdir(target) else os.remove(target)
    os.makedirs(blinkit_tracker.SCREENSHOT_DIR)
    core.save_config(blinkit_tracker.CONFIG_FILE, core.DEFAULT_CONFIG)
    core.save_config(zepto_tracker.CONFIG_FILE, core.DEFAULT_CONFIG)
    core._next_page_time.clear()
    yield


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(DATA_DIR, ignore_errors=True)
