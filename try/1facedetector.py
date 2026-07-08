import cv2
import face_recognition
import sys
import json
import numpy as np
import hashlib
from PIL import Image
sys.path.append('C:/Users/Norwe/Downloads/motion_project')
from db import get_connection

def load_known_faces():
    """Load face encodings from admins table in DB."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT admin_id, full_name, face_encoding FROM admins")
        admins = cursor.fetchall()
        cursor.close()
        conn.close()

        known_encodings = []
        known_names = []
        known_ids = []
        for admin_id, full_name, enc_json in admins:
            known_encodings.append(np.array(json.loads(enc_json)))
            known_names.append(full_name)
            known_ids.append(admin_id)

        print(f"{len(known_encodings)} face(s) loaded: {known_names}")
        return known_encodings, known_names, known_ids

    except Exception as e:
        print(f"DB error loading faces: {e}")
        print("Falling back to assets folder...")
        ref_images = [
            ("assets/Student1.jpg", "Annie"),
            ("assets/Student2.jpg", "Arnado"),
            ("assets/Student3.jpg", "Magsayo"),
            ("assets/Student4.jpg", "Arante"),
            ("assets/Student5.jpg", "Charles"),
        ]
        known_encodings = []
        known_names = []
        for path, name in ref_images:
            try:
                img = np.array(Image.open(path).convert("RGB"), dtype=np.uint8)
                enc = face_recognition.face_encodings(img)
                if enc:
                    known_encodings.append(enc[0])
                    known_names.append(name)
            except Exception as ex:
                print(f"Could not load {path}: {ex}")
        return known_encodings, known_names, [None] * len(known_names)

def log_login(admin_id, method, success):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO login_logs (admin_id, method, success)
            VALUES (%s, %s, %s)
        """, (admin_id, method, success))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"DB login log error: {e}")

known_encodings, names, admin_ids = load_known_faces()

cap = cv2.VideoCapture(0)   # 0 = built-in

while True:
    ret, frame = cap.read()
    if not ret:
        break

    small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
    rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

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
            log_login(matched_id, 'face_recognition', True)
            print(f"Access granted: {name}")
        else:
            log_login(None, 'face_recognition', False)

        top, right, bottom, left = top*4, right*4, bottom*4, left*4
        cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
        cv2.putText(frame, name, (left, bottom + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    cv2.imshow("Face Detection", frame)
    if cv2.waitKey(1) == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()