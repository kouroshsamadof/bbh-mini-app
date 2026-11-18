"""تست رتبه‌بندی امتیاز و آمار — روی کپی از دیتابیس."""
import os
import sqlite3
import tempfile

os.environ["BOT_TOKEN"] = "dummy-for-test"
_src = "C:/Users/sabas/telegram-bot/bot_data.db"
tmpdb = os.path.join(tempfile.gettempdir(), "tgbot_rank_test.db")
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(tmpdb + suffix):
        os.remove(tmpdb + suffix)

with sqlite3.connect(_src) as s, sqlite3.connect(tmpdb) as d:
    s.backup(d)
os.environ["DB_PATH"] = tmpdb

import bot
from bot import db

members = db.list_members(limit=10, offset=0)
print("members:", [(m["user_id"], m["full_name"]) for m in members])

# امتیازها: کاربر اول ۳ پلاس، دومی ۱ استرایک، سومی ۵ پلاس
m = members
db.add_point(m[0]["user_id"], m[0]["full_name"], -100, 1, "ادمین", "plus", "t1")
db.add_point(m[0]["user_id"], m[0]["full_name"], -100, 1, "ادمین", "plus", "t2")
db.add_point(m[0]["user_id"], m[0]["full_name"], -100, 1, "ادمین", "plus", "t3")
db.add_point(m[1]["user_id"], m[1]["full_name"], -100, 1, "ادمین", "strike", "t4")
db.add_point(m[2]["user_id"], m[2]["full_name"], -100, 1, "ادمین", "plus", "t5")
db.add_point(m[2]["user_id"], m[2]["full_name"], -100, 1, "ادمین", "plus", "t6")
db.add_point(m[2]["user_id"], m[2]["full_name"], -100, 1, "ادمین", "plus", "t7")
db.add_point(m[2]["user_id"], m[2]["full_name"], -100, 1, "ادمین", "plus", "t8")
db.add_point(m[2]["user_id"], m[2]["full_name"], -100, 1, "ادمین", "plus", "t9")

# آمارها: نفر اول همه‌ی آمارها ۸۰، نفر سوم IQ ۱۰۰ و بقیه ۰، نفر چهارم ۵۰ همه‌چیز
for stat in ("iq", "comedy", "simp", "retardness"):
    db.adjust_stat(m[0]["user_id"], stat, 80)

db.adjust_stat(m[2]["user_id"], "iq", 100)
for stat in ("iq", "comedy", "simp", "retardness"):
    db.adjust_stat(m[3]["user_id"], stat, 50)

print("\n== رتبه‌بندی امتیاز ==")
print(bot.build_points_ranking(m[0]["user_id"]))

print("\n== رتبه‌بندی آمار ==")
print(bot.build_stats_ranking(m[0]["user_id"]))

rank_rows = db.stats_ranking()
assert rank_rows[0]["avg"] >= rank_rows[1]["avg"] >= rank_rows[2]["avg"], "sort failed"
assert rank_rows[0]["full_name"] == m[0]["full_name"]
print("\nstats ranking sorted OK")

print("\n== پنل رتبه‌بندی ==")
text, kb = bot.build_ranking_panel()
print(text)
print("\n✅ همه‌ی تست‌های رتبه‌بندی قبول شد!")
