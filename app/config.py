import os

# Base Directories
# Moved inside app/, so go up two levels to reach root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
MODELS_DIR = os.path.join(BASE_DIR, 'models')

# Files
CONFIG_FILE = os.path.join(DATA_DIR, "cctv_config.json")
STATS_FILE = os.path.join(DATA_DIR, "traffic_stats.json")
YOLO_MODEL_PATH = os.path.join(MODELS_DIR, "yolov8l.pt")

# Server
HOST_IP = "0.0.0.0"
HOST_PORT = 5000

# YOLO & Detection Config
CONF_THRESHOLD = 0.15      # Slightly higher to reduce ghost detections with TTA
IOU_THRESHOLD = 0.8        # Higher to allow overlapping objects (e.g. piled up motorcycles)
PROCESS_INTERVAL = 5
# Increase history length to support up to ~30 days
# 30 days * 24h * 60m * 12 (5s intervals) = 518,400 points
HISTORY_MAX_LEN = 600000

# Vehicle Classes
VEHICLE_CLASSES = [1, 2, 3, 5, 7]
CLASS_CAR = 0
CLASS_MOTORCYCLE = 1
CLASS_MAPPING = {
    1: CLASS_MOTORCYCLE, # Bicycle -> Motorcycle
    2: CLASS_CAR,        # Car -> Car
    3: CLASS_MOTORCYCLE, # Motorcycle -> Motorcycle
    5: CLASS_CAR,        # Bus -> Car
    7: CLASS_CAR         # Truck -> Car
}
