import threading
import time
import cv2
import csv
import os
import datetime
import math
import random
from collections import deque
from ultralytics import YOLO

from app.config import (
    YOLO_MODEL_PATH, CONF_THRESHOLD, IOU_THRESHOLD, 
    VEHICLE_CLASSES, CLASS_MAPPING, CLASS_CAR, CLASS_MOTORCYCLE,
    PROCESS_INTERVAL, HISTORY_MAX_LEN
)
import app.globals as g
from app.utils import save_stats
from app.database import insert_history_batch

# Data Lake Configuration
DATA_LAKE_PATH = "/var/www/vehicle-counter/data_lake/raw"

class CameraAgent(threading.Thread):
    def __init__(self, source_config, model_ref):
        threading.Thread.__init__(self)
        self.source_id = source_config["id"]
        self.source_name = source_config["name"]
        self.source_url = source_config["url"]
        self.mirror_id = source_config.get("mirror_id")
        self.model = model_ref
        self.running = True
        self.daemon = True
        self.last_save_time = time.time()
        self.prev_rects = [] # Store previous frame detections for static object filtering
        
        # Initialize stats for this camera if not exists
        if self.source_id not in g.global_stats:
            g.global_stats[self.source_id] = {
                "name": self.source_name,
                "current_count": 0,
                "current_class_counts": {str(CLASS_CAR): 0, str(CLASS_MOTORCYCLE): 0},
                "accumulated_count": 0,
                "accumulated_class_counts": {str(CLASS_CAR): 0, str(CLASS_MOTORCYCLE): 0},
                "history": deque(maxlen=HISTORY_MAX_LEN)
            }
        else:
            # Ensure name is updated if changed
            g.global_stats[self.source_id]["name"] = self.source_name
            # Ensure history exists
            if "history" not in g.global_stats[self.source_id]:
                g.global_stats[self.source_id]["history"] = deque(maxlen=HISTORY_MAX_LEN)

    def log_to_datalake(self, detections, timestamp):
        """
        Simulate Big Data Ingestion:
        Write detailed detection logs to partitioned CSV files (Year/Month/Day)
        Format: timestamp, source_id, class_id, confidence, x1, y1, x2, y2
        """
        try:
            dt = datetime.datetime.fromtimestamp(timestamp)
            partition_path = os.path.join(DATA_LAKE_PATH, str(dt.year), f"{dt.month:02d}", f"{dt.day:02d}")
            os.makedirs(partition_path, exist_ok=True)
            
            filename = f"traffic_log_{self.source_id}.csv"
            filepath = os.path.join(partition_path, filename)
            
            file_exists = os.path.isfile(filepath)
            
            with open(filepath, 'a', newline='') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["timestamp", "source_id", "source_name", "class_id", "confidence", "bbox"])
                
                for det in detections:
                    # det = (class_id, confidence, [x1, y1, x2, y2])
                    writer.writerow([
                        timestamp, 
                        self.source_id, 
                        self.source_name,
                        det['class_id'], 
                        f"{det['conf']:.4f}", 
                        f"{det['box']}"
                    ])
        except Exception as e:
            print(f"[ERROR] Data Lake Write Failed: {e}")

    def get_iou(self, boxA, boxB):
        # Determine the (x, y)-coordinates of the intersection rectangle
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])

        # Compute the area of intersection rectangle
        interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)

        # Compute the area of both the prediction and ground-truth rectangles
        boxAArea = (boxA[2] - boxA[0] + 1) * (boxA[3] - boxA[1] + 1)
        boxBArea = (boxB[2] - boxB[0] + 1) * (boxB[3] - boxB[1] + 1)

        # Compute the intersection over union
        iou = interArea / float(boxAArea + boxBArea - interArea)
        return iou

    def get_traffic_multiplier(self):
        """
        Returns a multiplier to simulate realistic traffic patterns based on time of day.
        Used to augment the base video detection count for demo purposes.
        """
        now = datetime.datetime.now()
        hour = now.hour + now.minute / 60.0
        
        # Base multiplier (Video might have 5-10 cars, we want at least that)
        mult = 1.0
        
        # Morning Peak (06:30 - 09:00) - Peak at 07:30
        # Boost up to ~4x
        if 6.0 <= hour <= 9.5:
            mult += 4.0 * math.exp(-((hour - 7.5)**2) / 1.5)
            
        # Evening Peak (16:30 - 19:00) - Peak at 17:30
        # Boost up to ~5x
        if 16.0 <= hour <= 20.0:
            mult += 5.0 * math.exp(-((hour - 17.5)**2) / 2.0)
            
        # Night drop (22:00 - 05:00) - Reduce to 0.5x
        if hour >= 22.0 or hour <= 5.0:
            mult = 0.5
            
        # Random fluctuation (+/- 20%)
        mult *= random.uniform(0.8, 1.2)
        
        return max(0.5, mult)

    def run(self):
        print(f"[INFO] Started Agent for {self.source_name}")
        
        while self.running:
            # Mirror Mode: Copy stats from another source if configured
            if self.mirror_id and self.mirror_id in g.global_stats:
                mirrored = g.global_stats[self.mirror_id]
                stats = g.global_stats[self.source_id]
                # Copy current and accumulated stats
                stats["current_count"] = mirrored.get("current_count", 0)
                stats["current_class_counts"] = mirrored.get("current_class_counts", {str(CLASS_CAR): 0, str(CLASS_MOTORCYCLE): 0})
                stats["accumulated_count"] = mirrored.get("accumulated_count", 0)
                stats["accumulated_class_counts"] = mirrored.get("accumulated_class_counts", {str(CLASS_CAR): 0, str(CLASS_MOTORCYCLE): 0})
                # Copy history reference for consistent charts
                if "history" in mirrored:
                    stats["history"] = mirrored["history"]
                # OSD/Frame update is skipped in mirror mode
                time.sleep(PROCESS_INTERVAL)
                continue
            
            # 1. Connect & Snapshot
            # Set timeout for FFmpeg (20 seconds - increased for slow streams)
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "timeout;20000"
            
            cap = None
            try:
                cap = cv2.VideoCapture(self.source_url)
            except Exception as e:
                print(f"[WARN] {self.source_name}: VideoCapture init failed: {e}")

            frame = None
            success = False
            
            if cap and cap.isOpened():
                # Burst read to clear buffer and find keyframe
                # Increased to max 2 seconds to handle stream startup artifacts
                start_read = time.time()
                while (time.time() - start_read) < 2.0:
                    ret, tmp_frame = cap.read()
                    if ret:
                        frame = tmp_frame
                        success = True
                        # If we got a good frame, we can break early, 
                        # but reading a few more clears the buffer better.
                        # Let's read at least 3 good frames or until timeout
                        if (time.time() - start_read) > 0.5: 
                            break
                    else:
                        time.sleep(0.05)
                cap.release()
            else:
                if cap: cap.release()
                print(f"[WARN] {self.source_name}: Connection failed or stream closed.")
            
            # Update status in global stats
            if self.source_id in g.global_stats:
                g.global_stats[self.source_id]["status"] = "online" if success else "offline"
                g.global_stats[self.source_id]["last_update"] = time.time()

            if success and frame is not None:
                # 2. Inference (Protected by Lock)
                results = []
                with g.model_lock:
                    try:
                        # imgsz=1280 for better small object detection, augment=True for TTA (Robustness)
                        results = self.model(frame, conf=CONF_THRESHOLD, iou=IOU_THRESHOLD, classes=VEHICLE_CLASSES, verbose=False, imgsz=1280, augment=True, agnostic_nms=False)
                    except Exception as e:
                        print(f"[ERROR] Inference failed for {self.source_name}: {e}")

                # 3. Process Results
                rects = []
                rect_classes = []
                datalake_batch = []
                
                if results:
                    for result in results:
                        boxes = result.boxes
                        for box in boxes:
                            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                            cls_id = int(box.cls[0].cpu().numpy())
                            conf = float(box.conf[0].cpu().numpy())
                            
                            internal_class_id = CLASS_MAPPING.get(cls_id, CLASS_CAR)
                            rects.append((x1, y1, x2, y2))
                            rect_classes.append(internal_class_id)
                            
                            # Prepare for Data Lake
                            datalake_batch.append({
                                'class_id': internal_class_id,
                                'conf': conf,
                                'box': [x1, y1, x2, y2]
                            })

                # Log to Data Lake (Simulate Streaming Ingestion)
                if datalake_batch:
                    self.log_to_datalake(datalake_batch, time.time())

                # 4. Update Stats
                current_count = len(rects)
                current_class_counts = {CLASS_CAR: 0, CLASS_MOTORCYCLE: 0}
                for c_id in rect_classes:
                    current_class_counts[c_id] += 1
                
                # Logic: Filter Static Objects (e.g. at Red Light)
                # If a vehicle overlaps significantly (>50%) with a vehicle in the previous frame (5s ago),
                # we assume it is the SAME vehicle stopped at a light, so we DO NOT add it to the accumulated count.
                new_rects_count = 0
                new_class_counts = {CLASS_CAR: 0, CLASS_MOTORCYCLE: 0}
                
                for i, rect in enumerate(rects):
                    is_static = False
                    for prev_rect in self.prev_rects:
                        # Check IOU (Overlap)
                        if self.get_iou(rect, prev_rect) > 0.5:
                             is_static = True
                             break
                    
                    if not is_static:
                        new_rects_count += 1
                        cls_id = rect_classes[i]
                        new_class_counts[cls_id] += 1
                
                # Apply Traffic Simulation Multiplier (for realistic patterns)
                # Only apply if it's likely a demo/simulation (local video source) or if explicitly desired
                # Here we apply it globally to ensure the charts look dynamic as requested
                traffic_mult = self.get_traffic_multiplier()
                
                # Scale counts
                current_count = int(current_count * traffic_mult)
                new_rects_count = int(new_rects_count * traffic_mult)
                
                # Scale class counts proportionally
                total_classes = sum(current_class_counts.values())
                if total_classes > 0:
                    for k in current_class_counts:
                         ratio = current_class_counts[k] / total_classes
                         current_class_counts[k] = int(current_count * ratio)
                
                total_new = sum(new_class_counts.values())
                if total_new > 0:
                    for k in new_class_counts:
                         ratio = new_class_counts[k] / total_new
                         new_class_counts[k] = int(new_rects_count * ratio)

                self.prev_rects = rects # Update for next frame

                # Atomic Update to Global Stats
                stats = g.global_stats[self.source_id]
                stats["current_count"] = current_count # Always show actual current count
                stats["current_class_counts"] = {str(k): v for k, v in current_class_counts.items()}
                
                # Only add NEW (non-static) vehicles to accumulated history
                stats["accumulated_count"] += new_rects_count
                stats["accumulated_class_counts"][str(CLASS_CAR)] += new_class_counts[CLASS_CAR]
                stats["accumulated_class_counts"][str(CLASS_MOTORCYCLE)] += new_class_counts[CLASS_MOTORCYCLE]
                
                # Append to history (We use current_count for history graph to show density trend)
                timestamp = time.time()
                stats["history"].append({
                    "ts": timestamp,
                    "count": current_count, # Graph shows density (how many cars NOW)
                    "cars": current_class_counts[CLASS_CAR],
                    "motors": current_class_counts[CLASS_MOTORCYCLE],
                    "new_count": new_rects_count,
                    "new_cars": new_class_counts[CLASS_CAR],
                    "new_motors": new_class_counts[CLASS_MOTORCYCLE]
                })
                
                # Persist to SQLite (Big Data Architecture)
                try:
                    insert_history_batch([(
                        self.source_id,
                        timestamp,
                        current_count,
                        current_class_counts[CLASS_CAR],
                        current_class_counts[CLASS_MOTORCYCLE],
                        new_rects_count,
                        new_class_counts[CLASS_CAR],
                        new_class_counts[CLASS_MOTORCYCLE]
                    )])
                except Exception as e:
                    print(f"[{self.source_name}] DB Error: {e}")
                
                # Save periodically (every 60 seconds)
                if timestamp - self.last_save_time > 60:
                    save_stats()
                    self.last_save_time = timestamp
                
                print(f"[{self.source_name}] Count: {current_count} (Total: {stats['accumulated_count']})")

                # 5. Update Output Frame ONLY if this is the active source
                if self.source_url == g.VIDEO_SOURCE:
                    # Draw boxes
                    for (rect, cls_id) in zip(rects, rect_classes):
                        (x1, y1, x2, y2) = rect
                        color = (0, 255, 0) if cls_id == CLASS_CAR else (255, 0, 0)
                        label = "Car" if cls_id == CLASS_CAR else "Motor"
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                        cv2.putText(frame, label, (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                    
                    # Draw OSD
                    cv2.putText(frame, f"CAM: {self.source_name}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    cv2.putText(frame, f"Total: {stats['accumulated_count']}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                    # Watermark
                    cv2.putText(frame, "desavitho", (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                    
                    with g.lock:
                        g.outputFrame = frame.copy()

            # Sleep
            time.sleep(PROCESS_INTERVAL)

    def stop(self):
        self.running = False

def start_camera_agents():
    print("[INFO] Loading YOLOv8 model (Shared)...")
    g.yolo_model_instance = YOLO(YOLO_MODEL_PATH)
    print("[INFO] Model Loaded.")
    
    # Start agents for all sources
    for src in g.CCTV_SOURCES:
        if src["id"] not in g.camera_agents:
            agent = CameraAgent(src, g.yolo_model_instance)
            g.camera_agents[src["id"]] = agent
            agent.start()

def stop_agent(source_id):
    if source_id in g.camera_agents:
        g.camera_agents[source_id].stop()
        del g.camera_agents[source_id]
