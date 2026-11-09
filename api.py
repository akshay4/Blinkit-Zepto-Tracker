import ipaddress
import os
import queue
import random
import shutil
import sqlite3
import threading
import time
import sys
import asyncio
import urllib.parse
from contextlib import asynccontextmanager, closing
from typing import List, Dict, Optional
from datetime import datetime

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import blinkit_tracker as blinkit
import tracker_core as core
import zepto_tracker as zepto

TRACKERS = {"blinkit": blinkit, "zepto": zepto}
MAX_KEYWORDS = 25
SETUP_IDLE_LIMIT_SECONDS = 30 * 60
SETUP_POLL_MS = 1500


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.environ.get("TRACKER_BROWSER_OPENED") != "true" and os.environ.get("TRACKER_NO_BROWSER") != "true":
        os.environ["TRACKER_BROWSER_OPENED"] = "true"

        def _open():
            time.sleep(1.5)
            import webbrowser
            try:
                webbrowser.open("http://localhost:8000")
            except Exception as e:
                print(f"Failed to automatically open browser: {e}")

        threading.Thread(target=_open, daemon=True).start()
    yield


app = FastAPI(
    title="Blinkit & Zepto Stock Tracker API 🛒",
    description="REST API to manage hyperlocal product stock monitoring and background search discovery.",
    version=core.APP_VERSION,
    lifespan=lifespan,
)

LOCAL_ORIGIN_REGEX = (
    r"https?://(localhost|127\.0\.0\.1|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+"
    r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|[\w-]+\.local)(:\d+)?"
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=LOCAL_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _is_local_name(hostname: str) -> bool:
    """True for names that can only mean this network, unlike a public domain."""
    return "." not in hostname or hostname.endswith((".local", ".lan", ".home", ".internal"))


def _hostname(url: str) -> str:
    """The lowercase hostname, or an empty string for anything malformed."""
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _request_is_allowed(host_header: str, origin: Optional[str]) -> bool:
    """Stops websites open in a browser on this network from driving the local API.

    The mobile app sends no Origin. A dotted Host name is refused because a public
    domain can be re-pointed at this machine to get past same-origin checks.
    """
    host = _hostname(f"//{host_header}")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _is_local_name(host):
            return False
    if not origin:
        return True
    origin_host = _hostname(origin)
    if not origin_host:
        return False  # "null" is what a sandboxed frame on any website sends.
    if origin_host == host:
        return True
    try:
        return not ipaddress.ip_address(origin_host).is_global
    except ValueError:
        return _is_local_name(origin_host)


@app.middleware("http")
async def guard_local_api(request: Request, call_next):
    if not _request_is_allowed(request.headers.get("host", ""), request.headers.get("origin")):
        return JSONResponse({"detail": "This request came from outside the local network."}, status_code=403)
    return await call_next(request)


@app.exception_handler(core.ConfigError)
async def config_error_handler(_request: Request, exc: core.ConfigError):
    return JSONResponse({"detail": str(exc)}, status_code=500)


# Global tracking structures for background monitor threads
tracker_lock = threading.RLock()
active_threads: Dict[str, threading.Thread] = {}
stop_events: Dict[str, threading.Event] = {}
thread_metadata: Dict[str, dict] = {}

# Open location setup windows
setup_lock = threading.RLock()
active_setups: Dict[str, "SetupSession"] = {}


# --- Pydantic Schemas ---
class ProductItem(BaseModel):
    name: str = Field(..., max_length=400, description="Friendly name of the product")
    url: str = Field(..., max_length=2000, description="Direct Blinkit/Zepto detail page URL containing product identifier")
    status: Optional[str] = Field(None, description="Current stock status of the product")


class ProductList(BaseModel):
    products: List[ProductItem]


class KeywordList(BaseModel):
    keywords: List[str] = Field(..., description="List of search terms to scan for new arrivals")


class ReportRequest(BaseModel):
    note: str = Field("", max_length=2000, description="What the user was doing and what went wrong")
    context: str = Field("", max_length=100, description="Where the report was started from")
    include_screenshots: bool = Field(True, description="Add recent screenshots to the log bundle")


class TrackerStatus(BaseModel):
    provider: str
    is_running: bool
    last_run_time: Optional[str] = None
    check_interval_minutes: float
    tracked_products_count: int
    tracked_keywords_count: int
    state: str = "stopped"
    setup_saved: bool = False
    cooldown_seconds: int = 0
    block_message: Optional[str] = None


# --- Helpers ---
def get_tracker(provider: str):
    tracker = TRACKERS.get(provider)
    if tracker is None:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
    return tracker


def require_session(provider: str):
    tracker = get_tracker(provider)
    if not os.path.exists(tracker.STATE_FILE):
        raise HTTPException(status_code=400, detail=f"Location is not set up for {provider}. Run Location Setup first.")
    return tracker


def require_no_cooldown(provider: str):
    remaining = core.cooldown_remaining(provider)
    if remaining:
        raise HTTPException(
            status_code=429,
            detail=f"{core.provider_label(provider)} recently refused this connection. "
                   f"Checks are paused for {max(1, remaining // 60)} more minutes so the block can clear.",
        )


def get_db_history() -> List[dict]:
    """Retrieves recent stock status changes from the SQLite DB."""
    if not os.path.exists(blinkit.DB_FILE):
        return []
    try:
        with closing(core.connect_db(blinkit.DB_FILE)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, url, status, timestamp FROM status_history ORDER BY id DESC LIMIT 100").fetchall()
            return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# --- Worker Thread Loop ---
def run_tracking_cycle(provider: str, tracker, config: dict, stop_event: threading.Event, notified: set):
    """Checks every product and keyword once. Raises core.BlockedError at the first refusal."""
    products = config.get("products", [])
    keywords = config.get("tracked_keywords", [])
    with core.tracking_page(tracker.STATE_FILE) as page:
        for product in products:
            if stop_event.is_set():
                return
            status = tracker.check_product_stock(page, product)
            previous = tracker.update_status(product["url"], product["name"], status)
            if previous is not None and previous != status and status in ("IN_STOCK", "OUT_OF_STOCK"):
                tracker.trigger_notifications(product["name"], previous, status, product["url"], config)
            stop_event.wait(timeout=random.uniform(3.0, 6.0))

        tracked = {core.product_key(p["url"]) for p in products}
        for keyword in keywords:
            if stop_event.is_set():
                return
            found_any = False
            for item in tracker.search_products(page, keyword):
                if provider == "blinkit":
                    url = f"https://blinkit.com/prn/{tracker.slugify(item['name'])}/prid/{item['id']}"
                else:
                    url = item["url"]
                if core.product_key(url) not in tracked and url not in notified:
                    notified.add(url)
                    found_any = True
                    print(f"[{provider.upper()}] Background discovery: {item['name']}")
            if found_any:
                if config.get("desktop_notifications"):
                    tracker.send_desktop_notification(f"Keyword '{keyword}'", "DISCOVERED_NEW_ITEMS")
                core.send_telegram(
                    config.get("telegram") or {},
                    f"🔍 *{provider.upper()} Keyword Discovery Alert* 🔍\n\n"
                    f"New items matching keyword *'{core.markdown_escape(keyword)}'* have been detected!\n\n"
                    "Open the dashboard to add them.",
                )
            stop_event.wait(timeout=random.uniform(4.0, 8.0))


def background_tracker_worker(provider: str, stop_event: threading.Event, metadata: dict):
    """Keeps checking until stopped, pausing after a refusal and surviving unexpected errors."""
    tracker = TRACKERS[provider]
    notified: set = set()
    failures = 0
    try:
        tracker.init_db()
        while not stop_event.is_set():
            remaining = core.cooldown_remaining(provider)
            if remaining:
                metadata["state"] = "paused"
                stop_event.wait(timeout=min(remaining, 30))
                continue

            metadata["state"] = "checking"
            metadata["last_run_time"] = datetime.now().isoformat()
            try:
                config = tracker.load_config()
                run_tracking_cycle(provider, tracker, config, stop_event, notified)
                failures = 0
                metadata["block_message"] = None
                wait_seconds = core.check_interval_minutes(config) * 60 + random.randint(-30, 30)
            except core.BlockedError as e:
                metadata["block_message"] = e.user_message
                core.alert_blocked(provider, config, e)
                continue
            except Exception as e:
                failures += 1
                print(f"Error in {provider} background tracker: {core.scrub(str(e))[:300]}")
                core.record_incident(provider, "worker_error", "background check", snippet=str(e))
                wait_seconds = min(60 * 2 ** failures, 1800)
            metadata["state"] = "waiting"
            stop_event.wait(timeout=max(60, wait_seconds))
    finally:
        metadata["is_running"] = False
        metadata["state"] = "stopped"


# --- Location setup ---
class SetupSession:
    """A visible browser window owned by one thread.

    Playwright objects only work on the thread that created them, and API requests
    arrive on whichever thread is free, so requests send commands to this thread.
    """

    ACTIVE = {"starting", "open", "challenge", "blocked"}

    def __init__(self, provider: str):
        self.provider = provider
        self.tracker = TRACKERS[provider]
        self.commands: "queue.Queue[dict]" = queue.Queue()
        self.ready = threading.Event()
        self.info = {"provider": provider, "state": "starting", "message": "Opening the browser...", "browser": None}
        self.last_status = None
        self.reported_kind = None
        self.thread = threading.Thread(target=self._run, name=f"{provider}_setup", daemon=True)
        self.thread.start()

    @property
    def is_active(self) -> bool:
        return self.thread.is_alive() and self.info["state"] in self.ACTIVE

    def status(self) -> dict:
        return dict(self.info)

    def _set(self, state: str, message: str):
        self.info.update(state=state, message=message)

    def _track_navigation(self, response):
        try:
            if response.request.is_navigation_request() and response.frame == response.frame.page.main_frame:
                self.last_status = response.status
        except Exception:
            pass

    def _observe(self, context) -> None:
        pages = context.pages
        if not pages:
            return
        page = pages[-1]
        info = core.inspect_page(page)
        if info is None:
            return
        kind = core.classify_block(info, self.last_status if not info.get("length") else None)
        site = core.provider_label(self.provider)
        if kind == "challenge":
            self._set("challenge", f"{site} is showing a security check. Complete it in the browser window if it asks you to.")
        elif kind:
            if self.reported_kind != kind:
                self.reported_kind = kind
                try:
                    error = core.register_block(page, self.provider, kind, "location setup", info, self.last_status)
                    self.info["block_message"] = error.user_message
                except Exception as e:
                    print(f"Could not record the block: {core.scrub(str(e))[:200]}")
            self._set("blocked", core.block_message(self.provider, kind))
        else:
            self.reported_kind = None
            self.info.pop("block_message", None)
            if core.is_location_prompt(info):
                self._set("open", f"Set your delivery location in the {site} window.")
            else:
                self._set("open", "When your delivery address is showing correctly, save below.")

    def _handle(self, command: dict, context, label: str) -> bool:
        """Runs one command and returns True when the window should close."""
        if command["name"] == "close":
            command["result"]["ok"] = True
            self._set("closed", "Setup cancelled.")
            command["done"].set()
            return True
        pages = context.pages
        try:
            if not pages:
                raise core.SetupRefused("The browser window was closed.")
            core.save_session(context, pages[-1], self.provider, self.tracker.STATE_FILE, label)
        except core.SetupRefused as e:
            command["result"].update(ok=False, status=409, detail=str(e))
            command["done"].set()
            return False
        except Exception as e:
            command["result"].update(ok=False, status=500, detail=f"Could not save: {core.scrub(str(e))[:200]}")
            core.record_incident(self.provider, "setup_error", "location setup", snippet=str(e))
            command["done"].set()
            return False
        command["result"]["ok"] = True
        self._set("saved", "Location saved.")
        command["done"].set()
        return True

    def _run(self):
        from playwright.sync_api import sync_playwright

        browser = None
        try:
            with sync_playwright() as p:
                browser, context, label = core.launch_setup_browser(p)
                self.info["browser"] = label
                context.on("response", self._track_navigation)
                page = context.new_page()
                try:
                    page.goto(core.PROVIDERS[self.provider]["home"], wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(1500)
                    self._observe(context)
                except Exception as e:
                    self._set("open", "The page is slow to load. If it stays blank, cancel and try again.")
                    core.record_incident(self.provider, "page_error", "location setup", snippet=str(e))
                if self.info["state"] == "starting":
                    self._set("open", "Set your delivery location in the browser window.")
                self.ready.set()

                deadline = time.monotonic() + SETUP_IDLE_LIMIT_SECONDS
                while True:
                    finished = False
                    try:
                        while True:
                            command = self.commands.get_nowait()
                            if self._handle(command, context, label):
                                finished = True
                                break
                            deadline = time.monotonic() + SETUP_IDLE_LIMIT_SECONDS
                    except queue.Empty:
                        pass
                    if finished:
                        break
                    if not browser.is_connected() or not context.pages:
                        self._set("closed", "The browser window was closed before saving.")
                        break
                    if time.monotonic() > deadline:
                        self._set("closed", "Setup timed out after 30 minutes.")
                        break
                    try:
                        context.pages[-1].wait_for_timeout(SETUP_POLL_MS)
                        self._observe(context)
                    except Exception:
                        continue  # The window or tab is closing. The next pass sorts out which.
                if browser.is_connected():
                    browser.close()
        except Exception as e:
            message = core.scrub(str(e))[:300]
            self._set("error", message)
            core.record_incident(self.provider, "setup_error", "location setup", snippet=str(e))
        finally:
            self.ready.set()
            self._drain_commands()

    def _drain_commands(self):
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return
            command["result"].update(ok=False, status=409, detail="The browser window is no longer open.")
            command["done"].set()

    def send(self, name: str, timeout: float = 30) -> dict:
        command = {"name": name, "done": threading.Event(), "result": {}}
        self.commands.put(command)
        if not command["done"].wait(timeout):
            return {"ok": False, "status": 504, "detail": "The browser did not respond in time."}
        return command["result"]


def get_active_setup(provider: str) -> Optional[SetupSession]:
    with setup_lock:
        session = active_setups.get(provider)
        if session is not None and not session.is_active:
            del active_setups[provider]
            return None
        return session


# --- REST Routes ---

@app.get("/", include_in_schema=False)
def serve_dashboard():
    """Serves the Unified Dashboard Web UI."""
    return FileResponse(os.path.join(core.BASE_DIR, "index.html"), headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.get("/health", tags=["General"])
def health_check():
    """Simple service health validation."""
    return {
        "status": "online",
        "version": core.APP_VERSION,
        "timestamp": datetime.now().isoformat(),
        "active_background_threads": [p for p, t in active_threads.items() if t.is_alive()],
        "active_headed_setups": [p for p in TRACKERS if get_active_setup(p)],
    }


@app.get("/history", tags=["General"])
def get_status_change_history():
    """Fetches the past 100 stock transition logs logged in the SQLite tracker database."""
    history = get_db_history()
    return {"count": len(history), "history": history}


@app.get("/diagnostics", tags=["Problem Reports"])
def get_diagnostics():
    """Recent problems, with personal details already removed."""
    return {
        "incidents": core.recent_incidents(),
        "providers": {p: core.provider_summary(p) for p in TRACKERS},
    }


@app.post("/report", tags=["Problem Reports"])
def create_problem_report(payload: ReportRequest):
    """Builds a report for the user to review and send. Nothing is sent by the server."""
    return core.build_report(payload.note, payload.context)


@app.post("/report/logs", tags=["Problem Reports"])
def download_log_bundle(payload: ReportRequest):
    """Returns a zip of the report, app details, logs and screenshots for the user to send."""
    name = f"tracker-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        core.build_log_bundle(payload.note, payload.context, payload.include_screenshots),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/reset", tags=["General"])
def reset_application():
    """Stops all background trackers, deletes the SQLite database history, state cookies, configs, and screenshots."""
    with tracker_lock:
        for event in list(stop_events.values()):
            event.set()
        for thread in list(active_threads.values()):
            thread.join(timeout=8)
        active_threads.clear()
        stop_events.clear()
        thread_metadata.clear()

    with setup_lock:
        for session in list(active_setups.values()):
            if session.is_active:
                session.send("close", timeout=10)
        active_setups.clear()

    db_file = blinkit.DB_FILE
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except OSError:
            # The file is locked, so empty it instead.
            with closing(core.connect_db(db_file)) as conn, conn:
                conn.execute("DROP TABLE IF EXISTS product_status")
                conn.execute("DROP TABLE IF EXISTS status_history")

    for tracker in TRACKERS.values():
        for file_path in (tracker.STATE_FILE, f"{tracker.STATE_FILE}.meta.json"):
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
        core.save_config(tracker.CONFIG_FILE, core.DEFAULT_CONFIG)

    for folder in (blinkit.SCREENSHOT_DIR, core.DIAG_DIR):
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)

    return {"status": "success", "message": "All tracking states, database, and location cookies have been cleared successfully."}


# --- Setup Geolocation Routes (Non-blocking) ---

@app.post("/setup/{provider}", tags=["Location Setup"])
def initialize_location_setup(provider: str):
    """Opens a visible browser on the host machine so the user can set their delivery address."""
    get_tracker(provider)
    with setup_lock:
        session = get_active_setup(provider)
        reused = session is not None
        if session is None:
            session = SetupSession(provider)
            active_setups[provider] = session

    session.ready.wait(timeout=60)
    info = session.status()
    if info["state"] == "error":
        raise HTTPException(status_code=500, detail=f"Failed to launch browser: {info['message']}")
    return {
        **info,
        "status": "browser_already_open" if reused else "browser_launched",
    }


@app.get("/setup/{provider}/status", tags=["Location Setup"])
def get_location_setup_status(provider: str):
    """Reports what the setup window is showing, so a block is visible right away."""
    get_tracker(provider)
    with setup_lock:
        session = active_setups.get(provider)
    if session is None:
        return {"provider": provider, "state": "none", "message": "No setup window is open."}
    return session.status()


@app.post("/setup/{provider}/cancel", tags=["Location Setup"])
def cancel_location_setup(provider: str):
    """Closes the setup window without saving."""
    get_tracker(provider)
    session = get_active_setup(provider)
    if session is None:
        return {"status": "inactive", "message": "No setup window is open."}
    session.send("close", timeout=15)
    return {"status": "cancelled", "message": "Setup cancelled."}


@app.post("/setup/{provider}/save", tags=["Location Setup"])
def finalize_location_setup(provider: str):
    """Saves the session from the setup window and closes it. A blocked page is never saved."""
    tracker = get_tracker(provider)
    session = get_active_setup(provider)
    if session is None:
        raise HTTPException(status_code=400, detail=f"No setup window is open for {provider}. Start Location Setup again.")
    result = session.send("save", timeout=30)
    if not result.get("ok"):
        raise HTTPException(status_code=result.get("status", 500), detail=result.get("detail", "Could not save the location."))
    return {
        "status": "success",
        "message": f"Successfully saved your location to {os.path.basename(tracker.STATE_FILE)}.",
        "provider": provider,
    }


# --- Product Config Routes ---

@app.get("/products/{provider}", response_model=ProductList, tags=["Product Configuration"])
def get_tracked_products(provider: str):
    """Returns the tracked products with their latest known stock status."""
    tracker = get_tracker(provider)
    statuses = core.current_statuses(tracker.DB_FILE)
    products = [
        {"name": p["name"], "url": p["url"], "status": statuses.get(p["url"], "UNKNOWN")}
        for p in tracker.load_config().get("products", [])
    ]
    return {"products": products}


def scrape_and_update_product_stock_bg(provider: str, name: str, url: str):
    """Background task to scrape a newly added product once."""
    tracker = TRACKERS[provider]
    try:
        if not os.path.exists(tracker.STATE_FILE) or core.cooldown_remaining(provider):
            return
        with core.tracking_page(tracker.STATE_FILE) as page:
            status = tracker.check_product_stock(page, {"name": name, "url": url})
        tracker.init_db()
        tracker.update_status(url, name, status)
    except core.BlockedError:
        pass  # Already recorded, and the cooldown stops further checks.
    except Exception as e:
        print(f"Error in background scrape for newly added product {name}: {core.scrub(str(e))[:200]}")


@app.post("/products/{provider}", tags=["Product Configuration"])
def add_tracked_product(provider: str, product: ProductItem, background_tasks: BackgroundTasks):
    """Adds a new product item manually using Name and URL to config."""
    tracker = get_tracker(provider)
    if not core.is_allowed_url(provider, product.url):
        raise HTTPException(status_code=400, detail=f"That is not an https {core.provider_label(provider)} product link.")

    if any(p["url"] == product.url for p in tracker.load_config().get("products", [])):
        return {"status": "ignored", "message": "Product is already tracked.", "product": product}

    tracker.add_to_config(product.name, product.url)

    initial_status = product.status if product.status in ("IN_STOCK", "OUT_OF_STOCK") else "UNKNOWN"
    try:
        tracker.init_db()
        tracker.update_status(product.url, product.name, initial_status)
    except sqlite3.Error as e:
        print(f"Error initializing/updating DB status for newly added product: {e}")

    if initial_status == "UNKNOWN":
        background_tasks.add_task(scrape_and_update_product_stock_bg, provider, product.name, product.url)

    return {"status": "added", "message": "Successfully added product to track list.", "product": product}


@app.delete("/products/{provider}", tags=["Product Configuration"])
def remove_tracked_product(provider: str, url: str = Query(..., description="Direct URL of product to delete")):
    """Removes a product matching the provided URL query string from the configuration."""
    tracker = get_tracker(provider)
    with core.config_lock:
        config = tracker.load_config()
        products = config.get("products", [])
        filtered_products = [p for p in products if p["url"] != url]
        if len(filtered_products) == len(products):
            raise HTTPException(status_code=404, detail="Product not found with matching URL.")
        config["products"] = filtered_products
        core.save_config(tracker.CONFIG_FILE, config)
    return {"status": "deleted", "message": "Successfully removed product from config."}


# --- Tracked Keywords Routes ---

@app.get("/keywords/{provider}", response_model=KeywordList, tags=["Keyword Configuration"])
def get_tracked_keywords(provider: str):
    """Fetches the configuration search keyword list."""
    return {"keywords": get_tracker(provider).load_config().get("tracked_keywords", [])}


@app.post("/keywords/{provider}", tags=["Keyword Configuration"])
def update_tracked_keywords(provider: str, payload: KeywordList):
    """Overwrites/sets the tracked search keyword array in config."""
    tracker = get_tracker(provider)
    keywords = list(dict.fromkeys(k.strip() for k in payload.keywords if k.strip()))
    if len(keywords) > MAX_KEYWORDS or any(len(k) > 100 for k in keywords):
        raise HTTPException(status_code=400, detail=f"Use at most {MAX_KEYWORDS} keywords of up to 100 characters each.")
    with core.config_lock:
        config = tracker.load_config()
        config["tracked_keywords"] = keywords
        core.save_config(tracker.CONFIG_FILE, config)
    return {"status": "success", "message": "Keywords updated.", "keywords": keywords}


# --- Live Scraper Routes ---

def blocked_response(error: core.BlockedError) -> HTTPException:
    status = 429 if error.kind == "rate_limited" else 403
    return HTTPException(status_code=status, detail=error.user_message)


@app.get("/search/{provider}", tags=["Live Actions"])
def search_marketplace_products(provider: str, q: str = Query(..., max_length=100, description="Query phrase")):
    """Performs real-time, location-aware search query, returning stock statuses of products."""
    tracker = require_session(provider)
    require_no_cooldown(provider)
    try:
        with core.tracking_page(tracker.STATE_FILE) as page:
            results = tracker.search_products(page, q)
    except core.BlockedError as e:
        raise blocked_response(e)
    except Exception as e:
        core.record_incident(provider, "page_error", "search", snippet=str(e))
        raise HTTPException(status_code=500, detail=f"Search failed: {core.scrub(str(e))[:200]}")

    formatted_results = []
    for item in results:
        if provider == "blinkit":
            product_url = f"https://blinkit.com/prn/{tracker.slugify(item['name'])}/prid/{item['id']}"
        else:
            product_url = item["url"]
        formatted_results.append({
            "name": item["name"],
            "details": item["details"],
            "url": product_url,
            "status": item["status"],
        })
    return {"query": q, "count": len(formatted_results), "results": formatted_results}


@app.get("/stock/{provider}", tags=["Live Actions"])
def get_live_product_stock(provider: str, url: str = Query(..., max_length=2000, description="Direct page product URL")):
    """Scrapes stock state for a single product page immediately."""
    tracker = require_session(provider)
    if not core.is_allowed_url(provider, url):
        raise HTTPException(status_code=400, detail=f"That is not an https {core.provider_label(provider)} product link.")
    require_no_cooldown(provider)

    products = tracker.load_config().get("products", [])
    product_name = next((p["name"] for p in products if p["url"] == url), "API Live Test Product")
    try:
        with core.tracking_page(tracker.STATE_FILE) as page:
            status = tracker.check_product_stock(page, {"name": product_name, "url": url})
    except core.BlockedError as e:
        raise blocked_response(e)
    except Exception as e:
        core.record_incident(provider, "page_error", "stock check", url=url, snippet=str(e))
        raise HTTPException(status_code=500, detail=f"Scrape request failed: {core.scrub(str(e))[:200]}")

    try:
        tracker.init_db()
        tracker.update_status(url, product_name, status)
    except sqlite3.Error as e:
        print(f"Error updating DB status during live check: {e}")
    return {"url": url, "status": status, "timestamp": datetime.now().isoformat()}


# --- Tracker Background Controls ---

@app.get("/tracker/{provider}/status", response_model=TrackerStatus, tags=["Tracker Monitor Daemon"])
def get_background_tracker_status(provider: str):
    """Inspect status of background daemon threads."""
    tracker = get_tracker(provider)
    config = tracker.load_config()
    is_running = provider in active_threads and active_threads[provider].is_alive()
    meta = thread_metadata.get(provider, {})
    remaining = core.cooldown_remaining(provider)

    block_message = None
    if remaining:
        incidents = [
            i for i in core.recent_incidents(5)
            if i["provider"] == provider and i["kind"] in ("blocked", "rate_limited", "challenge")
        ]
        block_message = core.block_message(provider, incidents[0]["kind"] if incidents else "blocked")

    return {
        "provider": provider,
        "is_running": is_running,
        "last_run_time": meta.get("last_run_time"),
        "check_interval_minutes": core.check_interval_minutes(config),
        "tracked_products_count": len(config.get("products", [])),
        "tracked_keywords_count": len(config.get("tracked_keywords", [])),
        "state": meta.get("state", "stopped") if is_running else "stopped",
        "setup_saved": os.path.exists(tracker.STATE_FILE),
        "cooldown_seconds": remaining,
        "block_message": block_message,
    }


@app.post("/tracker/all/start", tags=["Tracker Monitor Daemon"])
def start_all_trackers():
    """Starts the background tracking check loops for both providers."""
    results = {}
    for provider in TRACKERS:
        try:
            results[provider] = start_background_tracker(provider)
        except HTTPException as e:
            results[provider] = {"status": "error", "detail": e.detail}
        except Exception as e:
            results[provider] = {"status": "error", "detail": core.scrub(str(e))[:200]}
    return results


@app.post("/tracker/all/stop", tags=["Tracker Monitor Daemon"])
def stop_all_trackers():
    """Signals stop and terminates background worker monitor threads for both providers."""
    results = {}
    for provider in TRACKERS:
        try:
            results[provider] = stop_background_tracker(provider)
        except Exception as e:
            results[provider] = {"status": "error", "detail": core.scrub(str(e))[:200]}
    return results


@app.post("/tracker/{provider}/start", tags=["Tracker Monitor Daemon"])
def start_background_tracker(provider: str):
    """Starts the background tracking check loop as a daemon thread."""
    tracker = get_tracker(provider)
    with tracker_lock:
        if provider in active_threads and active_threads[provider].is_alive():
            return {"status": "active", "message": f"Background tracker for {provider} is already running."}
        if not os.path.exists(tracker.STATE_FILE):
            raise HTTPException(status_code=400, detail=f"Location is not set up for {provider}. Run Location Setup first.")

        stop_event = threading.Event()
        metadata = {"is_running": True, "last_run_time": None, "state": "starting"}
        thread = threading.Thread(
            target=background_tracker_worker,
            args=(provider, stop_event, metadata),
            name=f"{provider}_background_tracker",
            daemon=True,
        )
        stop_events[provider] = stop_event
        thread_metadata[provider] = metadata
        active_threads[provider] = thread
        thread.start()

    return {"status": "started", "message": f"Background tracker thread started for {provider}."}


@app.post("/tracker/{provider}/stop", tags=["Tracker Monitor Daemon"])
def stop_background_tracker(provider: str):
    """Signals stop and terminates background worker monitor threads gracefully."""
    get_tracker(provider)
    with tracker_lock:
        if provider not in active_threads or not active_threads[provider].is_alive():
            return {"status": "inactive", "message": f"Background tracker for {provider} is not currently running."}
        stop_events[provider].set()
        thread = active_threads[provider]

    thread.join(timeout=5)
    if thread.is_alive():
        # A page load is still finishing. Keeping the entry stops a second tracker from starting.
        return {"status": "stopping", "message": f"Background tracker for {provider} will stop after the current page."}

    with tracker_lock:
        active_threads.pop(provider, None)
        stop_events.pop(provider, None)
        thread_metadata.pop(provider, None)
    return {"status": "stopped", "message": f"Background tracker thread terminated for {provider}."}


if __name__ == "__main__":
    core.install_log_capture()
    import socket
    local_ip = "localhost"
    try:
        # Connect to a dummy external address to find the active local network interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except OSError:
        pass

    print("Starting FastAPI service on http://localhost:8000")
    if local_ip != "localhost":
        print(f"📡 Access from other local devices (like iPad) at: http://{local_ip}:8000")
    print("Open http://localhost:8000/docs for Swagger documentation.")
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
