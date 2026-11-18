"""ربات مدیریت امتیاز کانال — کاملاً فارسی

قابلیت‌ها:
  • نگهداری اطلاعات اعضا (نام، سن، یوزرنیم، آیدی)
  • ثبت اخطار (۱-)، استرایک (۳-) و امتیاز مثبت (۱+) توسط ادمین‌ها
  • قانون ۳‌به۱: هر ۳ امتیاز مثبت، ۱ امتیاز منفی را پاک می‌کند
  • گزارش نهایی هر عضو با تفکیک کامل محاسبات
  • تغییر زنده‌ی کلمات دستورات (اخطار/استرایک/امتیاز) با /setword
"""
import logging
import re
import time
from datetime import datetime, timedelta

from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import db as database
import ftext
import scoring

from telegram.request import HTTPXRequest

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
# لاگ‌های httpx/httpcore آدرس کامل API (و حاوی توکن) را چاپ می‌کنند → بی‌صدا می‌کنیم
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

db = database.db

KIND_META = {
    "warn":   {"label": "اخطار",       "emoji": "🟡"},
    "strike": {"label": "استرایک",     "emoji": "🔴"},
    "plus":   {"label": "امتیاز مثبت", "emoji": "🟢"},
}

# وضعیت‌های گفت‌وگوی تعاملی «افزودن عضو»
ASK_TARGET, ASK_NAME, ASK_AGE, ASK_CONFIRM = range(4)
# وضعیت‌های گفت‌وگوی «عضویت خودکار» (/join)
JOIN_NAME, JOIN_AGE = 10, 11

PAGE_SIZE = 8

# دادگاه رأی‌گیری
VOTE_MINUTES = 5            # مدت استاندارد رأی‌گیری
LASTCALL_MINUTES = 2        # مدت «آخرین فرصت تغییر نظر» هنگام مساوی شدن
STAT_VOTE_MINUTES = 5       # مدت رأی‌گیری پیشنهاد آمار


# ---------------------------------------------------------------- ابزارها

def display_name(user) -> str:
    name = (getattr(user, "full_name", None) or "").strip()
    if name:
        return name
    uname = getattr(user, "username", None)
    return f"@{uname}" if uname else str(getattr(user, "id", "?"))


def posted_as_chat(update: Update) -> bool:
    """آیا پیام از طرف خودِ چت (ادمین ناشناس به نام کانال/گروه) منتشر شده؟"""
    msg, chat = update.effective_message, update.effective_chat
    sc = getattr(msg, "sender_chat", None) if msg else None
    return bool(sc and chat and sc.id == chat.id and chat.type in ("channel", "group", "supergroup"))


# کش وضعیت ادمینی برای جلوگیری از صدا زدن API تلگرام روی هر پیام
_admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}
_ADMIN_TTL = 120.0          # ۲ دقیقه
_ADMIN_CACHE_MAX = 512      # سقف اندازه کش


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """ادمین اصلی (کانفیگ) یا هر ادمین همان گروه/کانال — با کش ۲ دقیقه‌ای."""
    user = update.effective_user
    if user:
        uname = (user.username or "").lower()
        if user.id in config.MASTER_ADMIN_IDS:
            return True
        if uname and uname in config.MASTER_ADMIN_USERNAMES:
            return True
    if posted_as_chat(update):
        return True
    chat = update.effective_chat
    if not chat or not user:
        return False
    if chat.type == "private":
        return False
    key = (chat.id, user.id)
    now = time.monotonic()
    hit = _admin_cache.get(key)
    if hit is not None and now - hit[1] < _ADMIN_TTL:
        return hit[0]
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        st = getattr(member.status, "value", str(member.status)).lower()
        ok = st in ("creator", "owner", "administrator")
    except Exception as exc:  # ربات عضو/ادمین آن چت نیست یا خطای شبکه
        logger.warning("get_chat_member failed: %s", exc)
        ok = False
    if len(_admin_cache) >= _ADMIN_CACHE_MAX:
        _admin_cache.clear()
    _admin_cache[key] = (ok, now)
    return ok


async def is_master(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """فقط ادمین‌های اصلیِ تعریف‌شده در کانفیگ (یا پست به نام کانال)."""
    user = update.effective_user
    if user:
        uname = (user.username or "").lower()
        if user.id in config.MASTER_ADMIN_IDS:
            return True
        if uname and uname in config.MASTER_ADMIN_USERNAMES:
            return True
    return posted_as_chat(update)


class Target:
    def __init__(self, user_id: int, name: str, username: str | None, rest: list[str]):
        self.user_id = user_id
        self.name = name
        self.username = username
        self.rest = rest


def resolve_target(msg, tokens: list[str]):
    """هدف دستور را پیدا می‌کند.

    ترتیب اولویت: ریپلای به پیام  ←  منشن متنی (text_mention)  ←  اولین توکن (@یوزرنیم یا آیدی عددی)
    خروجی: (Target, None) یا (None, پیام خطا)
    """
    rest = list(tokens)

    reply = getattr(msg, "reply_to_message", None)
    if reply and getattr(reply, "from_user", None):
        u = reply.from_user
        return Target(u.id, display_name(u), (u.username or "").lower() or None, rest), None

    for ent in (getattr(msg, "entities", None) or []):
        if getattr(ent, "type", "") == "text_mention" and getattr(ent, "user", None):
            u = ent.user
            return Target(u.id, display_name(u), (u.username or "").lower() or None, rest), None

    if not rest:
        return None, "❓ هدف مشخص نیست!\nبه پیام کاربر ریپلای کنید، یا آیدی عددی / @یوزرنیم بنویسید."

    first = rest[0]
    ascii_num = ftext.to_ascii_digits(first)

    if re.fullmatch(r"\d{5,}", ascii_num):
        uid = int(ascii_num)
        m = db.get_member(uid)
        name = m["full_name"] if m else f"کاربر {uid}"
        username = m["username"] if m else None
        return Target(uid, name, username, rest[1:]), None

    if first.startswith("@"):
        uname = first[1:].lower()
        m = db.find_by_username(uname)
        if m:
            return Target(m["user_id"], m["full_name"], m["username"], rest[1:]), None
        return None, (
            f"❌ کاربری با یوزرنیم {first} در دیتابیس نیست.\n"
            f"اول او را ثبت کنید، مثلاً:\n/addmember {first} نام کاربر سن"
        )

    return None, (
        "❓ نتوانستم هدف را تشخیص دهم.\n"
        "روش‌ها: ریپلای به پیام کاربر، یا نوشتن @یوزرنیم، یا آیدی عددی.\n"
        "مثال: /points @example یا ریپلای + /points"
    )


def identity_for(target_id: int, fallback_name: str | None = None):
    """نام نمایشی و رکورد عضو برای یک آیدی — حتی اگر عضو حذف شده باشد."""
    m = db.get_member(target_id)
    if m:
        return m["full_name"], m
    hist = db.history(target_id, 1)
    name = (hist[0]["target_name"] if hist and hist[0]["target_name"] else None) \
        or fallback_name or f"کاربر {target_id}"
    return name, None


def build_report(target_id: int, preferred_name: str | None = None) -> str:
    name, member = identity_for(target_id, preferred_name)
    s = db.summary_for(target_id)

    lines = [f"📊 گزارش امتیازها — «{name}»", f"🆔 آیدی: {ftext.fa_digits(target_id)}"]
    if member:
        if member["username"]:
            lines.append(f"🔗 یوزرنیم: @{member['username']}")
        age = member["age"]
        lines.append(f"🎂 سن: {ftext.fa_digits(age)} سال" if age is not None else "🎂 سن: ثبت نشده")
    lines.append("")
    lines.append(f"🟡 اخطار: {ftext.fa_digits(s['warns'])} بار  →  {ftext.fa_digits(s['warns'] * scoring.WARN_COST)}- منفی  |  باقی‌مانده: {ftext.fa_digits(s['warns_remaining'])}")
    lines.append(f"🔴 استرایک: {ftext.fa_digits(s['strikes'])} بار  →  {ftext.fa_digits(s['strikes'] * scoring.STRIKE_COST)}- منفی  |  باقی‌مانده: {ftext.fa_digits(s['strikes_remaining'])}")
    lines.append(f"🟢 امتیاز مثبت: {ftext.fa_digits(s['pluses'])} بار  →  {ftext.fa_digits(s['pluses'])}+ مثبت")
    lines.append("")
    lines.append(f"➖ پاک‌شده: {ftext.fa_digits(s['strikes_erased'])} استرایک (هر ۵ پلاس) + {ftext.fa_digits(s['warns_erased'])} اخطار (هر ۳ پلاس)")
    lines.append(f"🧮 مثبت: +{ftext.fa_digits(s['positive'])}  |  منفی باقی‌مانده: -{ftext.fa_digits(s['remaining_negative'])}")
    lines.append("")
    if s["final"] > 0:
        lines.append(f"🏁 امتیاز نهایی: +{ftext.fa_digits(s['final'])} ✅")
    elif s["final"] < 0:
        lines.append(f"🏁 امتیاز نهایی: {ftext.fa_digits(s['final'])} ⚠️")
    else:
        lines.append("🏁 امتیاز نهایی: صفر ⚪")
    return "\n".join(lines)


def compact_stats_line(stats: dict) -> str:
    return (
        f"🟡 {ftext.fa_digits(stats['warns'])} اخطار  |  "
        f"🔴 {ftext.fa_digits(stats['strikes'])} استرایک (باقی‌مانده: {ftext.fa_digits(stats['strikes_remaining'])})  |  "
        f"🟢 {ftext.fa_digits(stats['pluses'])} مثبت  |  "
        f"➖ پاک‌شده: {ftext.fa_digits(stats['strikes_erased'])} استرایک + {ftext.fa_digits(stats['warns_erased'])} اخطار  |  "
        f"🏁 نهایی: {'+' if stats['final'] > 0 else ''}{ftext.fa_digits(stats['final'])}"
    )


def main_menu_keyboard(master: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("➕ افزودن عضو", callback_data="menu:add"),
         InlineKeyboardButton("📋 لیست اعضا", callback_data="menu:list:0")],
        [InlineKeyboardButton("📊 گزارش همه", callback_data="menu:all"),
         InlineKeyboardButton("⚖️ دادگاه‌ها", callback_data="menu:justice")],
        [InlineKeyboardButton("📈 آمار اعضا", callback_data="menu:stats"),
         InlineKeyboardButton("🏆 رتبه‌بندی", callback_data="menu:ranking")],
        [InlineKeyboardButton("❓ راهنما", callback_data="menu:help")],
    ]
    if master:
        rows.append([InlineKeyboardButton("⚙️ تنظیم کلمه‌های دستورات", callback_data="menu:words")])
    rows.append([InlineKeyboardButton("✍️ ثبت نام من", callback_data="join:start")])
    rows.append([InlineKeyboardButton("❌ بستن", callback_data="menu:close")])
    return InlineKeyboardMarkup(rows)


JUSTICE_PAGE_SIZE = 5


def _judgment_winner_label(j) -> str:
    if j["status"] in ("open", "lastcall"):
        return "🔴 در جریان"
    if j["winner"] == "strike":
        return "🔨 استرایک"
    return "🟩 فرصت دیگر"


def _judgment_line(i: int, j, votes: dict) -> str:
    try:
        when = ftext.fa_datetime(datetime.fromisoformat(j["created_at"]))
    except ValueError:
        when = j["created_at"]
    reason = f" | 💬 {j['reason']}" if j["reason"] else ""
    admin = f" | 👮 {j['admin_name']}" if j["admin_name"] else ""
    return (
        f"{ftext.fa_digits(i)}. «{j['target_name']}» — 🗓 {when}\n"
        f"   🟥 {ftext.fa_digits(votes['strike'])} — 🟩 {ftext.fa_digits(votes['chance'])}"
        f" | برنده: {_judgment_winner_label(j)}{admin}{reason}"
    )


def build_justice_panel():
    total = db.count_judgments()
    open_now = db.count_open_judgments()
    winners = db.judgment_winner_stats()
    lines = [
        "⚖️ پنل دادگاه",
        "",
        f"کل دادگاه‌های برگزارشده: {ftext.fa_digits(total)}",
        f"🟥 برنده استرایک: {ftext.fa_digits(winners.get('strike', 0))}"
        f"  |  🟩 برنده فرصت دیگر: {ftext.fa_digits(winners.get('chance', 0))}",
        f"🔴 دادگاه‌های در جریان: {ftext.fa_digits(open_now)}",
        "",
        "برای دیدن تاریخچه دکمه‌ی زیر را بزنید:",
    ]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 تاریخچه‌ی دادگاه‌ها", callback_data="menu:justice:hist:0"),
         InlineKeyboardButton(f"🔴 در جریان ({ftext.fa_digits(open_now)})", callback_data="menu:justice:open")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data="menu:back"),
         InlineKeyboardButton("❌ بستن", callback_data="menu:close")],
    ])
    return "\n".join(lines), keyboard


def build_justice_history(page: int):
    total = db.count_judgments()
    if total == 0:
        return "هنوز هیچ دادگاهی برگزار نشده است.", back_keyboard()
    total_pages = (total + JUSTICE_PAGE_SIZE - 1) // JUSTICE_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    rows = db.list_judgments(limit=JUSTICE_PAGE_SIZE, offset=page * JUSTICE_PAGE_SIZE)
    votes = db.votes_map([r["id"] for r in rows])
    lines = [
        f"📋 تاریخچه‌ی دادگاه‌ها — صفحه {ftext.fa_digits(page + 1)} از {ftext.fa_digits(total_pages)}"
        f" (کل: {ftext.fa_digits(total)})",
        "",
    ]
    for i, j in enumerate(rows, start=page * JUSTICE_PAGE_SIZE + 1):
        v = votes.get(j["id"], {"strike": 0, "chance": 0})
        lines.append(_judgment_line(i, j, v))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("‹ صفحه قبل", callback_data=f"menu:justice:hist:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("صفحه بعد ›", callback_data=f"menu:justice:hist:{page + 1}"))
    keyboard_rows = [nav] if nav else []
    keyboard_rows.append([
        InlineKeyboardButton("↩️ بازگشت", callback_data="menu:justice"),
        InlineKeyboardButton("❌ بستن", callback_data="menu:close"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)


def build_justice_open():
    rows = db.open_judgments()
    total = len(rows)
    if total == 0:
        lines = ["🔴 هیچ دادگاه بازی در حال حاضر برگزار نیست."]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 تازه‌سازی", callback_data="menu:justice:open"),
            InlineKeyboardButton("↩️ بازگشت", callback_data="menu:justice"),
        ]])
        return "\n".join(lines), keyboard
    votes = db.votes_map([r["id"] for r in rows])
    lines = [f"🔴 دادگاه‌های در جریان ({ftext.fa_digits(total)} عدد):", ""]
    for j in rows:
        v = votes.get(j["id"], {"strike": 0, "chance": 0})
        try:
            ends = ftext.fa_datetime(datetime.fromisoformat(j["closes_at"]))
        except ValueError:
            ends = j["closes_at"]
        lastcall = " 🚨 (آخرین فرصت)" if j["status"] == "lastcall" else ""
        lines.append(
            f"⚖️ «{j['target_name']}»{lastcall}\n"
            f"   🟥 {ftext.fa_digits(v['strike'])} — 🟩 {ftext.fa_digits(v['chance'])}"
            f" | ⏳ پایان: {ends}"
        )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تازه‌سازی", callback_data="menu:justice:open"),
         InlineKeyboardButton("↩️ بازگشت", callback_data="menu:justice")],
        [InlineKeyboardButton("❌ بستن", callback_data="menu:close")],
    ])
    return "\n".join(lines), keyboard


def join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✍️ ثبت نام من", callback_data="join:start"),
    ]])


HELP_TEXT = """❓ راهنمای ربات مدیریت امتیاز

🎯 هدف ربات: ثبت اخطار/استرایک/امتیاز مثبت برای اعضای کانال، برگزاری «دادگاه رأی‌گیری» برای استرایک، و محاسبه‌ی امتیاز نهایی با قوانین پاک‌سازی (هر ۵ پلاس = ۱ استرایک، هر ۳ پلاس = ۱ اخطار).

📌 ثبت امتیاز (فقط ادمین‌ها):
به پیام کاربر ریپلای کنید و یکی از این‌ها را بنویسید:
• {warn} — ثبت اخطار (۱ امتیاز منفی)
• {strike} — تشکیل دادگاه استرایک (۳ امتیاز منفی در صورت رأی بیشتر به نفع استرایک)
• {plus} — ثبت امتیاز مثبت (۱ امتیاز مثبت)

بدون ریپلای هم می‌شود، با آیدی یا یوزرنیم:
• {warn} @ali دلیل تخلف
• {strike} 123456789 توهین
• {plus} @ali همکاری خوب

(به‌جای کلمه‌های فارسی می‌توانید از /warn ، /strike و /plus هم استفاده کنید.)

⚖️ دادگاه استرایک (رأی‌گیری خودکار):
با دستور استرایک روی کاربر، به‌جای ثبت فوری، رأی‌گیری عمومی باز می‌شود:
• 🟥 استرایک بزنید  |  🟩 یک فرصت دیگر
• همه‌ی اعضا به‌جز خودِ فرد می‌توانند رأی دهند؛ رأی تا بسته شدن قابل تغییر است
• رأی‌گیری ۵ دقیقه باز است؛ ادمین‌ها با «🏁 اعلام نتیجه» زودتر می‌بندند
• اگر مساوی شود → «آخرین فرصت تغییر نظر» (۲ دقیقه‌ی دیگر)؛ اگر باز مساوی شود → استرایک ثبت می‌شود
• استرایکِ رأی‌گیری‌شده وارد محاسبات امتیاز و مجازات (۳ میوت / ۵ اخراج) می‌شود

📈 آمار اعضا (هر نفر ۰ تا ۱۰۰):
• /stats — آمار خودتان (یا ریپلای + /stats)
• /stats @ali — آمار یک نفر (همه می‌توانند ببینند)
• /mystats — آمار خودتان
• /allstats — آمار کلی همه (ادمین‌ها)
• /ability متن — تعریف یا تغییر توانایی شخصی خودتان
• دکمه‌ی «🌟 تنظیم توانایی شخصی» داخل /stats — ثبت توانایی با فرم

🏆 رتبه‌بندی:
• /ranking — جدول امتیازها از بهترین به بدترین (همراه با رتبه‌ی خودتان)
• /ranking stats — رتبه‌بندی بر اساس آمار (میانگین 💡+😂+🥰+🐢)
• /statsranking — میان‌بر همان رتبه‌بندی آمار
• دکمه‌ی «🏆 رتبه‌بندی» در /menu
• /stat iq @ali — پیشنهاد افزایش ۵٪ با رأی‌گیری گروه
• /stat iq @ali take — پیشنهاد برگرداندن ۵٪ با رأی‌گیری
• هر رأی‌گیری با بیش از ۵۰٪ تایید اعمال می‌شود (خودِ فرد نمی‌تواند رأی دهد)
• آمارها: 💡 IQ | 😂 COMEDY | 🥰 SIMPNESS | 🐢 RETARDNESS

📊 مشاهده‌ی گزارش‌ها:
• /points — گزارش کامل یک کاربر (با ریپلای یا آیدی/یوزرنیم)
• /mypoints — امتیاز خودتان
• /report — گزارش همه‌ی اعضا با امتیاز نهایی
• /history — تاریخچه‌ی آخرین رویدادهای یک کاربر
• /justice — آخرین ۱۰ دادگاه (با ریپلای/یوزرنیم برای یک نفر خاص)

👥 مدیریت اعضا (فقط ادمین‌ها):
• /addmember — ثبت عضو جدید (ریپلای + نام سن، یا آیدی/یوزرنیم، یا بدون آرگومان برای فرم تعاملی)
• /member — نمایش کارت یک عضو
• /listmembers — لیست اعضا با صفحه‌بندی
• /setname و /setage — تغییر نام و سن
• /delmember — حذف عضو (تاریخچه‌ی امتیازها حفظ می‌شود)

⚙️ تغییر کلمه‌های دستورات (فقط ادمین اصلی):
• /words — نمایش کلمه‌های فعلی
• /setword warn کلمهٔ_جدید
• /setword strike کلمهٔ_جدید
• /setword plus کلمهٔ_جدید

🔑 دستورات عمومی:
• /join — ثبت نام با یوزرنیم خودتان (همه)
• /start — شروع
• /menu — پنل دکمه‌ای
• /id — نمایش آیدی عددی خودتان
• /cancel — لغو فرم جاری

🔨 مجازات استرایک:
• ۳ استرایک باقی‌مانده → میوت ۱ روزه
• ۵ استرایک باقی‌مانده → اخراج از گروه
(فقط استرایک‌های «باقی‌مانده بعد از پاک‌سازی» شمرده می‌شوند؛ ادمین‌ها مجازات نمی‌شوند.)

🧮 نحوه‌ی محاسبه:
اخطار = ۱- | استرایک = ۳- | امتیاز مثبت = ۱+
پاک‌سازی: اول هر ۵ پلاس = ۱ استرایک، سپس هر ۳ پلاس باقی‌مانده = ۱ اخطار
امتیاز نهایی = مثبت − منفی باقی‌مانده

نکته: برای اینکه ربات کلمه‌های دستورات را ببیند، در BotFather حالت Privacy Mode را خاموش کنید (/setprivacy → Disable) یا ربات را ادمین کانال/گروه کنید.

━━━━━━━━━━━━━━
طراحی شده توسط کوروش ❤️"""


def render_help() -> str:
    words = db.get_words()
    return HELP_TEXT.format(warn=words["warn"], strike=words["strike"], plus=words["plus"])


# ---------------------------------------------------------------- /start، /help، /menu

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    admin = await is_admin(update, context)
    text = (
        "به اعضای برادری BBH خوش آمدید 👋\n\n"
        "من ربات مدیریت امتیاز این کانال هستم؛ اخطار، استرایک و امتیاز مثبت اعضا را ثبت و محاسبه می‌کنم.\n\n"
        f"🆔 آیدی عددی شما: {ftext.fa_digits(user.id) if user else '—'}\n\n"
        "برای دیدن همه‌ی دستورها /help را بفرستید.\n"
        "برای ثبت‌نام با یوزرنیم خودتان، دکمه‌ی «✍️ ثبت نام من» را بزنید یا /join را بفرستید.\n\n"
        "━━━━━━━━━━━━━━\n"
        "طراحی شده توسط کوروش ❤️"
    )
    if admin:
        master = await is_master(update, context)
        await update.effective_message.reply_text(text, reply_markup=main_menu_keyboard(master))
    else:
        await update.effective_message.reply_text(text, reply_markup=join_keyboard())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(render_help())


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        await update.effective_message.reply_text(f"🆔 آیدی عددی شما: `{user.id}`", parse_mode="Markdown")


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.effective_message.reply_text("⛔️ پنل مدیریت فقط برای ادمین‌هاست.")
        return
    master = await is_master(update, context)
    await update.effective_message.reply_text("🎛 پنل مدیریت:", reply_markup=main_menu_keyboard(master))


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """همه‌ی کاربران — دکمه‌ی باز کردن پنل برادری BBH را نشان می‌دهد."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    startapp = "https://t.me/azizmstrikebot?startapp"  # تلگرام این لینک را داخل خودش باز می‌کند
    pages_url = "https://kouroshsamadof.github.io/bbh-mini-app/"
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup", "channel"):
        # دکمه‌ی web_app در گروه/کانال کار نمی‌کند؛ لینک مستقیم تی‌.ام‌ای را بفرست
        text = "🎮 «پنل برادری BBH» — روی دکمه‌ی زیر بزن تا همان‌جا داخل تلگرام باز شود:"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎮 باز کردن پنل (داخل تلگرام)", url=startapp)]])
    else:
        text = "🎮 «پنل برادری BBH» آماده است!"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎮 باز کردن پنل (داخل چت)", web_app=WebAppInfo(url=pages_url))]])
    await update.effective_message.reply_text(text, reply_markup=kb)


# ---------------------------------------------------------------- گزارش‌ها

async def cmd_mypoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(build_report(user.id, display_name(user)))


async def cmd_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    target, error = resolve_target(msg, context.args or [])
    if error:
        await msg.reply_text(error)
        return
    await msg.reply_text(build_report(target.user_id, target.name))


# ---------------------------------------------------------------- 🏆 رتبه‌بندی

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def rank_prefix(i: int) -> str:
    return MEDALS.get(i, f"{ftext.fa_digits(i)}.")


def build_points_ranking(viewer_id: int | None, limit: int = 10) -> str:
    rows = db.summaries_for_all()
    viewer_rank = next((i + 1 for i, r in enumerate(rows) if r["user_id"] == viewer_id), None)
    lines = ["🏆 جدول امتیازها (از بهترین به بدترین):", ""]
    if not rows:
        lines.append("هنوز عضوی ثبت‌نام نکرده است.")
        return "\n".join(lines)
    for i, r in enumerate(rows[:limit], start=1):
        uname = f" @{r['username']}" if r["username"] else ""
        sign = "+" if r["final"] > 0 else ""
        medal = rank_prefix(i)
        lines.append(
            f"{medal} {r['full_name']}{uname} — 🏁 {sign}{ftext.fa_digits(r['final'])}"
        )
    lines.append("")
    if viewer_rank:
        lines.append(f"📍 رتبه‌ی شما: {ftext.fa_digits(viewer_rank)} از {ftext.fa_digits(len(rows))} نفر")
    lines.append(f"(کل اعضا: {ftext.fa_digits(len(rows))} نفر)")
    return "\n".join(lines)


def build_stats_ranking(viewer_id: int | None, limit: int = 10) -> str:
    rows = db.stats_ranking()
    viewer_rank = next((i + 1 for i, r in enumerate(rows) if r["user_id"] == viewer_id), None)
    lines = ["📊 جدول آمار اعضا (میانگین 💡+😂+🥰+🐢):", ""]
    if not rows:
        lines.append("هنوز عضوی ثبت‌نام نکرده است.")
        return "\n".join(lines)
    for i, r in enumerate(rows[:limit], start=1):
        uname = f" @{r['username']}" if r["username"] else ""
        lines.append(
            f"{rank_prefix(i)} {r['full_name']}{uname} — ⭐ {ftext.fa_digits(round(r['avg']))}%  "
            f"(جمع: {ftext.fa_digits(r['total'])})"
        )
    lines.append("")
    if viewer_rank:
        lines.append(f"📍 رتبه‌ی شما: {ftext.fa_digits(viewer_rank)} از {ftext.fa_digits(len(rows))} نفر")
    lines.append(f"(کل اعضا: {ftext.fa_digits(len(rows))} نفر)")
    return "\n".join(lines)


def build_ranking_panel():
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 رتبه‌بندی امتیاز", callback_data="menu:ranking:points"),
         InlineKeyboardButton("📊 رتبه‌بندی آمار", callback_data="menu:ranking:stats")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data="menu:back"),
         InlineKeyboardButton("❌ بستن", callback_data="menu:close")],
    ])
    return "🏆 رتبه‌بندی — کدام جدول را می‌خواهید ببینید؟", keyboard


async def cmd_ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    args = list(context.args or [])
    mode = args[0].lower() if args else "points"
    if mode in ("stats", "stat", "avg", "آمار"):
        await msg.reply_text(build_stats_ranking(user.id if user else None))
    else:
        await msg.reply_text(build_points_ranking(user.id if user else None))


async def cmd_statsranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.effective_message.reply_text(build_stats_ranking(user.id if user else None))


def build_all_report() -> str:
    rows = db.summaries_for_all()
    if not rows:
        return "هنوز عضوی در دیتابیس ثبت نشده است.\nبرای افزودن: /addmember"
    lines = ["📊 گزارش همه‌ی اعضا (از بهترین به بدتر):", ""]
    for i, r in enumerate(rows[:30], start=1):
        uname = f" (@{r['username']})" if r["username"] else ""
        sign = "+" if r["final"] > 0 else ""
        lines.append(
            f"{ftext.fa_digits(i)}. {r['full_name']}{uname} — "
            f"🏁 {sign}{ftext.fa_digits(r['final'])}"
            f"  (🟡{ftext.fa_digits(r['warns'])} 🔴{ftext.fa_digits(r['strikes'])} 🟢{ftext.fa_digits(r['pluses'])})"
        )
    if len(rows) > 30:
        lines.append(f"… و {ftext.fa_digits(len(rows) - 30)} عضو دیگر")
    return "\n".join(lines)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    await msg.reply_text(build_all_report())


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    target, error = resolve_target(msg, context.args or [])
    if error:
        await msg.reply_text(error)
        return
    rows = db.history(target.user_id, 10)
    if not rows:
        await msg.reply_text("هنوز رویدادی برای این کاربر ثبت نشده است.")
        return
    name, _ = identity_for(target.user_id, target.name)
    lines = [f"🕘 آخرین رویدادها برای «{name}»:", ""]
    for r in rows:
        meta = KIND_META[r["type"]]
        try:
            ts = datetime.fromisoformat(r["created_at"])
            when = ftext.fa_datetime(ts)
        except ValueError:
            when = r["created_at"]
        reason = f" — دلیل: {r['reason']}" if r["reason"] else ""
        admin = f" — ثبت‌کننده: {r['admin_name']}" if r["admin_name"] else ""
        lines.append(f"{meta['emoji']} {when} — {meta['label']}{reason}{admin}")
    await msg.reply_text("\n".join(lines))


# ---------------------------------------------------------------- مجازات خودکار استرایک

_MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_invite_users=False,
)


async def maybe_punish(context: ContextTypes.DEFAULT_TYPE, chat_id: int, target, stats: dict) -> list[str]:
    """اگر استرایک‌های باقی‌مانده به حد مجازات رسید، میوت/اخراج اعمال می‌شود."""
    cur = stats["current_strikes"]
    if cur < scoring.STRIKE_MUTE_LIMIT:
        return []
    # ادمین‌ها و خود ربات هرگز مجازات نمی‌شوند
    try:
        tmember = await context.bot.get_chat_member(chat_id, target.user_id)
        st = getattr(tmember.status, "value", str(tmember.status)).lower()
        if st in ("creator", "owner", "administrator"):
            return [f"ℹ️ «{target.name}» به {ftext.fa_digits(cur)} استرایک رسید، اما چون ادمین است مجازات نشد."]
    except Exception as exc:
        logger.warning("get_chat_member(target) failed: %s", exc)
        return [f"⚠️ «{target.name}» به {ftext.fa_digits(cur)} استرایک رسید، اما وضعیت ادمینی او مشخص نشد؛ مجازات اعمال نشد."]

    if cur >= scoring.STRIKE_BAN_LIMIT:
        try:
            await context.bot.ban_chat_member(chat_id, target.user_id)
            return [f"🔨 «{target.name}» به دلیل رسیدن به {ftext.fa_digits(cur)} استرایک، از گروه اخراج شد."]
        except Exception as exc:
            logger.warning("ban failed: %s", exc)
            return [f"⚠️ «{target.name}» به {ftext.fa_digits(cur)} استرایک رسید — اخراج خودکار انجام نشد (ربات اجازه‌ی اخراج ندارد)."]

    try:
        await context.bot.restrict_chat_member(
            chat_id,
            target.user_id,
            permissions=_MUTE_PERMISSIONS,
            until_date=datetime.now() + timedelta(days=1),
        )
        return [f"🔇 «{target.name}» به دلیل رسیدن به {ftext.fa_digits(cur)} استرایک، ۱ روز میوت شد."]
    except Exception as exc:
        logger.warning("mute failed: %s", exc)
        return [f"⚠️ «{target.name}» به {ftext.fa_digits(cur)} استرایک رسید — میوت خودکار انجام نشد (ربات اجازه‌ی میوت ندارد)."]


# ---------------------------------------------------------------- 📈 آمار اعضا (0 تا 100)

STAT_META = {
    "retardness": {"en": "RETARDNESS", "emoji": "🐢", "fa": "عقب‌ماندگی"},
    "iq":         {"en": "IQ",          "emoji": "💡", "fa": "هوش"},
    "simp":       {"en": "SIMPNESS",    "emoji": "🥰", "fa": "سیمپ بودن"},
    "comedy":     {"en": "COMEDY",      "emoji": "😂", "fa": "کمدین بودن"},
}
STAT_ORDER = ["iq", "comedy", "simp", "retardness"]
STAT_ALIASES = {
    "retard": "retardness", "retarded": "retardness", "retardness": "retardness",
    "iq": "iq", "هوش": "iq",
    "simp": "simp", "simpness": "simp", "simping": "simp", "سیمپ": "simp",
    "comedy": "comedy", "comedian": "comedy", "کمدین": "comedy",
}
STAT_INCREMENT = 5   # هر رأی موفق = ۵٪


def norm_stat(raw: str) -> str | None:
    return STAT_ALIASES.get(str(raw).strip().lower())


def build_stats_view(name: str, stats: dict, ability: str | None = None) -> str:
    lines = [f"📊 آمار «{name}»"]
    if ability:
        lines.append(f"🌟 توانایی: «{ability}»  ██████████  ۱۰۰٪")
    lines.append("")
    for key in STAT_ORDER:
        m = STAT_META[key]
        v = stats.get(key, 0)
        lines.append(f"{m['emoji']} {m['en']:<11} {ftext.stat_bar(v)}  {ftext.fa_digits(v)}%")
    lines.append("")
    lines.append("افزودن/کم کردن هر آمار با رأی‌گیری گروه ممکن است.")
    return "\n".join(lines)


def stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🌟 تنظیم توانایی شخصی", callback_data="ability:prompt"),
    ]])


def stats_identity(target_id: int, fallback_name: str | None = None):
    member = db.get_member(target_id)
    if member:
        return member["full_name"], member
    return identity_for(target_id, fallback_name)


async def send_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, tokens: list[str]):
    msg = update.effective_message
    target = None
    if tokens or (getattr(msg, "reply_to_message", None) and msg.reply_to_message.from_user):
        t, error = resolve_target(msg, tokens)
        if error:
            await msg.reply_text(error)
            return
        target = t
    user = update.effective_user
    if target is None:
        if user is None:
            return
        target = Target(user.id, display_name(user), (user.username or "").lower() or None, [])
    name, _ = stats_identity(target.user_id, target.name)
    stats = db.get_stats(target.user_id)
    ability = db.get_ability(target.user_id)
    await msg.reply_text(build_stats_view(name, stats, ability), reply_markup=stats_keyboard())


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stats — آمار خودتان، یا با @یوزرنیم/آیدی/ریپلای: آمار آن کاربر"""
    await send_stats(update, context, list(context.args or []))


async def cmd_mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not user:
        return
    stats = db.get_stats(user.id)
    ability = db.get_ability(user.id)
    await msg.reply_text(build_stats_view(display_name(user), stats, ability), reply_markup=stats_keyboard())


async def cmd_allstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    text, keyboard = build_all_stats(0)
    await msg.reply_text(text, reply_markup=keyboard)


STATS_PAGE_SIZE = 5


def build_all_stats(page: int):
    total = db.count_members()
    if total == 0:
        return "هنوز عضوی ثبت‌نام نکرده است.\nاعضا با /join ثبت‌نام می‌کنند.", None
    stats_map = db.get_all_stats_map()
    total_pages = (total + STATS_PAGE_SIZE - 1) // STATS_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    members = db.list_members(limit=STATS_PAGE_SIZE, offset=page * STATS_PAGE_SIZE)
    member_ids = [m["user_id"] for m in members]
    abilities = db.abilities_map(member_ids)   # یک کوئری برای همه‌ی اعضای صفحه
    lines = [
        f"📈 آمار کلی اعضا — صفحه {ftext.fa_digits(page + 1)} از {ftext.fa_digits(total_pages)}"
        f" (کل: {ftext.fa_digits(total)})",
        "",
    ]
    for i, m in enumerate(members, start=page * STATS_PAGE_SIZE + 1):
        s = stats_map.get(m["user_id"], {k: 0 for k in STAT_META})
        uname = f" @{m['username']}" if m["username"] else ""
        lines.append(f"{ftext.fa_digits(i)}. {m['full_name']}{uname}")
        ability = abilities.get(m["user_id"])
        if ability:
            lines.append(f"   🌟 توانایی: «{ability}»  ██████████  ۱۰۰٪")
        for k in STAT_ORDER:
            meta = STAT_META[k]
            v = s[k]
            lines.append(f"   {meta['emoji']} {meta['en']} {ftext.stat_bar(v)}  {ftext.fa_digits(v)}%")
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("‹ قبلی", callback_data=f"menu:stats:all:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی ›", callback_data=f"menu:stats:all:{page + 1}"))
    keyboard_rows = [nav] if nav else []
    keyboard_rows.append([
        InlineKeyboardButton("↩️ بازگشت", callback_data="menu:stats"),
        InlineKeyboardButton("❌ بستن", callback_data="menu:close"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)


def build_stats_panel():
    total = db.count_members()
    stats_map = db.get_all_stats_map()
    holders = sum(1 for v in stats_map.values() if any(v.values()))
    lines = [
        "📈 پنل آمار اعضا",
        "",
        f"اعضای ثبت‌شده: {ftext.fa_digits(total)}",
        f"اعضای دارای حداقل یک آمار: {ftext.fa_digits(holders)}",
        "",
        "نحوه‌ی پیشنهاد آمار (فقط ادمین‌ها):",
        "/stat iq @user        ← افزایش ۵٪ با رأی‌گیری",
        "/stat iq @user take   ← برگرداندن ۵٪ با رأی‌گیری",
        "",
        "آمارها: 💡 IQ | 😂 COMEDY | 🥰 SIMPNESS | 🐢 RETARDNESS",
    ]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 آمار کلی اعضا", callback_data="menu:stats:all:0")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data="menu:back"),
         InlineKeyboardButton("❌ بستن", callback_data="menu:close")],
    ])
    return "\n".join(lines), keyboard


def stat_vote_keyboard(proposal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"svote:approve:{proposal_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"svote:reject:{proposal_id}"),
        ],
        [InlineKeyboardButton("🏁 اعلام نتیجه (ادمین‌ها)", callback_data=f"svote:close:{proposal_id}")],
    ])


def stat_proposal_view(p, approve: int, reject: int) -> str:
    m = STAT_META[p["stat"]]
    if p["action"] == "give":
        action_txt = f"➕ افزایش {ftext.fa_digits(STAT_INCREMENT)}٪"
    else:
        action_txt = f"➖ برگرداندن {ftext.fa_digits(STAT_INCREMENT)}٪"
    current = db.get_stats(p["target_id"]).get(p["stat"], 0)
    lines = [
        f"📊 پیشنهاد آمار برای «{p['target_name']}»",
        f"{m['emoji']} {m['en']} — {action_txt}   (فعلی: {ftext.fa_digits(current)}٪)",
        f"👮 پیشنهاددهنده: {p['proposer_name'] or 'ادمین'}",
        "",
        f"✅ تایید: {ftext.fa_digits(approve)}    ❌ رد: {ftext.fa_digits(reject)}",
        "",
        f"⏳ رأی‌گیری {ftext.fa_digits(STAT_VOTE_MINUTES)} دقیقه باز است — با بیش از ۵۰٪ تایید اعمال می‌شود.",
        "(خودِ فرد نمی‌تواند رأی دهد؛ رأی قابل تغییر است.)",
    ]
    return "\n".join(lines)


def schedule_proposal_close(app, proposal_id: int, when: datetime):
    try:
        app.job_queue.run_once(
            stat_proposal_close_job, when=when, data=proposal_id,
            name=f"proposal_{proposal_id}",
        )
    except Exception as exc:
        logger.warning("برنامه‌ریزی بستن رأی‌گیری آمار ناموفق: %s", exc)


async def stat_proposal_close_job(context: ContextTypes.DEFAULT_TYPE):
    await finalize_stat_proposal(context, context.job.data)


async def cmd_stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stat iq @user [give|take] — باز کردن رأی‌گیری پیشنهاد آمار (فقط ادمین‌ها)"""
    msg = update.effective_message
    chat = update.effective_chat
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ فقط ادمین‌ها می‌توانند آمار پیشنهاد دهند.")
        return
    if chat is None or chat.type == "private":
        await msg.reply_text("📊 رأی‌گیری آمار فقط داخل گروه برگزار می‌شود.")
        return
    tokens = list(context.args or [])
    if len(tokens) < 2:
        await msg.reply_text(
            "❓ فرمت درست:\n"
            "/stat iq @ali        ← افزایش ۵٪"
            "/stat iq @ali take   ← برگرداندن ۵٪\n"
            "آزمایش: /stat simp 123456789 take"
        )
        return
    stat = norm_stat(tokens[0])
    if stat is None:
        await msg.reply_text("❌ آمار نامعتبر است. یکی از این‌ها: iq | comedy | simp | retardness")
        return
    rest = tokens[1:]
    action = "give"
    if rest and rest[-1].strip().lower() in ("give", "take", "back", "+", "-"):
        last = rest[-1].strip().lower()
        action = "give" if last in ("give", "+") else "take"
        rest = rest[:-1]
    target, error = resolve_target(msg, rest)
    if error:
        await msg.reply_text(error)
        return

    existing = db.open_proposal_for(chat.id, target.user_id, stat)
    if existing:
        await msg.reply_text("❗️ قبلاً یک پیشنهاد آمار باز برای این عضو و این آمار وجود دارد.")
        return

    user = update.effective_user
    if posted_as_chat(update):
        proposer_id, proposer_name = None, (chat.title if chat else "کانال")
    else:
        proposer_id = user.id if user else None
        proposer_name = display_name(user) if user else "ادمین"

    db.ensure_stub(target.user_id, target.name, target.username)
    closes = datetime.now() + timedelta(minutes=STAT_VOTE_MINUTES)
    pid = db.create_stat_proposal(
        chat_id=chat.id,
        target_id=target.user_id,
        target_name=target.name,
        proposer_id=proposer_id,
        proposer_name=proposer_name,
        stat=stat,
        action=action,
        closes_at=closes.isoformat(timespec="seconds"),
    )
    p = db.get_proposal(pid)
    sent = await msg.reply_text(stat_proposal_view(p, 0, 0), reply_markup=stat_vote_keyboard(pid))
    db.set_proposal_message(pid, sent.message_id)
    schedule_proposal_close(context.application, pid, closes)
    logger.info("پیشنهاد آمار #%s برای کاربر %s در چت %s باز شد", pid, target.user_id, chat.id)


async def stat_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    m = re.fullmatch(r"svote:(approve|reject|close):(\d+)", data)
    if not m:
        return
    action, pid = m.group(1), int(m.group(2))
    p = db.get_proposal(pid)
    if not p:
        await query.answer("این رأی‌گیری پیدا نشد!", show_alert=True)
        return
    user = update.effective_user

    if action == "close":
        if not await is_admin(update, context):
            await query.answer("⛔️ فقط ادمین‌ها می‌توانند نتیجه را اعلام کنند.", show_alert=True)
            return
        await query.answer()
        await finalize_stat_proposal(context, pid)
        return

    if p["status"] == "closed":
        await query.answer("این رأی‌گیری بسته شده است.", show_alert=True)
        return
    if user is None or user.is_bot:
        await query.answer("ربات‌ها رأی نمی‌دهند.", show_alert=True)
        return
    if user.id == p["target_id"]:
        await query.answer("شما نمی‌توانید برای آمار خودتان رأی دهید.", show_alert=True)
        return

    db.upsert_stat_vote(pid, user.id, action)
    await query.answer("✅ رأی شما ثبت شد.")

    approve, reject = db.stat_vote_counts(pid)
    p = db.get_proposal(pid)
    try:
        await query.edit_message_text(
            stat_proposal_view(p, approve, reject),
            reply_markup=stat_vote_keyboard(pid),
        )
    except BadRequest:
        pass
    except Exception as exc:
        logger.warning("به‌روزرسانی پیام رأی‌گیری آمار ناموفق: %s", exc)


async def finalize_stat_proposal(context: ContextTypes.DEFAULT_TYPE, pid: int):
    p = db.get_proposal(pid)
    if not p or p["status"] == "closed":
        return
    closes_at = datetime.fromisoformat(p["closes_at"])
    if closes_at > datetime.now() + timedelta(seconds=2):
        return  # جاب زودتر از موعد اجرا شده — هنوز باز است

    approve, reject = db.stat_vote_counts(pid)
    applied = approve > reject   # فقط اکثریت بیشتر → اعمال می‌شود
    delta = STAT_INCREMENT if p["action"] == "give" else -STAT_INCREMENT
    result = "applied" if applied else "rejected"
    db.close_proposal(pid, result, approve, reject)

    m = STAT_META[p["stat"]]
    votes_line = f"✅ {ftext.fa_digits(approve)} — ❌ {ftext.fa_digits(reject)}"
    if applied:
        new_value = db.adjust_stat(p["target_id"], p["stat"], delta)
        lines = [
            f"📊 رأی‌گیری آمار بسته شد:",
            f"{m['emoji']} {m['en']} برای «{p['target_name']}» {'افزایش' if delta > 0 else 'کاهش'} یافت → {ftext.fa_digits(new_value)}٪",
            votes_line,
        ]
    else:
        lines = [
            f"📊 رأی‌گیری آمار بسته شد:",
            f"❌ آمار «{m['en']}» برای «{p['target_name']}» تغییر نکرد (اکثریت به تایید نرسید).",
            votes_line,
        ]

    result_text = "\n".join(lines)
    delivered = False
    if p["message_id"]:
        try:
            await context.application.bot.edit_message_text(
                chat_id=p["chat_id"], message_id=p["message_id"], text=result_text,
            )
            delivered = True
        except Exception as exc:
            logger.warning("ویرایش پیام نتیجه‌ی آمار ناموفق: %s", exc)
    if not delivered:
        try:
            await context.application.bot.send_message(chat_id=p["chat_id"], text=result_text)
        except Exception as exc:
            logger.warning("ارسال پیام نتیجه‌ی آمار ناموفق: %s", exc)


# ---------------------------------------------------------------- 🌟 توانایی شخصی (خط اول آمار)

ASK_ABILITY_TEXT = 12


def clean_ability(text: str) -> str | None:
    t = str(text).strip().split("\n", 1)[0].strip()
    return t if 2 <= len(t) <= 48 else None


async def _save_ability(update, context, target_text: str):
    msg = update.effective_message
    user = update.effective_user
    if user is None:
        return ConversationHandler.END
    db.ensure_stub(user.id, display_name(user), (user.username or "").lower() or None)
    db.set_ability(user.id, target_text)
    await msg.reply_text(
        f"🌟 توانایی شما ثبت شد: «{target_text}»\n\n"
        "از این پس به‌عنوان خط اول آمار شما نمایش داده می‌شود."
    )
    return ConversationHandler.END


async def ability_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if query is not None:
        await query.answer()
        msg = query.message
        args = []
    else:
        msg = update.effective_message
        args = list(context.args or [])

    if args:
        cleaned = clean_ability(" ".join(args))
        if not cleaned:
            await msg.reply_text("❌ توانایی باید بین ۲ تا ۴۸ نویسه و یک خط باشد.\nمثال: /ability سریع مثل صاعقه")
            return ConversationHandler.END
        return await _save_ability(update, context, cleaned)

    current = db.get_ability(user.id) if user else None
    current_line = f"\n(فعلی: «{current}»)" if current else ""
    await msg.reply_text(
        "🌟 توانایی شخصی‌ات را بنویس (حداکثر ۴۸ نویسه، یک خط):\n"
        "مثال: «کارتر خشم‌آلود»" + current_line
    )
    return ASK_ABILITY_TEXT


async def ability_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    cleaned = clean_ability(msg.text or "")
    if not cleaned:
        await msg.reply_text("❌ توانایی باید بین ۲ تا ۴۸ نویسه و یک خط باشد. دوباره بفرست:")
        return ASK_ABILITY_TEXT
    return await _save_ability(update, context, cleaned)


# ---------------------------------------------------------------- ⚖️ دادگاه رأی‌گیری (برای استرایک)

def vote_keyboard(judgment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟥 استرایک بزنید", callback_data=f"jvote:strike:{judgment_id}"),
            InlineKeyboardButton("🟩 یک فرصت دیگر", callback_data=f"jvote:chance:{judgment_id}"),
        ],
        [InlineKeyboardButton("🏁 اعلام نتیجه (ادمین‌ها)", callback_data=f"jvote:close:{judgment_id}")],
    ])


def judgment_view(j, strikes: int, chances: int, voters: int) -> str:
    if j["status"] == "lastcall":
        status_line = "🚨 آخرین فرصت تغییر نظر — اگر باز مساوی شود، استرایک ثبت می‌شود"
    else:
        status_line = f"🟦 رأی‌گیری باز است ({ftext.fa_digits(VOTE_MINUTES)} دقیقه)"
    try:
        ends = ftext.fa_datetime(datetime.fromisoformat(j["closes_at"]))
        timing = f"⏳ پایان رأی‌گیری: {ends}"
    except ValueError:
        timing = "⏳ پایان رأی‌گیری: به‌زودی"
    lines = [
        f"⚖️ دادگاه برای «{j['target_name']}»",
        f"👮 پیشنهاددهنده: {j['admin_name'] or 'ادمین'}",
    ]
    if j["reason"]:
        lines.append(f"💬 دلیل: {j['reason']}")
    lines.append("")
    lines.append(
        f"🟥 استرایک: {ftext.fa_digits(strikes)} رأی    "
        f"🟩 فرصت دیگر: {ftext.fa_digits(chances)} رأی"
    )
    lines.append(f"👥 رأی‌دهندگان: {ftext.fa_digits(voters)} نفر")
    lines.append("")
    lines.append(status_line)
    lines.append(timing)
    lines.append("(خودِ فرد نمی‌تواند رأی دهد؛ رأی تا بسته شدن قابل تغییر است.)")
    return "\n".join(lines)


def schedule_judgment_close(app, judgment_id: int, when: datetime) -> None:
    try:
        app.job_queue.run_once(
            judgment_close_job, when=when, data=judgment_id,
            name=f"judgment_{judgment_id}",
        )
    except Exception as exc:
        logger.warning("برنامه‌ریزی بستن رأی‌گیری ناموفق: %s", exc)


async def judgment_close_job(context: ContextTypes.DEFAULT_TYPE):
    await finalize_judgment(context, context.job.data)


async def court_start(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      target, admin_id, admin_name, reason: str | None):
    msg = update.effective_message
    chat = update.effective_chat
    if chat is None or chat.type == "private":
        await msg.reply_text("⚖️ دادگاه رأی‌گیری فقط داخل گروه برگزار می‌شود.")
        return

    user = update.effective_user
    if user and user.id == target.user_id:
        await msg.reply_text("⚖️ نمی‌توانید برای خودتان دادگاه تشکیل دهید.")
        return

    existing = db.open_judgment_for(chat.id, target.user_id)
    if existing:
        await msg.reply_text(
            f"❗️ قبلاً یک دادگاه باز برای «{target.name}» وجود دارد.\n"
            "تا اعلام نتیجه‌ی آن صبر کنید."
        )
        return

    closes = datetime.now() + timedelta(minutes=VOTE_MINUTES)
    jid = db.create_judgment(
        chat_id=chat.id,
        target_id=target.user_id,
        target_name=target.name,
        admin_id=admin_id,
        admin_name=admin_name,
        reason=reason,
        closes_at=closes.isoformat(timespec="seconds"),
    )
    j = db.get_judgment(jid)
    sent = await msg.reply_text(judgment_view(j, 0, 0, 0), reply_markup=vote_keyboard(jid))
    db.set_judgment_message(jid, sent.message_id)
    schedule_judgment_close(context.application, jid, closes)
    logger.info("دادگاه #%s برای کاربر %s در چت %s باز شد", jid, target.user_id, chat.id)


async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    m = re.fullmatch(r"jvote:(strike|chance|close):(\d+)", data)
    if not m:
        return
    action, jid = m.group(1), int(m.group(2))
    j = db.get_judgment(jid)
    if not j:
        await query.answer("این رأی‌گیری پیدا نشد!", show_alert=True)
        return
    user = update.effective_user

    if action == "close":
        if not await is_admin(update, context):
            await query.answer("⛔️ فقط ادمین‌ها می‌توانند نتیجه را اعلام کنند.", show_alert=True)
            return
        await query.answer()
        await finalize_judgment(context, jid)
        return

    if j["status"] == "closed":
        await query.answer("این رأی‌گیری بسته شده است.", show_alert=True)
        return
    if user is None or user.is_bot:
        await query.answer("ربات‌ها رأی نمی‌دهند.", show_alert=True)
        return
    if user.id == j["target_id"]:
        await query.answer("شما نمی‌توانید به دادگاه خودتان رأی دهید.", show_alert=True)
        return

    db.upsert_vote(jid, user.id, display_name(user), action)
    await query.answer("✅ رأی شما ثبت شد.")

    strikes, chances, voters = db.vote_counts(jid)
    j = db.get_judgment(jid)
    try:
        await query.edit_message_text(
            judgment_view(j, strikes, chances, voters),
            reply_markup=vote_keyboard(jid),
        )
    except BadRequest:
        pass  # متن تغییر نکرده — نادیده
    except Exception as exc:
        logger.warning("به‌روزرسانی پیام رأی‌گیری ناموفق: %s", exc)


async def finalize_judgment(context: ContextTypes.DEFAULT_TYPE, jid: int):
    j = db.get_judgment(jid)
    if not j or j["status"] == "closed":
        return
    # اگر جاب برای تمدید lastcall کنسل‌نشده زودتر اجرا شد، فقط وقتی واقعاً رسید ببند
    closes_at = datetime.fromisoformat(j["closes_at"])
    if closes_at > datetime.now() + timedelta(seconds=2):
        return

    strikes, chances, voters = db.vote_counts(jid)
    if strikes > chances:
        winner = "strike"
    elif chances > strikes:
        winner = "chance"
    else:
        if j["status"] == "open":
            new_close = datetime.now() + timedelta(minutes=LASTCALL_MINUTES)
            db.set_judgment_lastcall(jid, new_close.isoformat(timespec="seconds"))
            j2 = db.get_judgment(jid)
            text = (
                f"⚖️ رأی‌ها مساوی شد ({ftext.fa_digits(strikes)} به {ftext.fa_digits(chances)})!\n"
                f"🚨 این «آخرین فرصت تغییر نظر» است — اگر نتیجه باز مساوی شود، استرایک ثبت می‌شود.\n\n"
                + judgment_view(j2, strikes, chances, voters)
            )
            schedule_judgment_close(context.application, jid, new_close)
            try:
                await context.application.bot.edit_message_text(
                    chat_id=j["chat_id"], message_id=j["message_id"],
                    text=text, reply_markup=vote_keyboard(jid),
                )
            except Exception as exc:
                logger.warning("ویرایش پیام lastcall ناموفق: %s", exc)
            return
        winner = "strike"  # مساوی دوباره → استرایک ثبت می‌شود

    db.close_judgment(jid, winner)
    votes_line = f"🟥 {ftext.fa_digits(strikes)} — 🟩 {ftext.fa_digits(chances)}   (👥 {ftext.fa_digits(voters)} رأی‌دهنده)"

    if winner == "chance":
        result_lines = [
            f"🟩 دادگاه بسته شد: «{j['target_name']}» فرصت دوباره گرفت.",
            votes_line,
        ]
    else:
        target = Target(j["target_id"], j["target_name"], None, [])
        admin_name = j["admin_name"] or "دادگاه"
        stats = record_point(target, j["admin_id"], admin_name, "strike",
                             j["reason"], j["chat_id"])
        result_lines = [
            f"🔨 دادگاه بسته شد: استرایک برای «{j['target_name']}» ثبت شد.",
            votes_line,
            "",
            compact_stats_line(stats),
        ]
        result_lines.extend(await maybe_punish(context, j["chat_id"], target, stats))

    result_text = "\n".join(result_lines)
    delivered = False
    if j["message_id"]:
        try:
            await context.application.bot.edit_message_text(
                chat_id=j["chat_id"], message_id=j["message_id"], text=result_text,
            )
            delivered = True
        except Exception as exc:
            logger.warning("ویرایش پیام نتیجه ناموفق: %s", exc)
    if not delivered:
        try:
            await context.application.bot.send_message(chat_id=j["chat_id"], text=result_text)
        except Exception as exc:
            logger.warning("ارسال پیام نتیجه ناموفق: %s", exc)


async def cmd_justice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ فقط ادمین‌ها می‌توانند تاریخچه‌ی دادگاه را ببینند.")
        return
    tokens = list(context.args or [])
    target = None
    if tokens or (getattr(msg, "reply_to_message", None) and msg.reply_to_message.from_user):
        t, error = resolve_target(msg, tokens)
        if error:
            await msg.reply_text(error)
            return
        target = t
    if target:
        rows = db.list_judgments_for(target.user_id, 10)
        header = f"⚖️ آخرین دادگاه‌های «{target.name}»:"
    else:
        rows = db.list_judgments(10, 0)
        header = "⚖️ آخرین ۱۰ دادگاه:"
    if not rows:
        await msg.reply_text("هنوز هیچ دادگاهی برگزار نشده است.")
        return
    votes = db.votes_map([r["id"] for r in rows])
    lines = [header, ""]
    for i, j in enumerate(rows, start=1):
        v = votes.get(j["id"], {"strike": 0, "chance": 0})
        lines.append(_judgment_line(i, j, v))
    lines.append("")
    lines.append("برای جزئیات بیشتر از پنل ⚖️ دادگاه‌ها در /menu استفاده کنید.")
    await msg.reply_text("\n".join(lines))


# ---------------------------------------------------------------- ثبت اخطار / استرایک / امتیاز

def record_point(target, admin_id, admin_name, kind: str, reason: str | None, chat_id: int | None):
    """ثبت رویداد امتیاز (بعد از رأی دادگاه یا مستقیم) و برگرداندن خلاصه‌ی امتیازها."""
    db.ensure_stub(target.user_id, target.name, target.username)
    db.add_point(
        target_id=target.user_id,
        target_name=target.name,
        chat_id=chat_id,
        admin_id=admin_id,
        admin_name=admin_name,
        kind=kind,
        reason=reason,
    )
    # نام و یوزرنیم به‌روز نگه داشته می‌شود
    if target.username or target.name:
        db.upsert_member(target.user_id, target.username, target.name, None)
    return db.summary_for(target.user_id)


async def do_action(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    msg = update.effective_message
    if msg is None:
        return
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ فقط ادمین‌های کانال یا ادمین‌های اصلی می‌توانند این دستور را اجرا کنند.")
        return

    target, error = resolve_target(msg, context.args or [])
    if error:
        words = db.get_words()
        example_word = words[kind]
        await msg.reply_text(f"{error}\n\nمثال: {example_word} @یوزرنیم دلیل")
        return

    reason = " ".join(target.rest).strip() or None
    user = update.effective_user
    if posted_as_chat(update):
        admin_id = None
        admin_name = update.effective_chat.title if update.effective_chat else "کانال"
    else:
        admin_id = user.id if user else None
        admin_name = display_name(user) if user else "ادمین"

    # استرایک دیگر مستقیم ثبت نمی‌شود — اول دادگاه رأی‌گیری باز می‌شود
    if kind == "strike":
        await court_start(update, context, target, admin_id, admin_name, reason)
        return

    stats = record_point(target, admin_id, admin_name, kind, reason,
                         update.effective_chat.id if update.effective_chat else None)
    meta = KIND_META[kind]
    sign = "+" if scoring.POINTS[kind] > 0 else ""
    lines = [
        f"✅ {meta['label']} برای «{target.name}» ثبت شد ({sign}{ftext.fa_digits(scoring.POINTS[kind])}).",
    ]
    if reason:
        lines.append(f"💬 دلیل: {reason}")
    lines.append("")
    lines.append(compact_stats_line(stats))
    await msg.reply_text("\n".join(lines))


async def cmd_warn(update, context):
    await do_action(update, context, "warn")


async def cmd_strike(update, context):
    await do_action(update, context, "strike")


async def cmd_plus(update, context):
    await do_action(update, context, "plus")


async def word_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """روتر کلمه‌های قابل‌تغییر دستورات (مثلاً «اخطار» به‌جای /warn)."""
    if context.user_data.get("form_flow"):
        return  # در میانه‌ی فرم افزودن عضو هستیم
    user = update.effective_user
    if user and user.is_bot:
        return  # پیام‌های ربات‌های دیگر نادیده گرفته می‌شود
    msg = update.effective_message
    if msg is None or not msg.text:
        return
    parts = re.split(r"\s+", msg.text.strip())
    if not parts:
        return
    words = db.get_words()
    if parts[0] == "آمار":
        await send_stats(update, context, parts[1:])
        return
    mapping = {words["warn"]: "warn", words["strike"]: "strike", words["plus"]: "plus"}
    kind = mapping.get(parts[0])
    if kind is None:
        return
    context.args = parts[1:]
    await do_action(update, context, kind)


# ---------------------------------------------------------------- مدیریت اعضا

def split_name_age(tokens: list[str]):
    """آخرین توکن اگر عدد معتبر سن بود، جدا می‌شود؛ بقیه نام است."""
    toks = list(tokens)
    age = None
    if toks:
        maybe = ftext.to_int(toks[-1])
        if maybe is not None and 1 <= maybe <= 150:
            age = maybe
            toks = toks[:-1]
    return " ".join(toks).strip(), age


def clear_form_state(context) -> None:
    for key in ("t_id", "t_name", "t_username", "t_fullname", "t_age", "form_flow"):
        context.user_data.pop(key, None)


def format_member_card(target_id: int, member) -> str:
    name = member["full_name"] if member else f"کاربر {target_id}"
    lines = [f"👤 {name}", f"🆔 آیدی: {ftext.fa_digits(target_id)}"]
    if member:
        if member["username"]:
            lines.append(f"🔗 یوزرنیم: @{member['username']}")
        age = member["age"]
        lines.append(f"🎂 سن: {ftext.fa_digits(age) if age is not None else 'ثبت نشده'}")
        if member["note"]:
            lines.append(f"📝 یادداشت: {member['note']}")
        try:
            ts = datetime.fromisoformat(member["created_at"])
            lines.append(f"🗓 تاریخ ثبت: {ftext.fa_datetime(ts)}")
        except ValueError:
            pass
    s = db.summary_for(target_id)
    lines.append("")
    lines.append(compact_stats_line(s))
    return "\n".join(lines)


async def addmember_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ورودی «افزودن عضو» — هم دکمه‌ی منو، هم دستور /addmember."""
    query = update.callback_query
    if query is not None:
        await query.answer()
        if not await is_admin(update, context):
            await query.edit_message_text("⛔️ فقط ادمین‌ها می‌توانند عضو اضافه کنند.")
            return ConversationHandler.END
        context.user_data["form_flow"] = True
        msg = query.message
        reply = getattr(msg, "reply_to_message", None)
        if reply and getattr(reply, "from_user", None):
            u = reply.from_user
            context.user_data["t_id"] = u.id
            context.user_data["t_name"] = display_name(u)
            context.user_data["t_username"] = (u.username or "").lower() or None
            await msg.reply_text(
                f"👤 {context.user_data['t_name']} انتخاب شد.\nحالا نام و نام‌خانوادگی را بفرستید:"
            )
            return ASK_NAME
        await msg.reply_text(
            "🆔 آیدی عددی کاربر یا @یوزرنیم او را بفرستید\n"
            "(یا پیام او را به همین پیام ریپلای کنید):"
        )
        return ASK_TARGET

    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ فقط ادمین‌ها می‌توانند عضو اضافه کنند.")
        return ConversationHandler.END

    tokens = list(context.args or [])

    # حالت ریپلای یا منشن متنی
    target = None
    rest = list(tokens)
    reply = getattr(msg, "reply_to_message", None)
    if reply and getattr(reply, "from_user", None):
        u = reply.from_user
        target = Target(u.id, display_name(u), (u.username or "").lower() or None, rest)
    else:
        for ent in (getattr(msg, "entities", None) or []):
            if getattr(ent, "type", "") == "text_mention" and getattr(ent, "user", None):
                u = ent.user
                target = Target(u.id, display_name(u), (u.username or "").lower() or None, rest)
                break
        if target is None and rest:
            first = rest[0]
            if re.fullmatch(r"\d{5,}", ftext.to_ascii_digits(first)):
                uid = int(ftext.to_ascii_digits(first))
                m = db.get_member(uid)
                target = Target(uid, m["full_name"] if m else f"کاربر {uid}",
                                m["username"] if m else None, rest[1:])
            elif first.startswith("@"):
                m = db.find_by_username(first)
                if m:
                    target = Target(m["user_id"], m["full_name"], m["username"], rest[1:])
                else:
                    await msg.reply_text(
                        f"❌ یوزرنیم {first} ناشناس است.\n"
                        "لطفاً آیدی عددی بدهید (با /id از خود کاربر بگیرید) یا به پیامش ریپلای کنید."
                    )
                    return ConversationHandler.END
            else:
                await msg.reply_text(
                    "❓ روش استفاده:\n"
                    "/addmember 123456789 علی رضایی 25\n"
                    "یا ریپلای به پیام کاربر: /addmember علی رضایی 25\n"
                    "یا فقط /addmember برای فرم مرحله‌به‌مرحله"
                )
                return ConversationHandler.END

    if target is not None:
        rest = target.rest
        name, age = split_name_age(rest)
        if not name:
            context.user_data["form_flow"] = True
            context.user_data["t_id"] = target.user_id
            context.user_data["t_name"] = target.name
            context.user_data["t_username"] = target.username
            await msg.reply_text(
                f"👤 هدف: {target.name}\nحالا نام و نام‌خانوادگی را بفرستید:"
            )
            return ASK_NAME
        db.upsert_member(target.user_id, target.username, name, age)
        card = format_member_card(target.user_id, db.get_member(target.user_id))
        await msg.reply_text(f"✅ عضو ثبت شد:\n\n{card}")
        return ConversationHandler.END

    # بدون هیچ آرگومانی → فرم تعاملی
    context.user_data["form_flow"] = True
    await msg.reply_text(
        "🆔 آیدی عددی کاربر یا @یوزرنیم او را بفرستید\n(یا پیامش را به این پیام ریپلای کنید):"
    )
    return ASK_TARGET


async def ask_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    tokens = (msg.text or "").split()
    target, error = resolve_target(msg, tokens)
    if error:
        await msg.reply_text(error + "\n\n(برای انصراف /cancel)")
        return ASK_TARGET
    context.user_data["t_id"] = target.user_id
    context.user_data["t_name"] = target.name
    context.user_data["t_username"] = target.username
    await msg.reply_text(f"👤 هدف: {target.name}\n\nحالا نام و نام‌خانوادگی را بفرستید:")
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    name = (msg.text or "").strip()
    if len(name) < 2 or len(name) > 100:
        await msg.reply_text("❌ نام باید بین ۲ تا ۱۰۰ نویسه باشد. دوباره بفرستید:")
        return ASK_NAME
    context.user_data["t_fullname"] = name
    await msg.reply_text("🎂 سن کاربر را بفرستید (مثلاً 25) — یا برای رد کردن «-» بفرستید:")
    return ASK_AGE


async def ask_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = (msg.text or "").strip()
    if text in ("-", "—", "ندارم", "skip"):
        age = None
    else:
        age = ftext.to_int(text)
        if age is None or not (1 <= age <= 150):
            await msg.reply_text("❌ سن نامعتبر است. عددی بین ۱ تا ۱۵۰ بفرستید (یا «-»):")
            return ASK_AGE
    context.user_data["t_age"] = age
    name = context.user_data["t_fullname"]
    age_txt = f"{ftext.fa_digits(age)} سال" if age is not None else "ثبت نمی‌شود"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ثبت کن", callback_data="form:save"),
        InlineKeyboardButton("❌ انصراف", callback_data="form:cancel"),
    ]])
    await msg.reply_text(
        f"🧾 پیش‌نمایش:\n«{name}» — {age_txt}\nآیدی: {ftext.fa_digits(context.user_data['t_id'])}\n\nثبت شود؟",
        reply_markup=keyboard,
    )
    return ASK_CONFIRM


async def ask_confirm_remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("لطفاً یکی از دکمه‌ها را بزنید: «ثبت کن» یا «انصراف».")
    return ASK_CONFIRM


async def form_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    db.upsert_member(
        user_id=context.user_data["t_id"],
        username=context.user_data.get("t_username"),
        full_name=context.user_data.get("t_fullname") or context.user_data.get("t_name"),
        age=context.user_data.get("t_age"),
        added_by=user.id if user else None,
    )
    card = format_member_card(context.user_data["t_id"], db.get_member(context.user_data["t_id"]))
    clear_form_state(context)
    await query.edit_message_text(f"✅ عضو با موفقیت ثبت شد:\n\n{card}")
    return ConversationHandler.END


async def form_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clear_form_state(context)
    await query.edit_message_text("🚫 ثبت عضو لغو شد.")
    return ConversationHandler.END


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_form_state(context)
    await update.effective_message.reply_text("🚫 عملیات لغو شد.")
    return ConversationHandler.END


# ---------------------------------------------------------------- عضویت خودکار اعضا (/join)

async def join_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هر کاربر می‌تواند خودش را با یوزرنیم واقعی تلگرام خودش ثبت کند."""
    query = update.callback_query
    if query is not None:
        await query.answer()
        msg = query.message
    else:
        msg = update.effective_message
    user = update.effective_user
    if user is None or user.is_bot:
        await msg.reply_text("❌ متاسفانه نتوانستم اکانت تلگرام شما را تشخیص دهم.")
        return ConversationHandler.END

    existing = db.get_member(user.id)
    context.user_data["join_id"] = user.id
    context.user_data["join_username"] = (user.username or "").lower() or None
    if existing:
        text = (
            f"👤 شما از قبل ثبت‌نام کرده‌اید:\n\n{format_member_card(user.id, existing)}\n\n"
            "اگر می‌خواهید «نام» را تغییر دهید، حالا نام جدید را بفرستید\n"
            "(یا برای نگه‌داشتن نام فعلی فقط «-» را بفرستید):"
        )
    else:
        text = (
            f"✍️ ثبت‌نام در ربات — «{display_name(user)}»\n\n"
            "نام و نام‌خانوادگی‌تان را بفرستید\n"
            "(برای استفاده از همان نامی که در تلگرام دارید، فقط «-» را بفرستید):"
        )
    await msg.reply_text(text)
    return JOIN_NAME


async def join_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    text = (msg.text or "").strip()
    existing = db.get_member(context.user_data.get("join_id", 0)) if context.user_data.get("join_id") else None
    if text in ("-", "—"):
        name = (existing["full_name"] if existing else (display_name(user) if user else "عضو")).strip()
    elif len(text) < 2 or len(text) > 100:
        await msg.reply_text("❌ نام باید بین ۲ تا ۱۰۰ نویسه باشد. دوباره بفرستید (یا «-»):")
        return JOIN_NAME
    else:
        name = text
    context.user_data["join_fullname"] = name
    if existing and existing["age"] is not None:
        await msg.reply_text(
            f"🎂 سن فعلی شما: {ftext.fa_digits(existing['age'])} سال\n"
            "سن جدید را بفرستید (یا «-» برای نگه‌داشتن همین):"
        )
    else:
        await msg.reply_text("🎂 سن شما را بفرستید (مثلاً 25) — یا برای رد کردن «-» را بفرستید:")
    return JOIN_AGE


async def join_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    text = (msg.text or "").strip()
    join_id = context.user_data.get("join_id")
    if not join_id or not user:
        await msg.reply_text("❌ خطایی رخ داد. لطفاً دوباره /join را بفرستید.")
        return ConversationHandler.END
    existing = db.get_member(join_id)
    if text in ("-", "—", "ندارم"):
        age = existing["age"] if existing else None
    else:
        age = ftext.to_int(text)
        if age is None or not (1 <= age <= 150):
            await msg.reply_text("❌ سن نامعتبر است. عددی بین ۱ تا ۱۵۰ بفرستید (یا «-»):")
            return JOIN_AGE
    db.upsert_member(
        user_id=join_id,
        username=context.user_data.get("join_username"),
        full_name=context.user_data.get("join_fullname") or display_name(user),
        age=age,
    )
    fullname = context.user_data.get("join_fullname") or display_name(user)
    uname = context.user_data.get("join_username")
    uname_line = f"🔗 یوزرنیم: @{uname}" if uname else "🔗 یوزرنیم: (یوزرنیم ندارید — ناشناس)"
    age_txt = f"{ftext.fa_digits(age)} سال" if age is not None else "ثبت نشده"
    for key in ("join_id", "join_username", "join_fullname"):
        context.user_data.pop(key, None)
    await msg.reply_text(
        f"✅ ثبت‌نام شما انجام شد!\n\n"
        f"👤 {fullname}\n"
        f"🆔 آیدی: {ftext.fa_digits(user.id)}\n"
        f"{uname_line}\n"
        f"🎂 سن: {age_txt}"
    )
    return ConversationHandler.END


async def cmd_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ فقط ادمین‌ها می‌توانند اطلاعات اعضا را ببینند.")
        return
    target, error = resolve_target(msg, context.args or [])
    if error:
        await msg.reply_text(error)
        return
    member = db.get_member(target.user_id)
    await msg.reply_text(format_member_card(target.user_id, member))


async def cmd_setname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ فقط ادمین‌ها می‌توانند نام را تغییر دهند.")
        return
    target, error = resolve_target(msg, context.args or [])
    if error:
        await msg.reply_text(error)
        return
    new_name = " ".join(target.rest).strip()
    if len(new_name) < 2:
        await msg.reply_text("❌ نام جدید را بعد از هدف بنویسید. مثال: /setname @ali علی رضایی")
        return
    if db.set_name(target.user_id, new_name):
        await msg.reply_text(f"✅ نام به «{new_name}» تغییر کرد.")
    else:
        await msg.reply_text("❌ این کاربر در دیتابیس نیست؛ اول او را ثبت کنید: /addmember")


async def cmd_setage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ فقط ادمین‌ها می‌توانند سن را تغییر دهند.")
        return
    tokens = list(context.args or [])
    # اگر آرگومانی نیست و ریپلای شده، اولین توکن سن است
    target, error = resolve_target(msg, tokens)
    if error:
        await msg.reply_text(error)
        return
    if len(target.rest) != 1:
        await msg.reply_text("❌ مثال: /setage @ali 25 (یا ریپلای + /setage 25)")
        return
    age = ftext.to_int(target.rest[0])
    if age is None or not (1 <= age <= 150):
        await msg.reply_text("❌ سن باید عددی بین ۱ تا ۱۵۰ باشد.")
        return
    if db.set_age(target.user_id, age):
        await msg.reply_text(f"✅ سن {ftext.fa_digits(age)} سال ثبت شد.")
    else:
        await msg.reply_text("❌ این کاربر در دیتابیس نیست؛ اول او را ثبت کنید: /addmember")


async def cmd_delmember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ فقط ادمین‌ها می‌توانند عضو حذف کنند.")
        return
    target, error = resolve_target(msg, context.args or [])
    if error:
        await msg.reply_text(error)
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑 بله، حذف کن", callback_data=f"confirm_del:{target.user_id}"),
        InlineKeyboardButton("❌ انصراف", callback_data="del_cancel"),
    ]])
    await msg.reply_text(
        f"حذف «{target.name}» از دیتابیس؟\n(تاریخچه‌ی امتیازها نگه داشته می‌شود)",
        reply_markup=keyboard,
    )


async def del_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await is_admin(update, context):
        await query.edit_message_text("⛔️ فقط ادمین‌ها می‌توانند حذف کنند.")
        return
    if query.data == "del_cancel":
        await query.edit_message_text("🚫 حذف لغو شد.")
        return
    m = re.fullmatch(r"confirm_del:(\d+)", query.data or "")
    if not m:
        return
    uid = int(m.group(1))
    if db.delete_member(uid):
        await query.edit_message_text("✅ عضو حذف شد. تاریخچه‌ی امتیازها حفظ شد.")
    else:
        await query.edit_message_text("❌ این عضو در دیتابیس نبود.")


def build_list_page(page: int):
    total = db.count_members()
    if total == 0:
        return "هنوز عضوی ثبت نشده است.\nبرای افزودن: /addmember", None
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    rows = db.list_members(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    stats = db.stats_map([m["user_id"] for m in rows])   # یک کوئری گروهی به‌جای ۸ کوئری
    lines = [f"📋 لیست اعضا — صفحه {ftext.fa_digits(page + 1)} از {ftext.fa_digits(total_pages)} (کل: {ftext.fa_digits(total)} نفر)", ""]
    for i, m in enumerate(rows, start=page * PAGE_SIZE + 1):
        uname = f" — @{m['username']}" if m["username"] else ""
        age = f"{ftext.fa_digits(m['age'])} سال" if m["age"] is not None else "سن: نامشخص"
        s = stats[m["user_id"]]
        sign = "+" if s["final"] > 0 else ""
        lines.append(
            f"{ftext.fa_digits(i)}. {m['full_name']}{uname} — {age} — 🏁 {sign}{ftext.fa_digits(s['final'])}"
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("‹ صفحه قبل", callback_data=f"menu:list:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("صفحه بعد ›", callback_data=f"menu:list:{page + 1}"))
    keyboard_rows = []
    if nav:
        keyboard_rows.append(nav)
    keyboard_rows.append([
        InlineKeyboardButton("↩️ بازگشت", callback_data="menu:back"),
        InlineKeyboardButton("❌ بستن", callback_data="menu:close"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)


async def cmd_listmembers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ فقط ادمین‌ها می‌توانند لیست اعضا را ببینند.")
        return
    text, keyboard = build_list_page(0)
    await msg.reply_text(text, reply_markup=keyboard)


# ---------------------------------------------------------------- تغییر کلمه‌های دستورات

_WORD_KIND_ALIASES = {
    "warn": "warn", "اخطار": "warn",
    "strike": "strike", "استرایک": "strike",
    "plus": "plus", "پلاس": "plus", "امتیاز": "plus",
}

_WORDS_HINT = (
    "⚙️ تنظیم کلمه‌های دستورات (فقط ادمین اصلی):\n\n"
    "/setword warn کلمهٔ_جدید\n"
    "/setword strike کلمهٔ_جدید\n"
    "/setword plus کلمهٔ_جدید\n\n"
    "مثال: /setword warn حمله\n"
    "نکته: کلمه‌ی جدید باید یک «کلمه» باشد، نه جمله. /warn و /strike و /plus همیشه کار می‌کنند."
)


def words_summary() -> str:
    w = db.get_words()
    return (
        "⚙️ کلمه‌های فعلی دستورات:\n\n"
        f"🟡 اخطار   → «{w['warn']}»   (یا /warn)\n"
        f"🔴 استرایک → «{w['strike']}»   (یا /strike)\n"
        f"🟢 مثبت    → «{w['plus']}»   (یا /plus)"
    )


async def cmd_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_master(update, context):
        await update.effective_message.reply_text("⛔️ این دستور فقط برای ادمین اصلی است.")
        return
    await update.effective_message.reply_text(words_summary() + "\n\n" + _WORDS_HINT)


async def cmd_setword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_master(update, context):
        await msg.reply_text("⛔️ این دستور فقط برای ادمین اصلی است.")
        return
    args = list(context.args or [])
    if len(args) != 2:
        await msg.reply_text(
            "❌ فرمت درست:\n/setword warn کلمهٔ_جدید\n/setword strike کلمهٔ_جدید\n/setword plus کلمهٔ_جدید\n\nمثال: /setword warn حمله"
        )
        return
    kind = _WORD_KIND_ALIASES.get(args[0].strip().lower())
    new_word = args[1].strip()
    if kind is None:
        await msg.reply_text("❌ نوع دستور باید یکی از این‌ها باشد: warn | strike | plus")
        return
    if new_word.startswith("/") or "@" in new_word or len(new_word) > 32:
        await msg.reply_text("❌ کلمه‌ی جدید نامعتبر است (بدون @ و / و حداکثر ۳۲ نویسه).")
        return
    current = db.get_words()
    for other_kind in ("warn", "strike", "plus"):
        if other_kind != kind and current[other_kind] == new_word:
            await msg.reply_text(f"❌ این کلمه در حال حاضر برای «{KIND_META[other_kind]['label']}» استفاده می‌شود.")
            return
    db.set_setting(f"word_{kind}", new_word)
    await msg.reply_text(f"✅ کلمه‌ی دستور «{KIND_META[kind]['label']}» به «{new_word}» تغییر کرد.\n\n{words_summary()}")


# ---------------------------------------------------------------- منوی دکمه‌ای

def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("↩️ بازگشت", callback_data="menu:back"),
        InlineKeyboardButton("❌ بستن", callback_data="menu:close"),
    ]])


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await is_admin(update, context):
        await query.edit_message_text("⛔️ فقط ادمین‌ها می‌توانند از پنل استفاده کنند.")
        return
    data = query.data or ""
    master = await is_master(update, context)

    if data == "menu:close":
        await query.message.delete()
        return
    if data == "menu:back":
        await query.edit_message_text("🎛 پنل مدیریت:", reply_markup=main_menu_keyboard(master))
        return
    if data == "menu:help":
        await query.edit_message_text(render_help(), reply_markup=back_keyboard())
        return
    if data == "menu:all":
        await query.edit_message_text(build_all_report(), reply_markup=back_keyboard())
        return
    if data == "menu:words":
        if not master:
            await query.edit_message_text("⛔️ تنظیم کلمه‌ها فقط برای ادمین اصلی است.", reply_markup=back_keyboard())
            return
        await query.edit_message_text(words_summary() + "\n\n" + _WORDS_HINT, reply_markup=back_keyboard())
        return
    if data == "menu:ranking":
        text, keyboard = build_ranking_panel()
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    if data in ("menu:ranking:points", "menu:ranking:stats"):
        viewer_id = update.effective_user.id if update.effective_user else None
        if data == "menu:ranking:points":
            text = build_points_ranking(viewer_id)
        else:
            text = build_stats_ranking(viewer_id)
        await query.edit_message_text(text, reply_markup=back_keyboard())
        return
    if data == "menu:justice":
        text, keyboard = build_justice_panel()
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    if data == "menu:stats":
        text, keyboard = build_stats_panel()
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    m = re.fullmatch(r"menu:stats:all:(\d+)", data)
    if m:
        text, keyboard = build_all_stats(int(m.group(1)))
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    if data == "menu:justice:open":
        text, keyboard = build_justice_open()
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    m = re.fullmatch(r"menu:justice:hist:(\d+)", data)
    if m:
        text, keyboard = build_justice_history(int(m.group(1)))
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    m = re.fullmatch(r"menu:list:(\d+)", data)
    if m:
        text, keyboard = build_list_page(int(m.group(1)))
        await query.edit_message_text(text, reply_markup=keyboard)
        return


# ---------------------------------------------------------------- شروع برنامه

async def on_start(app):
    """رأی‌گیری‌های نیمه‌کاره بعد از ری‌استارت بازیابی می‌شوند."""
    recovered = db.open_judgments()
    for j in recovered:
        closes = datetime.fromisoformat(j["closes_at"])
        when = max(closes, datetime.now() + timedelta(seconds=2))
        schedule_judgment_close(app, j["id"], when)
    if recovered:
        logger.info("%d رأی‌گیری نیمه‌کاره بازیابی شد", len(recovered))
    proposals = db.open_proposals()
    for p in proposals:
        closes = datetime.fromisoformat(p["closes_at"])
        when = max(closes, datetime.now() + timedelta(seconds=2))
        schedule_proposal_close(app, p["id"], when)
    if proposals:
        logger.info("%d پیشنهاد آمار نیمه‌کاره بازیابی شد", len(proposals))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    # دکمه‌ای که محتوای یکسان را دوباره نمایش می‌دهد → خطا را نادیده بگیر
    if isinstance(context.error, BadRequest) and "not modified" in str(context.error).lower():
        logger.debug("پیامی تغییر نکرد — نادیده گرفته شد")
        return
    # خطاهای شبکه: پیام دوستانه به‌جای «خطای داخلی»
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning("مشکل شبکه: %s", context.error)
        try:
            if isinstance(update, Update) and update.effective_message:
                await update.effective_message.reply_text(
                    "🔌 اتصال اینترنت ناپایدار است. لطفاً چند ثانیه دیگر دوباره تلاش کنید."
                )
        except Exception:
            pass
        return
    logger.exception("خطای داخلی ربات", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("❌ خطای داخلی رخ داد. لطفاً دوباره تلاش کنید.")
    except Exception:
        pass


def main() -> None:
    # Python 3.14+ دیگر لوپ جدید به‌صورت خودکار نمی‌سازد؛ صراحتاً می‌سازیم
    import asyncio

    asyncio.set_event_loop(asyncio.new_event_loop())

    # تایم‌اوت‌های طولانی‌تر برای اینترنت‌های ناپایدار (پیش‌فرض تلگرام ۵ ثانیه است)
    request = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=10.0,
    )
    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .request(request)
        .post_init(on_start)
        .build()
    )

    ability_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("ability", ability_entry),
            CallbackQueryHandler(ability_entry, pattern="^ability:prompt$"),
        ],
        states={
            ASK_ABILITY_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ability_text)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
        allow_reentry=True,
    )
    app.add_handler(ability_conversation)

    join_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("join", join_entry),
            CallbackQueryHandler(join_entry, pattern="^join:start$"),
        ],
        states={
            JOIN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, join_name)],
            JOIN_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, join_age)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
        allow_reentry=True,
    )
    app.add_handler(join_conversation)

    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("addmember", addmember_entry),
            CallbackQueryHandler(addmember_entry, pattern="^menu:add$"),
        ],
        states={
            ASK_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_target)],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_age)],
            ASK_CONFIRM: [
                CallbackQueryHandler(form_save, pattern="^form:save$"),
                CallbackQueryHandler(form_cancel, pattern="^form:cancel$"),
                MessageHandler(filters.ALL, ask_confirm_remind),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", conv_cancel),
            CallbackQueryHandler(form_cancel, pattern="^form:cancel$"),
        ],
        allow_reentry=True,
    )
    app.add_handler(conversation)

    simple_commands = [
        ("start", cmd_start), ("help", cmd_help), ("id", cmd_id), ("panel", cmd_panel), ("menu", cmd_menu),
        ("mypoints", cmd_mypoints), ("points", cmd_points), ("report", cmd_report),
        ("history", cmd_history), ("justice", cmd_justice), ("member", cmd_member),
        ("stats", cmd_stats), ("mystats", cmd_mystats), ("stat", cmd_stat), ("allstats", cmd_allstats),
        ("ranking", cmd_ranking), ("statsranking", cmd_statsranking),
        ("listmembers", cmd_listmembers), ("setname", cmd_setname), ("setage", cmd_setage),
        ("delmember", cmd_delmember), ("words", cmd_words), ("setword", cmd_setword),
        ("warn", cmd_warn), ("strike", cmd_strike), ("plus", cmd_plus),
    ]
    for name, handler in simple_commands:
        app.add_handler(CommandHandler(name, handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, word_router), group=1)
    app.add_handler(CallbackQueryHandler(stat_vote_callback, pattern=r"^svote:(approve|reject|close):\d+$"))
    app.add_handler(CallbackQueryHandler(vote_callback, pattern=r"^jvote:(strike|chance|close):\d+$"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:(close|back|help|all|words|justice(:open|:hist:\d+)?|stats(:all:\d+)?|ranking(:points|:stats)?|list:\d+)$"))
    app.add_handler(CallbackQueryHandler(del_callback, pattern=r"^(confirm_del:\d+|del_cancel)$"))

    app.add_error_handler(error_handler)
    logger.info("ربات در حال اجراست… (برای خروج: Ctrl+C)")
    app.run_polling(drop_pending_updates=True, poll_interval=0.5, timeout=30)


if __name__ == "__main__":
    main()

