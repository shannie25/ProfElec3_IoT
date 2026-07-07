from flask import Flask, request, jsonify, Response, send_from_directory
import cv2
import sys
import os
import json
import numpy as np
import hashlib
import time as time_module
from datetime import datetime
from PIL import Image

sys.path.append('C:/Users/Norwe/Downloads/motion_project')
from db import get_connection
from schedule_provider import get_class_periods_for_today

app = Flask(__name__, static_folder='.')

CAMERA_SOURCES = {
    "cam1": 0,   # built-in web cam
    "cam2": "http://172.19.xxx.xxx:8080/video",   # Phone IP Webcam
}

_prev_gray_store = {}
_motion_state = {}
_last_screenshot_time = {}
MOTION_THRESHOLD = 5000
SCREENSHOT_COOLDOWN = 60

def generate_frames(cam_id, source):
    cap = cv2.VideoCapture(source)
    while True:
        ret, frame = cap.read()
        if not ret:
            cap = cv2.VideoCapture(source)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if cam_id not in _prev_gray_store:
            _prev_gray_store[cam_id] = gray
            continue

        diff = cv2.absdiff(_prev_gray_store[cam_id], gray)
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        changed_pixels = cv2.countNonZero(thresh)
        motion_detected = changed_pixels > MOTION_THRESHOLD

        now = datetime.now()
        status = 'motion_detected' if motion_detected else 'no_motion'

        # Get schedule
        periods = get_class_periods_for_today(now.date())
        within = any(s <= now.time() < e for s, e in periods)

        # State-change logging
        if _motion_state.get(cam_id) != status:
            _motion_state[cam_id] = status
            screenshot_path = None

            if motion_detected and not within:
                now_ts = time_module.time()
                if (now_ts - _last_screenshot_time.get(cam_id, 0)) > SCREENSHOT_COOLDOWN:
                    os.makedirs('screenshots', exist_ok=True)
                    filename = f"screenshots/{now.strftime('%Y%m%d_%H%M%S')}_{cam_id}.jpg"
                    cv2.imwrite(filename, frame)
                    screenshot_path = filename
                    _last_screenshot_time[cam_id] = now_ts

            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO motion_logs (room_id, status, is_within_schedule, screenshot_path)
                    VALUES (%s, %s, %s, %s)
                """, (1, status, within, screenshot_path))
                conn.commit()
                cursor.close()
                conn.close()
            except Exception as e:
                print(f"DB log error: {e}")

        # Draw label on frame
        color = (0, 0, 255) if motion_detected else (0, 255, 0)
        label = "MOTION" if motion_detected else "No Motion"
        cv2.putText(frame, f"{cam_id.upper()} | {label}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        _prev_gray_store[cam_id] = gray

        _, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route('/stream/<cam_id>')
def stream(cam_id):
    source = CAMERA_SOURCES.get(cam_id)
    if source is None:
        return f"Unknown camera: {cam_id}", 404
    return Response(generate_frames(cam_id, source),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# ── API: Get motion events ─────────────────────────────────────
@app.route('/api/events')
def get_events():
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT ml.log_id, ml.status, ml.is_within_schedule,
                   ml.screenshot_path, ml.timestamp, r.room_name
            FROM motion_logs ml
            JOIN rooms r ON ml.room_id = r.room_id
            ORDER BY ml.timestamp DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        events = []
        for row in rows:
            events.append({
                "id": row['log_id'],
                "type": row['status'],
                "status": "expected" if row['is_within_schedule'] else "anomaly",
                "camera": row['room_name'],
                "time": row['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
                "screenshot": row['screenshot_path'] or "",
                "persons": 1 if row['status'] == 'motion_detected' else 0
            })
        return jsonify(events)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── API: Face login ────────────────────────────────────────────
@app.route('/api/face-login', methods=['POST'])
def face_login():
    try:
        import face_recognition

        # Load known faces from DB
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT admin_id, full_name, face_encoding FROM admins")
        admins = cursor.fetchall()

        known_encodings = []
        known_names = []
        known_ids = []
        for admin_id, full_name, enc_json in admins:
            known_encodings.append(np.array(json.loads(enc_json)))
            known_names.append(full_name)
            known_ids.append(admin_id)

        if not known_encodings:
            return jsonify({"recognized": False, "message": "No admins registered"}), 401

        # Capture from camera
        cap = cv2.VideoCapture(0)
        matched_name = None
        matched_id = None

        for _ in range(30):
            ret, frame = cap.read()
            if not ret:
                continue
            small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            rgb = np.array(Image.fromarray(rgb).convert("RGB"), dtype=np.uint8)

            locations = face_recognition.face_locations(rgb)
            encodings = face_recognition.face_encodings(rgb, locations)

            for encoding in encodings:
                matches = face_recognition.compare_faces(known_encodings, encoding, tolerance=0.5)
                if True in matches:
                    idx = matches.index(True)
                    matched_name = known_names[idx]
                    matched_id = known_ids[idx]
                    break
            if matched_name:
                break

        cap.release()

        success = matched_name is not None
        cursor.execute("""
            INSERT INTO login_logs (admin_id, method, success)
            VALUES (%s, 'face_recognition', %s)
        """, (matched_id, success))
        conn.commit()
        cursor.close()
        conn.close()

        if success:
            return jsonify({"recognized": True, "name": matched_name})
        else:
            return jsonify({"recognized": False, "message": "Face not recognized"}), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── API: ID login ──────────────────────────────────────────────
@app.route('/api/id-login', methods=['POST'])
def id_login():
    try:
        data = request.get_json()
        id_number = data.get('id_number', '')
        password  = data.get('password', '')
        password_hash = hashlib.sha256(password.encode()).hexdigest()

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT admin_id, full_name FROM admins
            WHERE id_number = %s AND password_hash = %s
        """, (id_number, password_hash))
        admin = cursor.fetchone()

        success = admin is not None
        admin_id = admin[0] if admin else None
        cursor.execute("""
            INSERT INTO login_logs (admin_id, method, success)
            VALUES (%s, 'id_backup', %s)
        """, (admin_id, success))
        conn.commit()
        cursor.close()
        conn.close()

        if success:
            return jsonify({"success": True, "name": admin[1]})
        else:
            return jsonify({"success": False, "message": "Invalid ID or password"}), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Serve the HTML dashboard ───────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('.', 'admin_dashboard.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)