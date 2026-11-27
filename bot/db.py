"""لایه‌ی دیتابیس SQLite — اعضا، رویدادهای امتیاز و تنظیمات ربات."""
import sqlite3
import threading
import time
from datetime import datetime

import config
import scoring

_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL UNIQUE,          -- آیدی عددی تلگرام
    username    TEXT,                             -- بدون @ و با حروف کوچک
    full_name   TEXT NOT NULL,
    age         INTEGER,
    note        TEXT,
    added_by    INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id   INTEGER NOT NULL,
    target_name TEXT,                             -- snapshot نام در لحظه‌ی ثبت
    chat_id     INTEGER,
    admin_id    INTEGER,
    admin_name  TEXT,
    type        TEXT NOT NULL CHECK (type IN ('warn', 'strike', 'plus')),
    delta       INTEGER NOT NULL,                 -- -1 | -3 | +1
    reason      TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_points_target ON points(target_id);
CREATE INDEX IF NOT EXISTS idx_points_created ON points(created_at);

CREATE TABLE IF NOT EXISTS personal_abilities (
    user_id    INTEGER PRIMARY KEY,
    ability    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS member_stats (
    user_id INTEGER NOT NULL,
    stat    TEXT NOT NULL CHECK (stat IN ('retardness', 'iq', 'simp', 'comedy')),
    value   INTEGER NOT NULL DEFAULT 0 CHECK (value BETWEEN 0 AND 100),
    PRIMARY KEY (user_id, stat)
);

CREATE TABLE IF NOT EXISTS stat_proposals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      INTEGER NOT NULL,
    target_id    INTEGER NOT NULL,
    target_name  TEXT,
    proposer_id  INTEGER,
    proposer_name TEXT,
    stat         TEXT NOT NULL CHECK (stat IN ('retardness', 'iq', 'simp', 'comedy')),
    action       TEXT NOT NULL CHECK (action IN ('give', 'take')),
    status       TEXT NOT NULL DEFAULT 'open',   -- open | closed
    result       TEXT,                           -- applied | rejected
    approve      INTEGER NOT NULL DEFAULT 0,
    reject       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    closes_at    TEXT NOT NULL,
    message_id   INTEGER
);

CREATE TABLE IF NOT EXISTS stat_votes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL REFERENCES stat_proposals(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    vote        TEXT NOT NULL CHECK (vote IN ('approve', 'reject')),
    created_at  TEXT NOT NULL,
    UNIQUE(proposal_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_stat_votes_proposal ON stat_votes(proposal_id);
CREATE INDEX IF NOT EXISTS idx_stat_proposals_target ON stat_proposals(target_id);

CREATE TABLE IF NOT EXISTS news (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    admin_id    INTEGER,
    admin_name  TEXT,
    content     TEXT NOT NULL,
    message_id  INTEGER,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_chat ON news(chat_id);
CREATE INDEX IF NOT EXISTS idx_news_expires ON news(expires_at);

CREATE TABLE IF NOT EXISTS strike_votes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    target_id   INTEGER NOT NULL,
    target_name TEXT,
    proposer_id INTEGER,
    proposer_name TEXT,
    reason      TEXT,
    status      TEXT NOT NULL DEFAULT 'open',   -- open | closed
    winner      TEXT,                           -- strike | cleared
    message_id  INTEGER,
    created_at  TEXT NOT NULL,
    closes_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strike_votes_chat ON strike_votes(chat_id);
CREATE INDEX IF NOT EXISTS idx_strike_votes_status ON strike_votes(status);
CREATE INDEX IF NOT EXISTS idx_strike_votes_target ON strike_votes(target_id);

CREATE TABLE IF NOT EXISTS strike_vote_votes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    strike_id     INTEGER NOT NULL REFERENCES strike_votes(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL,
    user_name     TEXT,
    vote          TEXT NOT NULL CHECK (vote IN ('strike', 'clear')),
    created_at    TEXT NOT NULL,
    UNIQUE(strike_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_strike_vote_votes_strike ON strike_vote_votes(strike_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_DEFAULT_SETTINGS = {
    "word_warn": config.DEFAULT_WARN_WORD,
    "word_strike": config.DEFAULT_STRIKE_WORD,
    "word_plus": config.DEFAULT_PLUS_WORD,
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")   # انتظار ۵ ثانیه‌ای به‌جای خطای قفل فوری
        self._lock = threading.Lock()
        self._settings_cache: dict[str, tuple[str | None, float]] = {}
        self._settings_ttl = 10.0   # کش ۱۰ ثانیه‌ای تنظیمات (کلمات دستورات)
        self._agg_cache: dict[str, tuple[object, float]] = {}
        self._agg_ttl = 5.0          # کش ۵ ثانیه‌ای نتایج تجمیعی (گزارش همه / رتبه‌بندی)
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            for key, value in _DEFAULT_SETTINGS.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, value),
                )

    # ---------- اعضا ----------

    _AGG_KEYS = ("summaries_for_all", "stats_ranking")

    def _invalidate_agg(self) -> None:
        """بعد از هر تغییر در امتیازها/آمار/اعضا، کش تجمیعی را خالی می‌کند."""
        for key in self._AGG_KEYS:
            self._agg_cache.pop(key, None)

    def _cached_agg(self, key: str, compute):
        cached = self._agg_cache.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[1] < self._agg_ttl:
            return cached[0]
        value = compute()
        self._agg_cache[key] = (value, now)
        return value

    def upsert_member(self, user_id: int, username: str | None, full_name: str,
                      age: int | None, added_by: int | None = None) -> None:
        """ساخت یا به‌روزرسانی عضو (نام/یوزرنیم همیشه آپدیت می‌شود)."""
        username = username.lower() if username else None
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO members(user_id, username, full_name, age, added_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username   = excluded.username,
                    full_name  = excluded.full_name,
                    age        = COALESCE(excluded.age, members.age),
                    updated_at = excluded.updated_at
                """,
                (user_id, username, full_name.strip(), age, added_by, now, now),
            )
        self._invalidate_agg()

    def ensure_stub(self, user_id: int, full_name: str | None = None,
                    username: str | None = None) -> None:
        """اگر کاربر در دیتابیس نبود، یک پروفایل حداقلی می‌سازد تا تاریخچه‌اش قابل نگهداری باشد."""
        if self.get_member(user_id):
            return
        self.upsert_member(
            user_id=user_id,
            username=username,
            full_name=(full_name or f"کاربر {user_id}").strip(),
            age=None,
        )

    def get_member(self, user_id: int) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM members WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row

    def find_by_username(self, username: str) -> sqlite3.Row | None:
        username = username.lstrip("@").lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM members WHERE username = ?", (username,)
            ).fetchone()
        return row

    def set_age(self, user_id: int, age: int | None) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE members SET age = ?, updated_at = ? WHERE user_id = ?",
                (age, _now(), user_id),
            )
        self._invalidate_agg()
        return cur.rowcount > 0

    def set_name(self, user_id: int, full_name: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE members SET full_name = ?, updated_at = ? WHERE user_id = ?",
                (full_name.strip(), _now(), user_id),
            )
        self._invalidate_agg()
        return cur.rowcount > 0

    def delete_member(self, user_id: int) -> bool:
        """عضو حذف می‌شود؛ تاریخچه‌ی امتیازها به‌صورت snapshot نگه داشته می‌شود."""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM members WHERE user_id = ?", (user_id,))
        self._invalidate_agg()
        return cur.rowcount > 0

    def list_members(self, limit: int = 8, offset: int = 0) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM members ORDER BY full_name COLLATE NOCASE LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return list(rows)

    def count_members(self) -> int:
        with self._lock:
            (n,) = self._conn.execute("SELECT COUNT(*) FROM members").fetchone()
        return n

    # ---------- امتیازها ----------

    def add_point(self, target_id: int, target_name: str | None, chat_id: int | None,
                  admin_id: int | None, admin_name: str | None,
                  kind: str, reason: str | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO points(target_id, target_name, chat_id, admin_id, admin_name,
                                   type, delta, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (target_id, target_name, chat_id, admin_id, admin_name,
                 kind, scoring.POINTS[kind], reason, _now()),
            )
        self._invalidate_agg()

    def raw_counts(self, target_id: int) -> tuple[int, int, int]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COALESCE(SUM(type = 'warn'), 0)   AS warns,
                    COALESCE(SUM(type = 'strike'), 0) AS strikes,
                    COALESCE(SUM(type = 'plus'), 0)   AS pluses
                FROM points WHERE target_id = ?
                """,
                (target_id,),
            ).fetchone()
        return int(row["warns"]), int(row["strikes"]), int(row["pluses"])

    def summary_for(self, target_id: int) -> dict:
        return scoring.summarize(*self.raw_counts(target_id))

    def history(self, target_id: int, limit: int = 10) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM points WHERE target_id = ? ORDER BY id DESC LIMIT ?",
                (target_id, limit),
            ).fetchall()
        return list(rows)

    def stats_map(self, user_ids: list[int]) -> dict[int, dict]:
        """خلاصه‌ی امتیازها برای چند آیدی — فقط با یک کوئری (جلوگیری از N+1)."""
        if not user_ids:
            return {}
        placeholders = ",".join("?" for _ in user_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT target_id, type, COUNT(*) AS c FROM points "
                f"WHERE target_id IN ({placeholders}) GROUP BY target_id, type",
                list(user_ids),
            ).fetchall()
        counts: dict[int, dict[str, int]] = {}
        for r in rows:
            counts.setdefault(r["target_id"], {})[r["type"]] = r["c"]
        return {
            uid: scoring.summarize(
                counts.get(uid, {}).get("warn", 0),
                counts.get(uid, {}).get("strike", 0),
                counts.get(uid, {}).get("plus", 0),
            )
            for uid in user_ids
        }

    def summaries_for_all(self) -> list[dict]:
        return self._cached_agg("summaries_for_all", self._compute_summaries_for_all)

    def _compute_summaries_for_all(self) -> list[dict]:
        """خلاصه‌ی نهایی همه‌ی اعضا به‌همراه امتیازهایشان — با ۲ کوئری."""
        with self._lock:
            member_rows = self._conn.execute(
                "SELECT user_id, full_name, username FROM members"
            ).fetchall()
        stats = self.stats_map([m["user_id"] for m in member_rows])
        out = [
            {
                "user_id": m["user_id"],
                "full_name": m["full_name"],
                "username": m["username"],
                **stats[m["user_id"]],
            }
            for m in member_rows
        ]
        out.sort(key=lambda x: x["final"], reverse=True)
        return out

    # ---------- توانایی شخصی ----------

    def set_ability(self, user_id: int, ability: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO personal_abilities(user_id, ability, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    ability    = excluded.ability,
                    updated_at = excluded.updated_at
                """,
                (user_id, ability.strip(), _now()),
            )

    def get_ability(self, user_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT ability FROM personal_abilities WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row["ability"] if row else None

    def abilities_map(self, user_ids: list[int]) -> dict[int, str]:
        """توانایی‌های چند نفر با یک کوئری — برای نمایش لیستی بدون N+1."""
        if not user_ids:
            return {}
        placeholders = ",".join("?" for _ in user_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT user_id, ability FROM personal_abilities WHERE user_id IN ({placeholders})",
                list(user_ids),
            ).fetchall()
        return {r["user_id"]: r["ability"] for r in rows}

    # ---------- خبر روز (news) ----------

    def create_news(self, chat_id: int, content: str, admin_id: int | None,
                    admin_name: str | None, message_id: int | None,
                    expires_at: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO news(chat_id, admin_id, admin_name, content, message_id,
                                 created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (chat_id, admin_id, admin_name, content, message_id, _now(), expires_at),
            )
        return int(cur.lastrowid)

    def deactivate_previous_news(self, chat_id: int) -> None:
        """خبرهای قبلی فعال همین چت را حذف می‌کند تا فقط آخرین خبر باقی بماند."""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM news WHERE chat_id = ? AND expires_at > ?",
                (chat_id, _now()),
            )

    def get_active_news(self, chat_id: int):
        """آخرین خبر فعال (نامنتهی به انقضا) را برمی‌گرداند."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM news
                WHERE chat_id = ? AND expires_at > ?
                ORDER BY id DESC LIMIT 1
                """,
                (chat_id, _now()),
            ).fetchone()
        return row

    def get_news(self, news_id: int):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM news WHERE id = ?", (news_id,)
            ).fetchone()
        return row

    def list_expired_news(self) -> list[sqlite3.Row]:
        """خبرهای منقضی‌شده را برای حذف خودکار برمی‌گرداند."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM news WHERE expires_at <= ? ORDER BY id",
                (_now(),),
            ).fetchall()
        return list(rows)

    def delete_news(self, news_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM news WHERE id = ?", (news_id,))

    # ---------- حذف المباشرة‌ی استرایک (حذف یک استرایک از کاربر) ----------

    def remove_strike(self, target_id: int, reason: str | None = None,
                      admin_id: int | None = None, admin_name: str | None = None,
                      chat_id: int | None = None) -> dict | None:
        m = self.get_member(target_id)
        if not m:
            return None
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM points WHERE target_id = ? AND type = 'strike' ORDER BY id DESC LIMIT 1",
                (target_id,),
            ).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM points WHERE id = ?", (row["id"],))
        self._invalidate_agg()
        return self.summary_for(target_id)

    def clear_strikes(self, target_id: int, reason: str | None = None,
                      admin_id: int | None = None, admin_name: str | None = None,
                      chat_id: int | None = None) -> dict | None:
        m = self.get_member(target_id)
        if not m:
            return None
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM points WHERE target_id = ? AND type = 'strike'",
                (target_id,),
            )
        self._invalidate_agg()
        return self.summary_for(target_id)

    # ---------- رأی‌گیری استرایک (strike voting) ----------

    def create_strike_vote(self, chat_id: int, target_id: int, target_name: str,
                           proposer_id: int | None, proposer_name: str | None,
                           reason: str | None, closes_at: str, message_id: int | None = None) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO strike_votes(chat_id, target_id, target_name, proposer_id,
                                         proposer_name, reason, status, message_id,
                                         created_at, closes_at)
                VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
                """,
                (chat_id, target_id, target_name, proposer_id, proposer_name,
                 reason, message_id, _now(), closes_at),
            )
        return int(cur.lastrowid)

    def get_strike_vote(self, strike_id: int):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM strike_votes WHERE id = ?", (strike_id,)
            ).fetchone()
        return row

    def open_strike_votes(self) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM strike_votes WHERE status = 'open' ORDER BY id DESC"
            ).fetchall()
        return list(rows)

    def open_strike_vote_for(self, chat_id: int, target_id: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM strike_votes WHERE chat_id = ? AND target_id = ? AND status = 'open'",
                (chat_id, target_id),
            ).fetchone()

    def set_strike_vote_message(self, strike_id: int, message_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE strike_votes SET message_id = ? WHERE id = ?", (message_id, strike_id)
            )

    def upsert_strike_vote(self, strike_id: int, user_id: int, user_name: str | None,
                           vote: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO strike_vote_votes(strike_id, user_id, user_name, vote, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(strike_id, user_id) DO UPDATE SET
                    vote       = excluded.vote,
                    user_name  = excluded.user_name,
                    created_at = excluded.created_at
                """,
                (strike_id, user_id, user_name, vote, _now()),
            )

    def strike_vote_counts(self, strike_id: int) -> tuple[int, int, int]:
        """خروجی: (رأی برای استرایک، رأی برای پاک کردن، مجموع رأی‌دهندگان)"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT vote, COUNT(*) AS c FROM strike_vote_votes WHERE strike_id = ? GROUP BY vote",
                (strike_id,),
            ).fetchall()
        counts = {r["vote"]: r["c"] for r in rows}
        return counts.get("strike", 0), counts.get("clear", 0), sum(counts.values())

    def close_strike_vote(self, strike_id: int, winner: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE strike_votes SET status = 'closed', winner = ? WHERE id = ?",
                (winner, strike_id),
            )

    def list_strike_votes(self, limit: int = 10, offset: int = 0) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM strike_votes ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return list(rows)

    # ---------- آمار اعضا ----------

    ALL_STATS = ("retardness", "iq", "simp", "comedy")

    def get_stats(self, user_id: int) -> dict[str, int]:
        out = {s: 0 for s in self.ALL_STATS}
        with self._lock:
            rows = self._conn.execute(
                "SELECT stat, value FROM member_stats WHERE user_id = ?", (user_id,)
            ).fetchall()
        for r in rows:
            out[r["stat"]] = r["value"]
        return out

    def get_all_stats_map(self) -> dict[int, dict[str, int]]:
        """همه‌ی اعضا با آمارهایشان — برای «آمار کلی»."""
        with self._lock:
            rows = self._conn.execute("SELECT user_id, stat, value FROM member_stats").fetchall()
        parsed: dict[int, dict[str, int]] = {}
        for r in rows:
            d = parsed.setdefault(r["user_id"], {s: 0 for s in self.ALL_STATS})
            d[r["stat"]] = r["value"]
        return parsed

    def adjust_stat(self, user_id: int, stat: str, delta: int) -> int:
        """اعمال با کلپ ۰..۱۰۰؛ برمی‌گرداند مقدار جدید."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO member_stats(user_id, stat, value) VALUES (?, ?, 0)",
                (user_id, stat),
            )
            self._conn.execute(
                "UPDATE member_stats SET value = MAX(0, MIN(100, value + ?)) WHERE user_id = ? AND stat = ?",
                (delta, user_id, stat),
            )
            (value,) = self._conn.execute(
                "SELECT value FROM member_stats WHERE user_id = ? AND stat = ?",
                (user_id, stat),
            ).fetchone()
        self._invalidate_agg()
        return int(value)

    # ---------- پیشنهاد آمار (رأی‌گیری) ----------

    def create_stat_proposal(self, chat_id: int, target_id: int, target_name: str,
                             proposer_id: int | None, proposer_name: str | None,
                             stat: str, action: str, closes_at: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO stat_proposals(chat_id, target_id, target_name, proposer_id,
                                           proposer_name, stat, action, status, created_at, closes_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (chat_id, target_id, target_name, proposer_id, proposer_name,
                 stat, action, _now(), closes_at),
            )
            return int(cur.lastrowid)

    def set_proposal_message(self, proposal_id: int, message_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE stat_proposals SET message_id = ? WHERE id = ?", (message_id, proposal_id)
            )

    def get_proposal(self, proposal_id: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM stat_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()

    def open_proposal_for(self, chat_id: int, target_id: int, stat: str):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM stat_proposals "
                "WHERE chat_id = ? AND target_id = ? AND stat = ? AND status = 'open'",
                (chat_id, target_id, stat),
            ).fetchone()

    def open_proposals(self) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM stat_proposals WHERE status = 'open'"
            ).fetchall()
        return list(rows)

    def close_proposal(self, proposal_id: int, result: str, approve: int, reject: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE stat_proposals SET status = 'closed', result = ?, approve = ?, reject = ? WHERE id = ?",
                (result, approve, reject, proposal_id),
            )

    def upsert_stat_vote(self, proposal_id: int, user_id: int, vote: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO stat_votes(proposal_id, user_id, vote, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(proposal_id, user_id) DO UPDATE SET
                    vote = excluded.vote,
                    created_at = excluded.created_at
                """,
                (proposal_id, user_id, vote, _now()),
            )

    def stat_vote_counts(self, proposal_id: int) -> tuple[int, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT vote, COUNT(*) AS c FROM stat_votes WHERE proposal_id = ? GROUP BY vote",
                (proposal_id,),
            ).fetchall()
        counts = {r["vote"]: r["c"] for r in rows}
        return counts.get("approve", 0), counts.get("reject", 0)

    def stats_ranking(self) -> list[sqlite3.Row]:
        return self._cached_agg("stats_ranking", self._compute_stats_ranking)

    def _compute_stats_ranking(self) -> list[sqlite3.Row]:
        """رتبه‌بندی همه‌ی اعضا بر اساس میانگین ۴ آمار (سپس مجموع)."""
        stats_map = self.get_all_stats_map()
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id, full_name, username FROM members"
            ).fetchall()
        out = []
        for m in rows:
            s = stats_map.get(m["user_id"], {k: 0 for k in self.ALL_STATS})
            total = sum(s.values())
            avg = total / len(self.ALL_STATS)
            out.append({
                "user_id": m["user_id"],
                "full_name": m["full_name"],
                "username": m["username"],
                "total": total,
                "avg": avg,
                **s,
            })
        out.sort(key=lambda x: (x["avg"], x["total"], x["full_name"]), reverse=True)
        return out

    # ---------- تنظیمات ----------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        now = time.monotonic()
        hit = self._settings_cache.get(key)
        if hit is not None and now - hit[1] < self._settings_ttl:
            return hit[0]
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        value = row["value"] if row else default
        self._settings_cache[key] = (value, now)
        return value

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        self._settings_cache[key] = (value, time.monotonic())

    def get_words(self) -> dict[str, str]:
        return {
            "warn": self.get_setting("word_warn", config.DEFAULT_WARN_WORD),
            "strike": self.get_setting("word_strike", config.DEFAULT_STRIKE_WORD),
            "plus": self.get_setting("word_plus", config.DEFAULT_PLUS_WORD),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


db = Database(config.DB_PATH)
