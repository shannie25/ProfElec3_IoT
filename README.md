# ProfElec3_IoT — SENTRY Room Monitor

SENTRY is a classroom and room monitoring project that combines a browser-based
admin dashboard with Python camera services. It can display camera streams,
detect motion, compare activity against room schedules, save screenshots of
out-of-schedule motion, and support administrator sign-in using an ID and
password or face recognition.

## Features

- Express web app for the login page, dashboard, activity logs, and settings.
- Python camera server with live MJPEG streams and room motion monitoring.
- Schedule-aware motion events and screenshots for activity outside class time.
- Face-recognition login using registered administrator face encodings.
- MySQL storage for rooms, schedules, administrators, motion events, and login
  events.

## Project layout

```text
ProfElec3_IoT/
├── app.py
├── facedetector.py
├── motiondetector.py
├── schedule_provider.py
├── admin/
│   └── Student*.jpg
└── room_monitoring/
    ├── camera_server.py
    ├── dashboard.html
    ├── db.py
    ├── facedetector.py
    ├── login.html
    ├── logs.html
    ├── motiondetector.py
    ├── package.json
    ├── register_admin.py
    ├── schedule_provider.py
    ├── server.js
    ├── settings.html
    └── assets/
        └── Student*.jpg
```

The `room_monitoring/` directory contains the integrated Express and Python
camera application. The scripts at the project root and under `try/` are
standalone or experimental versions.

## Requirements

- Windows 10/11
- Node.js and npm
- Python 3.11
- MySQL, with the database and schema expected by the application
- A built-in webcam or supported room camera
- Python packages used by the camera server:
  - `Flask`
  - `opencv-python`
  - `numpy`
  - `mysql-connector-python`
  - `Pillow`
  - `face-recognition`

`face-recognition` depends on `dlib`. On Windows, installing `dlib` from source
may require C++ build tools.

## Configuration

### Database

The integrated app's database settings are in `room_monitoring/db.py`. The
Express connection pool in `room_monitoring/server.js` uses the same local
database configuration. Configure both to match your MySQL installation before
starting the app.

The application expects a database named `motion_monitor` by default, with
tables for:

- `rooms` — room ID, room name, and camera source.
- `courses` — course code, meeting days, start/end times, and room name.
- `admins` — administrator name, ID, password hash, and face encoding.
- `motion_logs` — room, motion status, schedule status, screenshot path, and
  timestamp.
- `login_logs` — administrator, login method, success status, and timestamp.

This repository version does not include a database migration or schema
creation script. Create and configure the database before relying on
database-backed login, room-camera discovery, schedules, or event logging.

### Cameras

The integrated camera server reads room camera sources from the `rooms` table.
For face-login video, `ACCESS_CAMERA_SOURCE` selects the webcam device index;
it defaults to `1` in the provided version. Override it in PowerShell when
needed:

```powershell
$env:ACCESS_CAMERA_SOURCE = "0"
```

Camera indexes depend on Windows device ordering. Check which index corresponds
to the intended webcam on the machine running the Python server.

## Install dependencies

From PowerShell:

```powershell
cd .\room_monitoring
npm ci
py -3.11 -m pip install Flask opencv-python numpy mysql-connector-python Pillow face-recognition
```

If the `face-recognition` install fails while building `dlib`, install the
appropriate Visual C++ build tools, then retry.

## Run the integrated application

Start the Python camera server in one PowerShell window:

```powershell
cd .\room_monitoring
py -3.11 camera_server.py
```

Start the Express web server in a second PowerShell window:

```powershell
cd .\room_monitoring
npm start
```

Open the login page at **http://localhost:3000/login**. The Express app serves
the site on port `3000`; the Python camera server listens on port `5001` and
provides camera streams through the web app.

Run `register_admin.py` from `room_monitoring/` after the database is ready to
register the sample administrator images in `room_monitoring/assets/`. Review
the script before running it and replace its sample administrator details with
the intended accounts.

## Standalone scripts

The project-root scripts `motiondetector.py` and `facedetector.py` run separate
OpenCV windows and are not required when using the integrated web application.
They require a camera and their own Python dependencies. The `try/` directory
contains experimental scripts.

## Notes

- Keep administrator face images and encodings private, and only register
  people who have consented.
- Do not expose the development servers directly to the public internet.
- Configure database credentials for your own environment; do not commit
  passwords or other secrets to GitHub.
