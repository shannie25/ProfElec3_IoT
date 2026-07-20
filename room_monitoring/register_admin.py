import face_recognition
import json
import sys
import os
import hashlib
import numpy as np
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from db import get_connection
from PIL import Image

admins_to_register = [
    ("assets/Student1.jpg", "Annie",   "2025-00001", "admin123"),
    ("assets/Student2.jpg", "Arnado",  "2025-00002", "admin123"),
    ("assets/Student3.jpg", "Magsayo", "2025-00003", "admin123"),
    ("assets/Student4.jpg", "Arante",  "2025-00004", "admin123"),
    ("assets/Student5.jpg", "Charles", "2025-00005", "admin123"),
]

conn = get_connection()
cursor = conn.cursor()

for photo_path, full_name, id_number, password in admins_to_register:
    print(f"Registering {full_name}...")

    # PIL approach — fixes numpy uint8 compatibility with dlib
    img_pil = Image.open(photo_path).convert("RGB")
    img = np.array(img_pil, dtype=np.uint8)

    encodings = face_recognition.face_encodings(img)

    if not encodings:
        print(f"  WARNING: No face found in {photo_path} — skipping")
        continue

    encoding_json = json.dumps(encodings[0].tolist())
    password_hash = hashlib.sha256(password.encode()).hexdigest()

    try:
        cursor.execute("""
            INSERT INTO admins (full_name, id_number, password_hash, face_encoding)
            VALUES (%s, %s, %s, %s)
        """, (full_name, id_number, password_hash, encoding_json))
        print(f"  ✓ {full_name} registered successfully")
    except Exception as e:
        print(f"  ERROR registering {full_name}: {e}")

conn.commit()
cursor.close()
conn.close()
print("\nAll done! Check admins table in HeidiSQL.")