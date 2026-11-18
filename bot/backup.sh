#!/bin/sh
# بک‌آپ خودکار دیتابیس ربات (cron: روزانه ۰ ۳ صبح — python3 backup.py)
cd "$(dirname "$0")"
exec python3 backup.py
