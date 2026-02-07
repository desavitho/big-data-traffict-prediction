import sqlite3
import os
import time
from app.config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "traffic_data.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Create table for traffic history
    # Using specific types for efficiency
    c.execute('''
        CREATE TABLE IF NOT EXISTS traffic_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            total_count INTEGER DEFAULT 0,
            car_count INTEGER DEFAULT 0,
            motorcycle_count INTEGER DEFAULT 0,
            new_count INTEGER DEFAULT 0,
            new_cars INTEGER DEFAULT 0,
            new_motors INTEGER DEFAULT 0
        )
    ''')
    
    # Create index for fast time-range queries
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_camera_timestamp 
        ON traffic_history (camera_id, timestamp)
    ''')
    
    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")

def insert_history_batch(records):
    """
    Batch insert records.
    records: list of tuples (camera_id, timestamp, total, cars, motors)
    """
    if not records:
        return
        
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.executemany('''
            INSERT INTO traffic_history (camera_id, timestamp, total_count, car_count, motorcycle_count, new_count, new_cars, new_motors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)
        conn.commit()
    except Exception as e:
        print(f"Error inserting batch: {e}")
    finally:
        conn.close()

def clear_all_history():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM traffic_history")
        conn.commit()
    except Exception as e:
        print(f"Error clearing history: {e}")
    finally:
        conn.close()

def get_camera_history(camera_id, start_ts=None, end_ts=None):
    conn = get_db_connection()
    c = conn.cursor()
    
    query = "SELECT timestamp, total_count, car_count, motorcycle_count, new_count, new_cars, new_motors FROM traffic_history WHERE camera_id = ?"
    params = [camera_id]
    
    if start_ts:
        query += " AND timestamp >= ?"
        params.append(start_ts)
        
    if end_ts:
        query += " AND timestamp <= ?"
        params.append(end_ts)
        
    query += " ORDER BY timestamp ASC"
    
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    
    # Convert to list of dicts to match existing API format
    return [
        {
            "ts": row["timestamp"],
            "count": row["total_count"],
            "cars": row["car_count"],
            "motors": row["motorcycle_count"],
            "new_count": row["new_count"],
            "new_cars": row["new_cars"],
            "new_motors": row["new_motors"]
        }
        for row in rows
    ]

def predict_future_traffic(camera_id, day_of_week, hour_of_day):
    """
    Predict traffic volume for a specific day of week and hour.
    day_of_week: 0 (Sunday) to 6 (Saturday) - SQLite format
    hour_of_day: 0-23
    Returns: Average vehicles per hour
    """
    conn = get_db_connection()
    c = conn.cursor()
    
    # Calculate average hourly volume for this specific time slot across all historical data
    query = '''
        WITH HourlySums AS (
            SELECT 
                date(timestamp, 'unixepoch', 'localtime') as date_str,
                SUM(new_count) as hourly_total
            FROM traffic_history
            WHERE camera_id = ?
              AND cast(strftime('%w', datetime(timestamp, 'unixepoch', 'localtime')) as int) = ?
              AND cast(strftime('%H', datetime(timestamp, 'unixepoch', 'localtime')) as int) = ?
            GROUP BY date_str
        )
        SELECT AVG(hourly_total) as avg_hourly_traffic
        FROM HourlySums
    '''
    
    try:
        c.execute(query, (camera_id, day_of_week, hour_of_day))
        result = c.fetchone()
        avg_traffic = result['avg_hourly_traffic'] if result and result['avg_hourly_traffic'] is not None else 0
    except Exception as e:
        print(f"Prediction Error: {e}")
        avg_traffic = 0
    finally:
        conn.close()
    
    return avg_traffic

def get_total_lifetime():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT 
                COALESCE(SUM(new_count), 0) as total,
                COALESCE(SUM(new_cars), 0) as cars,
                COALESCE(SUM(new_motors), 0) as motors
            FROM traffic_history
        """)
        row = c.fetchone()
        return {
            "accumulated_count": row["total"] if row and "total" in row.keys() else 0,
            "cars": row["cars"] if row and "cars" in row.keys() else 0,
            "motorcycles": row["motors"] if row and "motors" in row.keys() else 0,
        }
    except Exception:
        return {"accumulated_count": 0, "cars": 0, "motorcycles": 0}
    finally:
        conn.close()

def get_aggregated_stats(days=30):
    """
    Get aggregated stats for the last N days.
    """
    conn = get_db_connection()
    c = conn.cursor()
    try:
        cutoff = time.time() - (days * 24 * 3600)
        c.execute("""
            SELECT 
                COALESCE(SUM(new_count), 0) as total,
                COALESCE(SUM(new_cars), 0) as cars,
                COALESCE(SUM(new_motors), 0) as motors
            FROM traffic_history
            WHERE timestamp >= ?
        """, (cutoff,))
        row = c.fetchone()
        return {
            "accumulated_count": row["total"] if row else 0,
            "cars": row["cars"] if row else 0,
            "motorcycles": row["motors"] if row else 0,
        }
    except Exception as e:
        print(f"Error getting aggregated stats: {e}")
        return {"accumulated_count": 0, "cars": 0, "motorcycles": 0}
    finally:
        conn.close()

def get_history_range(camera_id=None, start_ts=None, end_ts=None):
    """
    Fetch history rows across cameras within optional time range.
    Returns list of dicts including camera_id.
    """
    conn = get_db_connection()
    c = conn.cursor()
    try:
        conditions = []
        params = []
        if camera_id:
            conditions.append("camera_id = ?")
            params.append(camera_id)
        if start_ts:
            conditions.append("timestamp >= ?")
            params.append(start_ts)
        if end_ts:
            conditions.append("timestamp <= ?")
            params.append(end_ts)
        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"""
            SELECT camera_id, timestamp, total_count, car_count, motorcycle_count,
                   new_count, new_cars, new_motors
            FROM traffic_history
            {where_clause}
            ORDER BY camera_id, timestamp ASC
        """
        c.execute(query, params)
        rows = c.fetchall()
        return [
            {
                "camera_id": row["camera_id"],
                "ts": row["timestamp"],
                "count": row["total_count"],
                "cars": row["car_count"],
                "motors": row["motorcycle_count"],
                "new_count": row["new_count"],
                "new_cars": row["new_cars"],
                "new_motors": row["new_motors"],
            }
            for row in rows
        ]
    except Exception:
        return []
    finally:
        conn.close()
