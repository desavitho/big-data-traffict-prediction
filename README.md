# Vehicle Counter System

A real-time vehicle counting system powered by TensorFlow (SSD MobileNet V2), OpenCV, and Flask. Designed to run on headless VPS environments with a modern web interface.

## Features

- **Real-time Vehicle Detection & Counting**: Detects Cars, Motorcycles, Buses, Trucks, and Persons.
- **Centroid Tracking**: Prevents double counting of the same object.
- **Headless Support**: Runs on VPS without a display server; video is streamed via Web UI.
- **Dynamic Stream Switching**: Change CCTV/Video source instantly from the dashboard.
- **Modern Dashboard**: Glassmorphism UI with real-time statistics (Chart.js) and Tailwind CSS.
- **API Endpoint**: JSON API for external integrations.

## Requirements

- Python 3.8+
- OpenCV
- TensorFlow
- Flask

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/vehicle-counter.git
   cd vehicle-counter
   ```

2. **Create a Virtual Environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. **Run the Application**
   ```bash
   python vehicle_counter.py
   ```
   *Note: For production/VPS, consider using `nohup` or a service manager like systemd.*

2. **Access the Dashboard**
   Open your browser and navigate to:
   `http://localhost:5000` (or your VPS IP:5000)

3. **Configure Stream**
   - By default, it may look for a sample video.
   - Use the "Stream Settings" panel in the dashboard to enter a CCTV URL (RTSP/HTTP/HLS) or a video file path.

## Project Structure

- `vehicle_counter.py`: Main application logic (Flask + OpenCV + TensorFlow).
- `templates/index.html`: Web dashboard interface.
- `requirements.txt`: Python dependencies.

## License

MIT
