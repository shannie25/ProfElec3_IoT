import cv2
import sys
import os
import time as time_module
from datetime import datetime
sys.path.append('C:/Users/Norwe/Downloads/motion_project')
from db import get_connection
from schedule_provider import get_class_periods_for_today

SCHEDULE_REFRESH_SECONDS = 60
MOTION_THRESHOLD = 5000

# ── State tracking ─────────────────────────────────────────────
current_state = None

def is_class_time(now, class_periods):
    current = now.time()
    for start, end in class_periods:
        if start <= current < end:
            return True
    return False

def log_motion(room_id, status, within_schedule):
    global current_state
    if current_state == status:
        return  # no change, skip
    current_state = status

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO motion_logs (room_id, status, is_within_schedule, screenshot_path)
            VALUES (%s, %s, %s, NULL)
        """, (room_id, status, within_schedule))
        conn.commit()
        cursor.close()
        conn.close()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Logged: {status} | within schedule: {within_schedule}")
    except Exception as e:
        print(f"DB log error: {e}")

# ── Camera setup ───────────────────────────────────────────────
ROOM_ID   = 1
ROOM_NAME = 'IC1001'

cap = cv2.VideoCapture(0)   # 0 = built-in, 1 = Wonder Camera
ret, prev_frame = cap.read()
if not ret:
    raise RuntimeError("Could not read from camera")

prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
prev_gray = cv2.GaussianBlur(prev_gray, (21, 21), 0)

last_notified      = 0
last_schedule_load = 0
current_class_periods = []

# ── Main loop ──────────────────────────────────────────────────
while True:
    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (21, 21), 0)

    diff = cv2.absdiff(prev_gray, gray)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

    changed_pixels = cv2.countNonZero(thresh)
    motion_detected = changed_pixels > MOTION_THRESHOLD

    now    = datetime.now()
    now_ts = now.timestamp()

    # Refresh schedule from DB every 60 seconds
    if now_ts - last_schedule_load > SCHEDULE_REFRESH_SECONDS:
        current_class_periods = get_class_periods_for_today(now.date())
        last_schedule_load = now_ts

    within = is_class_time(now, current_class_periods)
    status = 'motion_detected' if motion_detected else 'no_motion'

    # Log to DB (state change only, no screenshot for now)
    log_motion(ROOM_ID, status, within)

    # Show on screen
    color = (0, 0, 255) if motion_detected else (0, 255, 0)
    label = f"MOTION ({changed_pixels}px)" if motion_detected else f"No motion ({changed_pixels}px)"
    cv2.putText(frame, label, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

    if motion_detected and not within:
        if now_ts - last_notified > 10:
            print(f"[{now.strftime('%H:%M:%S')}] Movement detected during no class time")
            last_notified = now_ts

    cv2.imshow("Motion Detector", frame)
    prev_gray = gray

    if cv2.waitKey(1) == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()