import os
import time
import random
import sqlite3
import threading
import urllib.parse
from typing import List, Dict, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
import uvicorn

# Import the tracker modules
try:
    import blinkit_tracker as blinkit
    import zepto_tracker as zepto
except ImportError as e:
    print(f"Error importing tracker modules: {e}")
    # Fallback paths if import system fails due to directory structure
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import blinkit_tracker as blinkit
    import zepto_tracker as zepto

app = FastAPI(
    title="Blinkit & Zepto Stock Tracker API 🛒",
    description="REST API to manage hyperlocal product stock monitoring and background search discovery.",
    version="1.0.0"
)

# Global tracking structures for background monitor threads
active_threads: Dict[str, threading.Thread] = {}
stop_events: Dict[str, threading.Event] = {}
thread_metadata: Dict[str, dict] = {}

# Global dictionary for active browser geolocation setups (non-blocking)
active_setups: Dict[str, dict] = {}


# --- Pydantic Schemas ---
class ProductItem(BaseModel):
    name: str = Field(..., description="Friendly name of the product", example="Hot Wheels High-Tail Chaser Die Cast Car")
    url: str = Field(..., description="Direct Blinkit/Zepto detail page URL containing product identifier", example="https://blinkit.com/prn/hot-wheels-high-tail-chaser-die-cast-car/prid/771923")

class ProductList(BaseModel):
    products: List[ProductItem]

class KeywordList(BaseModel):
    keywords: List[str] = Field(..., description="List of search terms to scan for new arrivals", example=["Hot Wheels", "Milk"])

class TrackerStatus(BaseModel):
    provider: str
    is_running: bool
    last_run_time: Optional[str] = None
    check_interval_minutes: int
    tracked_products_count: int
    tracked_keywords_count: int


# --- Helper Database Functions ---
def get_db_history() -> List[dict]:
    """Retrieves all stock status change logs from the SQLite DB."""
    DB_FILE = "tracker.db"
    if not os.path.exists(DB_FILE):
        return []
    
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, url, status, timestamp FROM status_history ORDER BY id DESC LIMIT 100")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# --- Worker Thread Loop ---
def background_tracker_worker(provider: str, stop_event: threading.Event, metadata: dict):
    """Periodically scrapes stock status and keyword discovery in the background."""
    tracker = blinkit if provider == "blinkit" else zepto
    tracker.init_db()
    
    metadata["last_run_time"] = datetime.now().isoformat()
    
    from playwright.sync_api import sync_playwright
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=tracker.STATE_FILE,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()
            
            notified_keyword_products = set()
            
            while not stop_event.is_set():
                metadata["last_run_time"] = datetime.now().isoformat()
                
                # Reload config dynamically
                config = tracker.load_config()
                products = config.get("products", [])
                
                # 1. Scrape Tracked Products
                for product in products:
                    if stop_event.is_set():
                        break
                    
                    status = tracker.check_product_stock(page, product)
                    prev_status = tracker.update_status(product["url"], product["name"], status)
                    
                    # Notify on state transition
                    if prev_status is not None and prev_status != status:
                        if status in ["IN_STOCK", "OUT_OF_STOCK"]:
                            tracker.trigger_notifications(
                                product["name"], prev_status, status, product["url"], config
                            )
                    
                    # Short jitter sleep between detail page loads
                    stop_event.wait(timeout=random.uniform(3.0, 6.0))
                
                # 2. Check Tracked Keywords
                keywords = config.get("tracked_keywords", [])
                if keywords and not stop_event.is_set():
                    for keyword in keywords:
                        if stop_event.is_set():
                            break
                        
                        found_items = tracker.search_products(page, keyword)
                        already_tracked_urls = {p["url"] for p in products}
                        new_products_found = False
                        
                        for item in found_items:
                            if provider == "blinkit":
                                slug = tracker.slugify(item["name"])
                                product_url = f"https://blinkit.com/prn/{slug}/prid/{item['id']}"
                            else:
                                product_url = item["url"]
                                
                            if product_url not in already_tracked_urls:
                                if product_url not in notified_keyword_products:
                                    notified_keyword_products.add(product_url)
                                    new_products_found = True
                                    print(f"[{provider.upper()}] Background discovery: {item['name']}")
                        
                        if new_products_found:
                            # Send desktop alert
                            if config.get("desktop_notifications"):
                                tracker.send_desktop_notification(f"Keyword '{keyword}'", "DISCOVERED_NEW_ITEMS")
                            
                            # Send telegram alert
                            telegram_config = config.get("telegram", {})
                            if telegram_config.get("enabled"):
                                bot_token = telegram_config.get("bot_token")
                                chat_id = telegram_config.get("chat_id")
                                if bot_token and chat_id and "YOUR_" not in bot_token:
                                    text = (
                                        f"🔍 *{provider.upper()} Keyword Discovery Alert* 🔍\n\n"
                                        f"New items matching keyword *'{keyword}'* have been detected!\n\n"
                                        f"Run keyword review on terminal or call API to add them."
                                    )
                                    telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                                    try:
                                        import requests
                                        requests.post(telegram_url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
                                    except Exception:
                                        pass
                        
                        # Jitter sleep between search pages
                        stop_event.wait(timeout=random.uniform(4.0, 8.0))
                
                # Sleep for configured interval
                interval_minutes = config.get("check_interval_minutes", 10)
                # Sleep in increments of 1 second checking stop event
                for _ in range(int(interval_minutes * 60)):
                    if stop_event.is_set():
                        break
                    time.sleep(1)
            
            browser.close()
    except Exception as e:
        print(f"Error in {provider} background tracker worker: {e}")
    finally:
        metadata["is_running"] = False


# --- REST Routes ---

@app.get("/", include_in_schema=False)
def root_redirect():
    """Redirect root access to Swagger UI documentation."""
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["General"])
def health_check():
    """Simple service health validation."""
    return {
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "active_background_threads": list(active_threads.keys()),
        "active_headed_setups": list(active_setups.keys())
    }


@app.get("/history", tags=["General"])
def get_status_change_history():
    """Fetches the past 100 stock transition logs logged in the SQLite tracker database."""
    history = get_db_history()
    return {"count": len(history), "history": history}


# --- Setup Geolocation Routes (Non-blocking) ---

@app.post("/setup/{provider}", tags=["Location Setup"])
def initialize_location_setup(provider: str):
    """
    Launches a visible (headed) Playwright browser window on the server/host machine.
    Use this to manually set your address pin and load stores.
    """
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
        
    if provider in active_setups:
        raise HTTPException(status_code=400, detail=f"Setup is already running for {provider}. Save it first or wait.")

    from playwright.sync_api import sync_playwright
    
    tracker = blinkit if provider == "blinkit" else zepto
    
    try:
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        
        url = "https://blinkit.com" if provider == "blinkit" else "https://www.zepto.com"
        page.goto(url)
        
        # Save pointers globally to retrieve and close during `/save`
        active_setups[provider] = {
            "playwright": p,
            "browser": browser,
            "context": context,
            "tracker": tracker
        }
        
        return {
            "status": "browser_launched",
            "message": f"Visible browser window opened at {url}. Perform location lookup, then POST to /setup/{provider}/save to complete.",
            "provider": provider
        }
    except Exception as e:
        # Cleanup if launch fails
        if provider in active_setups:
            del active_setups[provider]
        raise HTTPException(status_code=500, detail=f"Failed to launch browser: {str(e)}")


@app.post("/setup/{provider}/save", tags=["Location Setup"])
def finalize_location_setup(provider: str):
    """
    Saves the session state (cookies, localStorage) from the active setup browser window,
    stores it to state configuration files, and closes the browser context.
    """
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
        
    if provider not in active_setups:
        raise HTTPException(status_code=400, detail=f"No active setup browser session found for {provider}.")

    setup_data = active_setups[provider]
    p = setup_data["playwright"]
    browser = setup_data["browser"]
    context = setup_data["context"]
    tracker = setup_data["tracker"]
    
    try:
        # Extract storage state
        context.storage_state(path=tracker.STATE_FILE)
        
        # Shutdown browser
        browser.close()
        p.stop()
        
        # Clean from global
        del active_setups[provider]
        
        return {
            "status": "success",
            "message": f"Successfully loaded and saved geolocation context to {tracker.STATE_FILE}.",
            "provider": provider
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to finalize setup: {str(e)}")


# --- Product Config Routes ---

@app.get("/products/{provider}", response_model=ProductList, tags=["Product Configuration"])
def get_tracked_products(provider: str):
    """Returns the lists of currently configured product items in tracking configuration."""
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
    
    tracker = blinkit if provider == "blinkit" else zepto
    config = tracker.load_config()
    return {"products": config.get("products", [])}


@app.post("/products/{provider}", tags=["Product Configuration"])
def add_tracked_product(provider: str, product: ProductItem):
    """Adds a new product item manually using Name and URL to config."""
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
    
    tracker = blinkit if provider == "blinkit" else zepto
    config = tracker.load_config()
    products = config.get("products", [])
    
    # Check duplicate
    if any(p["url"] == product.url for p in products):
        return {"status": "ignored", "message": "Product is already tracked.", "product": product}
        
    tracker.add_to_config(product.name, product.url)
    return {"status": "added", "message": "Successfully added product to track list.", "product": product}


@app.delete("/products/{provider}", tags=["Product Configuration"])
def remove_tracked_product(provider: str, url: str = Query(..., description="Direct URL of product to delete")):
    """Removes a product matching the provided URL query string from the configuration."""
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
    
    tracker = blinkit if provider == "blinkit" else zepto
    config = tracker.load_config()
    products = config.get("products", [])
    
    filtered_products = [p for p in products if p["url"] != url]
    if len(filtered_products) == len(products):
        raise HTTPException(status_code=404, detail="Product not found with matching URL.")
        
    config["products"] = filtered_products
    with open(tracker.CONFIG_FILE, "w") as f:
        import json
        json.dump(config, f, indent=2)
        
    return {"status": "deleted", "message": "Successfully removed product from config."}


# --- Tracked Keywords Routes ---

@app.get("/keywords/{provider}", response_model=KeywordList, tags=["Keyword Configuration"])
def get_tracked_keywords(provider: str):
    """Fetches the configuration search keyword list."""
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
    
    tracker = blinkit if provider == "blinkit" else zepto
    config = tracker.load_config()
    return {"keywords": config.get("tracked_keywords", [])}


@app.post("/keywords/{provider}", tags=["Keyword Configuration"])
def update_tracked_keywords(provider: str, payload: KeywordList):
    """Overwrites/sets the tracked search keyword array in config."""
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
    
    tracker = blinkit if provider == "blinkit" else zepto
    config = tracker.load_config()
    config["tracked_keywords"] = payload.keywords
    
    with open(tracker.CONFIG_FILE, "w") as f:
        import json
        json.dump(config, f, indent=2)
        
    return {"status": "success", "message": "Keywords updated.", "keywords": payload.keywords}


# --- Live Scraper Routes ---

@app.get("/search/{provider}", tags=["Live Actions"])
def search_marketplace_products(provider: str, q: str = Query(..., description="Query phrase")):
    """Performs real-time, location-aware search query, returning stock statuses of products."""
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
        
    tracker = blinkit if provider == "blinkit" else zepto
    if not os.path.exists(tracker.STATE_FILE):
        raise HTTPException(status_code=400, detail=f"Location state file {tracker.STATE_FILE} missing. Run location setup.")
        
    from playwright.sync_api import sync_playwright
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=tracker.STATE_FILE,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()
            results = tracker.search_products(page, q)
            browser.close()
            
            # Map parameters to clean formats
            formatted_results = []
            for item in results:
                if provider == "blinkit":
                    slug = tracker.slugify(item["name"])
                    product_url = f"https://blinkit.com/prn/{slug}/prid/{item['id']}"
                else:
                    product_url = item["url"]
                formatted_results.append({
                    "name": item["name"],
                    "details": item["details"],
                    "url": product_url,
                    "status": item["status"]
                })
                
            return {"query": q, "count": len(formatted_results), "results": formatted_results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.get("/stock/{provider}", tags=["Live Actions"])
def get_live_product_stock(provider: str, url: str = Query(..., description="Direct page product URL")):
    """Scrapes stock state for a single product page immediately."""
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
        
    tracker = blinkit if provider == "blinkit" else zepto
    if not os.path.exists(tracker.STATE_FILE):
        raise HTTPException(status_code=400, detail=f"Location state file {tracker.STATE_FILE} missing. Run location setup.")
        
    from playwright.sync_api import sync_playwright
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=tracker.STATE_FILE,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()
            
            # Mock configuration object
            product = {"name": "API Live Test Product", "url": url}
            status = tracker.check_product_stock(page, product)
            browser.close()
            
            return {"url": url, "status": status, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scrape request failed: {str(e)}")


# --- Tracker Background Controls ---

@app.get("/tracker/{provider}/status", response_model=TrackerStatus, tags=["Tracker Monitor Daemon"])
def get_background_tracker_status(provider: str):
    """Inspect status of background daemon threads."""
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
        
    tracker = blinkit if provider == "blinkit" else zepto
    config = tracker.load_config()
    
    is_running = provider in active_threads and active_threads[provider].is_alive()
    meta = thread_metadata.get(provider, {})
    
    return {
        "provider": provider,
        "is_running": is_running,
        "last_run_time": meta.get("last_run_time"),
        "check_interval_minutes": config.get("check_interval_minutes", 10),
        "tracked_products_count": len(config.get("products", [])),
        "tracked_keywords_count": len(config.get("tracked_keywords", []))
    }


@app.post("/tracker/{provider}/start", tags=["Tracker Monitor Daemon"])
def start_background_tracker(provider: str):
    """Starts the background tracking check loop as a daemon thread."""
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
        
    if provider in active_threads and active_threads[provider].is_alive():
        return {"status": "active", "message": f"Background tracker for {provider} is already running."}
        
    tracker = blinkit if provider == "blinkit" else zepto
    if not os.path.exists(tracker.STATE_FILE):
        raise HTTPException(status_code=400, detail=f"Location state file {tracker.STATE_FILE} missing. Run location setup.")

    stop_event = threading.Event()
    stop_events[provider] = stop_event
    
    metadata = {"is_running": True, "last_run_time": None}
    thread_metadata[provider] = metadata
    
    thread = threading.Thread(
        target=background_tracker_worker,
        args=(provider, stop_event, metadata),
        name=f"{provider}_background_tracker",
        daemon=True
    )
    active_threads[provider] = thread
    thread.start()
    
    return {
        "status": "started",
        "message": f"Background tracker thread started for {provider}."
    }


@app.post("/tracker/{provider}/stop", tags=["Tracker Monitor Daemon"])
def stop_background_tracker(provider: str):
    """Signals stop and terminates background worker monitor threads gracefully."""
    if provider not in ["blinkit", "zepto"]:
        raise HTTPException(status_code=400, detail="Invalid provider. Must be 'blinkit' or 'zepto'.")
        
    if provider not in active_threads or not active_threads[provider].is_alive():
        return {"status": "inactive", "message": f"Background tracker for {provider} is not currently running."}

    # Signal stop
    stop_events[provider].set()
    
    # Wait block (max 5 seconds) to clean up
    active_threads[provider].join(timeout=5)
    
    # Clean up pointers
    del active_threads[provider]
    del stop_events[provider]
    if provider in thread_metadata:
        del thread_metadata[provider]
        
    return {
        "status": "stopped",
        "message": f"Graceful stop signal sent. Background tracker thread terminated for {provider}."
    }


if __name__ == "__main__":
    print("Starting FastAPI service on http://localhost:8000")
    print("Open http://localhost:8000/docs for Swagger documentation.")
    uvicorn.run("api:app", host="localhost", port=8000, reload=True)
