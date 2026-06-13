import os
import shutil
import sqlite3
import json

def reset_all():
    print("========================================================")
    print("          Blinkit & Zepto Stock Tracker Reset           ")
    print("========================================================\n")
    print("This will:")
    print("1. Delete the SQLite database (tracker.db - deletes history)")
    print("2. Delete stored location states (state.json & zepto_state.json)")
    print("3. Clear tracked products & keywords from config.json & zepto_config.json")
    print("4. Clear all saved product screenshots")
    print("\nWARNING: This action cannot be undone.\n")
    
    confirm = input("Type 'yes' to confirm and start fresh: ").strip().lower()
    if confirm != 'yes':
        print("\nReset cancelled.")
        return
        
    print("\nStarting clean reset...")

    # 1. Delete database file
    db_file = "tracker.db"
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
            print(f"✔️ Deleted {db_file} (Cleared stock history)")
        except Exception as e:
            print(f"❌ Could not delete {db_file} (might be locked): {e}")
            
    # 2. Delete state files (cookies/geolocation)
    state_files = ["state.json", "zepto_state.json"]
    for sf in state_files:
        if os.path.exists(sf):
            try:
                os.remove(sf)
                print(f"✔️ Deleted {sf} (Cleared location session context)")
            except Exception as e:
                print(f"❌ Could not delete {sf}: {e}")

    # 3. Reset configurations
    default_config = {
        "check_interval_minutes": 10,
        "desktop_notifications": True,
        "telegram": {
            "enabled": False,
            "bot_token": "YOUR_BOT_TOKEN",
            "chat_id": "YOUR_CHAT_ID"
        },
        "tracked_keywords": [],
        "products": []
    }

    config_files = ["config.json", "zepto_config.json"]
    for cf in config_files:
        try:
            with open(cf, "w") as f:
                json.dump(default_config, f, indent=2)
            print(f"✔️ Reset {cf} to default templates")
        except Exception as e:
            print(f"❌ Could not reset {cf}: {e}")

    # 4. Clean screenshots directory
    screenshots_dir = "screenshots"
    if os.path.exists(screenshots_dir):
        try:
            shutil.rmtree(screenshots_dir)
            os.makedirs(screenshots_dir)
            print("✔️ Cleared screenshots/ directory")
        except Exception as e:
            print(f"❌ Could not clear screenshots directory: {e}")

    print("\n✨ Clean reset complete! You can now run run_dashboard.bat to set up location and start fresh.")

if __name__ == "__main__":
    reset_all()
