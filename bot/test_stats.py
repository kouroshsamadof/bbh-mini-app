"""تست انتگرال سیستم آمار اعضا — بدون تلگرام (context ساختگی)."""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ["BOT_TOKEN"] = "dummy-for-test"
tmpdb = os.path.join(tempfile.gettempdir(), "tgbot_stats_test.db")
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(tmpdb + suffix):
        os.remove(tmpdb + suffix)
os.environ["DB_PATH"] = tmpdb

PAST = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")

import bot
from bot import db


class FakeBot:
    async def edit_message_text(self, **kwargs):
        print("  [TEST] EDIT:", kwargs.get("text", "").splitlines()[0][:70])

    async def send_message(self, **kwargs):
        print("  [TEST] SEND:", kwargs.get("text", "").splitlines()[0][:70])

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="member")


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()
        self.job_queue = SimpleNamespace(run_once=lambda *a, **kw: print(f"  [TEST] job: {kw.get('name')}"))


class FakeContext:
    def __init__(self):
        self.application = FakeApp()
        self.bot = self.application.bot


async def main():
    print("== seed ==")
    db.upsert_member(111, "sina", "سینا", 24)
    db.upsert_member(222, "ali", "علی", 30)
    db.upsert_member(333, "reza", "رضا", 28)

    print("== نمای آمار ==")
    view = bot.build_stats_view("سینا", db.get_stats(111))
    print(view)
    assert "IQ" in view and "COMEDY" in view

    print("== adjust_stat و کلپ ==")
    for _ in range(17):
        v = db.adjust_stat(111, "iq", 5)
    assert v == 85
    for _ in range(10):
        v = db.adjust_stat(111, "iq", 5)
    assert v == 100, v                      # سقف
    for _ in range(30):
        v = db.adjust_stat(111, "iq", -5)
    assert v == 0, v                        # کف
    print("clamp OK (100 -> 0)")

    print("== پیشنهاد آمار: تایید اکثریت ==")
    ctx = FakeContext()
    pid = db.create_stat_proposal(-100, 222, "علی", 1, "ادمین", "iq", "give", PAST)
    db.set_proposal_message(pid, 777)
    db.upsert_stat_vote(pid, 9001, "approve")
    db.upsert_stat_vote(pid, 9002, "approve")
    db.upsert_stat_vote(pid, 9003, "reject")
    await bot.finalize_stat_proposal(ctx, pid)
    assert db.get_stats(222)["iq"] == 5
    print("applied (+5) OK")

    print("== پیشنهاد آمار: رد اکثریت ==")
    pid = db.create_stat_proposal(-100, 222, "علی", 1, "ادمین", "comedy", "give", PAST)
    db.upsert_stat_vote(pid, 9001, "reject")
    db.upsert_stat_vote(pid, 9002, "reject")
    db.upsert_stat_vote(pid, 9003, "approve")
    await bot.finalize_stat_proposal(ctx, pid)
    assert db.get_stats(222)["comedy"] == 0
    print("rejected OK")

    print("== پیشنهاد آمار: مساوی → اعمال نمی‌شود ==")
    pid = db.create_stat_proposal(-100, 222, "علی", 1, "ادمین", "simp", "give", PAST)
    db.upsert_stat_vote(pid, 9001, "approve")
    db.upsert_stat_vote(pid, 9002, "reject")
    await bot.finalize_stat_proposal(ctx, pid)
    assert db.get_stats(222)["simp"] == 0
    print("tie rejected OK")

    print("== take back با اکثریت ==")
    db.adjust_stat(333, "comedy", 30)  # 30٪
    pid = db.create_stat_proposal(-100, 333, "رضا", 1, "ادمین", "comedy", "take", PAST)
    db.upsert_stat_vote(pid, 9001, "approve")
    db.upsert_stat_vote(pid, 9002, "approve")
    db.upsert_stat_vote(pid, 9003, "approve")
    db.upsert_stat_vote(pid, 9004, "reject")
    await bot.finalize_stat_proposal(ctx, pid)
    assert db.get_stats(333)["comedy"] == 25, db.get_stats(333)["comedy"]
    print("take back (-5) OK")

    print("== رأی‌گیری باز برای هر عضو+آمار فقط یکی ==")
    existing = None
    # شبیه‌سازی چک تکراری
    db.create_stat_proposal(-100, 333, "رضا", 1, "ادمین", "iq", "give", (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds"))
    existing = db.open_proposal_for(-100, 333, "iq")
    assert existing is not None
    print("duplicate check OK")

    print("== آمار کلی اعضا ==")
    text, kb = bot.build_all_stats(0)
    print(text)
    assert "سینا" in text and "آی" not in text
    text0, kb0 = bot.build_stats_panel()
    print("\n" + text0)
    assert "اعضای ثبت‌شده" in text0
    print("\n✅ همه‌ی تست‌های آمار قبول شد!")


asyncio.run(main())
