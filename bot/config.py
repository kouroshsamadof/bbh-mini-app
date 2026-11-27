"""پیکربندی ربات — همه‌چیز از متغیرهای محیطی (.env) خوانده می‌شود."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()

def _parse_id_list(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            print(f"⚠️  آیدی عددی نامعتبر در MASTER_ADMIN_IDS نادیده گرفته شد: {part!r}")
    return ids

def _parse_username_list(raw: str) -> set[str]:
    return {
        u.strip().lstrip("@").lower()
        for u in raw.split(",")
        if u.strip()
    }

# Default main admin ID for @kouroshhsam
_default_admin_ids = os.getenv("MASTER_ADMIN_IDS", "").strip()
if not _default_admin_ids:
    _default_admin_ids = "8680586669"
MASTER_ADMIN_IDS = _parse_id_list(_default_admin_ids)
_raw_usernames = os.getenv("MASTER_ADMIN_USERNAMES", "").strip()
# Always ensure kouroshhsam is a main admin
if "kouroshhsam" not in _raw_usernames.lower():
    _raw_usernames = (_raw_usernames + ",kouroshhsam").strip(",")
MASTER_ADMIN_USERNAMES = _parse_username_list(_raw_usernames)

DB_PATH = (os.getenv("DB_PATH") or "bot_data.db").strip()

# کلمات پیش‌فرض دستورات (قابل تغییر در خودِ ربات با /setword)
DEFAULT_WARN_WORD = os.getenv("DEFAULT_WARN_WORD", "اخطار").strip() or "اخطار"
DEFAULT_STRIKE_WORD = os.getenv("DEFAULT_STRIKE_WORD", "استرایک").strip() or "استرایک"
DEFAULT_PLUS_WORD = os.getenv("DEFAULT_PLUS_WORD", "امتیاز").strip() or "امتیاز"

if not BOT_TOKEN or "replace-me" in BOT_TOKEN or "your-real-token" in BOT_TOKEN:
    print("❌ خطا: توکن ربات در فایل .env تنظیم نشده است.")
    print("۱) در تلگرام به @BotFather پیام بدهید و با /newbot ربات بسازید")
    print("۲) فایل .env را باز کنید و BOT_TOKEN را با توکن دریافتی جایگزین کنید")
    print("۳) دوباره python bot.py را اجرا کنید")
    sys.exit(1)
