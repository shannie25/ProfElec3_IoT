from datetime import time
import re
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from db import get_connection

FALLBACK_SCHEDULE = [
    (time(9, 20),  time(13, 0)),
    (time(14, 50), time(15, 5)),
    (time(15, 55), time(16, 30)),
    (time(16, 35), time(16, 40)),
]

# Matches whole day tokens (Th, Su checked before the single-letter class so
# they aren't split into e.g. 'T'+'h'). Without this, a raw substring check
# like "'S' in days" would wrongly match "Su" (Sunday) on Saturdays, and
# "'T' in days" would wrongly match "Th"-only (Thursday) rows on Tuesdays.
DAY_TOKEN_RE = re.compile(r'Th|Su|[MTWFS]', re.IGNORECASE)

def day_tokens(days_str):
    return {m.group(0).capitalize() for m in DAY_TOKEN_RE.finditer(days_str or '')}

def get_class_periods_for_today(class_date, room_name='IC1004'):
    day_names = {
        0: 'M', 1: 'T', 2: 'W',
        3: 'Th', 4: 'F', 5: 'S', 6: 'Su',
    }
    day_code = day_names.get(class_date.weekday(), '')

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT days, start_time, end_time FROM courses
            WHERE room_name = %s
        """, (room_name,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        periods = []
        for days, start, end in rows:
            if day_code not in day_tokens(days):
                continue
            if hasattr(start, 'seconds'):
                h, m = divmod(start.seconds // 60, 60)
                start = time(h, m)
            if hasattr(end, 'seconds'):
                h, m = divmod(end.seconds // 60, 60)
                end = time(h, m)
            periods.append((start, end))

        if periods:
            print(f"[Schedule] {len(periods)} period(s) for {room_name} ({day_code})")
            return periods
        else:
            print(f"[Schedule] No DB schedule for {room_name} — using fallback")
            return FALLBACK_SCHEDULE

    except Exception as e:
        print(f"[Schedule] DB error: {e} — using fallback")
        return FALLBACK_SCHEDULE