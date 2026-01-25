# AI Vehicle Counter & Analytics System

Sistem penghitung kendaraan real-time berbasis AI (YOLOv8) yang dirancang untuk berjalan di lingkungan headless (VPS/Server) dengan antarmuka web modern untuk pemantauan dan analisis data lalu lintas.

## Fitur Utama

### 1. Deteksi & Perhitungan Real-time
- **AI Powered**: Menggunakan model YOLOv8 Nano (`yolov8n.pt`) untuk akurasi tinggi dan performa cepat.
- **Multi-Class**: Mendeteksi dan menghitung Mobil (`car`, `bus`, `truck`) dan Motor (`motorcycle`, `bicycle`).
- **Tracking**: Algoritma pelacakan objek untuk mencegah penghitungan ganda.

### 2. Analisis Data & History
- **Fleksibilitas Waktu**: Lihat data dalam berbagai rentang waktu:
  - Jangka Pendek: 30 Menit, 1 Jam, 6 Jam, 12 Jam, 24 Jam.
  - Jangka Panjang: 7 Hari, 30 Hari.
- **Visualisasi**: Grafik interaktif (Chart.js) yang menyesuaikan dengan periode yang dipilih.
- **Detail Harian (Drill-down)**: Klik tombol "View" pada data harian (7d/30d) untuk melihat detail per jam (00:00 - 24:00) pada hari tersebut.
- **Ringkasan Harian**: Popup modal menampilkan total kendaraan dan grafik Donut Chart (Persentase Mobil vs Motor).

### 3. Pelaporan (Export)
- **Export CSV**: Unduh laporan data lalu lintas dalam format CSV.
  - Mendukung unduhan data history utama.
  - Mendukung unduhan detail harian (per jam) dari modal.
- **Metadata**: File CSV menyertakan nama kamera, waktu generate, dan pemisahan kolom Tanggal/Jam yang rapi.

### 4. Manajemen Kamera
- **Multi-Camera Support**: Mendukung pemantauan beberapa titik CCTV (konfigurasi via `data/cctv_config.json`).
- **Stream Switching**: Ganti feed kamera secara instan melalui dashboard.

### 5. Keamanan & Akses
- **Admin Panel**: Fitur sensitif (seperti reset data/config) dilindungi login.
  - **Username**: `admin`
    - **Password**: `@dmin12345` (Default, silakan ubah di `app/routes.py`).

---

## Instalasi

### Prasyarat
- OS: Linux (Ubuntu/Debian recommended)
- Python 3.8+
- Library sistem untuk OpenCV (`libgl1`, `libglib2.0-0`)

### Langkah-langkah

1. **Clone Repository**
   ```bash
   git clone <repository_url>
   cd vehicle-counter
   ```

2. **Buat Virtual Environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   *Pastikan `ultralytics`, `flask`, `opencv-python`, `numpy` terinstall.*

---

## Penggunaan

### Menjalankan Aplikasi
```bash
# Pastikan virtual environment aktif
source venv/bin/activate

# Jalankan server
python run.py
```
Server akan berjalan di port **5000**.

### Mengakses Dashboard
Buka browser dan akses: `http://<IP_SERVER>:5000`

### Konfigurasi CCTV
Edit file `data/cctv_config.json` untuk menambah atau mengubah sumber video:
```json
[
    {
        "id": "cam1",
        "name": "Simpang Gedebage",
        "url": "https://pelindung.bandung.go.id:8443/api/cek/cctv/..."
    }
]
```

### Konfigurasi Sistem
Edit file `app/config.py` untuk pengaturan lanjutan:
- `HISTORY_MAX_LEN`: Menentukan berapa banyak data history yang disimpan (default cukup untuk >30 hari).
- `CONFIDENCE_THRESHOLD`: Sensitivitas deteksi AI.

---

## Struktur Proyek
```
vehicle-counter/
├── app/
│   ├── config.py          # Konfigurasi sistem (Baru)
│   ├── routes.py          # API & Web routes
│   ├── services/
│   │   └── camera.py      # Logic deteksi & pemrosesan video (Refactored)
│   ├── utils.py           # Helper functions & data management
│   ├── globals.py         # Global state
│   ├── templates/         # HTML Frontend
│   └── static/            # JS/CSS assets
├── data/
│   ├── cctv_config.json   # Config kamera
│   └── traffic_stats.json # Data persisten
├── models/
│   └── yolov8n.pt         # Model AI
├── logs/                  # Log files
├── run.py                 # Entry point
├── requirements.txt
└── README.md
```

```
vehicle-counter/
├── app/
│   ├── core/           # Logika utama (Camera, YOLO, Utils)
│   ├── templates/      # File HTML (Dashboard)
│   ├── routes.py       # Route Flask & API Endpoint
│   └── globals.py      # Variabel global
├── data/
│   ├── cctv_config.json # Daftar kamera
│   ├── traffic_stats.json # Database sederhana (JSON) untuk history
│   └── ...
├── run.py              # Entry point aplikasi
├── config.py           # Konfigurasi konstanta
└── requirements.txt    # Daftar dependensi
```

## Troubleshooting

- **Port 5000 in use**:
  Jika gagal start karena port terpakai, matikan proses lama:
  ```bash
  sudo lsof -t -i:5000 | xargs -r sudo kill -9
  ```
- **Stream Error**:
  Pastikan URL CCTV valid dan dapat diakses dari server. Beberapa stream mungkin memerlukan codec H.264 khusus atau membatasi akses geo-location.

---
