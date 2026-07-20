/**
 * SENTRY Motion Monitor — Express Backend
 * =========================================
 * Serves HTML pages, API routes, proxies camera streams from Python.
 *
 * SETUP:
 *   npm install
 *   node server.js
 *
 * REQUIRES Python camera server also running:
 *   py -3.11 camera_server.py   (in separate terminal)
 *
 * OPEN BROWSER:
 *   http://localhost:3000
 */

const express    = require('express')
const session    = require('express-session')
const mysql      = require('mysql2/promise')
const multer     = require('multer')
const csv        = require('csv-parser')
const http       = require('http')
const path       = require('path')
const fs         = require('fs')
const { Readable } = require('stream')

const app    = express()
const upload = multer({ storage: multer.memoryStorage() })

// ── Config ────────────────────────────────────────────────────────
const PORT         = 3000
const PYTHON_HOST  = process.env.PYTHON_HOST || 'localhost'   // where camera_server.py runs — set to the Pi's IP if it's on a different device
const PYTHON_PORT  = 5001

// ── MariaDB connection pool ───────────────────────────────────────
const db = mysql.createPool({
    host:     'localhost',
    user:     'root',
    password: 'root',
    port:     3307,          // laptop MariaDB port
    database: 'motion_monitor',
    waitForConnections: true,
    connectionLimit: 10,
})

// Test DB on startup
db.getConnection()
    .then(conn => {
        console.log('MariaDB connected')
        conn.release()
    })
    .catch(err => console.error('MariaDB error:', err.message))

// ── Middleware ────────────────────────────────────────────────────
app.use(express.json())
// Only expose actual public assets — NOT the whole project root (that used
// to also serve server.js, db.py, and every .html file by raw filename,
// which bypassed any auth check on the routes below).
app.use('/assets',      express.static(path.join(__dirname, 'assets')))
app.use('/screenshots', express.static(path.join(__dirname, 'screenshots')))

app.use(session({
    secret: process.env.SESSION_SECRET || 'sentry-dev-secret-change-me',
    resave: false,
    saveUninitialized: false,
    cookie: {
        httpOnly: true,
        maxAge: 8 * 60 * 60 * 1000,   // 8 hours
    },
}))

// Blocks page/API access for anyone without a logged-in session.
function requireAuth(req, res, next) {
    if (req.session && req.session.admin) return next()
    if (req.path.startsWith('/api/') || req.path.startsWith('/stream/')) {
        return res.status(401).json({ error: 'Not authenticated' })
    }
    return res.redirect('/login')
}

// ── HTML PAGE ROUTES ──────────────────────────────────────────────
app.get('/',          (req, res) => res.redirect('/login'))
app.get('/login',     (req, res) => res.sendFile(path.join(__dirname, 'login.html')))
app.get('/dashboard', requireAuth, (req, res) => res.sendFile(path.join(__dirname, 'dashboard.html')))
app.get('/logs',      requireAuth, (req, res) => res.sendFile(path.join(__dirname, 'logs.html')))
app.get('/settings',  requireAuth, (req, res) => res.sendFile(path.join(__dirname, 'settings.html')))

// ── CAMERA STREAM PROXY ───────────────────────────────────────────
// Browser requests /stream/cam1 → Express forwards to Python:5001/stream/cam1
// ── Clean stream proxy (no motion overlay — for login page) ──
app.get('/stream/clean/:camId', (req, res) => {
    const camId = req.params.camId
    const options = {
        hostname: PYTHON_HOST,
        port:     PYTHON_PORT,
        path:     `/stream/clean/${camId}`,
        method:   'GET',
    }
    const proxy = http.request(options, (pyRes) => {
        res.setHeader('Content-Type', pyRes.headers['content-type'] || 'multipart/x-mixed-replace; boundary=frame')
        res.setHeader('Cache-Control', 'no-cache')
        pyRes.pipe(res)
    })
    proxy.on('error', (err) => {
        console.error(`[Clean stream ${camId}] Python server not reachable:`, err.message)
        if (res.headersSent) return res.destroy(err)
        res.status(503).json({ error: 'Camera offline' })
    })
    proxy.end()
})

app.get('/stream/:camId', requireAuth, (req, res) => {
    const camId = req.params.camId

    const options = {
        hostname: PYTHON_HOST,
        port:     PYTHON_PORT,
        path:     `/stream/${camId}`,
        method:   'GET',
    }

    const proxy = http.request(options, (pyRes) => {
        res.setHeader('Content-Type', pyRes.headers['content-type'] || 'multipart/x-mixed-replace; boundary=frame')
        res.setHeader('Cache-Control', 'no-cache')
        pyRes.pipe(res)
    })

    proxy.on('error', (err) => {
        console.error(`[Stream ${camId}] Python server not reachable:`, err.message)
        // Do not send JSON after a streamed response has sent its headers.
        if (res.headersSent) return res.destroy(err)
        res.status(503).json({ error: `Camera ${camId} unavailable — is camera_server.py running?` })
    })

    proxy.end()
})

// ── API: FACE LOGIN ───────────────────────────────────────────────
// Express forwards the request to Python which does the actual face recognition
app.post('/api/face-login', async (req, res) => {
    try {
        const options = {
            hostname: PYTHON_HOST,
            port:     PYTHON_PORT,
            path:     '/api/face-login',
            method:   'POST',
            headers:  { 'Content-Type': 'application/json' },
        }

        const proxy = http.request(options, (pyRes) => {
            let data = ''
            pyRes.on('data', chunk => data += chunk)
            pyRes.on('end', () => {
                try {
                    const parsed = JSON.parse(data)
                    if (parsed.recognized) {
                        req.session.admin = { name: parsed.name }
                    }
                    res.status(pyRes.statusCode).json(parsed)
                } catch {
                    res.status(500).json({ error: 'Python response parse error' })
                }
            })
        })

        proxy.on('error', () => {
            res.status(503).json({
                recognized: false,
                message: 'Camera server not running — start camera_server.py'
            })
        })

        proxy.end()

    } catch (err) {
        res.status(500).json({ error: err.message })
    }
})

// ── API: ID LOGIN ─────────────────────────────────────────────────
const crypto = require('crypto')

app.post('/api/id-login', async (req, res) => {
    const { id_number, password } = req.body

    if (!id_number || !password) {
        return res.status(400).json({ success: false, message: 'Fill in both fields' })
    }

    try {
        const passwordHash = crypto
            .createHash('sha256')
            .update(password)
            .digest('hex')

        const [rows] = await db.execute(
            'SELECT admin_id, full_name FROM admins WHERE id_number = ? AND password_hash = ?',
            [id_number, passwordHash]
        )

        const admin   = rows[0]
        const success = !!admin

        await db.execute(
            "INSERT INTO login_logs (admin_id, method, success) VALUES (?, 'id_backup', ?)",
            [admin?.admin_id || null, success]
        )

        if (success) {
            req.session.admin = { id: admin.admin_id, name: admin.full_name }
            return res.json({ success: true, name: admin.full_name, adminName: admin.full_name })
        }
        res.status(401).json({ success: false, message: 'Invalid ID or password' })

    } catch (err) {
        console.error('[ID Login]', err.message)
        res.status(500).json({ error: err.message })
    }
})

// ── API: MOTION EVENTS ────────────────────────────────────────────
app.get('/api/events', requireAuth, async (req, res) => {
    const limit = parseInt(req.query.limit) || 100

    try {
        const [rows] = await db.execute(`
            SELECT ml.log_id, ml.status, ml.is_within_schedule,
                   ml.screenshot_path, ml.timestamp, r.room_name
            FROM motion_logs ml
            JOIN rooms r ON ml.room_id = r.room_id
            ORDER BY ml.timestamp DESC
            LIMIT ?
        `, [limit])

        const events = rows.map(row => ({
            id:              row.log_id,
            type:            row.status,
            status:          row.is_within_schedule ? 'expected' : 'anomaly',
            camera:          row.room_name,
            time:            new Date(row.timestamp).toLocaleString(),
            screenshot:      row.screenshot_path || '',
            persons:         row.status === 'motion_detected' ? 1 : 0,
            within_schedule: row.is_within_schedule,
        }))

        res.json(events)

    } catch (err) {
        console.error('[Events]', err.message)
        res.status(500).json({ error: err.message })
    }
})

// ── API: STATS ────────────────────────────────────────────────────
app.get('/api/stats', requireAuth, async (req, res) => {
    try {
        const [[motionRow]]   = await db.execute(`
            SELECT COUNT(*) AS count FROM motion_logs
            WHERE status = 'motion_detected' AND DATE(timestamp) = CURDATE()
        `)
        const [[noMotionRow]] = await db.execute(`
            SELECT COUNT(*) AS count FROM motion_logs
            WHERE status = 'no_motion' AND DATE(timestamp) = CURDATE()
        `)
        const [[alertsRow]]   = await db.execute(`
            SELECT COUNT(*) AS count FROM motion_logs
            WHERE status = 'motion_detected'
            AND is_within_schedule = FALSE
            AND DATE(timestamp) = CURDATE()
        `)
        const [[lastRow]]     = await db.execute(`
            SELECT status FROM motion_logs ORDER BY timestamp DESC LIMIT 1
        `)

        res.json({
            motion_today:    motionRow.count,
            no_motion_today: noMotionRow.count,
            alerts_today:    alertsRow.count,
            last_status:     lastRow?.status || 'no_motion',
        })

    } catch (err) {
        console.error('[Stats]', err.message)
        res.status(500).json({ error: err.message })
    }
})

// ── API: UPLOAD SCHEDULE (CSV) ────────────────────────────────────
// Expected columns (order doesn't matter; underscores/spaces/case don't
// either — "Class_Date", "Class Date", and "class date" all match):
// Class_Date, Start_Time, End_Time, Room Name, Course_Code
// Times may be 12-hour with a hyphen or space before AM/PM, e.g.
// "07:00:00-A.M" or "09:00:00 A.M" — converted here to 24-hour "HH:MM:SS".
function normalizeHeader(key) {
    return key.trim().toLowerCase().replace(/[\s_]+/g, '')
}

function normalizeRow(row) {
    const normalized = {}
    for (const [key, value] of Object.entries(row)) {
        normalized[normalizeHeader(key)] = value
    }
    return normalized
}

function parseClockTime(raw) {
    if (!raw) return null
    const cleaned = raw.trim().replace(/[-\s]+([AaPp]\.?[Mm]\.?)$/, ' $1')
    const match = cleaned.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp])\.?[Mm]\.?$/)
    if (!match) return null

    let hour = parseInt(match[1], 10)
    const minute = parseInt(match[2], 10)
    const second = match[3] ? parseInt(match[3], 10) : 0
    const isPM   = match[4].toUpperCase() === 'P'

    if (hour === 12) hour = isPM ? 12 : 0
    else if (isPM)   hour += 12

    return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}`
}

app.post('/api/upload-schedule', requireAuth, upload.single('file'), async (req, res) => {
    if (!req.file) {
        return res.status(400).json({ error: 'No file uploaded' })
    }
    if (!req.file.originalname.toLowerCase().endsWith('.csv')) {
        return res.status(400).json({ error: 'File must be a .csv' })
    }

    try {
        const results = []
        const errors  = []

        // Parse CSV from buffer — strip a leading UTF-8 BOM if present
        const text = req.file.buffer.toString('utf8').replace(/^﻿/, '')
        await new Promise((resolve, reject) => {
            Readable.from(text)
                .pipe(csv())
                .on('data', row => results.push(row))
                .on('end', resolve)
                .on('error', reject)
        })

        let count = 0
        for (const row of results) {
            const r = normalizeRow(row)
            const days       = r.classdate?.trim()
            const room       = r.roomname?.trim()
            const courseCode = r.coursecode?.trim()
            const rawStart   = r.starttime?.trim()
            const rawEnd     = r.endtime?.trim()

            // Skip fully blank rows (common at the end of exported sheets)
            if (!days && !room && !courseCode && !rawStart && !rawEnd) continue

            if (!days || !room || !courseCode || !rawStart || !rawEnd) {
                errors.push(`Skipped incomplete row: ${JSON.stringify(row)}`)
                continue
            }

            const start_time = parseClockTime(rawStart)
            const end_time   = parseClockTime(rawEnd)

            if (!start_time || !end_time) {
                errors.push(`${courseCode}: could not parse time "${rawStart}" - "${rawEnd}"`)
                continue
            }
            if (end_time <= start_time) {
                errors.push(`${courseCode}: end time (${end_time}) is not after start time (${start_time}) — check for an AM/PM typo in the source CSV`)
                continue
            }

            try {
                await db.execute(`
                    INSERT IGNORE INTO courses
                        (course_code, days, start_time, end_time, room_name)
                    VALUES (?, ?, ?, ?, ?)
                `, [courseCode, days, start_time, end_time, room])
                count++
            } catch (rowErr) {
                errors.push(`${courseCode}: ${rowErr.message}`)
            }
        }

        res.json({ success: true, inserted: count, errors })

    } catch (err) {
        res.status(500).json({ error: err.message })
    }
})

// ── API: CAMERA STATUS ────────────────────────────────────────────
app.get('/api/camera-status', requireAuth, (req, res) => {
    // Check if Python camera server is reachable
    const req2 = http.request(
        { hostname: PYTHON_HOST, port: PYTHON_PORT, path: '/health', method: 'GET' },
        (pyRes) => {
            res.json({ cam1: 'online', cam2: 'online' })
        }
    )
    req2.on('error', () => {
        res.json({ cam1: 'offline', cam2: 'offline' })
    })
    req2.end()
})

// ── API: Get admins list (for settings page) ─────────────────────────
app.get('/api/admins', requireAuth, async (req, res) => {
    try {
        const [rows] = await db.execute(
            'SELECT admin_id, full_name, id_number, created_at FROM admins ORDER BY admin_id'
        )
        const admins = rows.map(r => ({
            admin_id:   r.admin_id,
            full_name:  r.full_name,
            id_number:  r.id_number,
            created_at: new Date(r.created_at).toLocaleString(),
        }))
        res.json(admins)
    } catch (err) {
        res.status(500).json({ error: err.message })
    }
})

// ── API: Clear motion logs ────────────────────────────────────
app.post('/api/clear-logs', requireAuth, async (req, res) => {
    try {
        await db.execute('DELETE FROM motion_logs')
        await db.execute('DELETE FROM login_logs')
        res.json({ success: true, message: 'All logs cleared' })
    } catch (err) {
        res.status(500).json({ error: err.message })
    }
})

// ── API: LOGOUT ───────────────────────────────────────────────────
app.post('/api/logout', (req, res) => {
    if (!req.session) return res.json({ success: true })
    req.session.destroy(() => {
        res.clearCookie('connect.sid')
        res.json({ success: true })
    })
})

// ── START ─────────────────────────────────────────────────────────
app.listen(PORT, () => {
    console.log('='.repeat(50))
    console.log('SENTRY Motion Monitor — Express Server')
    console.log('='.repeat(50))
    console.log(`  Dashboard:  http://localhost:${PORT}`)
    console.log(`  Login:      http://localhost:${PORT}/login`)
    console.log(`  Cam1 feed:  http://localhost:${PORT}/stream/cam1`)
    console.log(`  Cam2 feed:  http://localhost:${PORT}/stream/cam2`)
    console.log(`  Events API: http://localhost:${PORT}/api/events`)
    console.log('='.repeat(50))
    console.log('Run py -3.11 camera_server.py (in another terminal)')
    console.log('='.repeat(50))
})
