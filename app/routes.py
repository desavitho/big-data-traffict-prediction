import time
import uuid
import io
import csv
import cv2
import numpy as np
from collections import deque
from flask import Blueprint, render_template, jsonify, request, Response, send_file

from app.config import CLASS_CAR, CLASS_MOTORCYCLE, HISTORY_MAX_LEN
import app.globals as g
from app.utils import save_config, save_stats, calculate_window_stats, get_history_series
from app.services.camera import start_camera_agents, stop_agent, CameraAgent

bp = Blueprint('main', __name__)

@bp.route("/")
def index():
    return render_template("index.html")

@bp.route("/api/stats")
def get_stats():
    # Identify active source
    active_source_id = next((s["id"] for s in g.CCTV_SOURCES if s["url"] == g.VIDEO_SOURCE), None)
    
    # Prepare Active Stats
    active_stats = {
        "accumulated_count": 0,
        "accumulated_class_counts": {str(CLASS_CAR): 0, str(CLASS_MOTORCYCLE): 0},
        "current_class_counts": {str(CLASS_CAR): 0, str(CLASS_MOTORCYCLE): 0},
        "window_stats": {}
    }
    
    if active_source_id and active_source_id in g.global_stats:
        # Copy basic stats
        src_stats = g.global_stats[active_source_id]
        active_stats = {k: v for k, v in src_stats.items() if k != "history"}
        # Calculate window stats on the fly
        if "history" in src_stats:
            active_stats["window_stats"] = calculate_window_stats(src_stats["history"])
        else:
             active_stats["window_stats"] = calculate_window_stats([])

    # Prepare Global Aggregate (Sum of all)
    total_accumulated = 0
    total_cars = 0
    total_motors = 0
    
    processed_sources = {}
    
    for key, val in g.global_stats.items():
        total_accumulated += val["accumulated_count"]
        total_cars += val["accumulated_class_counts"].get(str(CLASS_CAR), 0)
        total_motors += val["accumulated_class_counts"].get(str(CLASS_MOTORCYCLE), 0)
        
        # Prepare stats for sidebar/detail including window stats
        s_stats = {k: v for k, v in val.items() if k != "history"}
        if "history" in val:
            s_stats["window_stats"] = calculate_window_stats(val["history"])
        else:
            s_stats["window_stats"] = calculate_window_stats([])
            
        processed_sources[key] = s_stats

    # Return composite structure
    response = active_stats.copy()
    response["active_source_id"] = active_source_id
    response["global_total"] = {
        "accumulated_count": total_accumulated,
        "cars": total_cars,
        "motorcycles": total_motors
    }
    response["sources"] = processed_sources # Full detail with window stats
    
    return jsonify(response)

@bp.route("/api/history")
def get_history():
    period = request.args.get("period", "30m")
    start_ts = request.args.get("start_ts")
    
    # Identify active source
    active_source_id = next((s["id"] for s in g.CCTV_SOURCES if s["url"] == g.VIDEO_SOURCE), None)
    
    if active_source_id and active_source_id in g.global_stats:
        src_stats = g.global_stats[active_source_id]
        if "history" in src_stats:
            series = get_history_series(src_stats["history"], period, start_ts)
            return jsonify(series)
            
    return jsonify([])

@bp.route("/api/export_csv")
def export_history_csv():
    period = request.args.get("period", "30m")
    start_ts = request.args.get("start_ts")
    
    # Identify active source
    active_source = next((s for s in g.CCTV_SOURCES if s["url"] == g.VIDEO_SOURCE), None)
    active_source_id = active_source["id"] if active_source else None
    camera_name = active_source["name"].replace(" ", "_").replace('"', '') if active_source else "Camera"
    
    series = []
    if active_source_id and active_source_id in g.global_stats:
        src_stats = g.global_stats[active_source_id]
        if "history" in src_stats:
            series = get_history_series(src_stats["history"], period, start_ts)
            
    # Generate CSV
    si = io.StringIO()
    cw = csv.writer(si)
    
    # Write Metadata header (Optional, but makes it "rapih" / detailed)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    cw.writerow(["Traffic Volume Report"])
    cw.writerow(["Generated At", now_str])
    cw.writerow(["Camera Name", active_source["name"] if active_source else "Unknown"])
    cw.writerow(["Period/View", period])
    cw.writerow([]) # Empty line
    
    # Table Header
    cw.writerow(['Date', 'Time', 'Total Vehicles', 'Cars', 'Cars (%)', 'Motorcycles', 'Motorcycles (%)'])
    
    for row in series:
        # Use timestamp to generate clean date and time columns
        ts = row.get('ts')
        if ts:
            dt_struct = time.localtime(ts)
            date_str = time.strftime("%Y-%m-%d", dt_struct)
            time_str = time.strftime("%H:%M", dt_struct)
        else:
            # Fallback if ts missing (unlikely)
            date_str = "-"
            time_str = row['label']
            
        total = row['count']
        cars = row['cars']
        motors = row['motors']
        
        # Calculate percentages
        car_pct = f"{round((cars / total) * 100, 1)}%" if total > 0 else "0%"
        motor_pct = f"{round((motors / total) * 100, 1)}%" if total > 0 else "0%"
            
        cw.writerow([date_str, time_str, total, cars, car_pct, motors, motor_pct])
        
    output = io.BytesIO()
    output.write(si.getvalue().encode('utf-8'))
    output.seek(0)
    
    # Generate Clean Filename
    date_part = time.strftime("%Y-%m-%d")
    
    if start_ts:
        # Custom Daily Detail
        try:
            detail_date = time.strftime("%Y-%m-%d", time.localtime(float(start_ts)))
            filename = f"TrafficReport_{camera_name}_{detail_date}_DailyDetail.csv"
        except:
            filename = f"TrafficReport_{camera_name}_{date_part}_Detail.csv"
    else:
        # Standard Period
        filename = f"TrafficReport_{camera_name}_{date_part}_{period}.csv"
        
    return send_file(
        output,
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )

@bp.route("/api/sources")
def get_sources():
    # Update status active berdasarkan VIDEO_SOURCE saat ini
    for source in g.CCTV_SOURCES:
        source["active"] = (source["url"] == g.VIDEO_SOURCE)
    return jsonify(g.CCTV_SOURCES)

@bp.route("/api/switch_source", methods=["POST"])
def switch_source():
    try:
        data = request.json
        source_id = data.get("id")
        target_source = next((s for s in g.CCTV_SOURCES if s["id"] == source_id), None)
        
        if target_source:
            print(f"[INFO] Switching to source: {target_source['name']}")
            g.VIDEO_SOURCE = target_source["url"]
            return jsonify({"status": "success", "message": f"Switched to {target_source['name']}"})
        return jsonify({"status": "error", "message": "Source ID not found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@bp.route("/api/add_camera", methods=["POST"])
def add_camera():
    try:
        data = request.json
        name = data.get("name")
        url = data.get("url")
        username = data.get("username")
        password = data.get("password")

        # Hardcoded credentials
        if username != "admin" or password != "@dmin12345":
            return jsonify({"status": "error", "message": "Invalid credentials"}), 401
        
        if not name or not url:
            return jsonify({"status": "error", "message": "Name and URL are required"}), 400
            
        new_camera = {
            "id": str(uuid.uuid4()),
            "name": name,
            "url": url,
            "active": False
        }
        
        g.CCTV_SOURCES.append(new_camera)
        save_config(g.CCTV_SOURCES)
        
        # Start Agent for new camera
        if g.yolo_model_instance:
            agent = CameraAgent(new_camera, g.yolo_model_instance)
            g.camera_agents[new_camera["id"]] = agent
            agent.start()
        
        return jsonify({"status": "success", "message": "Camera added", "camera": new_camera})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@bp.route("/api/delete_camera", methods=["POST"])
def delete_camera():
    try:
        data = request.json
        source_id = data.get("id")
        username = data.get("username")
        password = data.get("password")

        # Hardcoded credentials
        if username != "admin" or password != "@dmin12345":
            return jsonify({"status": "error", "message": "Invalid credentials"}), 401
        
        camera_to_delete = next((s for s in g.CCTV_SOURCES if s["id"] == source_id), None)
        
        if not camera_to_delete:
            return jsonify({"status": "error", "message": "Camera not found"}), 404
            
        # Stop Agent
        stop_agent(source_id)
        
        # Remove from List
        g.CCTV_SOURCES = [s for s in g.CCTV_SOURCES if s["id"] != source_id]
        save_config(g.CCTV_SOURCES)
        
        # Remove from Stats
        if source_id in g.global_stats:
            del g.global_stats[source_id]
            save_stats()
        
        # If we deleted the active source, switch
        if camera_to_delete["url"] == g.VIDEO_SOURCE:
             if g.CCTV_SOURCES:
                 g.VIDEO_SOURCE = g.CCTV_SOURCES[0]["url"]
             else:
                 g.VIDEO_SOURCE = "" 
        
        return jsonify({"status": "success", "message": "Camera deleted"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@bp.route("/api/reset_data", methods=["POST"])
def reset_data():
    try:
        data = request.json
        username = data.get("username")
        password = data.get("password")

        # Hardcoded credentials as requested
        if username != "admin" or password != "@dmin12345":
            return jsonify({"status": "error", "message": "Invalid credentials"}), 401

        print("[INFO] Resetting all traffic data...")
        
        # Reset Global Stats
        for src_id in g.global_stats:
            g.global_stats[src_id]["current_count"] = 0
            g.global_stats[src_id]["current_class_counts"] = {str(CLASS_CAR): 0, str(CLASS_MOTORCYCLE): 0}
            g.global_stats[src_id]["accumulated_count"] = 0
            g.global_stats[src_id]["accumulated_class_counts"] = {str(CLASS_CAR): 0, str(CLASS_MOTORCYCLE): 0}
            
            # Reset History
            if "history" in g.global_stats[src_id]:
                g.global_stats[src_id]["history"].clear()
            else:
                g.global_stats[src_id]["history"] = deque(maxlen=HISTORY_MAX_LEN)
                
        # Save Empty Stats
        save_stats()
        
        return jsonify({"status": "success", "message": "All data has been erased."})
    except Exception as e:
        print(f"[ERROR] Failed to reset data: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@bp.route("/metrics")
def metrics():
    # Prometheus format
    lines = []
    lines.append("# HELP vehicle_count_total Total accumulated vehicles")
    lines.append("# TYPE vehicle_count_total counter")
    
    for src_id, stats in g.global_stats.items():
        name = stats["name"].replace('"', '')
        cars = stats["accumulated_class_counts"].get(str(CLASS_CAR), 0)
        motors = stats["accumulated_class_counts"].get(str(CLASS_MOTORCYCLE), 0)
        
        lines.append(f'vehicle_count_total{{camera="{name}", type="car"}} {cars}')
        lines.append(f'vehicle_count_total{{camera="{name}", type="motorcycle"}} {motors}')
        
    return Response("\n".join(lines), mimetype="text/plain")

@bp.route("/export/csv")
def export_csv():
    # Export stats to CSV (Detailed History)
    si = io.StringIO()
    cw = csv.writer(si)
    
    # Header: Timestamp, Camera Name, Total Count, Cars, Motorcycles
    cw.writerow(["Timestamp", "Camera Name", "Density (Total)", "Density (Cars)", "Density (Motors)", "New Passed (Total)", "New Passed (Cars)", "New Passed (Motors)"])
    
    # Iterate through all sources and their history
    for src_id, stats in g.global_stats.items():
        name = stats["name"]
        history = stats.get("history", [])
        
        # If history exists, write each data point
        if history:
            for item in history:
                # Convert timestamp to readable format
                ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item["ts"]))
                cw.writerow([
                    ts_str,
                    name,
                    item["count"],
                    item["cars"],
                    item["motors"],
                    item.get("new_count", 0),
                    item.get("new_cars", 0),
                    item.get("new_motors", 0)
                ])
        else:
            # If no history, just write current state as one row (fallback)
            cw.writerow([
                time.strftime("%Y-%m-%d %H:%M:%S"),
                name,
                stats["current_count"],
                stats["current_class_counts"].get(str(CLASS_CAR), 0),
                stats["current_class_counts"].get(str(CLASS_MOTORCYCLE), 0),
                0, 0, 0
            ])
        
    output = io.BytesIO()
    output.write(si.getvalue().encode('utf-8'))
    output.seek(0)
    
    return send_file(output, mimetype="text/csv", as_attachment=True, download_name="traffic_stats_history.csv")

def generate():
    # Create placeholder frame
    blank_frame = np.zeros((600, 800, 3), dtype=np.uint8)
    cv2.putText(blank_frame, "Initializing...", (200, 300), 
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    (flag, encodedImage) = cv2.imencode(".jpg", blank_frame)
    blank_bytes = bytearray(encodedImage)

    while True:
        with g.lock:
            if g.outputFrame is None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + blank_bytes + b'\r\n')
                time.sleep(0.5)
                continue
                
            (flag, encodedImage) = cv2.imencode(".jpg", g.outputFrame)
            if not flag:
                continue

        yield(b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + 
              bytearray(encodedImage) + b'\r\n')
        time.sleep(0.05)

@bp.route("/video_feed")
def video_feed():
    return Response(generate(),
                    mimetype = "multipart/x-mixed-replace; boundary=frame")
