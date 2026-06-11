# Blinkit & Zepto Stock Tracker 🛒

A location-aware, instant-delivery stock monitoring utility for **Blinkit** and **Zepto**. Because inventory on quick-commerce apps is highly localized, this tool runs a headless browser using Playwright, loaded with your delivery location's cookies/session, to monitor product availability. 

It alerts you via **Desktop Notifications** and **Telegram Messages** the second an out-of-stock item is back online!

---

## Table of Contents
1. [Use Case & Problem Statement](#use-case--problem-statement)
2. [Key Features](#key-features)
3. [Prerequisites & Setup](#prerequisites--setup)
4. [Step 1: Location Setup (Important!)](#step-1-location-setup-important)
5. [Step 2: Adding Products to Track](#step-2-adding-products-to-track)
6. [Step 3: Configuration Options (Telegram, Intervals)](#step-3-configuration-options-telegram-intervals)
7. [Step 4: Running the Trackers](#step-4-running-the-trackers)
8. [Data Storage & Debugging](#data-storage--debugging)
9. [Anti-Detection Mechanisms](#anti-detection-mechanisms)
10. [REST API Server](#rest-api-server)

---

## Use Case & Problem Statement

Quick commerce services like Blinkit and Zepto fulfill orders from localized dark stores (micro-warehouses). Stock levels fluctuate dynamically:
* In-demand products (like specific Hot Wheels die-cast cars, premium milk brands, organic produce, or niche snacks) sell out within minutes of restocking.
* Inventory is strictly location-dependent: a product available at one delivery address may be out-of-stock or completely unlisted for an address just 2 km away.

This tool solves this by simulating a real user at your exact location, constantly polling the product pages, and notifying you instantly when stock statuses transition (e.g. from `OUT_OF_STOCK` to `IN_STOCK`).

---

## Key Features

* **Hyperlocal Stock Scraper**: Saves browser session cookies and local storage state so checks are run exactly as they would appear at your delivery address.
* **Interactive CLI Add Wizards**: Search for products directly from your terminal, view their current stock status (clearly labeled and color-coded), and bulk-add them to your tracking list.
* **Automated Keyword Monitoring**: Performs background scans for configured search keywords, detects new products matching the keywords, and prompts or alerts you to start tracking them.
* **Dual Alerts**: 
  * **OS Desktop Alerts**: Instant popups using the `plyer` notification system.
  * **Telegram Bot integration**: Custom markdown messages containing links to immediately purchase the product or review newly discovered keyword results.
* **sqlite3 Database Storage**: Keeps a full log of historical status transitions in a local database (`tracker.db`) for analysis.
* **Scrape Visual Verification**: Automatically takes and updates screenshots of monitored products under a `screenshots/` directory for visual verification and debugging.
* **Rich Console UI**: Features a beautiful terminal table showing current status, previous status, and checking timestamps.

---

## Prerequisites & Setup

Ensure you have **Python 3.8+** installed.

### 1. Install Dependencies
Install the required packages using the `requirements.txt`:
```bash
pip install -r requirements.txt
```

### 2. Install Playwright Browsers
Playwright requires browser binaries to operate. Run the following command to download the Chromium browser:
```bash
playwright install chromium
```

---

## Step 1: Location Setup (Important!)

Since inventory is completely address-dependent, you must first save your location context. This is a headed browser helper that saves cookies/local state:

### For Blinkit:
```bash
python blinkit_tracker.py --setup
```

### For Zepto:
```bash
python zepto_tracker.py --setup
```

**Actions to take:**
1. A visible Chrome window will open.
2. Search and select your exact delivery address on the webpage.
3. Ensure the address pin is set correctly and the store items load.
4. Go back to your terminal window and press **Enter**.
5. This saves the authorization and session data locally to `state.json` (Blinkit) and `zepto_state.json` (Zepto).

---

## Step 2: Adding Products to Track

Each tracker runs on its own config file (`config.json` for Blinkit, `zepto_config.json` for Zepto). You can add products in two ways:

### Method A: Interactive Search Wizard (Recommended)
You can search items directly using the built-in location-aware search:
```bash
# For Blinkit
python blinkit_tracker.py --add

# For Zepto
python zepto_tracker.py --add
```
1. Select Option `1` (Search for products).
2. Enter your query (e.g., `Hot Wheels` or `Milk`).
3. You will see a formatted list of the top 15 matches, including a **Stock Status** column (`IN_STOCK` in green or `OUT_OF_STOCK` in red). This allows you to easily identify and select out-of-stock items that you wish to track.
4. Input the numbers you want to track using comma-separation (`1,3`), range (`1-4`), a single index (`2`), or `all`.
5. The script automatically generates appropriate URLs and adds them to the respective configuration files.

### Method B: Manually Paste URL
If you have the exact product link from your phone or desktop browser:
1. Select Option `2` in the `--add` wizard.
2. Paste the product URL.
3. Enter a friendly name.

*Alternatively, you can manually open `config.json` or `zepto_config.json` and append objects to the `products` list.*

---

## Step 3: Configuration Options (Telegram, Intervals)

Open `config.json` (for Blinkit) or `zepto_config.json` (for Zepto) to customize settings.

```json
{
  "check_interval_minutes": 10,
  "desktop_notifications": true,
  "telegram": {
    "enabled": false,
    "bot_token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID"
  },
  "tracked_keywords": [
    "Hot Wheels",
    "Country Delight Cow"
  ],
  "products": [
    {
      "name": "Product Display Name",
      "url": "https://..."
    }
  ]
}
```

### Track New Items using Keywords
You can specify search queries in `"tracked_keywords"` inside your config. The trackers will handle them in two ways:

1. **Terminal Review Wizard**: Run the `--keywords` command to interactively scan for new/untracked items and select which ones you'd like to add:
   ```bash
   # Review Blinkit keywords
   python blinkit_tracker.py --keywords

   # Review Zepto keywords
   python zepto_tracker.py --keywords
   ```
2. **Background Discovery Notification**: During the main loop, the script periodically queries these keywords in the background. If any new product is listed under these search queries that isn't already tracked in your `"products"`, you will receive a Desktop and/or Telegram notification alert notifying you of the discovery.

### Configuring Telegram Notifications:
1. Message `@BotFather` on Telegram and run `/newbot` to create your bot and copy the API token.
2. Message `@GetChatID_Bot` or `@userinfobot` to retrieve your personal numerical Telegram chat ID.
3. Paste the values into `bot_token` and `chat_id`.
4. Set `"enabled": true`.

---

## Step 4: Running the Trackers

### 1. Run Continuous Monitoring
Runs the script in a loop. It checks all products, outputs a status table, and waits for `check_interval_minutes` (plus random jitter) before checking again.

```bash
# Monitor Blinkit Products
python blinkit_tracker.py

# Monitor Zepto Products
python zepto_tracker.py
```

### 2. Run a One-Time Check (Cron / Task Scheduler)
If you prefer to schedule the checks externally (e.g., using Windows Task Scheduler or crontab), run the scripts with `--check-once`. It will check all items, trigger any notifications, update the database, and exit.

```bash
python blinkit_tracker.py --check-once
python zepto_tracker.py --check-once
```

---

## Data Storage & Debugging

* **SQLite Database (`tracker.db`)**: Stores persistent state. It has two tables:
  * `product_status`: Contains the last known status and timestamp for each product URL.
  * `status_history`: A log of every time a product transitioned between statuses (useful for charting stock trends over time).
* **Screenshots (`screenshots/`)**: Saves a visual snapshot of the page during the last check. If a product status is reported incorrectly or fails, view the image in this directory to see if there is an address selector overlay, captcha, or site redesign.

---

## Anti-Detection Mechanisms

Quick commerce sites employ anti-bot systems. To prevent blocks, the trackers automatically use:
1. **Dynamic Jitter**: Introduces random sleep times between individual product pages (3-6 seconds) and randomized offsets (up to 1 minute) on the check interval.
2. **Real Browser User Agents**: Configures headers to mimic standard Google Chrome on Windows.
3. **Session Reuse**: Avoids loading pages raw; instead, it uses the saved storage state from your location setup, which mimics a logged-in/active session.

---

## REST API Server

If you prefer to operate the trackers programmatically, query statuses in real time, or integrate them into a dashboard, you can host the built-in **FastAPI** web server.

### 1. Launch the API Server
Ensure `fastapi` and `uvicorn` are installed, then run:
```bash
python api.py
```
This starts the web server on `http://localhost:8000`.

### 2. Interactive Swagger UI Documentation
Open your browser and navigate to:
```
http://localhost:8000/docs
```
This provides a complete interactive sandbox where you can test all endpoints, check parameters, and view response schemas.

### 3. Key Endpoints Exposing Features
* **Headed Geolocation Setup**:
  * `POST /setup/{provider}`: Spawns a visible browser window on the host machine to select your delivery location without blocking stdin.
  * `POST /setup/{provider}/save`: Saves the state context, closes the browser, and completes setup.
* **Search Marketplace**:
  * `GET /search/{provider}?q=query`: Performs a location-aware search and returns a list of items with their names, details, URLs, and current stock status.
* **Direct Stock Check**:
  * `GET /stock/{provider}?url=url`: Scrapes a product page immediately and returns its live stock status.
* **Product & Keyword Management**:
  * `GET` / `POST` / `DELETE` `/products/{provider}`: Retrieve, add, or remove items in the configuration list.
  * `GET` / `POST` `/keywords/{provider}`: Read and set keywords lists.
* **Active Background Daemon Controls**:
  * `POST /tracker/{provider}/start`: Starts a background thread running the scraping checks and keyword scans periodically.
  * `POST /tracker/{provider}/stop`: Triggers a graceful shutdown event to terminate the daemon thread.
  * `GET /tracker/{provider}/status`: Tells you if the thread is alive and when it last ran.

