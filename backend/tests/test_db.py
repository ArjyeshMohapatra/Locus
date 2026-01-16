"""Simple script to test DB interactions without running the full API server"""

from app.database import models, crud
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import datetime

# Setup test DB
DATABASE_URL = "sqlite:///./locus_test.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
models.Base.metadata.create_all(bind=engine)


def test_db():
    db = SessionLocal()
    print("--- Testing Database ---")

    # 1. Add watched path
    print("Adding watched path...")
    try:
        path = crud.create_watched_path(db, "C:/Users/Test/Documents")
        print(f"✅ Created path: {path.path}")
    except Exception as e:
        print(f"⚠️  Path might already exist: {e}")

    # 2. Log activity
    print("Logging activity...")
    log = crud.log_activity(db, "app_focus", "code.exe", "Editing main.py")
    print(f"✅ Logged: {log.app_name} at {log.start_time}")

    # 3. Read back
    print("Reading timeline...")
    items = crud.get_activity_timeline(db)
    print(f"✅ Found {len(items)} items in timeline.")

    db.close()
    print("--- Test Complete ---")


if __name__ == "__main__":
    test_db()
