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

import time
import random
import argparse
import urllib.parse
from datetime import datetime
from plyer import notification

import tracker_core as core

# Try importing Rich for nice formatting, fallback to print if not installed yet
try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    console = Console()
except ImportError:
    class SimpleConsole:
        def log(self, msg, style=None):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        def print(self, *args, **kwargs):
            print(*args, **kwargs)
    console = SimpleConsole()

PROVIDER = "zepto"
DB_FILE = core.path("tracker.db")
STATE_FILE = core.path("zepto_state.json")
CONFIG_FILE = core.path("zepto_config.json")
SCREENSHOT_DIR = core.path("screenshots")
OUT_OF_STOCK_PATTERNS = ['out of stock', 'notify me', 'temporarily unavailable', 'currently unavailable', 'coming soon', 'sold out', 'store closed', 'currently closed']

# Ensure screenshots directory exists
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def init_db():
    """Initializes the sqlite database to track stock status changes."""
    core.init_db(DB_FILE)


def update_status(url, name, status):
    """Updates the product status and logs to history if it changed."""
    return core.update_status(DB_FILE, url, name, status)


def load_config():
    """Loads this provider's settings."""
    return core.load_config(CONFIG_FILE)


def add_to_config(name, url):
    """Adds a product to this provider's tracking list."""
    with core.config_lock:
        config = load_config()
        products = config.get("products", [])
        if any(p["url"] == url for p in products):
            console.print(f"[yellow]Product is already being tracked:[/] [cyan]{name}[/]")
            return
        products.append({"name": name, "url": url})
        config["products"] = products
        core.save_config(CONFIG_FILE, config)
    console.print(f"[bold green]Success![/] Added [cyan]{name}[/] to your tracking list.")


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


NON_NAME_LINES = {"ADD", "OUT OF STOCK", "CLOSED", "OFF", "COMING SOON"}
OFF_BADGE = re.compile(r"\bOFF\b", re.I)
RATING_COUNT = re.compile(r"^\(?[\d.,]+\s*[kKmM]?\)?$")
QUANTITY_UNITS = ["ml", "ltr", " g", " kg", " pc", " pack", " s", " unit"]
OUT_OF_STOCK_CARD_TEXT = ["out of stock", "notify me", "coming soon", "sold out", "store closed", "currently closed"]


def _is_name_line(line):
    return not (
        "%" in line
        or OFF_BADGE.search(line)
        or line.upper() in NON_NAME_LINES
        or "₹" in line
        or "Rs." in line
        or RATING_COUNT.match(line)
    )


def parse_search_anchor(full_url, text):
    """Turns one result link's text into an item, or None if it has no product name."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    name = next((l for l in lines if _is_name_line(l)), None)
    if not name:
        return None

    qty = ""
    price = ""
    for line in lines:
        if "₹" in line:
            price = price or line.replace("₹", "Rs. ")  # The first price is the sale price.
        elif line != name and any(unit in line.lower() for unit in QUANTITY_UNITS):
            qty = line
    details = f"{qty} | {price}" if qty and price else (price or qty)

    card_lower = text.lower()
    status = "OUT_OF_STOCK" if any(p in card_lower for p in OUT_OF_STOCK_CARD_TEXT) else "IN_STOCK"
    return {"name": name, "details": details.replace("ADD", "").strip(" |"), "url": full_url, "status": status}


def _is_broad_product_link(href):
    return any(p in href for p in ["/pn/", "/p/", "/pip/"]) and not any(c in href for c in ["/cn/", "/categories", "/zepto-cafe"])


def search_products(page, query):
    """Searches for products and returns parsed results. Raises core.BlockedError if refused."""
    search_url = f"https://www.zepto.com/search?query={urllib.parse.quote(query)}"
    console.log(f"Searching for [cyan]'{query}'[/] (location-aware)...")

    found_items = []
    try:
        core.open_search(page, PROVIDER, search_url, "a[href*='/pvid/']")
        anchors = page.evaluate("""() => [...document.querySelectorAll('a[href]')]
            .map(a => ({href: a.getAttribute('href'), text: a.innerText}))""")

        # Product links normally have /pvid/. The broader match is for when that layout changes.
        for matches in (lambda href: "/pvid/" in href, _is_broad_product_link):
            seen_urls = set()
            for anchor in anchors:
                href = anchor["href"] or ""
                if not matches(href):
                    continue
                full_url = "https://www.zepto.com" + href if href.startswith("/") else href
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                item = parse_search_anchor(full_url, anchor["text"].strip())
                if item:
                    found_items.append(item)
            if found_items:
                break
        core.finish_search(page, PROVIDER, search_url, len(found_items))
    except core.BlockedError:
        raise
    except Exception as e:
        console.log(f"[red]Error during search execution:[/] {core.scrub(str(e))[:200]}", style="red")
        core.record_incident(PROVIDER, "page_error", "search", url=search_url, snippet=str(e))

    return found_items


def run_add_item():
    """Interactive CLI wizard to search and add products to track (supports bulk)."""
    if not os.path.exists(STATE_FILE):
        console.print(f"[bold red]Error:[/] Session state file (`{STATE_FILE}`) not found.")
        console.print("You must set up your location first. Run:")
        console.print("  [bold green]python zepto_tracker.py --setup[/]\n")
        return

    console.print("\n[bold cyan]=======================================================[/]")
    console.print("[bold cyan]             ADD NEW PRODUCT(S) TO TRACK               [/]")
    console.print("[bold cyan]=======================================================[/]\n")
    console.print("Choose how you want to add a product:")
    console.print("1. [bold green]Search[/] for products (supports bulk selection)")
    console.print("2. [bold green]Paste URL[/] manually (copied from your browser/app)")
    
    choice = input("\nEnter choice (1 or 2): ").strip()
    
    if choice == "2":
        url = input("\nPaste the Zepto product URL: ").strip()
        if "/pvid/" not in url:
            console.print("[bold red]Error:[/] Invalid Zepto product URL. It should contain '/pvid/'")
            return
        name = input("Enter a friendly name for this product: ").strip()
        if not name:
            name_part = url.split("/pvid/")[0].split("/")[-1].replace("-", " ").title()
            name = name_part
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
        browser, _context, page = core.open_tracking_page(p, STATE_FILE)
        
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
        for index, item in enumerate(found_items[:limit_val], start=1):
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
            
            full_name = f"{selected_item['name']} ({selected_item['details']})" if selected_item['details'] else selected_item['name']
            # Clean up trailing ADD text
            for term in [" ADD", "ADD", " OFF"]:
                if full_name.endswith(term):
                    full_name = full_name[:-len(term)].strip()
                    
            add_to_config(full_name, selected_item["url"])


def run_keyword_check_wizard():
    """CLI wizard to search for keywords and add new items."""
    if not os.path.exists(STATE_FILE):
        console.print(f"[bold red]Error:[/] Session state file (`{STATE_FILE}`) not found.")
        console.print("You must set up your location first. Run:")
        console.print("  [bold green]python zepto_tracker.py --setup[/]\n")
        return

    config = load_config()
    keywords = config.get("tracked_keywords", [])
    if not keywords:
        console.print("[bold yellow]No keywords configured to track in zepto_config.json under 'tracked_keywords'.[/]")
        console.print("Please edit zepto_config.json and add search phrases to 'tracked_keywords'.\n")
        return

    console.print("\n[bold cyan]=======================================================[/]")
    console.print("[bold cyan]         REVIEW NEW PRODUCTS FOR TRACKED KEYWORDS      [/]")
    console.print("[bold cyan]=======================================================[/]\n")
    console.print(f"Scanning for keywords: [cyan]{', '.join(keywords)}[/]...\n")

    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        console.log("Launching headless browser...")
        browser, _context, page = core.open_tracking_page(p, STATE_FILE)
        
        all_untracked_items = []
        already_tracked_urls = {p["url"] for p in config.get("products", [])}
        
        for keyword in keywords:
            found_items = search_products(page, keyword)
            for item in found_items:
                if item["url"] not in already_tracked_urls:
                    all_untracked_items.append({
                        "name": item["name"],
                        "details": item["details"],
                        "url": item["url"],
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
    emoji = "🟢" if new_status == "IN_STOCK" else "🔴"
    text = (
        f"{emoji} *Zepto Stock Alert* {emoji}\n\n"
        f"*Product:* {core.markdown_escape(product_name)}\n"
        f"*Status changed:* `{old_status or 'NEW'}` ➡️ `{new_status}`\n\n"
        f"[View Product on Zepto]({url})"
    )
    core.send_telegram(telegram_config, text, console.log, disable_preview=False)


def send_desktop_notification(product_name, new_status):
    """Triggers a local OS notification."""
    if new_status == "IN_STOCK":
        title, message = "🛒 Zepto Stock Alert!", f"'{product_name}' is now IN STOCK! Quick, order before it runs out."
    elif new_status == "OUT_OF_STOCK":
        title, message = "🛒 Zepto Stock Alert!", f"'{product_name}' has gone OUT OF STOCK."
    else:
        title, message = "🔍 New Products Found!", f"{product_name}: new matching items were found."
    core.send_desktop(title, message, "Zepto Stock Tracker", console.log)


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
    console.print("[bold cyan]       ZEPTO TRACKER - GEOLOCATION SETUP              [/]")
    console.print("[bold cyan]=======================================================[/]\n")
    console.print("This script will open a visible browser window.")
    console.print("Please perform the following actions:")
    console.print("1. [bold green]Set your exact delivery address / location[/] on the Zepto page.")
    console.print("2. Ensure the correct store context is loaded (you can see correct delivery ETA or products).")
    console.print("3. Return to this terminal and press [bold yellow]Enter[/] to save the session state.\n")

    input("Press Enter to launch the browser...")

    with sync_playwright() as p:
        browser, context, label = core.launch_setup_browser(p)
        page = context.new_page()
        console.log(f"Navigating to Zepto using {label}...")
        try:
            response = page.goto("https://www.zepto.com", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            kind = core.classify_block(core.inspect_page(page), response.status if response else None)
        except Exception as e:
            kind = None
            console.print(f"[yellow]The page did not finish loading:[/] {core.scrub(str(e))[:200]}")
        if kind and kind != "challenge":
            error = core.register_block(page, PROVIDER, kind, "location setup", core.inspect_page(page), None)
            console.print(f"\n[bold red]{error.user_message}[/]")
            console.print("To send a report, run: [bold green]python tracker_core.py --report[/]")
            browser.close()
            sys.exit(1)

        console.print("\n[bold yellow]Browser is open. Go ahead and select your location on Zepto.[/]")
        while True:
            input("Press Enter here AFTER you have successfully set your location in the browser...")
            try:
                core.save_session(context, page, PROVIDER, STATE_FILE, label)
                break
            except core.SetupRefused as e:
                console.print(f"[bold red]Not saved:[/] {e}")
        console.log(f"[bold green]Success![/] Session state saved to [bold cyan]{STATE_FILE}[/]")

        browser.close()


def check_product_stock(page, product):
    """Returns the product's status. Raises core.BlockedError if the site refused the page."""
    return core.check_stock(page, PROVIDER, product, OUT_OF_STOCK_PATTERNS, SCREENSHOT_DIR, console.log)


def run_tracker(check_once=False):
    """Runs the tracker, pausing whenever the site refuses it."""
    while True:
        remaining = core.cooldown_remaining(PROVIDER)
        if remaining:
            console.print(f"[yellow]Zepto recently refused this connection. Pausing for {remaining // 60} more minutes.[/]")
            if check_once:
                sys.exit(1)
            time.sleep(min(remaining, 60))
            continue
        try:
            _tracker_loop(check_once)
            return
        except core.BlockedError as e:
            if check_once:
                raise
            console.print(f"[bold red]{e.user_message}[/]")
            console.print("To send a report, run: [bold green]python tracker_core.py --report[/]")
            core.alert_blocked(PROVIDER, load_config(), e, console.log)


def _tracker_loop(check_once=False):
    """Main tracking loop."""
    # Ensure state file exists
    if not os.path.exists(STATE_FILE):
        console.print(f"[bold red]Error:[/] Session state file (`{STATE_FILE}`) not found.")
        console.print("You must set up your location first. Run:")
        console.print("  [bold green]python zepto_tracker.py --setup[/]\n")
        sys.exit(1)
        
    config = load_config()
    products = config.get("products", [])
    
    if not products:
        console.log("[yellow]No products configured to track in zepto_config.json. Launching Add Product wizard...[/]")
        run_add_item()
        # Reload config
        config = load_config()
        products = config.get("products", [])
        if not products:
            console.log("[red]No products configured. Exiting.[/]")
            sys.exit(0)
        
    from playwright.sync_api import sync_playwright
    
    init_db()
    
    console.log(f"Starting Zepto Tracker. Tracking [bold]{len(products)}[/] products.")
    
    notified_keyword_products = set()
    
    with sync_playwright() as p:
        browser, _context, page = core.open_tracking_page(p, STATE_FILE)
        
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
                table = Table(title="Zepto Stock Tracking Status", box=box.ROUNDED)
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
                        product_url = item["url"]
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
                                message="New items matching your tracked keywords have been discovered. Run with --keywords to review and track them.",
                                app_name="Zepto Stock Tracker",
                                timeout=10
                            )
                        except Exception as e:
                            console.log(f"[yellow]Failed to trigger desktop notification:[/] {e}", style="yellow")
                    
                    # Telegram alert
                    core.send_telegram(
                        config.get("telegram", {}),
                        "🔍 *Zepto Keyword Discovery Alert* 🔍\n\n"
                        "New items matching your tracked keywords have been detected!\n\n"
                        "Run `python zepto_tracker.py --keywords` in your terminal to review and add them.",
                        console.log,
                    )
                                
            if check_once:
                break
                
            interval = core.check_interval_minutes(config)
            jitter_seconds = random.randint(-30, 30)
            sleep_seconds = int(max(60, (interval * 60) + jitter_seconds))
            
            console.log(f"Waiting [cyan]{sleep_seconds // 60}m {sleep_seconds % 60}s[/] before next check...")
            time.sleep(sleep_seconds)
            
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hyperlocal product stock tracker for Zepto.")
    parser.add_argument("--setup", action="store_true", help="Launch interactive browser to set delivery location and save cookies.")
    parser.add_argument("--add", action="store_true", help="Launch interactive wizard to search and add a product to track.")
    parser.add_argument("--keywords", action="store_true", help="Search and review new products matching tracked keywords.")
    parser.add_argument("--check-once", action="store_true", help="Perform a single stock check check and exit.")
    args = parser.parse_args()
    
    try:
        if args.setup:
            run_setup()
        elif args.add:
            run_add_item()
        elif args.keywords:
            run_keyword_check_wizard()
        else:
            run_tracker(check_once=args.check_once)
    except core.BlockedError as e:
        console.print(f"[bold red]{e.user_message}[/]")
        console.print("To send a report, run: [bold green]python tracker_core.py --report[/]")
        sys.exit(1)
