# Blinkit & Zepto Stock Tracker - Simple User Guide 🛒

Welcome! This guide is written for everyone. You do **not** need to know programming or command-line coding to use this tracker. Just follow these simple steps for your device!

---

## 💻 Windows (How to Run)

### Setup & Launch in 3 Simple Steps:
1. **Install Python** (if you don't have it):
   * Go to [python.org](https://www.python.org/downloads/) and click the yellow **Download Python** button.
   * Open the downloaded file to install it.
   * ⚠️ **IMPORTANT**: During installation, check the box at the bottom that says **"Add Python to PATH"** before clicking Install.
2. **Launch the Tracker**:
   * Open the tracker folder on your computer.
   * Find the file named **`run_dashboard.bat`** (it has a little gears icon).
   * **Double-click `run_dashboard.bat`**.
   * A black window will open. It will automatically download and set up everything for you. This might take 1–2 minutes on the first run.
3. **Start Tracking**:
   * Once setup finishes, your default web browser (like Chrome or Edge) will automatically open a page at `http://localhost:8000` showing your tracking dashboard!
   * *Note: Keep the black window open in the background while using the tracker. Closing it turns off the tracker.*

### ♻️ How to Start Over (Reset everything):
If you want to clear your list, delete history, or reset your address:
* Find **`reset_tracker.bat`** in the folder and double-click it. Type `yes` and press Enter.

---

## 🍎 Mac (How to Run)

### Setup & Launch in 4 Simple Steps:
1. **Verify Python is installed**:
   * Macs usually have Python installed. If not, download and install the Mac version from [python.org](https://www.python.org/downloads/).
2. **Give Permission to Launch** (Only needed the first time):
   * Open the **Terminal** app on your Mac (press `Cmd + Space` on your keyboard, type `Terminal`, and press Enter).
   * Type `chmod +x ` (make sure to type a space after the `x`).
   * Drag the **`run_dashboard.sh`** file from your Finder window and drop it directly into the Terminal window, then press Enter.
   * Repeat the same for the **`reset_tracker.sh`** file (type `chmod +x `, drag and drop `reset_tracker.sh`, and press Enter).
3. **Launch the Tracker**:
   * **Double-click `run_dashboard.sh`** in your Finder window.
   * A terminal window will open and configure everything automatically.
4. **Start Tracking**:
   * Your browser (Safari/Chrome) will open automatically to the dashboard at `http://localhost:8000`.
   * *Keep the Terminal window open in the background while tracking.*

### ♻️ How to Start Over (Reset everything):
* Double-click **`reset_tracker.sh`** in Finder. Type `yes` and press Enter to wipe all history and configs.

---

## 📱 iPad / Phone (How to View the Dashboard)

You cannot run the tracker setup directly on an iPad or iPhone, but you can easily view and control it from your iPad screen while it runs on your computer!

### How to Connect:
1. Make sure your computer (PC or Mac) is running the tracker dashboard.
2. Ensure both your computer and your iPad are connected to the **same Wi-Fi network**.
3. Look at the black command window on your computer. It will display a helper address, for example:
   `📡 Access from other local devices (like iPad) at: http://192.168.1.15:8000`
4. Open **Safari** or **Chrome** on your iPad.
5. Type that exact address (e.g. `http://192.168.1.15:8000`) into the web address bar at the top and press Go.
6. The dashboard will load on your iPad, allowing you to control everything from your tablet!

---

## 🔄 How to Use the Dashboard (Once Opened)

Once the dashboard page is open in your browser, here is how you use it:

### Step 1: Set Your Delivery Address (Crucial!)
Because stock levels are different for every neighborhood, the tracker must know where you live:
1. Under the **Blinkit** or **Zepto** card at the top, click the **Location Setup** button.
2. A browser window will open. Search for and select your exact delivery address on the website.
3. Once set, return to the dashboard page, and click **Save & Save State Context** on the popup.

### Step 2: Find & Add Products
1. Go to the **Search & Add Wizard** tab in the middle of the page.
2. Select your store (Blinkit or Zepto), type what you want to find (e.g., "milk" or "Hot Wheels"), and click **Search**.
3. Find the item you want to watch from the search results, and click **+ Track Item**.

### Step 3: Turn on Monitoring
1. Click the purple **Start All Trackers** button at the top right of the page.
2. The tracker is now active! It will watch your items in the background and trigger desktop notifications/Telegram alerts the moment they come back in stock.
