"""تست توانایی شخصی — روی کپی از دیتابیس واقعی."""
import os
import sqlite3
import tempfile

os.environ["BOT_TOKEN"] = "dummy-for-test"
_src = "C:/Users/sabas/telegram-bot/bot_data.db"
tmpdb = os.path.join(tempfile.gettempdir(), "tgbot_ability_test.db")
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(tmpdb + suffix):
        os.remove(tmpdb + suffix)

with sqlite3.connect(_src) as s, sqlite3.connect(tmpdb) as d:
    s.backup(d)
os.environ["DB_PATH"] = tmpdb

import bot
from bot import db

members = db.list_members(limit=4, offset=0)
print("found members:", [(m["user_id"], m["full_name"]) for m in members])
assert members, "اعضا یافت نشدند"

target = members[0]
print("\n== تست توانایی برای", target["full_name"], "==")

db.set_ability(target["user_id"], "سریع مثل صاعقه")
ability = db.get_ability(target["user_id"])
assert ability == "سریع مثل صاعقه"
view = bot.build_stats_view(target["full_name"], db.get_stats(target["user_id"]), ability)
print(view)
assert "🌟 توانایی: «سریع مثل صاعقه»" in view.splitlines()[1]
print("ability line is first ✓")

print("\n== abilities_map (همه‌ی اعضا) ==")
ids = [m["user_id"] for m in members]
mapping = db.abilities_map(ids)
print(mapping)
assert mapping[target["user_id"]] == "سریع مثل صاعقه"
print("abilities_map OK")

print("\n== تغییر توانایی ==")
db.set_ability(target["user_id"], "بازیکن زرنگ")
assert db.get_ability(target["user_id"]) == "بازیکن زرنگ"

print("\n== آمار کلی با توانایی ==")
text, kb = bot.build_all_stats(0)
print(text)
assert "سریع" not in text and "بازیکن زرنگ" in text

print("\n✅ همه‌ی تست‌های توانایی قبول شد!")
