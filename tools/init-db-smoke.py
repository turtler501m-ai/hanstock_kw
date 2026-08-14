from src.db.repository import connect_db, init_db
from src.dashboard import _init_approval_db

try:
    init_db()
    _init_approval_db()
    print("DB init success!")
except Exception as e:
    print(f"Error: {e}")
