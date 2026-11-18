"""ابزارهای متن فارسی: ارقام فارسی، تاریخ شمسی و تبدیل اعداد."""
from datetime import datetime

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
FA_TO_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def fa_digits(value) -> str:
    """تبدیل اعداد لاتین به فارسی برای نمایش."""
    return str(value).translate(FA_DIGITS)


def to_ascii_digits(text: str) -> str:
    """تبدیل ارقام فارسی/عربی ورودی کاربر به لاتین تا قابل پردازش باشد."""
    return str(text).translate(FA_TO_ASCII).translate(AR_DIGITS)


def to_int(text: str):
    """ورودی‌ی عددی (با ارقام فارسی/عربی/لاتین) را به int تبدیل می‌کند؛ در صورت نامعتبری None."""
    cleaned = to_ascii_digits(str(text)).strip()
    if cleaned.isdigit():
        return int(cleaned)
    return None


def fa_datetime(dt: datetime) -> str:
    """تاریخ و ساعت به شمسی (اگر jdatetime نصب باشد) وگرنه میلادی."""
    try:
        import jdatetime

        jdt = jdatetime.datetime.fromgregorian(datetime=dt)
        return fa_digits(jdt.strftime("%Y/%m/%d — %H:%M"))
    except Exception:
        return fa_digits(dt.strftime("%Y-%m-%d %H:%M"))


def stat_bar(value: int, width: int = 10) -> str:
    """نوار متنی ۰ تا ۱۰۰ برای نمایش آمار (مثلاً ████████░░ 80%)."""
    value = max(0, min(100, int(value)))
    filled = round(value / 100 * width)
    return "█" * filled + "░" * (width - filled)
