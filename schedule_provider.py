from datetime import time


# Temporary fallback while the admin dashboard/database is still being built.
# Replace this function's body with a database query once schedules are ready.
def get_class_periods_for_today(class_date):
    return [
        (time(9, 20), time(13, 0)),
        (time(14, 50), time(15, 5)),
        (time(15, 55), time(16, 30)),
        (time(16, 35), time(16, 40)),
    ]
