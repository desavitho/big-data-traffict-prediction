import csv
import random
from datetime import datetime, timedelta

ROWS = 500

start_times = {
    "Tol Jakarta-Cikampek": datetime(1970, 1, 1, 7, 0, 0),
    "Tol Tangerang-Merak": datetime(1970, 1, 1, 6, 0, 0),
    "Tol Kunciran-Serpong": datetime(1970, 1, 1, 9, 0, 0),
}

current = start_times.copy()

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_FILE = os.path.join(BASE_DIR, "data", "data_tol_500.csv")

with open(OUTPUT_FILE, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(start_times.keys())

    for _ in range(ROWS):
        row = []
        for tol in start_times:
            row.append(current[tol].strftime("%d/%m/%Y %H:%M:%S"))
            current[tol] += timedelta(seconds=random.randint(5, 30))
        writer.writerow(row)

print("✅ data_tol_500.csv berhasil dibuat (500 baris)")
