import json
import os
import time
import uuid
import shutil
from collections import deque
from app.config import CONFIG_FILE, STATS_FILE, HISTORY_MAX_LEN
import app.globals as g

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")
        return []

def save_config(sources):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(sources, f, indent=4)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save config: {e}")
        return False

def load_stats():
    # Try loading from main file
    files_to_try = [STATS_FILE, STATS_FILE + ".bak"]
    
    for file_path in files_to_try:
        if not os.path.exists(file_path):
            continue
            
        try:
            with open(file_path, 'r') as f:
                stats = json.load(f)
                # Convert history lists back to deque
                for src_id, data in stats.items():
                    if "history" in data:
                        data["history"] = deque(data["history"], maxlen=HISTORY_MAX_LEN)
                print(f"[INFO] Successfully loaded stats from {file_path}")
                return stats
        except Exception as e:
            print(f"[ERROR] Failed to load stats from {file_path}: {e}")
            # Continue to try backup if this was main file
            
    return {}

def save_stats():
    try:
        # Create a copy for saving, converting deque to list
        stats_to_save = {}
        for k, v in g.global_stats.items():
            stats_to_save[k] = v.copy()
            if "history" in v and isinstance(v["history"], deque):
                stats_to_save[k]["history"] = list(v["history"])
        
        # Atomic Write: Write to temp -> Move to final
        temp_file = STATS_FILE + ".tmp"
        backup_file = STATS_FILE + ".bak"
        
        # Write to temp file first
        with open(temp_file, 'w') as f:
            json.dump(stats_to_save, f, indent=4)
            
        # If write successful, backup old file then replace
        if os.path.exists(STATS_FILE):
            try:
                shutil.copy2(STATS_FILE, backup_file)
            except Exception as e:
                print(f"[WARN] Failed to create backup: {e}")
                
        shutil.move(temp_file, STATS_FILE)
        
    except Exception as e:
        print(f"[ERROR] Failed to save stats: {e}")

def sync_stats_with_config():
    valid_ids = {src["id"] for src in g.CCTV_SOURCES}
    to_remove = [k for k in g.global_stats.keys() if k not in valid_ids]
    
    if to_remove:
        print(f"[INFO] Cleaning up {len(to_remove)} zombie stats entries.")
        for k in to_remove:
            del g.global_stats[k]
        save_stats()

def calculate_window_stats(history):
    now = time.time()
    windows = {
        "10s": 10,
        "30m": 1800,
        "1h": 3600,
        "5h": 18000,
        "24h": 86400
    }
    
    results = {}
    
    hist_list = list(history)
    
    for label, seconds in windows.items():
        # Filter items within window
        cutoff = now - seconds
        relevant_items = [item for item in hist_list if item["ts"] >= cutoff]
        
        count = len(relevant_items)
        if count > 0:
            # Calculate Total Volume (Flux) - Sum of new vehicles
            # Use .get() for backward compatibility with old history data
            total_volume = sum(item.get("new_count", 0) for item in relevant_items)
            total_cars = sum(item.get("new_cars", 0) for item in relevant_items)
            total_motors = sum(item.get("new_motors", 0) for item in relevant_items)
            
            # Also calculate Average Density for reference (optional, but keeping logic)
            avg_density = round(sum(item["count"] for item in relevant_items) / count)
        else:
            total_volume = 0
            total_cars = 0
            total_motors = 0
            avg_density = 0
            
        results[label] = {
            "total_volume": total_volume,
            "cars": total_cars,
            "motors": total_motors,
            "avg_density": avg_density
        }
        
    return results

def get_history_series(history, period="30m", start_ts=None):
    now = time.time()
    
    # Custom 24h view for a specific day
    if period == "custom" and start_ts:
        try:
            # Align start_ts to 00:00 of that day
            ts_float = float(start_ts)
            t = time.localtime(ts_float)
            start_time = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
            
            duration = 86400 # 24 hours
            bucket_size = 3600 # 1 hour buckets
            time_format = "%H:%M"
            
            # Create buckets
            buckets = []
            num_buckets = 24
            for i in range(num_buckets):
                bucket_start = start_time + (i * bucket_size)
                buckets.append({
                    "ts": bucket_start,
                    "label": time.strftime(time_format, time.localtime(bucket_start)),
                    "count": 0,
                    "cars": 0,
                    "motors": 0
                })
                
            # Fill buckets
            end_time = start_time + duration
            hist_list = list(history)
            for item in hist_list:
                ts = item["ts"]
                if ts < start_time or ts >= end_time:
                    continue
                    
                idx = int((ts - start_time) / bucket_size)
                if 0 <= idx < num_buckets:
                    buckets[idx]["count"] += item.get("new_count", 0)
                    buckets[idx]["cars"] += item.get("new_cars", 0)
                    buckets[idx]["motors"] += item.get("new_motors", 0)
            
            return buckets
            
        except ValueError:
            pass # Fallback to standard logic if invalid start_ts

    # Define period duration and bucket size
    if period == "30d":
        duration = 2592000 # 30 days
        bucket_size = 86400 # 24 hour buckets (1 point per day)
        time_format = "%a, %d %b" # e.g. Mon, 25 Jan
    elif period == "7d":
        duration = 604800 # 7 days
        bucket_size = 86400 # 24 hour buckets (1 point per day)
        time_format = "%A, %d %b" # e.g. Monday, 25 Jan
    elif period == "24h":
        duration = 86400
        bucket_size = 3600 # 1 hour buckets (24 points)
        time_format = "%H:%M"
    elif period == "12h":
        duration = 43200
        bucket_size = 1800 # 30 min buckets (24 points)
        time_format = "%H:%M"
    elif period == "6h":
        duration = 21600
        bucket_size = 900 # 15 min buckets (24 points)
        time_format = "%H:%M"
    elif period == "1h":
        duration = 3600
        bucket_size = 120 # 2 min buckets (30 points)
        time_format = "%H:%M"
    elif period == "30m":
        duration = 1800
        bucket_size = 60 # 1 minute buckets (30 points)
        time_format = "%H:%M"
    else:
        # Default fallback (30m)
        duration = 1800
        bucket_size = 60
        time_format = "%H:%M"
    
    start_time = now - duration
    
    # Special handling for 24h: Align to today 00:00 - 24:00
    if period == "24h":
        t = time.localtime(now)
        start_time = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        # Ensure we cover full 24h from start of day
        duration = 86400 
    
    # Initialize buckets
    # Align start time to the nearest bucket boundary for cleaner charts
    # e.g. if bucket is 1 hour, start at XX:00:00
    # But for sliding window, we might just want "last 24h"
    
    num_buckets = int(duration / bucket_size)
    buckets = []
    for i in range(num_buckets):
        bucket_start = start_time + (i * bucket_size)
        buckets.append({
            "ts": bucket_start,
            "label": time.strftime(time_format, time.localtime(bucket_start)),
            "count": 0,
            "cars": 0,
            "motors": 0
        })
    
    # Fill buckets
    hist_list = list(history)
    for item in hist_list:
        ts = item["ts"]
        if ts < start_time:
            continue
            
        # Find bucket index
        idx = int((ts - start_time) / bucket_size)
        if 0 <= idx < num_buckets:
            buckets[idx]["count"] += item.get("new_count", 0)
            buckets[idx]["cars"] += item.get("new_cars", 0)
            buckets[idx]["motors"] += item.get("new_motors", 0)
            
    return buckets
