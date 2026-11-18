#!/usr/bin/env python3
"""بک‌آپ امنِ دیتابیس ربات — با sqlite3.backup() (شامل داده‌های موجود در -wal).

پشتیبانی از Windows و Linux — هر دو نسخه‌ی backup.bat و backup.sh همین فایل را صدا می‌زنند.
"""
import os
import sqlite3
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "bot_data.db")
BACKUPS = os.path.join(ROOT, "backups")
KEEP_DAYS = 30

os.makedirs(BACKUPS, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
dest = os.path.join(BACKUPS, f"bot_data_{ts}.db")

src = sqlite3.connect(DB)
dst = sqlite3.connect(dest)
with src, dst:
    src.backup(dst)
src.close()
dst.close()

cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
removed = 0
for f in os.listdir(BACKUPS):
    if f.startswith("bot_data_") and f.endswith(".db"):
        p = os.path.join(BACKUPS, f)
        if datetime.fromtimestamp(os.path.getmtime(p)) < cutoff:
            os.remove(p)
            removed += 1

print(f"backup saved: {dest}" + (f" ({removed} old backups removed)" if removed else ""))
