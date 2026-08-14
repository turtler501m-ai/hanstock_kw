import json
import os
import sys

# Ensure project root is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.db.repository import save_scheduler_result

def main():
    json_path = ".runtime/daily_auto_last_result.json"
    if not os.path.exists(json_path):
        print(f"Error: {json_path} does not exist.")
        return

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        mode = data.get("mode", "daily_auto")
        recorded_at = data.get("recorded_at")
        result = data.get("result")

        if not recorded_at or not result:
            print("Error: Invalid JSON format (missing recorded_at or result).")
            return

        save_scheduler_result(mode, recorded_at, result)
        print(f"Successfully backfilled DB with run from {recorded_at}!")
    except Exception as e:
        print(f"Failed to backfill database: {e}")

if __name__ == "__main__":
    main()
