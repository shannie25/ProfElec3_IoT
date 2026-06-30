import cv2
from datetime import datetime, time


class_periods = [
    (time(9, 20), time(13, 0)),
    (time(14, 50), time(15, 53)),
    (time(16, 0), time(16, 30)),  
    (time(16, 35), time(16, 40)),  
]

def is_class_time(now):
    current = now.time()
    for start, end in class_periods:
        if start <= current < end:
            return True
    return False

cap = cv2.VideoCapture(1)

ret, prev_frame = cap.read()
prev_frame = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)

last_notified = 0

while True:
    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(prev_frame, gray)
    _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)

    motion_detected = thresh.sum() > 500000  

    now = datetime.now()

    if motion_detected and not is_class_time(now):
        now_ts = now.timestamp()
        if now_ts - last_notified > 10: 
            print(f"[{now.strftime('%H:%M:%S')}] Movement detected during no class time")
            last_notified = now_ts

    cv2.imshow("Motion Detector", frame)

    prev_frame = gray

    if cv2.waitKey(1) == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()