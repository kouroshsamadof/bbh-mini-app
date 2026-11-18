"""تست انتگرال منطق دادگاه — بدون نیاز به تلگرام (context ساختگی)."""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ["BOT_TOKEN"] = "dummy-for-test"
tmpdb = os.path.join(tempfile.gettempdir(), "tgbot_court_test.db")
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(tmpdb + suffix):
        os.remove(tmpdb + suffix)
os.environ["DB_PATH"] = tmpdb

import bot
from bot import Target, db

PAST = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")


class FakeBot:
    def __init__(self):
        self.console = []

    async def edit_message_text(self, **kwargs):
        self.console.append(("EDIT", kwargs.get("text", "")))

    async def send_message(self, **kwargs):
        self.console.append(("SEND", kwargs.get("text", "")))

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="member")

    async def ban_chat_member(self, chat_id, user_id):
        print(f"  [TEST] ban_chat_member {user_id}")

    async def restrict_chat_member(self, chat_id, user_id, **kw):
        print(f"  [TEST] restrict_chat_member {user_id} until={kw.get('until_date')}")


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()
        self.job_queue = SimpleNamespace(run_once=lambda *a, **kw: print(f"  [TEST] job scheduled: {kw.get('name')}"))


class FakeContext:
    def __init__(self):
        self.application = FakeApp()
        self.bot = self.application.bot


def make_judgment(target_id, name, chat_id=-100, admin="admin1"):
    jid = db.create_judgment(chat_id=chat_id, target_id=target_id, target_name=name,
                             admin_id=1, admin_name=admin, reason="تست", closes_at=PAST)
    db.set_judgment_message(jid, 555)
    return jid


def cast(jid, voters):
    for uid, vote in voters.items():
        db.upsert_vote(jid, uid, f"user{uid}", vote)


async def main():
    ctx = FakeContext()

    # --- سناریوی ۱: استرایک رأی بیاورد (۳ استرایک vs ۲ فرصت) ---
    print("سناریو ۱: استرایک برنده می‌شود")
    jid = make_judgment(111, "متهم‌اول")
    cast(jid, {201: "strike", 202: "strike", 203: "strike", 204: "chance", 205: "chance"})
    await bot.finalize_judgment(ctx, jid)
    j = db.get_judgment(jid)
    strikes_recorded = db.raw_counts(111)[1]
    print(f"  winner={j['winner']} | استرایک‌های ثبت‌شده={strikes_recorded}")
    assert j["winner"] == "strike" and strikes_recorded == 1

    # --- سناریوی ۲: مساوی اول → lastcall → مساوی دوباره → استرایک ---
    print("سناریو ۲: مساوی → آخرین فرصت → مساوی دوباره → استرایک")
    jid = make_judgment(222, "متهم‌دوم")
    cast(jid, {301: "strike", 302: "strike", 303: "chance", 304: "chance"})
    await bot.finalize_judgment(ctx, jid)
    j = db.get_judgment(jid)
    print(f"  بعد از مساوی اول: status={j['status']} (باید lastcall باشد)")
    assert j["status"] == "lastcall"
    assert db.raw_counts(222)[1] == 0  # هنوز استرایک ثبت نشده
    # زمان بسته شدن را به گذشته برمی‌گردانیم (شبیه‌سازی پایان آخرین فرصت)
    with db._lock, db._conn:
        db._conn.execute("UPDATE judgments SET closes_at = ? WHERE id = ?", (PAST, jid))
    await bot.finalize_judgment(ctx, jid)
    j = db.get_judgment(jid)
    strikes_recorded = db.raw_counts(222)[1]
    print(f"  بعد از مساوی دوباره: winner={j['winner']} | استرایک={strikes_recorded}")
    assert j["winner"] == "strike" and strikes_recorded == 1

    # --- سناریوی ۳: فرصت دیگر برنده می‌شود ---
    print("سناریو ۳: فرصت دوباره برنده می‌شود")
    jid = make_judgment(333, "متهم‌سوم")
    cast(jid, {401: "chance", 402: "chance", 403: "chance", 404: "strike"})
    await bot.finalize_judgment(ctx, jid)
    j = db.get_judgment(jid)
    strikes_recorded = db.raw_counts(333)[1]
    print(f"  winner={j['winner']} | استرایک={strikes_recorded}")
    assert j["winner"] == "chance" and strikes_recorded == 0

    # --- سناریوی ۴: تغییر رأی (اصلاح) ---
    print("سناریو ۴: تغییر رأی یک نفر")
    jid = make_judgment(444, "متهم‌چهارم")
    db.upsert_vote(jid, 501, "user501", "strike")
    db.upsert_vote(jid, 501, "user501", "chance")  # رأی‌اش عوض شد
    counts = db.vote_counts(jid)
    print(f"  strike={counts[0]}, chance={counts[1]}, total={counts[2]}")
    assert counts == (0, 1, 1)

    # --- سناریوهای مجازات: ۳ استرایک → میوت، استرایک‌های باقی‌مانده شمرده می‌شوند ---
    print("سناریو ۵: مجازات بعد از ۲ رأی استرایک قبلی")
    for _ in range(2):
        if False:
            pass
    # ۲ استرایک قبلی ثبت کن (مستقیم در دیتابیس)
    db.add_point(555, "متهم‌پنجم", -100, 1, "admin", "strike", "s1")
    db.add_point(555, "متهم‌پنجم", -100, 1, "admin", "strike", "s2")
    jid = make_judgment(555, "متهم‌پنجم")
    cast(jid, {601: "strike", 602: "strike", 603: "chance"})
    await bot.finalize_judgment(ctx, jid)
    total_strikes = db.raw_counts(555)[1]
    s = db.summary_for(555)
    print(f"  استرایک کل={total_strikes} | باقی‌مانده={s['strikes_remaining']}")
    assert total_strikes == 3 and s["strikes_remaining"] == 3

    print("\n✅ همه‌ی تست‌های دادگاه قبول شد!")


asyncio.run(main())
