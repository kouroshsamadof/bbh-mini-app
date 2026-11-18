"""موتور محاسبه‌ی امتیازها.

قانون‌ها (نسخه‌ی جدید):
  اخطار   = ۱ امتیاز منفی
  استرایک = ۳ امتیاز منفی
  امتیاز مثبت = ۱ امتیاز مثبت

پاک‌سازی (هر عضو):
  هر ۵ امتیاز مثبت = پاک کردن ۱ استرایک   ← قانون جدید
  هر ۳ امتیاز مثبت باقی‌مانده = پاک کردن ۱ اخطار

  الگوریتم: اول استرایک‌ها را پاک می‌کنیم (هر ۵ تا پلاس برای یک استرایک)،
  سپس پلاس‌های باقی‌مانده صرف اخطارها می‌شوند (هر ۳ تا پلاس برای یک اخطار).

  strikes_erased = min(strikes, positive // 5)
  remaining      = positive - strikes_erased * 5
  warns_erased   = min(warns, remaining // 3)
  remaining_negative = (strikes - strikes_erased) * 3 + (warns - warns_erased)
  final          = positive - remaining_negative

مجازات:
  اگر استرایک‌های باقی‌مانده (بعد از پاک‌سازی) به ۳ برسد → میوت ۱ روزه
  اگر به ۵ برسد → اخراج از گروه
"""

WARN_COST = 1
STRIKE_COST = 3
PLUS_VALUE = 1
PLUS_PER_STRIKE_ERASE = 5   # هر ۵ امتیاز مثبت = پاک کردن ۱ استرایک
PLUS_PER_WARN_ERASE = 3     # هر ۳ امتیاز مثبت = پاک کردن ۱ اخطار

STRIKE_MUTE_LIMIT = 3       # با ۳ استرایک باقی‌مانده: میوت ۱ روزه
STRIKE_BAN_LIMIT = 5        # با ۵ استرایک باقی‌مانده: اخراج

POINTS = {"warn": -WARN_COST, "strike": -STRIKE_COST, "plus": PLUS_VALUE}


def current_strikes(warns: int, strikes: int, pluses: int) -> tuple[int, int]:
    """استرایک و اخطار باقی‌مانده بعد از پاک‌سازی."""
    s = summarize(warns, strikes, pluses)
    return s["strikes_remaining"], s["warns_remaining"]


def summarize(warns: int, strikes: int, pluses: int) -> dict:
    positive = pluses * PLUS_VALUE
    negative = warns * WARN_COST + strikes * STRIKE_COST

    # ۱) پاک کردن استرایک‌ها: هر ۵ پلاس برای یک استرایک
    strikes_erased = min(strikes, positive // PLUS_PER_STRIKE_ERASE)
    remaining_pluses = positive - strikes_erased * PLUS_PER_STRIKE_ERASE

    # ۲) پاک کردن اخطارها با پلاس‌های باقی‌مانده: هر ۳ پلاس برای یک اخطار
    warns_erased = min(warns, remaining_pluses // PLUS_PER_WARN_ERASE)

    erased_negative = strikes_erased * STRIKE_COST + warns_erased * WARN_COST
    strikes_remaining = strikes - strikes_erased
    warns_remaining = warns - warns_erased
    remaining_negative = strikes_remaining * STRIKE_COST + warns_remaining * WARN_COST
    final = positive - remaining_negative

    return {
        "warns": warns,
        "strikes": strikes,
        "pluses": pluses,
        "positive": positive,
        "negative": negative,
        # تفکیک پاک‌سازی جدید
        "strikes_erased": strikes_erased,
        "warns_erased": warns_erased,
        "erased": erased_negative,
        "strikes_remaining": strikes_remaining,
        "warns_remaining": warns_remaining,
        "remaining_negative": remaining_negative,
        "final": final,
        # مجازات
        "current_strikes": strikes_remaining,
    }
