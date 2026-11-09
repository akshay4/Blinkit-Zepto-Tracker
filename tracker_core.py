"""Code shared by the Blinkit tracker, the Zepto tracker and the API.

Storage, browser sessions, block detection and problem reports live here so
that every entry point handles them the same way.
"""
import io
import json
import os
import platform
import random
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import zipfile
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from importlib import metadata as importlib_metadata

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Tests point this at a temporary folder so they never touch real settings or cookies.
DATA_DIR = os.environ.get("TRACKER_DATA_DIR") or BASE_DIR
DIAG_DIR = os.path.join(DATA_DIR, "diagnostics")
INCIDENT_FILE = os.path.join(DIAG_DIR, "incidents.jsonl")
BLOCK_FILE = os.path.join(DIAG_DIR, "block_state.json")
LOG_FILE = os.path.join(DIAG_DIR, "app.log")

APP_VERSION = "1.1.0"
REPORT_REPO = "akshay4/Blinkit-Zepto-Tracker"

PROVIDERS = {
    "blinkit": {
        "label": "Blinkit",
        "home": "https://blinkit.com",
        "hosts": {"blinkit.com", "www.blinkit.com"},
        "state_file": "state.json",
        "config_file": "config.json",
    },
    "zepto": {
        "label": "Zepto",
        "home": "https://www.zepto.com",
        "hosts": {"zepto.com", "www.zepto.com", "zeptonow.com", "www.zeptonow.com"},
        "state_file": "zepto_state.json",
        "config_file": "zepto_config.json",
    },
}

# Sessions saved before this release were created with this user agent, and a
# site's clearance cookie only stays valid with the one it was issued to.
LEGACY_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1280, "height": 800}

MIN_CHECK_INTERVAL_MINUTES = 5
MIN_PAGE_GAP_SECONDS = 2.5
BLOCK_COOLDOWN_MINUTES = 30
MAX_BLOCK_COOLDOWN_MINUTES = 360
MAX_INCIDENTS = 200
MAX_DIAG_SCREENSHOTS = 10
MAX_REPORT_URL_LENGTH = 7500
MAX_LOG_BYTES = 2_000_000

# These statuses mean "the check told us nothing", so they never replace a real one.
TRANSIENT_STATUSES = {"UNKNOWN"}

DEFAULT_CONFIG = {
    "check_interval_minutes": 10,
    "desktop_notifications": True,
    "telegram": {"enabled": False, "bot_token": "YOUR_BOT_TOKEN", "chat_id": "YOUR_CHAT_ID"},
    "tracked_keywords": [],
    "products": [],
}

RATE_LIMIT_MARKERS = ("http error 429", "too many requests", "error 1015", "you are being rate limited")
BLOCK_MARKERS = (
    "access denied",
    "you have been blocked",
    "you don't have permission to access",
    "request blocked",
    "error 1020",
)
CHALLENGE_MARKERS = (
    "just a moment",
    "verify you are human",
    "checking your browser",
    "performing security verification",
    "attention required",
    "complete the security check",
)
LOCATION_PROMPT_MARKERS = ("please provide your delivery location",)
SHORT_PAGE_LENGTH = 4000

PAGE_INFO_JS = """() => {
  const text = document.body ? document.body.innerText : '';
  return {title: document.title || '', text: text.slice(0, 6000), length: text.length};
}"""

config_lock = threading.RLock()
_block_lock = threading.RLock()
_log_lock = threading.RLock()
_pace_lock = threading.Lock()
_next_page_time = {}
_browser_slots = threading.BoundedSemaphore(2)


class ConfigError(Exception):
    """A configuration file exists but cannot be read."""


class SetupRefused(Exception):
    """The setup window is not in a state worth saving."""


class BlockedError(Exception):
    """The site refused the page instead of serving it."""

    def __init__(self, provider, kind, stage, url="", http_status=None, cooldown_until=None, first=True):
        self.provider = provider
        self.kind = kind
        self.stage = stage
        self.url = url
        self.http_status = http_status
        self.cooldown_until = cooldown_until
        self.first = first
        self.user_message = block_message(provider, kind)
        super().__init__(self.user_message)


def path(*parts):
    return os.path.join(DATA_DIR, *parts)


def provider_label(provider):
    return PROVIDERS[provider]["label"]


def block_message(provider, kind):
    site = provider_label(provider)
    if kind == "rate_limited":
        lead = f"{site} is rate limiting this connection (too many requests)."
    elif kind == "challenge":
        lead = f"{site} showed a security check that did not clear."
    else:
        lead = f"{site} refused this connection (Access Denied)."
    return (
        f"{lead} This is usually about the network or IP address rather than your settings. "
        "Turn off any VPN or proxy, or try another network such as a phone hotspot, wait 30 to 60 minutes, "
        "then run Location Setup again. If it keeps happening, use Report a problem."
    )


# --- Files ---

def atomic_write_text(file_path, text, mode=None):
    tmp = f"{file_path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(tmp, mode)
    for attempt in range(5):
        try:
            os.replace(tmp, file_path)
            return
        except PermissionError:
            # Windows refuses to replace a file another thread has open for a moment.
            if attempt == 4:
                raise
            time.sleep(0.1)


def load_config(config_file):
    with config_lock:
        if not os.path.exists(config_file):
            save_config(config_file, DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"{os.path.basename(config_file)} is not valid JSON: {e}") from e
    if not isinstance(config, dict):
        raise ConfigError(f"{os.path.basename(config_file)} must contain a JSON object.")
    config["products"] = [
        p for p in config.get("products") or []
        if isinstance(p, dict) and p.get("url") and p.get("name")
    ]
    return config


def save_config(config_file, config):
    with config_lock:
        atomic_write_text(config_file, json.dumps(config, indent=2))


def check_interval_minutes(config):
    try:
        interval = float(config.get("check_interval_minutes", 10))
    except (TypeError, ValueError):
        interval = 10
    return max(MIN_CHECK_INTERVAL_MINUTES, interval)


def product_key(url):
    """Identifies a product by its site ID, because the slug part of a URL can differ."""
    match = re.search(r"/(?:prid|pvid)/([^/?#]+)", url or "")
    return match.group(1) if match else url


def is_allowed_url(provider, url):
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname in PROVIDERS[provider]["hosts"]


# --- Database ---

def connect_db(db_file):
    return sqlite3.connect(db_file, timeout=15)


def init_db(db_file):
    with closing(connect_db(db_file)) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_status (
                url TEXT PRIMARY KEY,
                name TEXT,
                status TEXT,
                last_checked TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                status TEXT,
                timestamp TIMESTAMP
            )
        """)


def update_status(db_file, url, name, status):
    """Stores a check result and returns the status it replaced."""
    now = datetime.now().isoformat()
    with closing(connect_db(db_file)) as conn, conn:
        row = conn.execute("SELECT status FROM product_status WHERE url = ?", (url,)).fetchone()
        previous = row[0] if row else None
        if previous is None:
            conn.execute(
                "INSERT INTO product_status (url, name, status, last_checked) VALUES (?, ?, ?, ?)",
                (url, name, status, now),
            )
        elif previous == status or status in TRANSIENT_STATUSES:
            conn.execute("UPDATE product_status SET last_checked = ? WHERE url = ?", (now, url))
        else:
            conn.execute(
                "UPDATE product_status SET name = ?, status = ?, last_checked = ? WHERE url = ?",
                (name, status, now, url),
            )
        if previous != status and (previous is None or status not in TRANSIENT_STATUSES):
            conn.execute(
                "INSERT INTO status_history (url, status, timestamp) VALUES (?, ?, ?)",
                (url, status, now),
            )
    return previous


def current_statuses(db_file):
    if not os.path.exists(db_file):
        return {}
    try:
        with closing(connect_db(db_file)) as conn:
            return dict(conn.execute("SELECT url, status FROM product_status").fetchall())
    except sqlite3.OperationalError:
        return {}


# --- Browser sessions ---

def _state_meta_file(state_file):
    return f"{state_file}.meta.json"


def read_state_meta(state_file):
    try:
        with open(_state_meta_file(state_file), "r", encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def launch_browser(playwright, headless, channels):
    """Launches the first browser in `channels` that is installed. None is Playwright's Chromium."""
    last_error = None
    for channel in channels:
        kwargs = {"headless": headless}
        if channel:
            kwargs["channel"] = channel
        try:
            return playwright.chromium.launch(**kwargs), channel or "chromium"
        except Exception as e:
            last_error = e
    raise RuntimeError(
        "No browser could be started. Install Google Chrome, or run: python -m playwright install chromium. "
        f"Details: {scrub(str(last_error))[:300]}"
    )


def launch_setup_browser(playwright):
    """Setup is done by a person, so it uses their real browser with its own identity."""
    browser, label = launch_browser(playwright, False, ("chrome", "msedge", None))
    return browser, browser.new_context(no_viewport=True), label


def open_tracking_page(playwright, state_file):
    """Opens a headless page that presents the same identity the session was saved with."""
    meta = read_state_meta(state_file)
    saved_channel = meta.get("channel")
    channels = [None]
    if saved_channel and saved_channel != "chromium":
        channels.insert(0, saved_channel)
    browser, _ = launch_browser(playwright, True, channels)
    context = browser.new_context(
        storage_state=state_file,
        user_agent=meta.get("user_agent") or LEGACY_USER_AGENT,
        viewport=VIEWPORT,
    )
    return browser, context, context.new_page()


@contextmanager
def tracking_page(state_file):
    """Yields a page and always closes its browser. At most two run at once."""
    from playwright.sync_api import sync_playwright

    with _browser_slots:
        with sync_playwright() as p:
            browser, _, page = open_tracking_page(p, state_file)
            try:
                yield page
            finally:
                browser.close()


def pace(provider):
    """Keeps page loads for one site apart, however many callers are running."""
    with _pace_lock:
        now = time.monotonic()
        start = max(now, _next_page_time.get(provider, 0.0))
        _next_page_time[provider] = start + MIN_PAGE_GAP_SECONDS + random.uniform(0, 1)
    if start > now:
        time.sleep(start - now)


def inspect_page(page):
    """Returns the page's title and text, or None while it is navigating."""
    try:
        return page.evaluate(PAGE_INFO_JS)
    except Exception:
        return None


def classify_block(info, http_status=None):
    """Returns 'rate_limited', 'blocked', 'challenge', or None for a normal page."""
    if not info:
        return None
    title = (info.get("title") or "").lower()
    length = info.get("length") or 0
    haystack = title
    # Product pages can contain any words, so body text only counts on a short page.
    if length < SHORT_PAGE_LENGTH:
        haystack = f"{title}\n{(info.get('text') or '').lower()}"
    if any(m in haystack for m in RATE_LIMIT_MARKERS):
        return "rate_limited"
    if any(m in haystack for m in BLOCK_MARKERS):
        return "blocked"
    if any(m in haystack for m in CHALLENGE_MARKERS):
        return "challenge"
    if http_status == 429:
        return "rate_limited"
    if http_status == 403:
        return "blocked"
    if http_status == 202 and length == 0:
        return "challenge"
    return None


def is_location_prompt(info):
    text = ((info or {}).get("text") or "").lower()
    return any(m in text for m in LOCATION_PROMPT_MARKERS)


def goto(page, provider, url, timeout=30000):
    pace(provider)
    return page.goto(url, wait_until="domcontentloaded", timeout=timeout)


def verify_access(page, provider, stage, response=None, wait_seconds=12):
    """Raises BlockedError if the site refused the page. A challenge gets time to clear by itself."""
    status = response.status if response else None
    deadline = time.monotonic() + wait_seconds
    first = True
    while True:
        info = inspect_page(page)
        effective = status if first or not info or info.get("length") == 0 else None
        kind = classify_block(info, effective)
        if kind is None:
            return info
        if kind != "challenge" or time.monotonic() >= deadline:
            raise register_block(page, provider, kind, stage, info, effective)
        first = False
        page.wait_for_timeout(1000)


def register_block(page, provider, kind, stage, info, http_status):
    """Records a refusal, starts the cooldown and returns the error to raise."""
    until, first = note_block(provider)
    url = getattr(page, "url", "") or ""
    record_incident(
        provider, kind, stage, url=url, http_status=http_status,
        title=(info or {}).get("title", ""), snippet=(info or {}).get("text", ""),
        screenshot=capture_diagnostic(page, provider, kind),
    )
    return BlockedError(provider, kind, stage, url, http_status, until, first)


def save_session(context, page, provider, state_file, browser_label):
    """Saves a finished setup. The previous session is kept if this one is not usable."""
    info = inspect_page(page)
    kind = classify_block(info)
    site = provider_label(provider)
    if kind == "challenge":
        raise SetupRefused(f"{site} is still showing a security check. Finish it in the browser window, then save.")
    if kind:
        raise SetupRefused(block_message(provider, kind))
    if not info or not info.get("length"):
        raise SetupRefused(f"The {site} page has not finished loading. Wait a moment and save again.")
    if is_location_prompt(info):
        raise SetupRefused(f"{site} is still asking for your delivery location. Set it in the browser window, then save.")

    meta = {
        "user_agent": page.evaluate("navigator.userAgent"),
        "channel": browser_label,
        "saved_at": datetime.now().isoformat(),
    }
    tmp = f"{state_file}.tmp"
    context.storage_state(path=tmp)
    if os.path.exists(tmp):
        os.chmod(tmp, 0o600)
    os.replace(tmp, state_file)
    atomic_write_text(_state_meta_file(state_file), json.dumps(meta, indent=2), mode=0o600)
    clear_block(provider)


# --- Stock and search pages ---

def read_stock_signal(page, out_of_stock_patterns):
    text = page.evaluate("document.body ? document.body.innerText : ''") or ""
    lowered = text.lower()
    for pattern in out_of_stock_patterns:
        if pattern in lowered:
            return "OUT_OF_STOCK"
    if "add to cart" in lowered or any(line.strip() == "ADD" for line in text.splitlines()):
        return "IN_STOCK"
    return None


def wait_for_stock_signal(page, out_of_stock_patterns, timeout_seconds=10):
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            signal = read_stock_signal(page, out_of_stock_patterns)
        except Exception:
            signal = None  # The page is still navigating.
        if signal:
            # "Out of stock" can render just after a carousel's ADD buttons.
            page.wait_for_timeout(1000)
            return read_stock_signal(page, out_of_stock_patterns)
        if is_location_prompt(inspect_page(page)):
            return "LOCATION_REQUIRED"
        if time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(400)


def check_stock(page, provider, product, out_of_stock_patterns, screenshot_dir, log=print):
    """Returns IN_STOCK, OUT_OF_STOCK or UNKNOWN. Raises BlockedError if the site refused the page."""
    name, url = product["name"], product["url"]
    if not is_allowed_url(provider, url):
        log(f"Skipping {name}: not a {provider_label(provider)} product URL.")
        return "UNKNOWN"
    try:
        log(f"Checking {name}...")
        response = goto(page, provider, url)
        verify_access(page, provider, "stock check", response)
        signal = wait_for_stock_signal(page, out_of_stock_patterns)
        if signal in ("IN_STOCK", "OUT_OF_STOCK"):
            clear_block(provider)
            return signal
        verify_access(page, provider, "stock check", wait_seconds=0)
    except BlockedError:
        raise
    except Exception as e:
        log(f"Error checking {name}: {scrub(str(e))[:200]}")
        record_incident(provider, "page_error", "stock check", url=url, snippet=str(e))
        return "UNKNOWN"

    kind = "location_missing" if signal == "LOCATION_REQUIRED" else "unknown_status"
    log(f"Could not read a stock status for {name} ({kind}).")
    record_incident(provider, kind, "stock check", url=url, title=_safe_title(page))
    _save_product_screenshot(page, name, screenshot_dir)
    return "UNKNOWN"


def open_search(page, provider, url, selector, stage="search", selector_timeout=10000):
    """Loads a results page and waits for its first result. Raises BlockedError if refused."""
    response = goto(page, provider, url)
    verify_access(page, provider, stage, response)
    try:
        page.wait_for_selector(selector, state="visible", timeout=selector_timeout)
        page.wait_for_timeout(500)
    except Exception:
        verify_access(page, provider, stage, wait_seconds=0)


def finish_search(page, provider, url, count, stage="search"):
    if count:
        clear_block(provider)
    else:
        info = inspect_page(page)
        kind = "location_missing" if is_location_prompt(info) else "no_results"
        record_incident(provider, kind, stage, url=url, title=(info or {}).get("title", ""))


def _safe_title(page):
    try:
        return page.title()
    except Exception:
        return ""


def _save_product_screenshot(page, name, screenshot_dir):
    clean = "".join(c if c.isalnum() else "_" for c in name)[:80]
    try:
        page.screenshot(path=os.path.join(screenshot_dir, f"{clean}_latest.png"), timeout=5000)
    except Exception:
        pass


# --- Blocks and cooldown ---

def _read_block_state():
    try:
        with open(BLOCK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_block_state(data):
    os.makedirs(DIAG_DIR, exist_ok=True)
    atomic_write_text(BLOCK_FILE, json.dumps(data, indent=2))


def note_block(provider):
    """Starts or extends the cooldown. Returns its end and whether this began a new episode."""
    with _block_lock:
        data = _read_block_state()
        count = int(data.get(provider, {}).get("count", 0)) + 1
        minutes = min(BLOCK_COOLDOWN_MINUTES * 2 ** (count - 1), MAX_BLOCK_COOLDOWN_MINUTES)
        until = datetime.now() + timedelta(minutes=minutes)
        data[provider] = {"count": count, "until": until.isoformat()}
        _write_block_state(data)
    return until, count == 1


def clear_block(provider):
    with _block_lock:
        data = _read_block_state()
        if provider in data:
            del data[provider]
            _write_block_state(data)


def cooldown_remaining(provider):
    """Seconds until automatic and live checks may touch this site again."""
    with _block_lock:
        until = _read_block_state().get(provider, {}).get("until")
    if not until:
        return 0
    try:
        return max(0, int((datetime.fromisoformat(until) - datetime.now()).total_seconds()))
    except ValueError:
        return 0


# --- Notifications ---

def markdown_escape(text):
    """Stops product names such as 'Tom_and_Jerry' from making Telegram reject the alert."""
    return re.sub(r"([_*`\[])", r"\\\1", str(text))


def send_telegram(telegram_config, text, log=print, disable_preview=None):
    if not telegram_config.get("enabled"):
        return False
    token = telegram_config.get("bot_token")
    chat_id = telegram_config.get("chat_id")
    if not token or not chat_id or "YOUR_" in token:
        log("Telegram is enabled but its credentials are not configured.")
        return False
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if disable_preview is not None:
        payload["disable_web_page_preview"] = disable_preview
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
        if response.status_code != 200:
            log(f"Telegram API error: HTTP {response.status_code} - {scrub(response.text)[:200]}")
            return False
        return True
    except Exception as e:
        log(f"Failed to send Telegram notification: {scrub(str(e))[:200]}")
        return False


def send_desktop(title, message, app_name, log=print):
    try:
        from plyer import notification
        # Windows rejects long notifications outright.
        notification.notify(title=title[:60], message=message[:240], app_name=app_name[:60], timeout=10)
    except Exception as e:
        log(f"Failed to trigger desktop notification: {scrub(str(e))[:200]}")


def alert_blocked(provider, config, error, log=print):
    """Tells the user once per episode that checks have paused."""
    site = provider_label(provider)
    if not error.first:
        return
    resume = error.cooldown_until.strftime("%H:%M") if error.cooldown_until else "later"
    summary = f"{site} refused the tracker. Checks are paused until {resume}."
    if config.get("desktop_notifications"):
        send_desktop(f"{site} tracker paused", summary, f"{site} Stock Tracker", log)
    send_telegram(config.get("telegram") or {}, f"⚠️ {markdown_escape(summary)}", log)


# --- Log file ---

ANSI_CODES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def write_log_line(line):
    with _log_lock:
        os.makedirs(DIAG_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
            os.replace(LOG_FILE, f"{LOG_FILE}.1")
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {scrub(line)}\n")


class LogTee:
    """Passes output through to the terminal and keeps a cleaned copy in the log file."""

    def __init__(self, stream):
        self._stream = stream
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text):
        written = self._stream.write(text)
        try:
            with self._lock:
                self._buffer += ANSI_CODES.sub("", text)
                *lines, self._buffer = self._buffer.split("\n")
            for line in lines:
                if line.strip():
                    write_log_line(line.rstrip())
        except Exception:
            pass  # A full disk must never stop the tracker.
        return written

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_log_capture():
    """Saves everything printed from now on, including errors, to the log file."""
    if isinstance(sys.stdout, LogTee):
        return
    sys.stdout = LogTee(sys.stdout)
    sys.stderr = LogTee(sys.stderr)
    write_log_line(f"--- Started app {APP_VERSION} on {platform.system()} {platform.release()}, Python {platform.python_version()} ---")


# --- Incidents and reports ---

def scrub(text):
    """Removes personal or secret details before text is stored or shared."""
    text = str(text or "")
    text = re.sub(r"([?&](?:q|query)=)[^\s&\"']+", r"\1<query>", text)
    text = re.sub(r"bot\d+:[\w-]+", "bot<token>", text)
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", text)
    text = re.sub(r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b", "<ip>", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<email>", text)
    text = re.sub(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)[^\s/\\]+", "<home>", text)
    return text


def safe_url(url):
    """Keeps the site and page, and drops any query such as a search term."""
    try:
        parts = urllib.parse.urlsplit(url or "")
        return f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.netloc else ""
    except ValueError:
        return ""


def capture_diagnostic(page, provider, kind):
    os.makedirs(DIAG_DIR, exist_ok=True)
    name = f"{provider}-{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    try:
        page.screenshot(path=os.path.join(DIAG_DIR, name), timeout=5000)
    except Exception:
        return ""
    shots = sorted(
        (f for f in os.listdir(DIAG_DIR) if f.endswith(".png")),
        key=lambda f: os.path.getmtime(os.path.join(DIAG_DIR, f)),
    )
    for old in shots[:-MAX_DIAG_SCREENSHOTS]:
        try:
            os.remove(os.path.join(DIAG_DIR, old))
        except OSError:
            pass
    return name


def record_incident(provider, kind, stage, url="", http_status=None, title="", snippet="", screenshot=""):
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "kind": kind,
        "stage": stage,
        "url": safe_url(url),
        "http_status": http_status,
        "title": scrub(title)[:120],
        "snippet": scrub(re.sub(r"\s+", " ", snippet or ""))[:200],
        "screenshot": screenshot,
    }
    with _block_lock:
        os.makedirs(DIAG_DIR, exist_ok=True)
        lines = []
        if os.path.exists(INCIDENT_FILE):
            with open(INCIDENT_FILE, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        lines.append(json.dumps(entry, ensure_ascii=False))
        atomic_write_text(INCIDENT_FILE, "\n".join(lines[-MAX_INCIDENTS:]) + "\n")
    return entry


def recent_incidents(limit=15):
    with _block_lock:
        if not os.path.exists(INCIDENT_FILE):
            return []
        with open(INCIDENT_FILE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    incidents = []
    for line in reversed(lines):
        try:
            incidents.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(incidents) >= limit:
            break
    return incidents


def provider_summary(provider):
    info = PROVIDERS[provider]
    state_file = path(info["state_file"])
    meta = read_state_meta(state_file)
    remaining = cooldown_remaining(provider)
    return {
        "setup_saved": os.path.exists(state_file),
        "setup_browser": meta.get("channel") or ("older setup" if os.path.exists(state_file) else "none"),
        "cooldown_seconds": remaining,
    }


def _package_version(name):
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return "not installed"


def build_report(note="", context=None):
    """Builds the text a user can send. It holds no cookies, address, IP address or tokens."""
    incidents = recent_incidents()
    lines = ["### What happened", scrub(note).strip() or "_(no description given)_", ""]
    lines += [
        "### Environment",
        f"- App: {APP_VERSION}",
        f"- OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"- Python: {platform.python_version()}",
        f"- Playwright: {_package_version('playwright')}",
        "",
        "### Status",
    ]
    for provider in PROVIDERS:
        s = provider_summary(provider)
        lines.append(
            f"- {provider_label(provider)}: setup saved={s['setup_saved']}, browser={s['setup_browser']}, "
            f"cooldown={s['cooldown_seconds'] // 60} min"
        )
    if context:
        lines += ["", f"Reported from: {scrub(context)[:100]}"]
    lines += ["", f"### Recent problems ({len(incidents)})"]
    if not incidents:
        lines.append("None recorded.")
    for i in incidents:
        http = f" HTTP {i['http_status']}" if i.get("http_status") else ""
        shot = f" (screenshot: {i['screenshot']})" if i.get("screenshot") else ""
        lines.append(f"- {i['time']} {i['provider']} / {i['stage']}: **{i['kind']}**{http}{shot}")
        details = " | ".join(x for x in (i.get("url"), i.get("title"), i.get("snippet")) if x)
        if details:
            lines.append(f"  {details}")
    text = "\n".join(lines)

    latest = incidents[0] if incidents else None
    summary = f"{latest['provider']} {latest['kind']} during {latest['stage']}" if latest else "Problem report"
    title = f"[Report] {summary}"
    return {"title": title, "text": text, "github_url": report_url(title, text), "repo": REPORT_REPO}


def app_context():
    """Everything useful about this install, without cookies, address or tokens."""
    statuses = current_statuses(path("tracker.db"))
    providers = {}
    for provider, info in PROVIDERS.items():
        details = provider_summary(provider)
        meta = read_state_meta(path(info["state_file"]))
        details.update(user_agent=meta.get("user_agent"), setup_saved_at=meta.get("saved_at"))
        try:
            config = load_config(path(info["config_file"]))
            telegram = config.get("telegram") or {}
            details.update(
                products=len(config["products"]),
                keywords=len(config.get("tracked_keywords") or []),
                check_interval_minutes=check_interval_minutes(config),
                desktop_notifications=bool(config.get("desktop_notifications")),
                telegram_enabled=bool(telegram.get("enabled")),
            )
        except ConfigError as e:
            details["config_error"] = scrub(str(e))
        details["status_counts"] = dict(Counter(
            status for url, status in statuses.items() if is_allowed_url(provider, url)
        ))
        providers[provider] = details
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": APP_VERSION,
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
        "packages": {name: _package_version(name) for name in ("playwright", "fastapi", "uvicorn", "rich", "plyer", "requests")},
        "providers": providers,
        "incidents": recent_incidents(MAX_INCIDENTS),
    }


def _newest_screenshots():
    """Yields the most recent screenshots as (path, name inside the zip)."""
    for folder, label in ((DIAG_DIR, "blocks"), (path("screenshots"), "products")):
        if not os.path.isdir(folder):
            continue
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".png")]
        files.sort(key=os.path.getmtime, reverse=True)
        for file_path in files[:MAX_DIAG_SCREENSHOTS]:
            yield file_path, f"screenshots/{label}/{os.path.basename(file_path)}"


def build_log_bundle(note="", context=None, include_screenshots=True):
    """Builds one zip for the user to send: report, app details, logs and screenshots."""
    report = build_report(note, context)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
        archive.writestr("report.txt", report["text"])
        archive.writestr("app-context.json", json.dumps(app_context(), indent=2, ensure_ascii=False))
        files = [(p, f"logs/{os.path.basename(p)}") for p in (f"{LOG_FILE}.1", LOG_FILE)]
        if include_screenshots:
            files += list(_newest_screenshots())
        with _log_lock:
            for file_path, name in files:
                try:
                    archive.write(file_path, name)
                except OSError:
                    pass  # Not created yet, or removed since it was listed.
    return buffer.getvalue()


def report_url(title, text):
    """Builds a pre-filled GitHub issue link, shortening the body to fit a URL."""
    base = f"https://github.com/{REPORT_REPO}/issues/new?"
    note = "\n\n_(Shortened to fit the link.)_"
    body = text
    while True:
        url = base + urllib.parse.urlencode({"title": title, "body": body})
        if len(url) <= MAX_REPORT_URL_LENGTH:
            return url
        body = body[: int(len(body) * 0.85)].rstrip() + note


if __name__ == "__main__":
    if "--report" not in sys.argv:
        print("Usage: python tracker_core.py --report")
        sys.exit(2)
    report = build_report(" ".join(a for a in sys.argv[1:] if a != "--report"), context="command line")
    os.makedirs(DIAG_DIR, exist_ok=True)
    report_path = os.path.join(DIAG_DIR, "report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report["text"])
    print(report["text"])
    print(f"\nSaved to {report_path}")
    bundle_path = os.path.join(DIAG_DIR, "logs-to-send.zip")
    with open(bundle_path, "wb") as f:
        f.write(build_log_bundle(" ".join(a for a in sys.argv[1:] if a != "--report"), "command line"))
    print(f"\nSend this file to the developer: {bundle_path}")
    print(f"\nOr open this link to report it on GitHub:\n{report['github_url']}")
