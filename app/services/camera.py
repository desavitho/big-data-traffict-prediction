import threading
import time
import cv2
from collections import deque
from ultralytics import YOLO

from app.config import (
    YOLO_MODEL_PATH, CONF_THRESHOLD, IOU_THRESHOLD, 
    VEHICLE_CLASSES, CLASS_MAPPING, CLASS_CAR, CLASS_MOTORCYCLE,
    PROCESS_INTERVAL, HISTORY_MAX_LEN
)
import app.globals as g
from app.utils import save_stats

class CameraAgent(threading.Thread):
    def __init__(self, source_config, model_ref):
        threading.Thread.__init__(self)
        self.source_id = source_config["id"]
        self.source_name = source_config["name"]
        self.source_url = source_config["url"]
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

    def run(self):
        print(f"[INFO] Started Agent for {self.source_name}")
        
        while self.running:
            # 1. Connect & Snapshot
            cap = cv2.VideoCapture(self.source_url)
            frame = None
            success = False
            
            if cap.isOpened():
                # Burst read to clear buffer
                for _ in range(10): 
                    ret, tmp_frame = cap.read()
                    if ret:
                        frame = tmp_frame
                        success = True
                    else:
                        time.sleep(0.05)
                cap.release()
            else:
                print(f"[WARN] {self.source_name}: Connection failed.")
            
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
                
                if results:
                    for result in results:
                        boxes = result.boxes
                        for box in boxes:
                            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                            cls_id = int(box.cls[0].cpu().numpy())
                            internal_class_id = CLASS_MAPPING.get(cls_id, CLASS_CAR)
                            rects.append((x1, y1, x2, y2))
                            rect_classes.append(internal_class_id)

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
