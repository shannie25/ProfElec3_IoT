import cv2
import sys
import os
import time as time_module
from datetime import datetime, date
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from db import get_connection
from schedule_provider import get_class_periods_for_today

SCHEDULE_REFRESH_SECONDS = 60
MOTION_THRESHOLD         = 5000
SCREENSHOT_COOLDOWN      = 60

# ── Camera sources ───────────────────────────────────────────────
#CAMERAS = {
 #   "cam1": {"source": 0,                                     "room_id": 1, "room_name": "IC1004"},
  #  "cam2": {"source": "http://192.168.1.105:8080/video",     "room_id": 2, "room_name": "IC1005"},
    # }

def load_cameras_from_db():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT room_id, room_name, camera_source FROM rooms")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        cameras = {}
        for room_id, room_name, camera_source in rows:
            try:
                source = int(camera_source)
            except (ValueError, TypeError):
                source = camera_source
            cam_id = f"cam{room_id}"
            cameras[cam_id] = {
                "source": source,
                "room_id": room_id,
                "room_name": room_name,
            }
            print(f"  {cam_id}: {room_name} ({source})")
        return cameras
    except Exception as e:
        print(f"[DB] Camera load failed: {e}")
        return {"cam1": {"source": 0, "room_id": 1, "room_name": "Room 1"}}

CAMERAS = load_cameras_from_db()

def log_to_db(room_id, status, within, screenshot_path=None):
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

def is_class_time(now, class_periods):
    current = now.time()
    for start, end in class_periods:
        if start <= current < end:
            return True
    return False

def make_state():
    return {"prev_gray": None, "current_status": None, "last_screenshot_ts": 0}

def process_frame(cam_id, cam_cfg, state, frame, class_periods):
    now = datetime.now()
    now_ts = now.timestamp()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (21, 21), 0)
    if state["prev_gray"] is None:
        state["prev_gray"] = gray
        return frame
    diff = cv2.absdiff(state["prev_gray"], gray)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    changed_pixels  = cv2.countNonZero(thresh)
    motion_detected = changed_pixels > MOTION_THRESHOLD
    state["prev_gray"] = gray
    within = is_class_time(now, class_periods)
    status = 'motion_detected' if motion_detected else 'no_motion'

    # Screenshot — once per event, 60s cooldown, only outside schedule
    screenshot_path = None
    if motion_detected and not within:
        elapsed = now_ts - state["last_screenshot_ts"]
        if elapsed > SCREENSHOT_COOLDOWN:
            os.makedirs('screenshots', exist_ok=True)
            filename = f"screenshots/{now.strftime('%Y%m%d_%H%M%S')}_{cam_id}.jpg"
            cv2.imwrite(filename, frame)
            screenshot_path = filename
            state["last_screenshot_ts"] = now_ts
            print(f"[Screenshot] {filename}")
        else:
            print(f"[Screenshot] Cooldown: {int(SCREENSHOT_COOLDOWN - elapsed)}s remaining")

    if state["current_status"] != status:
        state["current_status"] = status
        log_to_db(cam_cfg["room_id"], status, within, screenshot_path)

    color = (0, 0, 255) if motion_detected else (0, 255, 0)
    cv2.putText(frame, f"{cam_cfg['room_name']} | {'MOTION' if motion_detected else 'Clear'} ({changed_pixels}px)",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
    cv2.putText(frame, "Class in session" if within else "No class scheduled",
                (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (0, 200, 100) if within else (0, 140, 255), 1)
    if motion_detected and not within:
        cv2.putText(frame, "! OUTSIDE SCHEDULE", (10, 82),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    return frame

# ── Open cameras ─────────────────────────────────────────────────
print("Opening cameras...")
caps, states = {}, {}
for cam_id, cam_cfg in CAMERAS.items():
    cap = cv2.VideoCapture(cam_cfg["source"])
    if cap.isOpened():
        print(f"  {cam_id} ({cam_cfg['room_name']}): OK")
        caps[cam_id]   = cap
        states[cam_id] = make_state()
    else:
        print(f"  {cam_id} ({cam_cfg['room_name']}): FAILED — skipping")

if not caps:
    print("No cameras found. Check CAMERAS config.")
    exit(1)

last_schedule_load = 0
schedules = {}
print("Running — press Q to quit\n")

while True:
    now_ts = time_module.time()
    if now_ts - last_schedule_load > SCHEDULE_REFRESH_SECONDS:
        for cam_id, cam_cfg in CAMERAS.items():
            schedules[cam_id] = get_class_periods_for_today(date.today(), cam_cfg["room_name"])
        last_schedule_load = now_ts

    for cam_id, cap in caps.items():
        ret, frame = cap.read()
        if not ret:
            print(f"[{cam_id}] Reconnecting...")
            caps[cam_id] = cv2.VideoCapture(CAMERAS[cam_id]["source"])
            continue
        frame = process_frame(cam_id, CAMERAS[cam_id], states[cam_id], frame, schedules.get(cam_id, []))
        cv2.imshow(f"Motion — {CAMERAS[cam_id]['room_name']}", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

for cap in caps.values():
    cap.release()
cv2.destroyAllWindows()