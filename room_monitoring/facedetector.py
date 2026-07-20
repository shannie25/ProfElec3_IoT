import cv2
import face_recognition
import sys
import os
import json
import numpy as np
from PIL import Image
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from db import get_connection

# ── Load known faces from DB, fall back to assets/ ───────────────
def load_known_faces():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT admin_id, full_name, face_encoding FROM admins")
        admins = cursor.fetchall()
        cursor.close()
        conn.close()
        if not admins:
            raise Exception("No admins in DB — run register_admin.py first")
        known_encodings, known_names, known_ids = [], [], []
        for admin_id, full_name, enc_json in admins:
            known_encodings.append(np.array(json.loads(enc_json)))
            known_names.append(full_name)
            known_ids.append(admin_id)
        print(f"[FaceDetector] {len(known_encodings)} face(s) loaded from DB: {known_names}")
        return known_encodings, known_names, known_ids
    except Exception as e:
        print(f"[FaceDetector] DB failed: {e} — using assets/ folder")
        assets = [
            ("assets/Student1.jpg", "Annie"),
            ("assets/Student2.jpg", "Arnado"),
            ("assets/Student3.jpg", "Magsayo"),
            ("assets/Student4.jpg", "Arante"),
            ("assets/Student5.jpg", "Charles"),
        ]
        known_encodings, known_names = [], []
        for path, name in assets:
            try:
                img = np.array(Image.open(path).convert("RGB"), dtype=np.uint8)
                enc = face_recognition.face_encodings(img)
                if enc:
                    known_encodings.append(enc[0])
                    known_names.append(name)
                    print(f"  Loaded: {name}")
            except Exception as ex:
                print(f"  ERROR: {path}: {ex}")
        return known_encodings, known_names, [None]*len(known_names)

def log_login(admin_id, success):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO login_logs (admin_id, method, success)
            VALUES (%s, 'face_recognition', %s)
        """, (admin_id, success))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[LoginLog Error] {e}")

# ── Load faces ───────────────────────────────────────────────────
known_encodings, names, admin_ids = load_known_faces()

if not known_encodings:
    print("ERROR: No faces loaded. Check assets/ or run register_admin.py")
    exit(1)

# ── Camera: 0 = built-in ACER, 1 = Wonder Camera ────────────────
cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("ERROR: Cannot open camera")
    exit(1)

print("[FaceDetector] Running — press Q to quit")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)

    # PIL fix for numpy compatibility with dlib
    rgb_small = np.array(
        Image.fromarray(
            cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
        ).convert("RGB"),
        dtype=np.uint8
    )

    face_locations = face_recognition.face_locations(rgb_small)
    face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

    for (top, right, bottom, left), encoding in zip(face_locations, face_encodings):
        matches = face_recognition.compare_faces(known_encodings, encoding, tolerance=0.5)
        name = "Unknown"
        matched_id = None

        if True in matches:
            idx = matches.index(True)
            name = names[idx]
            matched_id = admin_ids[idx]
            log_login(matched_id, True)
            print(f"Access granted: {name}")
        else:
            log_login(None, False)

        top, right, bottom, left = top*4, right*4, bottom*4, left*4
        color = (0, 255, 0) if name != "Unknown" else (0, 0, 255)
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.putText(frame, name, (left, bottom + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

    cv2.imshow("Face Detection — Admin Login", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()