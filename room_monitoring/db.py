import mysql.connector

def get_connection():
    return mysql.connector.connect(
        host='localhost',
        #user='admin',
        #password='scs123',
        user='root',
        password='root',
        port=3307,
        database='motion_monitor'
    )