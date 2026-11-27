"""ربات مدیریت امتیاز کانال — BBH

قابلیت‌ها:
  • ثبت اخطار (۱-)، استرایک (۳-) و امتیاز مثبت (۱+) توسط ادمین‌ها
  • رأی‌گیری استرایک با حذف خودکار بعد از مهلت
  • حذف مستقیم استرایک (Unstrike) توسط ادمین‌ها
  • گزارش نهایی هر عضو با تفکیک کامل محاسبات
  • رتبه‌بندی و آمار اعضاء (۰ تا ۱۰۰)
  • capacidad persoonли (توانایی شخصی)
  • خبر روز با حذف خودکار بعد از ۲۴ ساعت
"""
import logging
import re
import time
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
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
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

db = database.db

KIND_META = {
    "warn":   {"label": "اخطار",       "emoji": "🟡"},
    "strike": {"label": "استرایک",     "emoji": "🔴"},
    "plus":   {"label": "امتیاز مثبت", "emoji": "🟢"},
}

# خبر روز — خود حذف شدن بعد از ۲۴ ساعت
NEWS_HOURS = 24

# رأی‌گیری استرایک
STRIKE_VOTE_MINUTES = 5

# رأی‌گیری آمار
STAT_VOTE_MINUTES = 5

# مجازات‌های استرایک
STRIKE_MUTE_LIMIT = 3   # ۳ استرایک باقی‌مانده → میوت ۱ روزه
STRIKE_BAN_LIMIT = 5    # ۵ استرایک باقی‌مانده → اخراج


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


# کش وضعیت ادمینی
_admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}
_ADMIN_TTL = 120.0
_ADMIN_CACHE_MAX = 512


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
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
    except Exception as exc:
        logger.warning("get_chat_member failed: %s", exc)
        ok = False
    if len(_admin_cache) >= _ADMIN_CACHE_MAX:
        _admin_cache.clear()
    _admin_cache[key] = (ok, now)
    return ok


async def is_master(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
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
            f"اول او را ثبت کنید (از طریق ربات اصلی یا Mini App)."
        )
    return None, (
        "❓ نتوانستم هدف را تشخیص دهم.\n"
        "روش‌ها: ریپلای به پیام کاربر، یا نوشتن @یوزرنیم، یا آیدی عددی.\n"
        "مثال: /points @example یا ریپلای + /points"
    )


def identity_for(target_id: int, fallback_name: str | None = None):
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


def main_menu_keyboard(master: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📊 گزارش همه", callback_data="menu:all"),
         InlineKeyboardButton("📈 آمار اعضا", callback_data="menu:stats")],
        [InlineKeyboardButton("🏆 رتبه‌بندی", callback_data="menu:ranking")],
    ]
    if master:
        rows.append([InlineKeyboardButton("📢 خبر روز", callback_data="menu:news")])
    rows.append([InlineKeyboardButton("❌ بستن", callback_data="menu:close")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------- /start، /id، /menu

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    admin = await is_admin(update, context)
    text = (
        "به اعضای برادری BBH خوش آمدید 👋\n\n"
        "من ربات مدیریت امتیاز این کانال هستم؛ اخطار، استرایک و امتیاز مثبت اعضا را ثبت و محاسبه می‌کنم.\n\n"
        f"🆔 آیدی عددی شما: {ftext.fa_digits(user.id) if user else '—'}\n\n"
        "برای دیدن پنل مدیریت، /menu را بفرستید.\n\n"
        "━━━━━━━━━━━━━━\n"
        "طراحی شده توسط کوروش ❤️"
    )
    if admin:
        master = await is_master(update, context)
        await update.effective_message.reply_text(text, reply_markup=main_menu_keyboard(master))
    else:
        await update.effective_message.reply_text(text)


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
    from telegram import WebAppInfo
    startapp = "https://t.me/azizmstrikebot?startapp"
    pages_url = "https://kouroshsamadof.github.io/bbh-mini-app/"
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup", "channel"):
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
        return "هنوز عضوی در دیتابیس ثبت نشده است."
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


# ---------------------------------------------------------------- 📈 آمار اعضا (0 تا 100)

STAT_META = {
    "retardness": {"en": "RETARDNESS", "emoji": "🐢", "fa": "عقب‌mondگی"},
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
STAT_INCREMENT = 5


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
        return "هنوز عضوی ثبت‌نام نکرده است.", None
    stats_map = db.get_all_stats_map()
    total_pages = (total + STATS_PAGE_SIZE - 1) // STATS_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    members = db.list_members(limit=STATS_PAGE_SIZE, offset=page * STATS_PAGE_SIZE)
    member_ids = [m["user_id"] for m in members]
    abilities = db.abilities_map(member_ids)
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
        return
    approve, reject = db.stat_vote_counts(pid)
    applied = approve > reject
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


# ---------------------------------------------------------------- 📢 خبر روز (/news)

MAX_NEWS_LEN = 500


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    args = list(context.args or [])
    if not args:
        news_list = db.list_all_news(chat.id if chat else None, limit=5)
        if news_list:
            lines = ["\u00f0\u0178\u0093\u00a2 Last news:", ""]
            for n in news_list:
                author = n["author_name"] or "Unknown"
                lines.append("\u00f0\u0178\u0093\u00b0 " + n["content"])
                lines.append("   \u00f0\u0178\u0091\u00a4 By: " + author + " | \u00f0\u0178\u0097\u0093 " + ftext.fa_datetime(datetime.fromisoformat(n["created_at"])))
                lines.append("")
            await msg.reply_text("\n".join(lines))
        else:
            await msg.reply_text(
                "No news yet.\n"
                "Usage: /news your news text"
            )
        return

    content_text = " ".join(args).strip()
    if len(content_text) < 2:
        await msg.reply_text("News text too short.")
        return
    if len(content_text) > MAX_NEWS_LEN:
        await msg.reply_text("News text too long (max " + str(MAX_NEWS_LEN) + " chars).")
        return

    author_id = user.id if user else None
    author_name = display_name(user) if user else "Unknown"

    sent = await msg.reply_text(
        "\u00f0\u0178\u0093\u00a2 News:\n\n" + content_text + "\n\n"
        "\u00f0\u0178\u0091\u00a4 By: " + author_name
    )

    news_id = db.create_news(
        chat_id=chat.id if chat else 0,
        content=content_text,
        author_id=author_id,
        author_name=author_name,
        message_id=sent.message_id,
    )

    logger.info("News #%s posted in chat %s by %s", news_id, chat.id if chat else 0, author_name)


# ---------------------------------------------------------------- ثبت اخطار / استرایک / امتیاز / حذف استرایک

_MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False, can_send_audios=False, can_send_documents=False,
    can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
    can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
    can_add_web_page_previews=False, can_invite_users=False,
)


def record_point(target, admin_id, admin_name, kind: str, reason: str | None, chat_id: int | None):
    db.ensure_stub(target.user_id, target.name, target.username)
    db.add_point(
        target_id=target.user_id, target_name=target.name, chat_id=chat_id,
        admin_id=admin_id, admin_name=admin_name, kind=kind, reason=reason,
    )
    if target.username or target.name:
        db.upsert_member(target.user_id, target.username, target.name, None)
    return db.summary_for(target.user_id)


async def maybe_punish(context: ContextTypes.DEFAULT_TYPE, chat_id: int, target, stats: dict) -> list[str]:
    cur = stats["strikes_remaining"]
    if cur < STRIKE_MUTE_LIMIT:
        return []
    try:
        tmember = await context.bot.get_chat_member(chat_id, target.user_id)
        st = getattr(tmember.status, "value", str(tmember.status)).lower()
        if st in ("creator", "owner", "administrator"):
            return [f"ℹ️ «{target.name}» به {ftext.fa_digits(cur)} استرایک رسید، اما چون ادمین است مجازات نشد."]
    except Exception as exc:
        logger.warning("get_chat_member(target) failed: %s", exc)
        return [f"⚠️ «{target.name}» به {ftext.fa_digits(cur)} استرایک رسید، اما وضعیت ادمینی او مشخص نشد؛ مجازات اعمال نشد."]
    if cur >= STRIKE_BAN_LIMIT:
        try:
            await context.bot.ban_chat_member(chat_id, target.user_id)
            return [f"🔨 «{target.name}» به دلیل رسیدن به {ftext.fa_digits(cur)} استرایک، از گروه اخراج شد."]
        except Exception as exc:
            logger.warning("ban failed: %s", exc)
            return [f"⚠️ «{target.name}» به {ftext.fa_digits(cur)} استرایک رسید — اخراج خودکار انجام نشد."]
    try:
        await context.bot.restrict_chat_member(
            chat_id, target.user_id, permissions=_MUTE_PERMISSIONS,
            until_date=datetime.now() + timedelta(days=1),
        )
        return [f"🔇 «{target.name}» به دلیل رسیدن به {ftext.fa_digits(cur)} استرایک، ۱ روز میوت شد."]
    except Exception as exc:
        logger.warning("mute failed: %s", exc)
        return [f"⚠️ «{target.name}» به {ftext.fa_digits(cur)} استرایک رسید — میوت خودکار انجام نشد."]


async def do_action(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    msg = update.effective_message
    if msg is None:
        return
    if kind != "plus" and not await is_admin(update, context):
        await msg.reply_text("Only admins can give warnings or strikes.")
        return
    target, error = resolve_target(msg, context.args or [])
    if error:
        words = db.get_words()
        example_word = words[kind]
        await msg.reply_text(f"{error}\n\nمثال: {example_word} @یوزرنیم دلیل")
        return
    reason = " ".join(target.rest).strip() or None
    if not reason and kind == "plus":
        await msg.reply_text("Please provide a reason for the plus point.\nExample: /plus @user good teamwork")
        return
    if not reason and kind == "plus":
        await msg.reply_text("Please provide a reason for the plus point.\nExample: /plus @user good teamwork")
        return
    user = update.effective_user
    if posted_as_chat(update):
        admin_id = None
        admin_name = update.effective_chat.title if update.effective_chat else "کانال"
    else:
        admin_id = user.id if user else None
        admin_name = display_name(user) if user else "ادمین"

    # استرایک → باز کردن رأی‌گیری (نه ثبت فوری)
    if kind == "strike":
        existing = db.open_strike_vote_for(chat_id=update.effective_chat.id if update.effective_chat else 0,
                                          target_id=target.user_id)
        if existing:
            await msg.reply_text(
                f"❗️ قبلاً یک رأی‌گیری استرایک باز برای «{target.name}» وجود دارد.\n"
                "تا اعلام نتیجه‌ی آن صبر کنید."
            )
            return
        closes = datetime.now() + timedelta(minutes=STRIKE_VOTE_MINUTES)
        vid = db.create_strike_vote(
            chat_id=update.effective_chat.id if update.effective_chat else 0,
            target_id=target.user_id, target_name=target.name,
            proposer_id=admin_id, proposer_name=admin_name, reason=reason,
            closes_at=closes.isoformat(timespec="seconds"),
        )
        v = db.get_strike_vote(vid)
        sent = await msg.reply_text(
            strike_vote_view(v, 0, 0, 0), reply_markup=strike_vote_keyboard(vid)
        )
        db.set_strike_vote_message(vid, sent.message_id)
        schedule_strike_vote_close(context.application, vid, closes)
        logger.info("رای‌گیری استرایک #%s برای کاربر %s در چت %s باز شد", vid, target.user_id, chat_id if update.effective_chat else 0)
        return

    stats = record_point(target, admin_id, admin_name, kind, reason,
                         update.effective_chat.id if update.effective_chat else None)
    meta = KIND_META[kind]
    sign = "+" if scoring.POINTS[kind] > 0 else ""
    lines = [f"✅ {meta['label']} برای «{target.name}» ثبت شد ({sign}{ftext.fa_digits(scoring.POINTS[kind])})."]
    if reason:
        lines.append(f"💬 دلیل: {reason}")
    lines.append("")
    lines.append(f"Given by: {admin_name}")
    lines.append("")
    lines.append(compact_stats_line(stats))
    await msg.reply_text("\n".join(lines))


async def cmd_warn(update, context):
    await do_action(update, context, "warn")


async def cmd_strike(update, context):
    await do_action(update, context, "strike")


async def cmd_unstrike(update, context):
    """حذف مستقیم یک استرایک از کاربر (فقط ادمین اصلی)"""
    msg = update.effective_message
    if not await is_master(update, context):
        await msg.reply_text("⛔️ فقط ادمین اصلی می‌تواند استرایک حذف کند.")
        return
    target, error = resolve_target(msg, context.args or [])
    if error:
        await msg.reply_text(error)
        return
    reason = " ".join(target.rest).strip() or None
    user = update.effective_user
    admin_id = user.id if user else None
    admin_name = display_name(user) if user else "ادمین"
    result = db.remove_strike(target.user_id, reason=reason, admin_id=admin_id,
                              admin_name=admin_name,
                              chat_id=update.effective_chat.id if update.effective_chat else None)
    if not result:
        await msg.reply_text("❌ این کاربر در دیتابیس نیست.")
        return
    lines = [
        f"🟢 یک استرایک برای «{target.name}» حذف شد."
    ]
    if reason:
        lines.append(f"💬 دلیل: {reason}")
    lines.append("")
    lines.append(compact_stats_line(result))
    await msg.reply_text("\n".join(lines))


async def cmd_delstrike(update, context):
    await cmd_unstrike(update, context)


async def cmd_clearstrike(update, context):
    msg = update.effective_message
    if not await is_master(update, context):
        await msg.reply_text("Only master admin can clear strikes.")
        return
    target, error = resolve_target(msg, context.args or [])
    if error:
        await msg.reply_text(error)
        return
    reason = " ".join(target.rest).strip() or None
    user = update.effective_user
    admin_id = user.id if user else None
    admin_name = display_name(user) if user else "admin"
    result = db.clear_strikes(target.user_id, reason=reason, admin_id=admin_id,
                              admin_name=admin_name,
                              chat_id=update.effective_chat.id if update.effective_chat else None)
    if not result:
        await msg.reply_text("User not found.")
        return
    lines = ["All strikes cleared for " + target.name + "."]
    if reason:
        lines.append("Reason: " + reason)
    lines.append("")
    lines.append(compact_stats_line(result))
    await msg.reply_text("\n".join(lines))


async def cmd_plus(update, context):
    await do_action(update, context, "plus")


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/kick @user or reply — kicks (bans) a user from the group (admin only)."""
    msg = update.effective_message
    chat = update.effective_chat
    if not await is_admin(update, context):
        await msg.reply_text("⛔️ فقط ادمین‌ها می‌توانند کاربر را اخراج کنند.")
        return
    if chat is None or chat.type == "private":
        await msg.reply_text("❌ اخراج فقط داخل گروه قابل انجام است.")
        return
    target, error = resolve_target(msg, context.args or [])
    if error:
        await msg.reply_text(error)
        return
    # Check if target is an admin
    try:
        tmember = await context.bot.get_chat_member(chat.id, target.user_id)
        st = getattr(tmember.status, "value", str(tmember.status)).lower()
        if st in ("creator", "owner", "administrator"):
            await msg.reply_text("⛔️ نمی‌توانید ادمین را اخراج کنید.")
            return
    except Exception:
        pass
    try:
        await context.bot.ban_chat_member(chat.id, target.user_id)
        await msg.reply_text(f"🔨 «{target.name}» از گروه اخراج شد.")
        logger.info("User %s kicked from chat %s by %s", target.user_id, chat.id, update.effective_user.id if update.effective_user else None)
    except Exception as exc:
        logger.warning("kick failed: %s", exc)
        await msg.reply_text(f"❌ اخراج ناموفق — ربات اجازه‌ی اخراج ندارد.\n({exc})")


async def word_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and user.is_bot:
        return
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
    # Persian keywords for delete/clear strike
    if parts[0] in ("حذف استرایک", "delstrike"):
        context.args = parts[1:]
        await cmd_delstrike(update, context)
        return
    if parts[0] in ("پاک کردن استرایک", "clearstrike"):
        context.args = parts[1:]
        await cmd_clearstrike(update, context)
        return
    kind = mapping.get(parts[0])
    if kind is None:
        return
    context.args = parts[1:]
    await do_action(update, context, kind)


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


# ---------------------------------------------------------------- رأی‌گیری استرایک (strike voting)

def strike_vote_keyboard(strike_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 استرایک بزنید", callback_data=f"jvote:strike:{strike_id}"),
            InlineKeyboardButton("🟢 پاک کردن", callback_data=f"jvote:clear:{strike_id}"),
        ],
        [InlineKeyboardButton("🏁 اعلام نتیجه (ادمین‌ها)", callback_data=f"jvote:close:{strike_id}")],
    ])


def strike_vote_view(j, strikes: int, clears: int, voters: int) -> str:
    try:
        ends = ftext.fa_datetime(datetime.fromisoformat(j["closes_at"]))
        timing = f"⏳ پایان رأی‌گیری: {ends}"
    except ValueError:
        timing = "⏳ پایان رأی‌گیری: به‌زودی"
    lines = [
        f"⚖️ رأی‌گیری استرایک برای «{j['target_name']}»",
        f"👮 پیشنهاددهنده: {j['proposer_name'] or 'ادمین'}",
    ]
    if j["reason"]:
        lines.append(f"💬 دلیل: {j['reason']}")
    lines.append("")
    lines.append(
        f"🔴 استرایک: {ftext.fa_digits(strikes)} رأی    "
        f"🟢 پاک کردن: {ftext.fa_digits(clears)} رأی"
    )
    lines.append(f"👥 رأی‌دهندگان: {ftext.fa_digits(voters)} نفر")
    lines.append("")
    lines.append(f"🟦 رأی‌گیری باز است ({ftext.fa_digits(STRIKE_VOTE_MINUTES)} دقیقه)")
    lines.append(timing)
    lines.append("(خودِ فرد نمی‌تواند رأی دهد؛ رأی تا بسته شدن قابل تغییر است.)")
    return "\n".join(lines)


def schedule_strike_vote_close(app, strike_id: int, when: datetime):
    try:
        app.job_queue.run_once(
            strike_vote_close_job, when=when, data=strike_id,
            name=f"strike_{strike_id}",
        )
    except Exception as exc:
        logger.warning("برنامه‌ریزی بستن رأی‌گیری استرایک ناموفق: %s", exc)


async def strike_vote_close_job(context: ContextTypes.DEFAULT_TYPE):
    await finalize_strike_vote(context, context.job.data)


async def strike_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    m = re.fullmatch(r"jvote:(strike|clear|close):(\d+)", data)
    if not m:
        return
    action, vid = m.group(1), int(m.group(2))
    j = db.get_strike_vote(vid)
    if not j:
        await query.answer("این رأی‌گیری پیدا نشد!", show_alert=True)
        return
    user = update.effective_user
    if action == "close":
        if not await is_admin(update, context):
            await query.answer("⛔️ فقط ادمین‌ها می‌توانند نتیجه را اعلام کنند.", show_alert=True)
            return
        await query.answer()
        await finalize_strike_vote(context, vid)
        return
    if j["status"] == "closed":
        await query.answer("این رأی‌گیری بسته شده است.", show_alert=True)
        return
    if user is None or user.is_bot:
        await query.answer("ربات‌ها رأی نمی‌دهند.", show_alert=True)
        return
    if user.id == j["target_id"]:
        await query.answer("شما نمی‌توانید به رأی‌گیری خودتان رأی دهید.", show_alert=True)
        return
    db.upsert_strike_vote(vid, user.id, display_name(user), action)
    await query.answer("✅ رأی شما ثبت شد.")
    strikes, clears, voters = db.strike_vote_counts(vid)
    j = db.get_strike_vote(vid)
    try:
        await query.edit_message_text(
            strike_vote_view(j, strikes, clears, voters), reply_markup=strike_vote_keyboard(vid),
        )
    except BadRequest:
        pass
    except Exception as exc:
        logger.warning("به‌روزرسانی پیام رأی‌گیری استرایک ناموفق: %s", exc)


async def finalize_strike_vote(context: ContextTypes.DEFAULT_TYPE, vid: int):
    j = db.get_strike_vote(vid)
    if not j or j["status"] == "closed":
        return
    closes_at = datetime.fromisoformat(j["closes_at"])
    if closes_at > datetime.now() + timedelta(seconds=2):
        return
    strikes, clears, voters = db.strike_vote_counts(vid)
    if strikes > clears:
        winner = "strike"
    elif clears > strikes:
        winner = "clear"
    else:
        winner = "strike"  # مساوی → استرایک ثبت می‌شود

    db.close_strike_vote(vid, winner)
    votes_line = f"🔴 {ftext.fa_digits(strikes)} — 🟢 {ftext.fa_digits(clears)}   (👥 {ftext.fa_digits(voters)} رأی‌دهنده)"

    target = Target(j["target_id"], j["target_name"], None, [])
    admin_name = j["proposer_name"] or "رای‌گیری"

    if winner == "clear":
        result_lines = [
            f"🟢 رأی‌گیری استرایک بسته شد: «{j['target_name']}» پاک شد (استرایک حذف شد).",
            votes_line,
        ]
        # حذف یک استرایک از کاربر
        result = db.remove_strike(target.user_id, reason="رای‌گیری پاک کردن استرایک",
                                  admin_id=j["proposer_id"], admin_name=admin_name,
                                  chat_id=j["chat_id"])
        if result:
            result_lines.append("")
            result_lines.append(compact_stats_line(result))
    else:
        result_lines = [
            f"🔴 رأی‌گیری استرایک بسته شد: استرایک برای «{j['target_name']}» ثبت شد.",
            votes_line,
        ]
        stats = record_point(target, j["proposer_id"], admin_name, "strike",
                             j["reason"], j["chat_id"])
        result_lines.append("")
        result_lines.append(compact_stats_line(stats))
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
            logger.warning("ویرایش پیام نتیجه استرایک ناموفق: %s", exc)
    if not delivered:
        try:
            await context.application.bot.send_message(chat_id=j["chat_id"], text=result_text)
        except Exception as exc:
            logger.warning("ارسال پیام نتیجه استرایک ناموفق: %s", exc)


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
    if data == "menu:all":
        await query.edit_message_text(build_all_report(), reply_markup=back_keyboard())
        return
    if data == "menu:news":
        if not master:
            await query.edit_message_text("⛔️ خبر روز فقط برای ادمین اصلی است.", reply_markup=back_keyboard())
            return
        chat = update.effective_chat
        current = db.get_active_news(chat.id) if chat else None
        if current:
            try:
                expires = ftext.fa_datetime(datetime.fromisoformat(current["expires_at"]))
            except ValueError:
                expires = current["expires_at"]
            await query.edit_message_text(
                f"📢 خبر فعلی:\n\n{current['content']}\n\n⏳ تا: {expires}",
                reply_markup=back_keyboard(),
            )
        else:
            await query.edit_message_text("❓ خبر فعالی ثبت نشده است.", reply_markup=back_keyboard())
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
    if data == "menu:stats":
        text, keyboard = build_stats_panel()
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    m = re.fullmatch(r"menu:stats:all:(\d+)", data)
    if m:
        text, keyboard = build_all_stats(int(m.group(1)))
        await query.edit_message_text(text, reply_markup=keyboard)
        return


# ---------------------------------------------------------------- شروع برنامه

async def on_start(app):
    """رای‌گیری‌های ناقصه بعد از ری‌استارت بازیابی می‌شوند."""
    proposals = db.open_proposals()
    for p in proposals:
        closes = datetime.fromisoformat(p["closes_at"])
        when = max(closes, datetime.now() + timedelta(seconds=2))
        schedule_proposal_close(app, p["id"], when)
    if proposals:
        logger.info("%d پیشنهاد آمار نیمه‌کاره بازیابی شد", len(proposals))

    # ripristino strike votes باز
    strikes = db.open_strike_votes()
    for s in strikes:
        closes = datetime.fromisoformat(s["closes_at"])
        when = max(closes, datetime.now() + timedelta(seconds=2))
        schedule_strike_vote_close(app, s["id"], when)
    if strikes:
        logger.info("%d رأی‌گیری استرایک نیمه‌کاره بازیابی شد", len(strikes))



async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, BadRequest) and "not modified" in str(context.error).lower():
        logger.debug("پیامی تغییر نکرد — نادیده گرفته شد")
        return
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
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    request = HTTPXRequest(
        connection_pool_size=16, connect_timeout=20.0, read_timeout=20.0,
        write_timeout=20.0, pool_timeout=10.0,
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

    simple_commands = [
        ("start", cmd_start), ("id", cmd_id), ("panel", cmd_panel), ("menu", cmd_menu),
        ("mypoints", cmd_mypoints), ("points", cmd_points), ("report", cmd_report),
        ("history", cmd_history), ("member", cmd_member),
        ("stats", cmd_stats), ("mystats", cmd_mystats), ("stat", cmd_stat), ("allstats", cmd_allstats),
        ("ranking", cmd_ranking), ("statsranking", cmd_statsranking),
        ("warn", cmd_warn), ("strike", cmd_strike), ("unstrike", cmd_unstrike), ("delstrike", cmd_delstrike), ("clearstrike", cmd_clearstrike), ("plus", cmd_plus), ("kick", cmd_kick),
        ("news", cmd_news),
    ]
    for name, handler in simple_commands:
        app.add_handler(CommandHandler(name, handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, word_router), group=1)
    app.add_handler(CallbackQueryHandler(stat_vote_callback, pattern=r"^svote:(approve|reject|close):\d+$"))
    app.add_handler(CallbackQueryHandler(strike_vote_callback, pattern=r"^jvote:(strike|clear|close):\d+$"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:(close|back|all|news|ranking(:points|:stats)?|stats(:all:\d+)?)$"))
    app.add_handler(CallbackQueryHandler(ability_entry, pattern="^ability:prompt$"))

    app.add_error_handler(error_handler)
    logger.info("ربات در حال اجراست… (برای خروج: Ctrl+C)")
    app.run_polling(drop_pending_updates=True, poll_interval=0.5, timeout=30)


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("🚫 عملیات لغو شد.")
    return ConversationHandler.END


if __name__ == "__main__":
    main()
