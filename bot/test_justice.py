"""تست پنل دادگاه — seed داده و ساخت متن پنل/تاریخچه/در جریان."""
import os
import tempfile
from datetime import datetime, timedelta

os.environ["BOT_TOKEN"] = "dummy-for-test"
tmpdb = os.path.join(tempfile.gettempdir(), "tgbot_justice_test.db")
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(tmpdb + suffix):
        os.remove(tmpdb + suffix)
os.environ["DB_PATH"] = tmpdb

PAST = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
FUTURE = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")

import bot
from bot import db

db.upsert_member(111, "a", "متهم اول", 25)
db.upsert_member(222, "b", "متهم دوم", 30)

# ۸ دادگاه بسته‌شده + ۱ باز
for i in range(8):
    jid = db.create_judgment(-100, 1000 + i * 100, f"عضو{i + 1}", 1, "ادمین", f"دلیل {i + 1}", PAST)
    for v in ("strike", "strike", "chance", "silence" if False else "chance"):
        db.upsert_vote(jid, 5000 + v.count("s") + i, f"user{i}", v)
    db.close_judgment(jid, "strike" if i % 2 == 0 else "chance")
jid_open = db.create_judgment(-100, 222, "متهم دوم", 1, "ادمین", "توهین", FUTURE)
db.set_judgment_message(jid_open, 999)
db.upsert_vote(jid_open, 6001, "n1", "strike")
db.upsert_vote(jid_open, 6002, "n2", "chance")

print("counts →", db.count_judgments(), "total |", db.count_open_judgments(), "open")
print("winners →", db.judgment_winner_stats())

text, kb = bot.build_justice_panel()
print("\n=== پنل دادگاه ===\n" + text)

text, kb = bot.build_justice_history(0)
print("\n=== تاریخچه صفحه ۱ ===\n" + text)
assert "صفحه ۱ از ۲" in text

text, kb = bot.build_justice_history(1)
print("\n=== تاریخچه صفحه ۲ ===\n" + text)
assert "صفحه ۲ از ۲" in text

text, kb = bot.build_justice_open()
print("\n=== دادگاه‌های باز ===\n" + text)
assert "متهم دوم" in text

rows = db.list_judgments_for(100000, 10)  # ناموجود
print("\nدادگاه عضو ناموجود:", len(rows))
print("VOTES_MAP:", db.votes_map([jid_open]))
print("\n✅ JUSTICE PANEL TESTS OK")
