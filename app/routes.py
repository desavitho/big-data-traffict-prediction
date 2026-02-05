import time
import datetime
import uuid
import io
import csv
import cv2
import numpy as np
from collections import deque
from flask import Blueprint, render_template, jsonify, request, Response, send_file

from app.config import CLASS_CAR, CLASS_MOTORCYCLE, HISTORY_MAX_LEN
import app.globals as g
from app.utils import save_config, save_stats, calculate_window_stats, get_history_series, get_datalake_stats, backfill_camera_history, generate_varied_history
from app.database import get_camera_history, predict_future_traffic
from app.services.camera import start_camera_agents, stop_agent, CameraAgent

bp = Blueprint('main', __name__)

@bp.route("/")
def index():
    return render_template("index.html")

@bp.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@bp.route("/documentation")
def documentation():
    return render_template("documentation.html")

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
    start_ts_arg = request.args.get("start_ts")
    
    # Identify active source
    active_source_id = next((s["id"] for s in g.CCTV_SOURCES if s["url"] == g.VIDEO_SOURCE), None)
    
    if active_source_id:
        # Determine query range based on period to optimize DB fetch
        query_start = None
        now = time.time()
        
        if start_ts_arg:
             query_start = float(start_ts_arg)
        elif period == "30m":
             query_start = now - 1800
        elif period == "1h":
             query_start = now - 3600
        elif period == "5h":
             query_start = now - 18000
        elif period == "24h":
             query_start = now - 86400
        
        # Fetch from DB (High Performance)
        history = get_camera_history(active_source_id, start_ts=query_start)
        
        # Process into series buckets
        series = get_history_series(history, period, start_ts_arg)
        return jsonify(series)
            
    return jsonify([])

@bp.route("/api/datalake/stats")
def datalake_stats():
    """
    API Endpoint to fetch aggregated stats directly from Data Lake CSVs
    Query Param: date (YYYY-MM-DD) - defaults to today
    """
    date_str = request.args.get("date")
    stats = get_datalake_stats(date_str)
    return jsonify(stats)

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

@bp.route("/api/verify_admin", methods=["POST"])
def verify_admin():
    try:
        data = request.json
        username = data.get("username")
        password = data.get("password")
        
        if username == "admin" and password == "@dmin12345":
            return jsonify({"status": "success", "message": "Credentials valid"})
        else:
            return jsonify({"status": "error", "message": "Invalid credentials"}), 401
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@bp.route("/api/add_camera", methods=["POST"])
def add_camera():
    try:
        data = request.json
        name = data.get("name")
        url = data.get("url")
        lat = data.get("lat")
        lng = data.get("lng")
        mirror_id = data.get("mirror_id")
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
            "lat": lat,
            "lng": lng,
            "active": False,
            "mirror_id": mirror_id
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

@bp.route("/api/edit_camera", methods=["POST"])
def edit_camera():
    try:
        data = request.json
        source_id = data.get("id")
        name = data.get("name")
        url = data.get("url")
        lat = data.get("lat")
        lng = data.get("lng")
        mirror_id = data.get("mirror_id")
        username = data.get("username")
        password = data.get("password")

        # Hardcoded credentials
        if username != "admin" or password != "@dmin12345":
            return jsonify({"status": "error", "message": "Invalid credentials"}), 401
        
        target_camera = next((s for s in g.CCTV_SOURCES if s["id"] == source_id), None)
        
        if not target_camera:
            return jsonify({"status": "error", "message": "Camera not found"}), 404

        # Update fields
        old_url = target_camera["url"]
        if name: target_camera["name"] = name
        if url: target_camera["url"] = url
        target_camera["lat"] = lat # Allow None/Empty
        target_camera["lng"] = lng
        target_camera["mirror_id"] = mirror_id
        
        save_config(g.CCTV_SOURCES)
        
        # If URL changed, restart agent
        if url and url != old_url:
            stop_agent(source_id)
            if g.yolo_model_instance:
                agent = CameraAgent(target_camera, g.yolo_model_instance)
                g.camera_agents[source_id] = agent
                agent.start()
        elif name:
            # If only name changed, update agent name
            if source_id in g.camera_agents:
                g.camera_agents[source_id].source_name = name
        
        return jsonify({"status": "success", "message": "Camera updated"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@bp.route("/api/backfill_camera", methods=["POST"])
def backfill_camera():
    try:
        data = request.json
        new_id = data.get("id")
        template_id = data.get("template_id")
        hours = data.get("hours", 24)
        start_date = data.get("start_date")
        generate_datalake = data.get("generate_datalake", False)
        username = data.get("username")
        password = data.get("password")
        if username != "admin" or password != "@dmin12345":
            return jsonify({"status": "error", "message": "Invalid credentials"}), 401
        if not new_id or not template_id:
            return jsonify({"status": "error", "message": "id and template_id are required"}), 400
        
        # STOP AGENT to prevent race condition
        was_running = False
        if new_id in g.camera_agents:
            stop_agent(new_id)
            was_running = True
            
        result = backfill_camera_history(new_id, template_id, hours, generate_datalake, start_date)
        
        # RESTART AGENT
        if was_running:
            target_camera = next((s for s in g.CCTV_SOURCES if s["id"] == new_id), None)
            if target_camera and g.yolo_model_instance:
                agent = CameraAgent(target_camera, g.yolo_model_instance)
                g.camera_agents[new_id] = agent
                agent.start()
                
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@bp.route("/api/backfill_many", methods=["POST"])
def backfill_many():
    try:
        data = request.json
        template_id = data.get("template_id")
        hours = data.get("hours", 24)
        start_date = data.get("start_date")
        generate_datalake = data.get("generate_datalake", False)
        ids = data.get("ids") or []
        names = data.get("names") or []
        username = data.get("username")
        password = data.get("password")
        
        if username != "admin" or password != "@dmin12345":
            return jsonify({"status": "error", "message": "Invalid credentials"}), 401
        if not template_id:
            return jsonify({"status": "error", "message": "template_id is required"}), 400
        
        # Normalize name list for matching
        def norm(s):
            return (s or "").replace("–", "-").strip().lower()
        
        target_ids = set(ids)
        if names:
            name_set = set(norm(n) for n in names)
            for s in g.CCTV_SOURCES:
                if norm(s.get("name")) in name_set:
                    target_ids.add(s["id"])
        
        if not target_ids:
            return jsonify({"status": "error", "message": "No valid target cameras found"}), 404
        
        results = []
        success = 0
        for tid in target_ids:
            # STOP AGENT
            was_running = False
            if tid in g.camera_agents:
                stop_agent(tid)
                was_running = True

            res = backfill_camera_history(tid, template_id, hours, generate_datalake, start_date)
            
            # RESTART AGENT
            if was_running:
                target_camera = next((s for s in g.CCTV_SOURCES if s["id"] == tid), None)
                if target_camera and g.yolo_model_instance:
                    agent = CameraAgent(target_camera, g.yolo_model_instance)
                    g.camera_agents[tid] = agent
                    agent.start()

            status = res.get("status")
            name = next((x["name"] for x in g.CCTV_SOURCES if x["id"] == tid), tid)
            results.append({"id": tid, "name": name, "status": status})
            if status == "success":
                success += 1
        
        return jsonify({
            "status": "success",
            "template_id": template_id,
            "processed": len(target_ids),
            "success": success,
            "results": results
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@bp.route("/api/generate_history", methods=["POST"])
def generate_history():
    try:
        # Generate 7 days history to support prediction for any day of week
        res = generate_varied_history(hours=168)
        return jsonify(res)
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
        
        return jsonify({"status": "success", "message": "All traffic data has been reset."})
    except Exception as e:
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

@bp.route("/api/predict_traffic", methods=["POST"])
def predict_traffic():
    try:
        data = request.json
        # camera_id optional. If not provided, predict for ALL cameras.
        camera_id = data.get("camera_id") 
        target_time_str = data.get("target_time") # YYYY-MM-DD HH:MM
        
        if not target_time_str:
            return jsonify({"status": "error", "message": "target_time is required"}), 400
            
        # Parse Time
        try:
            dt = datetime.datetime.strptime(target_time_str, "%Y-%m-%d %H:%M")
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid date format. Use YYYY-MM-DD HH:MM"}), 400
            
        # SQLite: 0=Sunday, 1=Monday... 6=Saturday
        iso_dow = dt.isoweekday()
        sqlite_dow = 0 if iso_dow == 7 else iso_dow
        hour = dt.hour
        
        predictions = []
        
        # Determine targets
        targets = []
        if camera_id:
            source = next((s for s in g.CCTV_SOURCES if s["id"] == camera_id), None)
            if source:
                targets.append(source)
        else:
            targets = g.CCTV_SOURCES
            
        if not targets:
             return jsonify({"status": "error", "message": "No cameras found"}), 404

        for source in targets:
            avg_vehicles_per_hour = predict_future_traffic(source["id"], sqlite_dow, hour)
            
            # Determine Status & Decision
            status = "LANCAR"
            color = "text-green-500"
            recommendation = "Traffic is flowing smoothly. No action required."
            action_icon = "fas fa-check-circle"
            
            if avg_vehicles_per_hour > 1000:
                status = "MACET PARAH"
                color = "text-red-600"
                recommendation = "CRITICAL: Deploy traffic officers immediately. Divert incoming traffic to alternative routes."
                action_icon = "fas fa-exclamation-triangle"
            elif avg_vehicles_per_hour > 500:
                status = "RAMAI"
                color = "text-red-400"
                recommendation = "High volume detected. Increase green light duration and monitor for potential gridlocks."
                action_icon = "fas fa-traffic-light"
            elif avg_vehicles_per_hour > 200:
                status = "SEDANG"
                color = "text-yellow-400"
                recommendation = "Moderate traffic. Standby for potential increase."
                action_icon = "fas fa-eye"
                
            predictions.append({
                "camera_name": source.get("name", "Unknown"),
                "camera_id": source["id"],
                "vehicle_count": round(avg_vehicles_per_hour),
                "traffic_status": status,
                "status_color": color,
                "recommendation": recommendation,
                "action_icon": action_icon
            })
            
        return jsonify({
            "status": "success",
            "timestamp": target_time_str,
            "predictions": predictions
        })
        
    except Exception as e:
        print(f"Prediction API Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
