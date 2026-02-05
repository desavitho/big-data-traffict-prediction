# AI Vehicle Counter & Traffic Analytics System

Sistem penghitung kendaraan berbasis AI (YOLOv8) dengan analisis Big Data dan visualisasi Dashboard real-time.

## Fitur Utama

*   **Real-time Detection**: Menggunakan YOLOv8 untuk mendeteksi Mobil dan Motor.
*   **Multi-Camera Support**: Mendukung banyak stream CCTV (m3u8/RTSP) sekaligus.
*   **Traffic Analytics**:
    *   Menghitung kepadatan lalu lintas (Sepi/Sedang/Ramai).
    *   Statistik per jam, harian, dan total akumulasi.
    *   Pemisahan kategori kendaraan (Mobil vs Motor).
*   **Interactive Dashboard**:
    *   Peta sebaran CCTV dengan indikator kemacetan warna-warni.
    *   Grafik tren kepadatan lalu lintas.
    *   Integrasi data cuaca real-time.
*   **Data Lake Integration**: Menyimpan log deteksi detail ke format CSV terpartisi untuk analisis Big Data.
*   **Resilience**: Sistem otomatis reconnect jika stream terputus dan menangani koneksi lambat.

## Instalasi

1.  Clone repositori ini.
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
    *(Pastikan FFmpeg dan PyTorch terinstall)*
3.  Jalankan aplikasi:
    ```bash
    python3 run.py
    ```

## Konfigurasi

*   Edit `data/cctv_config.json` untuk menambah/mengubah daftar kamera.
*   Edit `app/config.py` untuk parameter deteksi (Threshold, ROI, dll).

## Catatan Database

*   **Traffic Stats**: Data real-time dan history jangka pendek disimpan di `data/traffic_stats.json`.
*   **History Panjang**: Data historis lengkap disimpan di `data/traffic_data.db` (SQLite).
    *   *Note: File database SQLite tidak di-upload ke GitHub karena ukurannya besar (>100MB).*
    *   *Gunakan backup lokal atau Git LFS jika ingin menyinkronkan database.*

---
*Developed by Desavitho*
