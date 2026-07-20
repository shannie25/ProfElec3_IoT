"""
SENTRY Camera Server — Python
==============================
Background threads capture CCTV cameras on startup.
The access webcam starts only while the login page needs it.
"""

from flask import Flask, Response, jsonify
import cv2, sys, os, json, numpy as np
import time as time_module
import threading
from datetime import datetime, date

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from db import get_connection
from schedule_provider import get_class_periods_for_today

app = Flask(__name__)

MOTION_THRESHOLD    = 5000
EVENT_END_DEBOUNCE  = 2  # seconds of continuous calm before an outside-schedule motion event is considered over
FACE_SCAN_INTERVAL  = 0.75  # seconds; keep the live stream responsive
ACCESS_CAM_ID        = "access"

def parse_camera_source(value):
    """Keep USB camera indexes as ints and IP-camera URLs as strings."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return value

# The access camera is intentionally separate from all room CCTV feeds.
# Override it when needed, for example: $env:ACCESS_CAMERA_SOURCE='1'
ACCESS_CAMERA_SOURCE = parse_camera_source(os.getenv("ACCESS_CAMERA_SOURCE", "0"))

_prev_gray          = {}
_motion_state       = {}
_event_active       = {}   # is an outside-schedule motion event currently open, per cam
_event_calm_since   = {}   # when the current calm streak started, per cam (for debounce)
_latest_frame       = {}   # raw frame per cam_id, updated by background threads
_schedule_cache     = {}   # one schedule lookup per room per day
_face_overlays      = {}   # latest face boxes/labels per camera
_last_face_scan_ts  = {}
_face_cache         = {"date": None, "encodings": [], "names": []}
_access_capture_lock = threading.Lock()
_access_capture_stop = threading.Event()
_access_capture_thread = None

# ════════════════════════════════════════════════════════════════
# LOAD CAMERAS FROM DB
# ════════════════════════════════════════════════════════════════
def load_cameras_from_db():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT room_id, room_name, camera_source FROM rooms ORDER BY room_id")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        if not rows:
            print("[Cameras] No rooms in DB")
            return {}
        cameras = {}
        for room_id, room_name, camera_source in rows:
            source = parse_camera_source(camera_source)
            # Do not open the laptop access webcam as a room CCTV camera too.
            if source == ACCESS_CAMERA_SOURCE:
                print(f"  Skipping {room_name}: source {source} is reserved for access login")
                continue
            cam_id = f"cam{room_id}"
            cameras[cam_id] = {
                "source": source, "room_id": room_id, "room_name": room_name,
                "role": "cctv"
            }
            print(f"  {cam_id} → {room_name} | {source}")
        return cameras
    except Exception as e:
        print(f"[DB Error] {e} — falling back to webcam 0")
        return {}

# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════
def is_class_time(now, class_periods):
    current = now.time()
    for start, end in class_periods:
        if start <= current < end:
            return True
    return False

def get_cached_schedule(class_date, room_name):
    """Avoid a database query (and debug print) for every video frame."""
    key = (class_date, room_name)
    if key not in _schedule_cache:
        _schedule_cache[key] = get_class_periods_for_today(class_date, room_name)
    return _schedule_cache[key]

def get_known_faces():
    """Load face encodings once per day instead of once per video frame."""
    if _face_cache["date"] == date.today():
        return _face_cache["encodings"], _face_cache["names"]
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT full_name, face_encoding FROM admins")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        _face_cache["encodings"] = [np.array(json.loads(enc)) for _, enc in rows if enc]
        _face_cache["names"] = [name for name, enc in rows if enc]
        _face_cache["date"] = date.today()
        print(f"[Face Overlay] {len(_face_cache['names'])} registered face(s) loaded")
    except Exception as e:
        print(f"[Face Overlay] Could not load registered faces: {e}")
        _face_cache["encodings"], _face_cache["names"] = [], []
        _face_cache["date"] = date.today()
    return _face_cache["encodings"], _face_cache["names"]

def update_face_overlay(cam_id, frame, now_ts):
    """Recognize faces periodically and retain the result for stream frames."""
    if now_ts - _last_face_scan_ts.get(cam_id, 0) < FACE_SCAN_INTERVAL:
        return
    _last_face_scan_ts[cam_id] = now_ts
    try:
        import face_recognition
        small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb)
        encodings = face_recognition.face_encodings(rgb, locations)
        known_encodings, known_names = get_known_faces()
        faces = []
        for (top, right, bottom, left), encoding in zip(locations, encodings):
            label, recognized = "Unknown", False
            if known_encodings:
                matches = face_recognition.compare_faces(known_encodings, encoding, tolerance=0.5)
                if True in matches:
                    label, recognized = known_names[matches.index(True)], True
            faces.append((top * 4, right * 4, bottom * 4, left * 4, label, recognized))
        _face_overlays[cam_id] = faces
    except Exception as e:
        # Face recognition is optional: a failure must not stop camera capture.
        print(f"[Face Overlay] {e}")

def draw_face_overlay(frame, cam_id):
    for top, right, bottom, left, label, recognized in _face_overlays.get(cam_id, []):
        color = (0, 200, 0) if recognized else (0, 165, 255)
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        text_y = max(top - 10, 20)
        cv2.putText(frame, label, (left, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

def log_motion_db(room_id, status, within, screenshot_path=None):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO motion_logs (room_id, status, is_within_schedule, screenshot_path)
            VALUES (%s, %s, %s, %s)
        """, (room_id, status, within, screenshot_path))
        conn.commit()
        cursor.close()
        conn.close()
        ts = datetime.now().strftime('%H:%M:%S')
        print(f"[{ts}][DB] {status} | room={room_id} | within={within}"
              + (f" | shot={screenshot_path}" if screenshot_path else ""))
    except Exception as e:
        print(f"[DB Error] {e}")

def access_capture():
    """Capture the laptop webcam only while face login is active."""
    cap = cv2.VideoCapture(ACCESS_CAMERA_SOURCE)
    print("[Access] Webcam started for login")
    try:
        while not _access_capture_stop.is_set():
            ret, frame = cap.read()
            if not ret:
                print("[Access] Webcam frame failed")
                break
            _latest_frame[ACCESS_CAM_ID] = frame.copy()
    finally:
        cap.release()
        _latest_frame.pop(ACCESS_CAM_ID, None)
        _face_overlays.pop(ACCESS_CAM_ID, None)
        print("[Access] Webcam released")

def start_access_capture():
    """Start the access webcam once, when the login page needs a frame."""
    global _access_capture_thread
    with _access_capture_lock:
        if _access_capture_thread and _access_capture_thread.is_alive():
            return
        _access_capture_stop.clear()
        _access_capture_thread = threading.Thread(
            target=access_capture,
            daemon=True,
        )
        _access_capture_thread.start()

def stop_access_capture():
    """Ask the access capture thread to release the laptop webcam."""
    _access_capture_stop.set()

# ════════════════════════════════════════════════════════════════
# BACKGROUND CAPTURE THREAD
# Runs for each CCTV camera on startup — always capturing frames
# so _latest_frame is always populated regardless of browser connections
# ════════════════════════════════════════════════════════════════
def background_capture(cam_id, cam_cfg):
    source    = cam_cfg["source"]
    room_id   = cam_cfg["room_id"]
    room_name = cam_cfg["room_name"]
    role      = cam_cfg["role"]

    cap = cv2.VideoCapture(source)
    print(f"[BG] {cam_id} capture started → {room_name}")

    while True:
        ret, frame = cap.read()
        if not ret:
            print(f"[BG] {cam_id} frame failed — reconnecting...")
            cap.release()
            time_module.sleep(1)
            cap = cv2.VideoCapture(source)
            continue

        # Store raw frame (BEFORE overlay) for face login + clean stream
        _latest_frame[cam_id] = frame.copy()

        now    = datetime.now()
        now_ts = time_module.time()

        # Access login uses this camera only for face recognition.  It never
        # creates motion events, schedule lookups, or room screenshots.
        if role == "access":
            update_face_overlay(cam_id, frame, now_ts)
            continue

        # CCTV cameras: motion detection and schedule monitoring only.
        # Throttled to ~10fps — room-scale motion doesn't need native camera
        # framerate, and this is the main sustained CPU cost on low-power
        # hardware (e.g. Raspberry Pi) since it runs continuously per camera.
        time_module.sleep(0.1)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if cam_id not in _prev_gray:
            _prev_gray[cam_id] = gray
            continue

        diff = cv2.absdiff(_prev_gray[cam_id], gray)
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        changed_pixels  = cv2.countNonZero(thresh)
        motion_detected = changed_pixels > MOTION_THRESHOLD
        _prev_gray[cam_id] = gray

        # Schedule + screenshot + DB log
        periods = get_cached_schedule(date.today(), room_name)
        within  = is_class_time(now, periods)
        status  = 'motion_detected' if motion_detected else 'no_motion'
        # Keep the overlay accurate even when no alert is written to the DB.
        _motion_state[cam_id] = status

        # ── Event-based screenshot + DB log ──────────────────────────
        # ONLY fire once per outside-schedule motion event — at the moment
        # the event *starts* — instead of repeatedly while motion continues.
        # An event is considered over (and a later motion becomes a NEW
        # event) once the trigger condition has been continuously false
        # for EVENT_END_DEBOUNCE seconds, which absorbs single-frame
        # flicker from the raw frame-diff detector.
        if motion_detected and not within:
            _event_calm_since.pop(cam_id, None)
            if not _event_active.get(cam_id, False):
                _event_active[cam_id] = True

                os.makedirs('screenshots', exist_ok=True)
                filename = f"screenshots/{now.strftime('%Y%m%d_%H%M%S')}_{room_name}.jpg"
                cv2.imwrite(filename, frame)
                screenshot_path = filename
                print(f"[Screenshot] {filename}")

                log_motion_db(room_id, status, within, screenshot_path)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Motion outside schedule — {room_name}")
        elif _event_active.get(cam_id, False):
            calm_start = _event_calm_since.setdefault(cam_id, now_ts)
            if now_ts - calm_start >= EVENT_END_DEBOUNCE:
                _event_active[cam_id] = False
                _event_calm_since.pop(cam_id, None)

# ════════════════════════════════════════════════════════════════
# STREAM GENERATORS
# ════════════════════════════════════════════════════════════════
def generate_frames(cam_id, cam_cfg):
    room_name = cam_cfg["room_name"]
    role = cam_cfg["role"]
    """Dashboard stream — WITH motion overlay text."""
    while True:
        frame = _latest_frame.get(cam_id)
        if frame is None:
            time_module.sleep(0.05)
            continue

        frame = frame.copy()
        if role == "access":
            draw_face_overlay(frame, cam_id)
            cv2.putText(frame, "ACCESS CAMERA | FACE RECOGNITION",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 180, 0), 2)
            _, buffer = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' +
                   buffer.tobytes() + b'\r\n')
            time_module.sleep(0.033)
            continue

        motion_detected = _motion_state.get(cam_id) == 'motion_detected'

        color = (0, 0, 255) if motion_detected else (0, 255, 0)
        cv2.putText(frame,
            f"{room_name} | {'MOTION DETECTED' if motion_detected else 'Clear'}",
            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

        # Show schedule status
        now     = datetime.now()
        periods = get_cached_schedule(date.today(), room_name)
        within  = is_class_time(now, periods)
        cv2.putText(frame,
            "Class in session" if within else "No class scheduled",
            (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
            (0, 200, 100) if within else (0, 140, 255), 1)
        if motion_detected and not within:
            cv2.putText(frame, "! OUTSIDE SCHEDULE",
                (10, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

        _, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' +
               buffer.tobytes() + b'\r\n')
        time_module.sleep(0.033)

def generate_clean_frames(cam_id):
    """Login page stream — NO overlay, just raw camera."""
    if cam_id == ACCESS_CAM_ID:
        start_access_capture()
    try:
        while not _access_capture_stop.is_set() or cam_id != ACCESS_CAM_ID:
            frame = _latest_frame.get(cam_id)
            if frame is None:
                time_module.sleep(0.05)
                continue
            _, buffer = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' +
                   buffer.tobytes() + b'\r\n')
            time_module.sleep(0.033)
    finally:
        if cam_id == ACCESS_CAM_ID:
            stop_access_capture()

# ════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ════════════════════════════════════════════════════════════════
@app.route('/stream/<cam_id>')
def stream(cam_id):
    """Dashboard CCTV stream or the on-demand login webcam stream."""
    cam_cfg = CAMERAS.get(cam_id)
    if not cam_cfg:
        return f"Unknown camera: {cam_id}", 404
    if cam_id == ACCESS_CAM_ID:
        return Response(
            generate_clean_frames(cam_id),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )
    return Response(
        generate_frames(cam_id, cam_cfg),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/stream/clean/<cam_id>')
def stream_clean(cam_id):
    """Login page camera — NO overlay."""
    if cam_id not in CAMERAS:
        return f"Unknown camera: {cam_id}", 404
    return Response(
        generate_clean_frames(cam_id),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "cameras": {
            cam_id: {
                "room": cfg["room_name"],
                "role": cfg["role"],
                "source": str(cfg["source"]),
                "has_frame": cam_id in _latest_frame
            }
            for cam_id, cfg in CAMERAS.items()
        }
    })

@app.route('/api/face-login', methods=['POST'])
def face_login():
    """
    Face recognition login — reads from _latest_frame (no camera conflict).
    Checks face against ALL admins in DB.
    Multiple admins supported.
    No fallback — DB only.
    """
    try:
        import face_recognition
        from PIL import Image

        start_access_capture()

        # Load admins from DB
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT admin_id, full_name, face_encoding FROM admins")
        admins = cursor.fetchall()

        if not admins:
            return jsonify({
                "recognized": False,
                "message": "No admins registered — run register_admin.py first"
            }), 401

        known_encodings, known_names, known_ids = [], [], []
        for admin_id, full_name, enc_json in admins:
            known_encodings.append(np.array(json.loads(enc_json)))
            known_names.append(full_name)
            known_ids.append(admin_id)

        print(f"[Face Login] Checking {len(admins)} admin(s): {known_names}")

        first_cam_id = ACCESS_CAM_ID
        matched_name = matched_id = None
        face_detected = False
        locs = []   

        # Try up to 20 frames from background capture (~2 seconds)
        for attempt in range(20):
            frame = _latest_frame.get(first_cam_id)
            if frame is None:
                print(f"[Face Login] Waiting for camera... ({attempt+1}/20)")
                time_module.sleep(0.1)
                continue

            small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            rgb   = np.array(
                Image.fromarray(
                    cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                ).convert("RGB"),
                dtype=np.uint8
            )
            locs = face_recognition.face_locations(rgb)
            encs = face_recognition.face_encodings(rgb, locs)

            if locs:
                face_detected = True
                print(f"[Face Login] {len(locs)} face(s) detected")

            for enc in encs:
                matches = face_recognition.compare_faces(
                    known_encodings, enc, tolerance=0.5)
                if True in matches:
                    idx          = matches.index(True)
                    matched_name = known_names[idx]
                    matched_id   = known_ids[idx]
                    print(f"[Face Login] ✓ Match: {matched_name}")
                    break
            if matched_name:
                break
            time_module.sleep(0.1)

        success = matched_name is not None

        # Log attempt
        cursor.execute(
            "INSERT INTO login_logs (admin_id, method, success) VALUES (%s, 'face_recognition', %s)",
            (matched_id, success)
        )
        conn.commit()
        cursor.close()
        conn.close()

        if success:
            stop_access_capture()
            return jsonify({"recognized": True, "name": matched_name})

        # Return helpful error message
        if first_cam_id not in _latest_frame:
            return jsonify({
                "recognized": False,
                "message": "Camera not ready — wait a moment and try again"
            }), 401
        if not face_detected:
            return jsonify({
                "recognized": False,
                "message": "No face detected — look directly at the camera"
            }), 401
        return jsonify({
            "recognized": False,
            "message": "Face not recognized — ensure you are registered as an admin"
        }), 401

    except Exception as e:
        print(f"[Face Login Error] {e}")
        return jsonify({"error": str(e)}), 500

# ════════════════════════════════════════════════════════════════
# STARTUP — launch background capture threads for all cameras
# ════════════════════════════════════════════════════════════════
print("Loading cameras from DB...")
CCTV_CAMERAS = load_cameras_from_db()
CAMERAS = {
    ACCESS_CAM_ID: {
        "source": ACCESS_CAMERA_SOURCE,
        "room_id": None,
        "room_name": "Access Camera",
        "role": "access",
    },
    **CCTV_CAMERAS,
}
print(f"  1 access camera + {len(CCTV_CAMERAS)} CCTV camera(s) loaded\n")

print("Starting CCTV capture threads...")
for cam_id, cam_cfg in CAMERAS.items():
    if cam_cfg["role"] == "access":
        print(f"  {cam_id} starts on demand for login")
        continue
    t = threading.Thread(
        target=background_capture,
        args=(cam_id, cam_cfg),
        daemon=True
    )
    t.start()
    print(f"  {cam_id} thread started")
print()

if __name__ == '__main__':
    print("=" * 50)
    print("SENTRY Camera Server — Python")
    print("=" * 50)
    for cam_id, cfg in CAMERAS.items():
        print(f"  {cam_id}: {cfg['room_name']} → {cfg['source']}")
    print(f"  Running on port 5001")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
