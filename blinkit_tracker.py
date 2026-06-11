import os
import sys
import re

# Reconfigure stdout and stderr to handle UTF-8 symbols (like the Rupee symbol and Emojis) on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import json
import time
import random
import sqlite3
import argparse
import urllib.parse
from datetime import datetime
import requests
from plyer import notification

# Try importing Rich for nice formatting, fallback to print if not installed yet
try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich import box
    console = Console()
except ImportError:
    class SimpleConsole:
        def log(self, msg, style=None):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        def print(self, *args, **kwargs):
            print(*args, **kwargs)
    console = SimpleConsole()

DB_FILE = "tracker.db"
STATE_FILE = "state.json"
CONFIG_FILE = "config.json"
SCREENSHOT_DIR = "screenshots"

# Ensure screenshots directory exists
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def init_db():
    """Initializes the sqlite database to track stock status changes."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_status (
            url TEXT PRIMARY KEY,
            name TEXT,
            status TEXT,
            last_checked TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            status TEXT,
            timestamp TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_previous_status(url):
    """Retrieves the last known status of a product from the database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM product_status WHERE url = ?", (url,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def update_status(url, name, status):
    """Updates the product status and logs to history if it changed."""
    prev_status = get_previous_status(url)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    if prev_status is None:
        # First time tracking
        cursor.execute(
            "INSERT INTO product_status (url, name, status, last_checked) VALUES (?, ?, ?, ?)",
            (url, name, status, now)
        )
        cursor.execute(
            "INSERT INTO status_history (url, status, timestamp) VALUES (?, ?, ?)",
            (url, status, now)
        )
    elif prev_status != status:
        # Status changed
        cursor.execute(
            "UPDATE product_status SET name = ?, status = ?, last_checked = ? WHERE url = ?",
            (name, status, now, url)
        )
        cursor.execute(
            "INSERT INTO status_history (url, status, timestamp) VALUES (?, ?, ?)",
            (url, status, now)
        )
    else:
        # Just update last checked time
        cursor.execute(
            "UPDATE product_status SET last_checked = ? WHERE url = ?",
            (now, url)
        )

    conn.commit()
    conn.close()
    return prev_status


def load_config():
    """Loads settings from config.json."""
    if not os.path.exists(CONFIG_FILE):
        console.log(f"[bold red]Error:[/] {CONFIG_FILE} not found. Please create it or copy the default template.", style="bold red")
        sys.exit(1)
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def add_to_config(name, url):
    """Utility to write a product URL to config.json."""
    config = load_config()
    products = config.get("products", [])
    
    # Check if URL already tracked
    if any(p["url"] == url for p in products):
        console.print(f"[yellow]Product is already being tracked:[/] [cyan]{name}[/]")
        return
        
    products.append({
        "name": name,
        "url": url
    })
    config["products"] = products
    
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
        
    console.print(f"[bold green]Success![/] Added [cyan]{name}[/] to your tracking list.")


def slugify(text):
    """Converts a product name into a URL-friendly slug."""
    text = text.lower().strip()
    # Replace non-alphanumeric characters with hyphens
    text = re.sub(r'[^a-z0-9]+', '-', text)
    # Strip leading/trailing hyphens
    return text.strip('-')


def parse_selection_input(input_str, max_val):
    """Parses selection inputs like '2', '1,3,5', '1-4', or 'all'."""
    input_str = input_str.strip().lower()
    if not input_str:
        return []
        
    if input_str == "all":
        return list(range(max_val))
        
    selected_indices = set()
    parts = input_str.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start_str, end_str = part.split("-", 1)
                start = int(start_str.strip())
                end = int(end_str.strip())
                # Convert 1-indexed to 0-indexed and include endpoints
                for idx in range(start - 1, end):
                    if 0 <= idx < max_val:
                        selected_indices.add(idx)
            except ValueError:
                continue
        else:
            try:
                idx = int(part) - 1
                if 0 <= idx < max_val:
                    selected_indices.add(idx)
            except ValueError:
                continue
                
    return sorted(list(selected_indices))


def search_products(page, query):
    """Searches for products and returns parsed results."""
    search_url = f"https://blinkit.com/s/?q={urllib.parse.quote(query)}"
    console.log(f"Searching for [cyan]'{query}'[/] (location-aware)...")
    
    found_items = []
    try:
        page.goto(search_url, wait_until="load", timeout=20000)
        try:
            # Wait dynamically for the product cards to render (much faster than a hard sleep)
            page.wait_for_selector("div[role='button'][id]", state="visible", timeout=8000)
            page.wait_for_timeout(500)  # Small settle buffer
        except Exception:
            time.sleep(3.0)  # Fallback sleep if selector fails
        
        # Find all product card divs
        cards = page.locator("div[role='button']").all()
        
        for card in cards:
            try:
                cid = card.get_attribute("id")
                # Verify it's a numeric ID (Blinkit product IDs are numbers)
                if not cid or not cid.isdigit():
                    continue
                
                # Extract text components
                text = card.inner_text().strip()
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if not lines:
                    continue
                
                # Find product name
                name_candidate = None
                for line in lines:
                    if "MINS" in line.upper() or "MIN" in line.upper():
                        continue
                    if line.upper() in ["ADD", "OUT OF STOCK", "CLOSED", "OFF"]:
                        continue
                    name_candidate = line
                    break
                    
                if not name_candidate:
                    continue
                    
                # Try to find quantity and price
                qty = ""
                price = ""
                for line in lines:
                    if "₹" in line:
                        price = line.replace("₹", "Rs. ")
                    elif line != name_candidate and not any(k in line.upper() for k in ["MINS", "MIN", "ADD", "OUT OF STOCK", "CLOSED", "OFF"]):
                        qty = line
                        
                details = f"{qty} | {price}" if qty and price else (price or qty)
                # Remove trailing "ADD" or "OFF"
                details = details.replace("ADD", "").strip(" |")
                
                # Determine stock status
                card_lower = text.lower()
                status = "IN_STOCK"
                if any(p in card_lower for p in ["out of stock", "notify me", "coming soon", "temporarily unavailable", "currently unavailable", "closed"]):
                    status = "OUT_OF_STOCK"
                
                found_items.append({
                    "name": name_candidate,
                    "details": details,
                    "id": cid,
                    "status": status,
                    "locator_index": len(found_items)
                })
            except Exception:
                continue
    except Exception as e:
        console.log(f"[red]Error during search execution:[/] {e}", style="red")
        
    return found_items


def run_add_item():
    """Interactive CLI wizard to search and add products to track (supports bulk)."""
    if not os.path.exists(STATE_FILE):
        console.print("[bold red]Error:[/] Session state file (`state.json`) not found.")
        console.print("You must set up your location first. Run:")
        console.print("  [bold green]python blinkit_tracker.py --setup[/]\n")
        return

    console.print("\n[bold cyan]=======================================================[/]")
    console.print("[bold cyan]             ADD NEW PRODUCT(S) TO TRACK               [/]")
    console.print("[bold cyan]=======================================================[/]\n")
    console.print("Choose how you want to add a product:")
    console.print("1. [bold green]Search[/] for products (supports bulk selection)")
    console.print("2. [bold green]Paste URL[/] manually (copied from your browser/app)")
    
    choice = input("\nEnter choice (1 or 2): ").strip()
    
    if choice == "2":
        url = input("\nPaste the Blinkit product URL: ").strip()
        if "/prn/" not in url or "/prid/" not in url:
            console.print("[bold red]Error:[/] Invalid Blinkit product URL. It should look like: https://blinkit.com/prn/[slug]/prid/[id]")
            return
        name = input("Enter a friendly name for this product: ").strip()
        if not name:
            name = url.split("/prn/")[1].split("/")[0].replace("-", " ").title()
            console.print(f"Using auto-generated name: [cyan]{name}[/]")
            
        add_to_config(name, url)
        return
        
    elif choice != "1":
        console.print("[bold red]Invalid choice. Exiting wizard.[/]")
        return
        
    query = input("\nEnter item name or keyword to search: ").strip()
    if not query:
        console.print("[bold red]Search query cannot be empty.[/]")
        return
        
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        console.log("Launching headless browser...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=STATE_FILE,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        
        found_items = search_products(page, query)
        browser.close()
        
        if not found_items:
            console.print("[bold red]No products found matching your search. Please try a different keyword or check your setup.[/]")
            return
            
        # Print table of results
        table = Table(title=f"Search Results for '{query}'", box=box.ROUNDED)
        table.add_column("No.", style="yellow")
        table.add_column("Product Name", style="cyan")
        table.add_column("Details (Price/Qty)", style="green")
        table.add_column("Stock Status", style="bold")
        
        limit_val = min(len(found_items), 15)
        for index, item in enumerate(found_items[:limit_val], start=1):  # Limit to top 15 results
            status_style = "green" if item["status"] == "IN_STOCK" else "red"
            table.add_row(str(index), item["name"], item["details"], f"[{status_style}]{item['status']}[/]")
            
        console.print(table)
        
        console.print("\n[bold yellow]Selection format:[/] Enter a single number (e.g. `2`), list (e.g. `1,3,5`), range (e.g. `1-4`), or `all`")
        selection = input(f"Enter the number(s) (1-{limit_val}) to track (or press Enter to cancel): ").strip()
        if not selection:
            console.print("[yellow]Cancelled.[/]")
            return
            
        selected_indices = parse_selection_input(selection, limit_val)
        if not selected_indices:
            console.print("[bold red]No valid selections entered. Exiting wizard.[/]")
            return
            
        console.log(f"Instantly adding [bold]{len(selected_indices)}[/] items to tracking list...")
        
        for sel_idx in selected_indices:
            selected_item = found_items[sel_idx]
            
            # Slugify the product name offline
            slug = slugify(selected_item["name"])
            # Generate direct detail page URL matching Blinkit routing pattern
            product_url = f"https://blinkit.com/prn/{slug}/prid/{selected_item['id']}"
            
            full_name = f"{selected_item['name']} ({selected_item['details']})" if selected_item['details'] else selected_item['name']
            # Clean up trailing ADD text
            for term in [" ADD", "ADD", " OFF"]:
                if full_name.endswith(term):
                    full_name = full_name[:-len(term)].strip()
                    
            add_to_config(full_name, product_url)


def run_keyword_check_wizard():
    """CLI wizard to search for keywords and add new items."""
    if not os.path.exists(STATE_FILE):
        console.print("[bold red]Error:[/] Session state file (`state.json`) not found.")
        console.print("You must set up your location first. Run:")
        console.print("  [bold green]python blinkit_tracker.py --setup[/]\n")
        return

    config = load_config()
    keywords = config.get("tracked_keywords", [])
    if not keywords:
        console.print("[bold yellow]No keywords configured to track in config.json under 'tracked_keywords'.[/]")
        console.print("Please edit config.json and add search phrases to 'tracked_keywords'.\n")
        return

    console.print("\n[bold cyan]=======================================================[/]")
    console.print("[bold cyan]         REVIEW NEW PRODUCTS FOR TRACKED KEYWORDS      [/]")
    console.print("[bold cyan]=======================================================[/]\n")
    console.print(f"Scanning for keywords: [cyan]{', '.join(keywords)}[/]...\n")

    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        console.log("Launching headless browser...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=STATE_FILE,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        
        all_untracked_items = []
        already_tracked_urls = {p["url"] for p in config.get("products", [])}
        
        for keyword in keywords:
            found_items = search_products(page, keyword)
            for item in found_items:
                slug = slugify(item["name"])
                product_url = f"https://blinkit.com/prn/{slug}/prid/{item['id']}"
                if product_url not in already_tracked_urls:
                    all_untracked_items.append({
                        "name": item["name"],
                        "details": item["details"],
                        "url": product_url,
                        "status": item["status"],
                        "keyword": keyword
                    })
                    
        browser.close()

        if not all_untracked_items:
            console.print("[bold green]No new products found for your tracked keywords! All match results are already tracked.[/]")
            return

        table = Table(title="New Products Discovered Matching Keywords", box=box.ROUNDED)
        table.add_column("No.", style="yellow")
        table.add_column("Keyword Match", style="magenta")
        table.add_column("Product Name", style="cyan")
        table.add_column("Details (Price/Qty)", style="green")
        table.add_column("Stock Status", style="bold")
        
        for index, item in enumerate(all_untracked_items, start=1):
            status_style = "green" if item["status"] == "IN_STOCK" else "red"
            full_name = f"{item['name']} ({item['details']})" if item['details'] else item['name']
            # Clean up trailing ADD text
            for term in [" ADD", "ADD", " OFF"]:
                if full_name.endswith(term):
                    full_name = full_name[:-len(term)].strip()
            table.add_row(str(index), item["keyword"], full_name, item["details"], f"[{status_style}]{item['status']}[/]")
            
        console.print(table)
        
        console.print("\n[bold yellow]Selection format:[/] Enter a single number (e.g. `2`), list (e.g. `1,3,5`), range (e.g. `1-4`), or `all`")
        selection = input(f"Enter the number(s) (1-{len(all_untracked_items)}) to track (or press Enter to cancel): ").strip()
        if not selection:
            console.print("[yellow]Cancelled.[/]")
            return
            
        selected_indices = parse_selection_input(selection, len(all_untracked_items))
        if not selected_indices:
            console.print("[bold red]No valid selections entered. Exiting.[/]")
            return
            
        console.log(f"Adding [bold]{len(selected_indices)}[/] items to tracking list...")
        
        for sel_idx in selected_indices:
            selected_item = all_untracked_items[sel_idx]
            
            full_name = f"{selected_item['name']} ({selected_item['details']})" if selected_item['details'] else selected_item['name']
            for term in [" ADD", "ADD", " OFF"]:
                if full_name.endswith(term):
                    full_name = full_name[:-len(term)].strip()
            add_to_config(full_name, selected_item["url"])


def send_telegram_notification(product_name, old_status, new_status, url, telegram_config):
    """Sends a Telegram message alert."""
    if not telegram_config.get("enabled"):
        return

    bot_token = telegram_config.get("bot_token")
    chat_id = telegram_config.get("chat_id")

    if not bot_token or not chat_id or "YOUR_" in bot_token:
        console.log("[yellow]Warning:[/] Telegram notifications are enabled, but credentials are not configured.", style="yellow")
        return

    emoji = "🟢" if new_status == "IN_STOCK" else "🔴"
    text = (
        f"{emoji} *Blinkit Stock Alert* {emoji}\n\n"
        f"*Product:* {product_name}\n"
        f"*Status changed:* `{old_status or 'NEW'}` ➡️ `{new_status}`\n\n"
        f"[View Product on Blinkit]({url})"
    )

    telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }

    try:
        response = requests.post(telegram_url, json=payload, timeout=10)
        if response.status_code != 200:
            console.log(f"[red]Telegram API Error:[/] HTTP {response.status_code} - {response.text}", style="red")
    except Exception as e:
        console.log(f"[red]Failed to send Telegram notification:[/] {e}", style="red")


def send_desktop_notification(product_name, new_status):
    """Triggers a local OS notification."""
    title = "🛒 Blinkit Stock Alert!"
    message = f"'{product_name}' is now IN STOCK! Quick, order before it runs out." if new_status == "IN_STOCK" else f"'{product_name}' has gone OUT OF STOCK."
    
    try:
        notification.notify(
            title=title,
            message=message,
            app_name="Blinkit Stock Tracker",
            timeout=10
        )
    except Exception as e:
        console.log(f"[yellow]Failed to trigger desktop notification:[/] {e}", style="yellow")


def trigger_notifications(product_name, old_status, new_status, url, config):
    """Dispatches notifications across configured channels."""
    console.log(f"[bold green]🔔 Stock Alert![/] [yellow]{product_name}[/] changed from [red]{old_status or 'NEW'}[/] to [green]{new_status}[/]")
    
    if config.get("desktop_notifications"):
        send_desktop_notification(product_name, new_status)
        
    if config.get("telegram", {}).get("enabled"):
        send_telegram_notification(product_name, old_status, new_status, url, config["telegram"])


def run_setup():
    """Headed browser session to set user location and save auth state."""
    from playwright.sync_api import sync_playwright

    console.print("\n[bold cyan]=======================================================[/]")
    console.print("[bold cyan]       BLINKIT TRACKER - GEOLOCATION SETUP            [/]")
    console.print("[bold cyan]=======================================================[/]\n")
    console.print("This script will open a visible browser window.")
    console.print("Please perform the following actions:")
    console.print("1. [bold green]Set your exact delivery address / location[/] on the Blinkit page.")
    console.print("2. Ensure the correct store context is loaded (you can see correct delivery ETA).")
    console.print("3. Return to this terminal and press [bold yellow]Enter[/] to save the session state.\n")

    input("Press Enter to launch the browser...")

    with sync_playwright() as p:
        # Launch headed chromium browser
        browser = p.chromium.launch(headless=False)
        # Create context
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        
        page = context.new_page()
        console.log("Navigating to Blinkit...")
        page.goto("https://blinkit.com")
        
        console.print("\n[bold yellow]Browser is open. Go ahead and select your location on Blinkit.[/]")
        input("Press Enter here AFTER you have successfully set your location in the browser...")
        
        # Save storage state (cookies, local storage, session storage)
        context.storage_state(path=STATE_FILE)
        console.log(f"[bold green]Success![/] Session state saved to [bold cyan]{STATE_FILE}[/]")
        
        browser.close()


def check_product_stock(page, product):
    """Loads product page and parses whether it's in stock or out of stock."""
    name = product["name"]
    url = product["url"]
    
    try:
        console.log(f"Checking [cyan]{name}[/]...")
        # Navigate to product page
        page.goto(url, wait_until="load", timeout=30000)
        
        # Sleep a bit to allow animations and dynamic content to finish loading
        time.sleep(random.uniform(2.0, 4.0))
        
        # Take screenshot for debugging/verification
        clean_name = "".join([c if c.isalnum() else "_" for c in name])
        screenshot_path = os.path.join(SCREENSHOT_DIR, f"{clean_name}_latest.png")
        page.screenshot(path=screenshot_path)
        
        # Check if page loaded successfully (e.g., contains some headers or elements)
        # Blinkit pages typically have product titles in h1 or h2 elements.
        h1_elements = page.locator("h1").all_text_contents()
        if not h1_elements:
            console.log(f"[yellow]Warning:[/] No <h1> tags found on page. Page might still be loading or blocked by CAPTCHA. Saved screenshot to {screenshot_path}.", style="yellow")
            
        # Parse stock status using locator heuristic
        # Heuristic 1: Check for "Out of Stock", "Notify Me", etc.
        out_of_stock_patterns = [
            "out of stock", 
            "notify me", 
            "temporarily unavailable", 
            "currently unavailable",
            "coming soon"
        ]
        
        is_out_of_stock = False
        for pattern in out_of_stock_patterns:
            locator = page.get_by_text(pattern, exact=False)
            # Check if any matching element is visible
            for i in range(locator.count()):
                if locator.nth(i).is_visible():
                    is_out_of_stock = True
                    console.log(f"Found stockout indicator matching: [italic]{pattern}[/]")
                    break
            if is_out_of_stock:
                break
                
        # Heuristic 2: Check for presence of "ADD" button
        # Usually it is a button or div containing "ADD" (often uppercase)
        has_add_button = False
        add_locator = page.get_by_text("ADD", exact=True)
        for i in range(add_locator.count()):
            if add_locator.nth(i).is_visible():
                has_add_button = True
                break
                
        if not has_add_button:
            # Check lowercase or mixed case ADD
            add_locator_mixed = page.get_by_text("Add to cart", exact=False)
            for i in range(add_locator_mixed.count()):
                if add_locator_mixed.nth(i).is_visible():
                    has_add_button = True
                    break
        
        if is_out_of_stock:
            return "OUT_OF_STOCK"
        elif has_add_button:
            return "IN_STOCK"
        else:
            # Fallback check - search the text content of the page
            page_text = page.content().lower()
            if "add" in page_text and "out of stock" not in page_text:
                return "IN_STOCK"
            elif "out of stock" in page_text or "notify me" in page_text:
                return "OUT_OF_STOCK"
            else:
                return "UNKNOWN"
                
    except Exception as e:
        console.log(f"[red]Error scraping {name}:[/] {e}", style="red")
        return "UNKNOWN"


def run_tracker(check_once=False):
    """Main tracking loops."""
    # Ensure state file exists
    if not os.path.exists(STATE_FILE):
        console.print("[bold red]Error:[/] Session state file (`state.json`) not found.")
        console.print("You must set up your location first. Run:")
        console.print("  [bold green]python blinkit_tracker.py --setup[/]\n")
        sys.exit(1)
        
    config = load_config()
    products = config.get("products", [])
    
    if not products:
        console.log("[yellow]No products configured to track in config.json. Launching Add Product wizard...[/]")
        run_add_item()
        # Reload config
        config = load_config()
        products = config.get("products", [])
        if not products:
            console.log("[red]No products configured. Exiting.[/]")
            sys.exit(0)
        
    from playwright.sync_api import sync_playwright
    
    init_db()
    
    console.log(f"Starting Blinkit Tracker. Tracking [bold]{len(products)}[/] products.")
    
    notified_keyword_products = set()
    
    with sync_playwright() as p:
        # Launch browser in headless mode
        browser = p.chromium.launch(headless=True)
        # Create browser context using saved session state
        context = browser.new_context(
            storage_state=STATE_FILE,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        
        while True:
            # Reload config dynamically in case changes were made (e.g., via --keywords in another process)
            config = load_config()
            products = config.get("products", [])
            
            results = []
            
            for product in products:
                status = check_product_stock(page, product)
                prev_status = update_status(product["url"], product["name"], status)
                
                # Check for state transition to trigger notifications
                if prev_status is not None and prev_status != status:
                    if status in ["IN_STOCK", "OUT_OF_STOCK"]:
                        trigger_notifications(product["name"], prev_status, status, product["url"], config)
                
                results.append({
                    "name": product["name"],
                    "status": status,
                    "prev_status": prev_status or "N/A",
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                
                # Sleep between checking different products to mimic user behavior
                time.sleep(random.uniform(3.0, 6.0))
            
            # Print a neat status table using rich (if available)
            if "Table" in globals():
                table = Table(title="Blinkit Stock Tracking Status", box=box.ROUNDED)
                table.add_column("Product Name", style="cyan")
                table.add_column("Current Status", style="bold")
                table.add_column("Previous Status", style="dim")
                table.add_column("Last Checked", style="magenta")
                
                for r in results:
                    status_style = "green" if r["status"] == "IN_STOCK" else ("red" if r["status"] == "OUT_OF_STOCK" else "yellow")
                    table.add_row(
                        r["name"], 
                        f"[{status_style}]{r['status']}[/]", 
                        r["prev_status"], 
                        r["time"]
                    )
                console.print(table)
            else:
                # Text fallback
                print("\n=== Tracking Results ===")
                for r in results:
                    print(f"{r['name']}: {r['status']} (Previous: {r['prev_status']}) - Checked at {r['time']}")
                print("========================\n")
                
            # Scan tracked keywords for new products in background
            keywords = config.get("tracked_keywords", [])
            if keywords:
                console.log("[bold magenta]Scanning tracked keywords in the background...[/]")
                already_tracked_urls = {p["url"] for p in products}
                new_products_found = False
                
                for keyword in keywords:
                    found_items = search_products(page, keyword)
                    for item in found_items:
                        slug = slugify(item["name"])
                        product_url = f"https://blinkit.com/prn/{slug}/prid/{item['id']}"
                        if product_url not in already_tracked_urls:
                            if product_url not in notified_keyword_products:
                                notified_keyword_products.add(product_url)
                                console.log(f"[bold yellow]New product matching keyword '{keyword}' found:[/] {item['name']}")
                                new_products_found = True
                
                if new_products_found:
                    # Desktop alert
                    if config.get("desktop_notifications"):
                        try:
                            notification.notify(
                                title="🔍 New Products Found!",
                                message=f"New items matching your tracked keywords have been discovered. Run with --keywords to review and track them.",
                                app_name="Blinkit Stock Tracker",
                                timeout=10
                            )
                        except Exception as e:
                            console.log(f"[yellow]Failed to trigger desktop notification:[/] {e}", style="yellow")
                    
                    # Telegram alert
                    telegram_config = config.get("telegram", {})
                    if telegram_config.get("enabled"):
                        bot_token = telegram_config.get("bot_token")
                        chat_id = telegram_config.get("chat_id")
                        if bot_token and chat_id and "YOUR_" not in bot_token:
                            text = (
                                "🔍 *Blinkit Keyword Discovery Alert* 🔍\n\n"
                                "New items matching your tracked keywords have been detected!\n\n"
                                "Run `python blinkit_tracker.py --keywords` in your terminal to review and add them."
                            )
                            telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                            payload = {
                                "chat_id": chat_id,
                                "text": text,
                                "parse_mode": "Markdown"
                            }
                            try:
                                requests.post(telegram_url, json=payload, timeout=10)
                            except Exception as e:
                                console.log(f"[red]Failed to send Telegram keyword notification:[/] {e}", style="red")
                                
            if check_once:
                break
                
            interval = config.get("check_interval_minutes", 10)
            # Add small random jitter (up to 1 minute) to check interval to avoid strict pattern detection
            jitter_seconds = random.randint(-30, 30)
            sleep_seconds = max(60, (interval * 60) + jitter_seconds)
            
            console.log(f"Waiting [cyan]{sleep_seconds // 60}m {sleep_seconds % 60}s[/] before next check...")
            time.sleep(sleep_seconds)
            
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hyperlocal product stock tracker for Blinkit.")
    parser.add_argument("--setup", action="store_true", help="Launch interactive browser to set delivery location and save cookies.")
    parser.add_argument("--add", action="store_true", help="Launch interactive wizard to search and add a product to track.")
    parser.add_argument("--keywords", action="store_true", help="Search and review new products matching tracked keywords.")
    parser.add_argument("--check-once", action="store_true", help="Perform a single stock check check and exit.")
    args = parser.parse_args()
    
    if args.setup:
        run_setup()
    elif args.add:
        run_add_item()
    elif args.keywords:
        run_keyword_check_wizard()
    else:
        run_tracker(check_once=args.check_once)
