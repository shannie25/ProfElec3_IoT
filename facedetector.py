import cv2
import face_recognition

ref_images = [
    face_recognition.load_image_file("admin/Student1.jpg"),
    face_recognition.load_image_file("admin/Student2.jpg"),
    face_recognition.load_image_file("admin/Student3.jpg"),
    face_recognition.load_image_file("admin/Student4.jpg"),
    face_recognition.load_image_file("admin/Student5.jpg")
]

names = ["Annie", "Arnado", "Magsayo", "Arante", "Charles"]

known_encodings = []
for img in ref_images:
    encoding = face_recognition.face_encodings(img)
    if encoding:
        known_encodings.append(encoding[0])

cap = cv2.VideoCapture(1)

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
        if True in matches:
            name = names[matches.index(True)]

        
        top, right, bottom, left = top*4, right*4, bottom*4, left*4

        cv2.putText(frame, name, (left, bottom + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    cv2.imshow("Face Detection", frame)
    if cv2.waitKey(1) == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()