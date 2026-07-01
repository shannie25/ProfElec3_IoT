import cv2
from datetime import datetime
from schedule_provider import get_class_periods_for_today


SCHEDULE_REFRESH_SECONDS = 60


def is_class_time(now, class_periods):
    current = now.time()
    for start, end in class_periods:
        if start <= current < end:
            return True
    return False

cap = cv2.VideoCapture(1)

ret, prev_frame = cap.read()
if not ret:
    raise RuntimeError("Could not read from camera")

prev_frame = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)

last_notified = 0
last_schedule_load = 0
current_class_periods = []

while True:
    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(prev_frame, gray)
    _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)

    motion_detected = thresh.sum() > 500000  

    now = datetime.now()
    now_ts = now.timestamp()

    if now_ts - last_schedule_load > SCHEDULE_REFRESH_SECONDS:
        current_class_periods = get_class_periods_for_today(now.date())
        last_schedule_load = now_ts

    if motion_detected and not is_class_time(now, current_class_periods):
        if now_ts - last_notified > 10: 
            print(f"[{now.strftime('%H:%M:%S')}] Movement detected during no class time")
            last_notified = now_ts

    cv2.imshow("Motion Detector", frame)

    prev_frame = gray

    if cv2.waitKey(1) == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
