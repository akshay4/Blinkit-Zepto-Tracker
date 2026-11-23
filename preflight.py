"""Checks that everything the tracker needs is in place, before it starts.

    python3 preflight.py              # check and report
    python3 preflight.py --fix        # also install missing packages and the browser
    python3 preflight.py --dashboard  # also check that the dashboard port is free

Exits with 1 if something would stop the tracker from running, otherwise 0.
Uses only the standard library, so it works before anything is installed.
"""
import argparse
import importlib
import json
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

MIN_PYTHON = (3, 9)  # Playwright 1.49 needs 3.9 or newer.
DASHBOARD_PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("TRACKER_DATA_DIR") or BASE_DIR
PY = "python" if os.name == "nt" else "python3"  # As the user would type it.

PROVIDERS = [
    # label, script, config file, session file, home page
    ("Blinkit", "blinkit_tracker.py", "config.json", "state.json", "https://blinkit.com"),
    ("Zepto", "zepto_tracker.py", "zepto_config.json", "zepto_state.json", "https://www.zepto.com"),
]

OK, WARN, FAIL = "ok", "warn", "fail"


class Report:
    def __init__(self):
        self.results = []
        if os.name == "nt":
            os.system("")  # Turns on colour codes in the Windows console.
        self.colour = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def add(self, level, title, detail="", fix="", fixable=False):
        """fixable: running again with --fix can solve it."""
        self.results.append((level, title, detail, fix, fixable))
        marks = {OK: ("OK  ", "32"), WARN: ("WARN", "33"), FAIL: ("FAIL", "31")}
        word, code = marks[level]
        if self.colour:
            word = "\033[1;%sm%s\033[0m" % (code, word)
        print("  [%s] %s" % (word, title))
        if detail:
            print("         %s" % detail)
        if fix:
            print("         -> %s" % fix)

    def count(self, level):
        return sum(1 for r in self.results if r[0] == level)


def heading(text):
    print("\n%s" % text)


def parse_version(text):
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def marker_applies(marker):
    """Evaluates the simple markers requirements.txt uses, such as
    sys_platform == "darwin" and python_version < "3.10"."""
    values = {"sys_platform": sys.platform, "python_version": "%d.%d" % sys.version_info[:2]}
    for part in re.split(r"\s+and\s+", marker.strip()):
        m = re.match(r"""(\w+)\s*(==|!=|>=|<=|<|>)\s*["']([^"']+)["']$""", part.strip())
        if not m or m.group(1) not in values:
            return True  # Unknown marker: check the package rather than skip it.
        key, op, want = m.groups()
        have = values[key]
        if key == "python_version":
            have, want = parse_version(have), parse_version(want)
        ok = {"==": have == want, "!=": have != want, ">=": have >= want,
              "<=": have <= want, "<": have < want, ">": have > want}[op]
        if not ok:
            return False
    return True


def read_requirements():
    """Returns [(name, operator, version)] for the requirements that apply to this computer."""
    wanted = []
    with open(os.path.join(BASE_DIR, "requirements.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            spec, _, marker = line.partition(";")
            if marker and not marker_applies(marker):
                continue
            m = re.match(r"([A-Za-z0-9_.\-]+)\s*(?:(>=|==)\s*([0-9.]+))?", spec.strip())
            if m:
                wanted.append(m.groups())
    return wanted


def installed_version(name):
    try:
        from importlib import metadata
    except ImportError:  # Python 3.7 and older; the Python check already fails.
        return None
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def pip_blocked():
    """True for Pythons (such as Homebrew's) that refuse pip installs outside a virtual environment."""
    import sysconfig
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    marker = os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")
    return not in_venv and os.path.exists(marker)


def run(cmd, timeout=900):
    print("         $ %s" % " ".join(cmd))
    return subprocess.call(cmd, timeout=timeout) == 0


def browser_probe():
    """Tries to start each browser the tracker may use. Returns {name: error or None}."""
    code = r"""
import json
from playwright.sync_api import sync_playwright
out = {}
with sync_playwright() as p:
    for channel in (None, "chrome", "msedge"):
        try:
            kw = {"headless": True}
            if channel:
                kw["channel"] = channel
            p.chromium.launch(**kw).close()
            out[channel or "chromium"] = None
        except Exception as e:
            out[channel or "chromium"] = str(e).strip().splitlines()[0][:200]
print("PROBE" + json.dumps(out))
"""
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"chromium": "timed out after 120 seconds"}
    for line in proc.stdout.splitlines():
        if line.startswith("PROBE"):
            return json.loads(line[5:])
    return {"chromium": (proc.stderr.strip().splitlines() or ["could not start"])[-1][:200]}


# --- Checks ---

def check_python(r):
    heading("Python")
    version = ".".join(str(n) for n in sys.version_info[:3])
    if sys.version_info[:2] < MIN_PYTHON:
        r.add(FAIL, "Python %s is too old" % version,
              "The tracker needs Python %d.%d or newer." % MIN_PYTHON,
              "Install a newer Python from https://www.python.org/downloads/")
        return False
    r.add(OK, "Python %s (%s)" % (version, sys.executable))
    return True


def check_packages(r, fix):
    heading("Python packages (requirements.txt)")
    problems = []
    for name, op, version in read_requirements():
        have = installed_version(name)
        if have is None:
            problems.append((name, "not installed"))
        elif op == ">=" and parse_version(have) < parse_version(version):
            problems.append((name, "%s installed, %s or newer needed" % (have, version)))
        elif op == "==" and parse_version(have) != parse_version(version):
            problems.append((name, "%s installed, %s needed" % (have, version)))
        else:
            r.add(OK, "%s %s" % (name, have))
    if problems and fix and not pip_blocked():
        print("  Installing missing packages...")
        if run([sys.executable, "-m", "pip", "install", "-r", os.path.join(BASE_DIR, "requirements.txt")]):
            importlib.invalidate_caches()
            return check_packages(r, fix=False)
    if problems:
        names = ", ".join(name for name, _ in problems)
        details = "; ".join("%s %s" % (name, why) for name, why in problems)
        if pip_blocked():
            r.add(FAIL, "Missing or outdated: %s" % names,
                  details + ". This Python does not allow installing packages outside a virtual environment.",
                  "Create one in this folder and run from it: %s -m venv .venv && "
                  "source .venv/bin/activate && python preflight.py --fix" % PY)
        else:
            r.add(FAIL, "Missing or outdated: %s" % names, details,
                  "%s -m pip install -r requirements.txt  (or run this with --fix)" % PY, fixable=True)
    return not problems


def check_browsers(r, fix):
    heading("Browsers")
    found = browser_probe()
    if found.get("chromium") and fix:
        print("  Installing Playwright's Chromium...")
        if run([sys.executable, "-m", "playwright", "install", "chromium"]):
            found = browser_probe()
    if found.get("chromium"):
        r.add(FAIL, "Playwright's Chromium cannot start", found["chromium"],
              "%s -m playwright install chromium  (or run this with --fix)" % PY, fixable=True)
    else:
        r.add(OK, "Playwright's Chromium (used to check stock)")
    real = [name for name in ("chrome", "msedge") if not found.get(name)]
    if real:
        r.add(OK, "%s found (used for location setup)" % " and ".join(
            {"chrome": "Google Chrome", "msedge": "Microsoft Edge"}[n] for n in real))
    else:
        r.add(WARN, "Neither Google Chrome nor Microsoft Edge was found",
              "Location setup will use Playwright's Chromium, which sites block more often.",
              "Install Google Chrome from https://www.google.com/chrome/")
    return found


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_data_folder(r):
    heading("Data folder")
    try:
        os.makedirs(os.path.join(DATA_DIR, "diagnostics"), exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=DATA_DIR, prefix=".preflight-"):
            pass
        r.add(OK, "Can save settings, sessions and logs in %s" % DATA_DIR)
        return True
    except OSError as e:
        r.add(FAIL, "Cannot write to %s" % DATA_DIR, str(e),
              "Move the project to a folder you own, or fix its permissions.")
        return False


def check_provider(r, label, script, config_name, state_name, browsers):
    heading(label)
    config_path = os.path.join(DATA_DIR, config_name)
    config = None
    if not os.path.exists(config_path):
        r.add(OK, "%s not created yet (made with defaults on first run)" % config_name)
    else:
        try:
            config = load_json(config_path)
            if not isinstance(config, dict):
                raise ValueError("it must contain a JSON object")
            r.add(OK, "%s is valid" % config_name)
        except ValueError as e:
            r.add(FAIL, "%s is broken" % config_name, str(e),
                  "Correct the file, or delete it to start again with default settings "
                  "(you would need to add your products again)")
            config = None

    state_path = os.path.join(DATA_DIR, state_name)
    if not os.path.exists(state_path):
        r.add(WARN, "Delivery location not set up",
              "%s cannot check stock until you choose your location once." % label,
              "%s %s --setup  (or use Setup in the dashboard)" % (PY, script))
    else:
        try:
            load_json(state_path)
            meta = {}
            if os.path.exists(state_path + ".meta.json"):
                meta = load_json(state_path + ".meta.json")
            channel = meta.get("channel") if isinstance(meta, dict) else None
            if channel and channel != "chromium" and browsers.get(channel):
                name = {"chrome": "Google Chrome", "msedge": "Microsoft Edge"}.get(channel, channel)
                r.add(WARN, "Location was saved with %s, which can no longer start" % name,
                      "Checks will fall back to Chromium, which %s may block." % label,
                      "Reinstall %s, or redo setup: %s %s --setup" % (name, PY, script))
            else:
                r.add(OK, "Delivery location is set up")
        except ValueError:
            r.add(WARN, "Saved location session is damaged", "",
                  "%s %s --setup" % (PY, script))

    if config is not None:
        products = [p for p in config.get("products") or [] if isinstance(p, dict) and p.get("url")]
        keywords = config.get("tracked_keywords") or []
        if products or keywords:
            r.add(OK, "Tracking %d product(s) and %d keyword(s)" % (len(products), len(keywords)))
        else:
            r.add(WARN, "No products or keywords to track", "",
                  "%s %s --add  (or add them in the dashboard)" % (PY, script))
        telegram = config.get("telegram") or {}
        if telegram.get("enabled"):
            token, chat = str(telegram.get("bot_token") or ""), str(telegram.get("chat_id") or "")
            if not re.match(r"^\d+:[\w-]{20,}$", token) or not chat or chat.startswith("YOUR_"):
                r.add(WARN, "Telegram alerts are on, but the bot token or chat ID is not filled in",
                      fix="Edit the telegram section in %s, or turn Telegram off" % config_name)
            else:
                r.add(OK, "Telegram alerts configured")
    return config


def check_notifications(r, configs):
    if not any((c or {}).get("desktop_notifications", True) for c in configs):
        return
    heading("Desktop notifications")
    if installed_version("plyer") is None:
        return  # Already reported as a missing package.
    try:
        from plyer.utils import platform as plyer_platform
        importlib.import_module("plyer.platforms.%s.notification" % plyer_platform)
        r.add(OK, "Desktop notifications are available")
    except Exception as e:
        hint = ""
        if sys.platform == "darwin":
            hint = "On macOS plyer also needs pyobjus: %s -m pip install -r requirements.txt" % PY
        r.add(WARN, "Desktop notifications will not appear",
              "%s: %s" % (type(e).__name__, e), hint or "Use Telegram alerts instead.")


def check_network(r, configs):
    heading("Network")
    for label, _, _, _, url in PROVIDERS:
        host = urllib.parse.urlparse(url).hostname
        try:
            ip = socket.gethostbyname(host)
        except OSError as e:
            r.add(WARN, "Cannot look up %s" % host, str(e), "Check your internet connection.")
            continue
        if ip in ("0.0.0.0", "127.0.0.1"):
            r.add(FAIL, "%s is blocked by your DNS (it resolves to %s)" % (host, ip),
                  "An ad blocker such as Pi-hole is blocking it.",
                  "Allow %s in your DNS filter" % host)
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            urllib.request.urlopen(req, timeout=10).close()
            r.add(OK, "%s is reachable" % host)
        except urllib.error.HTTPError:
            r.add(OK, "%s is reachable" % host)  # Bot checks often answer scripts with 403.
        except Exception as e:
            r.add(WARN, "Cannot reach %s" % host, str(getattr(e, "reason", e)),
                  "Check your internet connection.")
    if any(((c or {}).get("telegram") or {}).get("enabled") for c in configs):
        try:
            urllib.request.urlopen("https://api.telegram.org", timeout=10).close()
            r.add(OK, "api.telegram.org is reachable")
        except urllib.error.HTTPError:
            r.add(OK, "api.telegram.org is reachable")
        except Exception as e:
            r.add(WARN, "Cannot reach api.telegram.org", str(getattr(e, "reason", e)),
                  "Telegram alerts will fail until it is reachable.")


def check_port(r):
    heading("Dashboard")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", DASHBOARD_PORT))
        r.add(OK, "Port %d is free" % DASHBOARD_PORT)
        return True
    except OSError:
        r.add(FAIL, "Port %d is already in use" % DASHBOARD_PORT,
              "The dashboard may already be running: open http://localhost:%d" % DASHBOARD_PORT,
              "Close the other window, or stop whatever is using port %d" % DASHBOARD_PORT)
        return False
    finally:
        s.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Checks that the tracker can run.")
    parser.add_argument("--fix", action="store_true", help="install missing packages and the browser")
    parser.add_argument("--dashboard", action="store_true", help="also check the dashboard port")
    parser.add_argument("--offline", action="store_true", help="skip the network checks")
    args = parser.parse_args(argv)

    print("Blinkit & Zepto Tracker - pre-flight check (%s %s)" % (platform.system(), platform.release()))
    r = Report()
    if check_python(r):
        packages_ok = check_packages(r, args.fix)
        browsers = check_browsers(r, args.fix) if installed_version("playwright") else {}
        check_data_folder(r)
        configs = [check_provider(r, label, script, cfg, state, browsers)
                   for label, script, cfg, state, _ in PROVIDERS]
        if packages_ok:
            check_notifications(r, configs)
        if not args.offline:
            check_network(r, configs)
        if args.dashboard:
            check_port(r)

    fails, warns = r.count(FAIL), r.count(WARN)
    print("")
    if fails:
        fixable = any(res[0] == FAIL and res[4] for res in r.results)
        print("%d problem(s) must be fixed before the tracker can run%s." % (
            fails, " (try: %s preflight.py --fix)" % PY if fixable and not args.fix else ""))
    elif warns:
        print("Ready to run, with %d warning(s) above." % warns)
    else:
        print("Everything is ready.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
