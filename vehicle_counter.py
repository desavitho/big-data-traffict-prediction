import cv2
import numpy as np
import tensorflow as tf
import tensorflow_hub as hub
import time
import threading
import json
from collections import OrderedDict
from flask import Flask, Response, render_template, jsonify, request

# ==========================================
# KONFIGURASI
# ==========================================

# Ganti ini dengan URL stream CCTV Anda
# Jika menggunakan file video lokal, masukkan path-nya
VIDEO_SOURCE = "https://eofficev2.bekasikota.go.id/backupcctv/m3/terminal_bekasi.m3u8"

# URL Model TensorFlow Hub (SSD MobileNet V2)
MODEL_URL = "https://tfhub.dev/tensorflow/ssd_mobilenet_v2/2"

# ID Kelas COCO Dataset
VEHICLE_CLASSES = [3, 4, 6, 8] # Car, Motorcycle, Bus, Truck

# Posisi garis penghitung (0.0 - 1.0)
LINE_POSITION = 0.5 

# Flask Server Config
HOST_IP = "0.0.0.0"
HOST_PORT = 5000

# Global variables for thread safety
outputFrame = None
lock = threading.Lock()

# Shared Stats Data
stats_data = {
    "total_count": 0,
    "class_counts": {3: 0, 4: 0, 6: 0, 8: 0},
    "fps": 0.0
}

# Initialize Flask
app = Flask(__name__)

# ==========================================
# CLASS TRACKER
# ==========================================
class CentroidTracker:
    def __init__(self, max_disappeared=50):
        self.next_object_id = 0
        self.objects = OrderedDict()
        self.disappeared = OrderedDict()
        self.max_disappeared = max_disappeared
        self.counted_ids = set()
        self.object_classes = {} # Simpan kelas untuk setiap ID

    def register(self, centroid, class_id):
        self.objects[self.next_object_id] = centroid
        self.object_classes[self.next_object_id] = class_id
        self.disappeared[self.next_object_id] = 0
        self.next_object_id += 1

    def deregister(self, object_id):
        del self.objects[object_id]
        del self.disappeared[object_id]
        if object_id in self.object_classes:
            del self.object_classes[object_id]

    def update(self, rects, classes_list):
        if len(rects) == 0:
            for object_id in list(self.disappeared.keys()):
                self.disappeared[object_id] += 1
                if self.disappeared[object_id] > self.max_disappeared:
                    self.deregister(object_id)
            return self.objects

        input_centroids = np.zeros((len(rects), 2), dtype="int")
        for (i, (start_x, start_y, end_x, end_y)) in enumerate(rects):
            c_x = int((start_x + end_x) / 2.0)
            c_y = int((start_y + end_y) / 2.0)
            input_centroids[i] = (c_x, c_y)

        if len(self.objects) == 0:
            for i in range(0, len(input_centroids)):
                self.register(input_centroids[i], classes_list[i])
        else:
            object_ids = list(self.objects.keys())
            object_centroids = list(self.objects.values())

            D = []
            for oc in object_centroids:
                row = []
                for ic in input_centroids:
                    dist = np.linalg.norm(np.array(oc) - np.array(ic))
                    row.append(dist)
                D.append(row)
            D = np.array(D)

            rows = D.min(axis=1).argsort()
            cols = D.argmin(axis=1)[rows]

            used_rows = set()
            used_cols = set()

            for (row, col) in zip(rows, cols):
                if row in used_rows or col in used_cols:
                    continue

                object_id = object_ids[row]
                self.objects[object_id] = input_centroids[col]
                self.disappeared[object_id] = 0
                
                # Update class ID if needed (optional, assuming consistency)
                # self.object_classes[object_id] = classes_list[col] 

                used_rows.add(row)
                used_cols.add(col)

            unused_rows = set(range(0, D.shape[0])).difference(used_rows)
            unused_cols = set(range(0, D.shape[1])).difference(used_cols)

            if D.shape[0] >= D.shape[1]:
                for row in unused_rows:
                    object_id = object_ids[row]
                    self.disappeared[object_id] += 1
                    if self.disappeared[object_id] > self.max_disappeared:
                        self.deregister(object_id)
            else:
                for col in unused_cols:
                    self.register(input_centroids[col], classes_list[col])

        return self.objects

# ==========================================
# PROCESSING THREAD
# ==========================================
def detect_vehicles():
    global outputFrame, lock, stats_data

    print("[INFO] Loading TensorFlow model...")
    detector = hub.load(MODEL_URL)
    print("[INFO] Model loaded successfully.")

    print(f"[INFO] Opening video source: {VIDEO_SOURCE}")
    
    current_source = VIDEO_SOURCE
    cap = cv2.VideoCapture(current_source)

    tracker = CentroidTracker(max_disappeared=40)
    fps_start_time = time.time()
    fps_frame_count = 0

    while True:
        # Check for dynamic source update
        if VIDEO_SOURCE != current_source:
            print(f"[INFO] Switching source to: {VIDEO_SOURCE}")
            
            # Update frame to "Switching..." immediately
            blank_frame = np.zeros((600, 800, 3), dtype=np.uint8)
            cv2.putText(blank_frame, "Switching Source...", (250, 300), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            cv2.putText(blank_frame, "Please wait...", (300, 350), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
            with lock:
                outputFrame = blank_frame.copy()

            current_source = VIDEO_SOURCE
            cap.release()
            tracker = CentroidTracker(max_disappeared=40)
            cap = cv2.VideoCapture(current_source)
            time.sleep(0.5) # Reduced delay
            continue

        ret, frame = cap.read()
        if not ret:
            print(f"[INFO] Stream error or end. Reconnecting in 5 seconds...")
            cap.release()
            time.sleep(5)
            cap = cv2.VideoCapture(current_source)
            continue

        # Resize for performance consistency
        frame = cv2.resize(frame, (800, 600))
        (H, W) = frame.shape[:2]

        input_tensor = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        input_tensor = tf.convert_to_tensor(input_tensor)
        input_tensor = input_tensor[tf.newaxis, ...]

        result = detector(input_tensor)
        result = {key:value.numpy() for key,value in result.items()}

        boxes = result['detection_boxes'][0]
        classes = result['detection_classes'][0].astype(int)
        scores = result['detection_scores'][0]

        rects = []
        rect_classes = []

        for i in range(len(boxes)):
            if scores[i] > 0.4: 
                if classes[i] in VEHICLE_CLASSES:
                    ymin, xmin, ymax, xmax = boxes[i]
                    start_x = int(xmin * W)
                    start_y = int(ymin * H)
                    end_x = int(xmax * W)
                    end_y = int(ymax * H)
                    rects.append((start_x, start_y, end_x, end_y))
                    rect_classes.append(classes[i])
                    
                    # Draw box
                    cv2.rectangle(frame, (start_x, start_y), (end_x, end_y), (0, 255, 0), 2)

        objects = tracker.update(rects, rect_classes)
        line_y = int(H * LINE_POSITION)
        cv2.line(frame, (0, line_y), (W, line_y), (0, 0, 255), 2)

        for (object_id, centroid) in objects.items():
            text = f"ID {object_id}"
            cv2.putText(frame, text, (centroid[0] - 10, centroid[1] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            cv2.circle(frame, (centroid[0], centroid[1]), 4, (0, 255, 0), -1)

            if object_id not in tracker.counted_ids:
                if line_y - 15 < centroid[1] < line_y + 15:
                    # Update Stats
                    stats_data["total_count"] += 1
                    tracker.counted_ids.add(object_id)
                    
                    # Update Class Count
                    obj_class = tracker.object_classes.get(object_id)
                    if obj_class in stats_data["class_counts"]:
                        stats_data["class_counts"][obj_class] += 1
                        
                    cv2.line(frame, (0, line_y), (W, line_y), (0, 255, 255), 3)

        # Calculate FPS
        fps_frame_count += 1
        if (time.time() - fps_start_time) > 1:
            stats_data["fps"] = fps_frame_count / (time.time() - fps_start_time)
            fps_frame_count = 0
            fps_start_time = time.time()

        # Update global frame
        with lock:
            outputFrame = frame.copy()

    cap.release()

# ==========================================
# FLASK ROUTES
# ==========================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/stats")
def get_stats():
    global stats_data
    return jsonify(stats_data)

def generate():
    global outputFrame, lock
    
    # Create placeholder frame
    blank_frame = np.zeros((600, 800, 3), dtype=np.uint8)
    cv2.putText(blank_frame, "Initializing / Connecting...", (200, 300), 
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    (flag, encodedImage) = cv2.imencode(".jpg", blank_frame)
    blank_bytes = bytearray(encodedImage)

    while True:
        with lock:
            if outputFrame is None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + blank_bytes + b'\r\n')
                time.sleep(0.5) # Kirim frame loading pelan-pelan
                continue
                
            (flag, encodedImage) = cv2.imencode(".jpg", outputFrame)
            if not flag:
                continue

        yield(b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + 
              bytearray(encodedImage) + b'\r\n')
        time.sleep(0.05) # Limit to ~20 FPS streaming to save bandwidth

@app.route("/api/update_source", methods=["POST"])
def update_source():
    global VIDEO_SOURCE, stats_data
    try:
        data = request.json
        new_url = data.get("url")
        if new_url:
            print(f"[INFO] Received new stream URL: {new_url}")
            VIDEO_SOURCE = new_url
            
            # Reset stats
            stats_data["total_count"] = 0
            stats_data["class_counts"] = {k: 0 for k in stats_data["class_counts"]}
            
            return jsonify({"status": "success", "message": "Stream updating..."})
        return jsonify({"status": "error", "message": "URL required"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/video_feed")
def video_feed():
    return Response(generate(),
                    mimetype = "multipart/x-mixed-replace; boundary=frame")

# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    # Start detection thread
    t = threading.Thread(target=detect_vehicles)
    t.daemon = True
    t.start()

    # Start Flask server
    print(f"[INFO] Starting web server at http://{HOST_IP}:{HOST_PORT}")
    app.run(host=HOST_IP, port=HOST_PORT, debug=False, threaded=True, use_reloader=False)
