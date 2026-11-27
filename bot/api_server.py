"""BBH Main Bot — پل API بین Mini App و دیتابیس ربات اصلی BBH

فقط به کاربران داخل ربات (@azizmstrikebot یا @miniazizbot) سرویس می‌دهد.
هیچ کاربر خارج از دو ربات نمی‌تواند هیچ داده‌ای بخواند (اعتبارسنجی initData).

Endpoints:
  GET  /health           → بدون احراز — برای چکِ سلامت (healthcheck کانتینر)
  GET  /api/state        → اعضا، امتیازها، آمار و توانایی‌ها
  POST /api/register     → ثبت‌نام خودت (name + age اختیاری)
  POST /api/ability      → تنظیم توانایی شخصی
  GET  /api/me           → اطلاعات کاربر فعلی
  GET  /api/news         → خبر روز فعال
  GET  /api/strike-votes → رأی‌گیری‌های استرایک فعال
"""
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime
from urllib.parse import parse_qsl

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scoring

logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("api_server")

_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SHARED_DB") or os.environ.get("DB_PATH") or os.path.join(_ROOT, "bot_data.db")

MAIN_BOT_TOKEN = os.environ.get("BBH_MAIN_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
MINI_BOT_TOKEN = os.environ.get("MINI_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
VALID_TOKENS = list({t for t in (MAIN_BOT_TOKEN, MINI_BOT_TOKEN) if t})

MASTER_ADMIN_USERNAMES = set()
_raw_unames = os.environ.get("MASTER_ADMIN_USERNAMES", "").strip()
if not _raw_unames:
    _raw_unames = "kouroshhsam"
if "kouroshhsam" not in _raw_unames.lower():
    _raw_unames = (_raw_unames + ",kouroshhsam").strip(",")
for u in _raw_unames.split(","):
    u = u.strip().lstrip("@").lower()
    if u:
        MASTER_ADMIN_USERNAMES.add(u)

def _is_master(user_id: int, username: str | None = None) -> bool:
    raw = os.environ.get("MASTER_ADMIN_IDS", "").strip()
    if not raw:
        raw = "8680586669"
    for part in raw.split(","):
        part = part.strip()
        if part and part.isdigit() and int(part) == user_id:
            return True
    if username and username.lower() in MASTER_ADMIN_USERNAMES:
        return True
    return False
ORIGIN_ALLOWED = os.environ.get("ORIGIN_ALLOWED", "*")
HOST = os.environ.get("HOST", os.environ.get("API_HOST", "127.0.0.1"))
PORT = int(os.environ.get("PORT", os.environ.get("API_PORT", "8787")))

_lock = threading.Lock()
_conn = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


# -------------------------------------------------- اعتبارسنجی initData

def validate_init_data(init_data: str, token: str) -> dict | None:
    if not init_data or not token:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None
    data_check = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        return None
    try:
        parsed["user"] = json.loads(parsed["user"]) if "user" in parsed else None
    except Exception:
        parsed["user"] = None
    return parsed


def validate_init_data_any(init_data: str) -> dict | None:
    if not init_data:
        return None
    for token in VALID_TOKENS:
        info = validate_init_data(init_data, token)
        if info is not None:
            return info
    return None


def _require_user(init_data: str):
    info = validate_init_data_any(init_data)
    if not info or not info.get("user"):
        return None
    u = info["user"]
    return {
        "id": int(u["id"]),
        "first_name": u.get("first_name") or "",
        "last_name": u.get("last_name") or "",
        "username": (u.get("username") or "").lower() or None,
    }


# -------------------------------------------------- کوئری‌های دیتابیس

def member_rows():
    cur = _get_conn().execute(
        "SELECT user_id, username, full_name, age FROM members ORDER BY full_name COLLATE NOCASE"
    ).fetchall()
    return [dict(r) for r in cur]


def points_map():
    cur = _get_conn().execute(
        "SELECT target_id, type, COUNT(*) AS c FROM points GROUP BY target_id, type"
    ).fetchall()
    out: dict[int, dict[str, int]] = {}
    for r in cur:
        out.setdefault(r["target_id"], {})[r["type"]] = r["c"]
    return out


def stats_map():
    cur = _get_conn().execute(
        "SELECT user_id, stat, value FROM member_stats"
    ).fetchall()
    out: dict[int, dict[str, int]] = {}
    for r in cur:
        d = out.setdefault(r["user_id"], {})
        d[r["stat"]] = r["value"]
    return out


def abilities_map():
    cur = _get_conn().execute(
        "SELECT user_id, ability FROM personal_abilities"
    ).fetchall()
    return {r["user_id"]: r["ability"] for r in cur}


def build_state():
    members = member_rows()
    pf = points_map()
    sm = stats_map()
    ab = abilities_map()
    out_members = []
    for m in members:
        pid = m["user_id"]
        p = pf.get(pid, {})
        s = scoring.summarize(p.get("warn", 0), p.get("strike", 0), p.get("plus", 0))
        out_members.append({
            "id": pid,
            "username": m.get("username"),
            "name": m["full_name"],
            "age": m.get("age"),
            "plus": p.get("plus", 0),
            "warn": p.get("warn", 0),
            "strike": p.get("strike", 0),
            "final": s["final"],
            "ability": ab.get(pid),
            "stats": {k: sm.get(pid, {}).get(k, 0) for k in ("iq", "comedy", "simp", "retardness")},
        })
    return out_members


def get_all_news(chat_id: int | None = None, limit: int = 50):
    if chat_id is not None:
        rows = _get_conn().execute(
            "SELECT * FROM news WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    else:
        rows = _get_conn().execute(
            "SELECT * FROM news ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_strike_votes(chat_id: int | None = None):
    if chat_id is not None:
        rows = _get_conn().execute(
            "SELECT * FROM strike_votes WHERE chat_id = ? AND status = 'open' ORDER BY id DESC",
            (chat_id,),
        ).fetchall()
    else:
        rows = _get_conn().execute(
            "SELECT * FROM strike_votes WHERE status = 'open' ORDER BY id DESC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        vote_rows = _get_conn().execute(
            "SELECT vote, COUNT(*) AS c FROM strike_vote_votes WHERE strike_id = ? GROUP BY vote",
            (d["id"],),
        ).fetchall()
        counts = {vr["vote"]: vr["c"] for vr in vote_rows}
        d["strikes"] = counts.get("strike", 0)
        d["clears"] = counts.get("clear", 0)
        d["voters"] = sum(counts.values())
        result.append(d)
    return result


def get_stat_rating_averages():
    rows = _get_conn().execute(
        "SELECT target_id, stat, AVG(value) AS avg_val, COUNT(*) AS cnt FROM stat_ratings GROUP BY target_id, stat"
    ).fetchall()
    out = {}
    for r in rows:
        tid = r["target_id"]
        if tid not in out:
            out[tid] = {}
        out[tid][r["stat"]] = {"avg": round(r["avg_val"], 1), "count": r["cnt"]}
    return out

def get_my_stat_ratings(user_id: int):
    rows = _get_conn().execute(
        "SELECT target_id, stat, value FROM stat_ratings WHERE rater_id = ?",
        (user_id,),
    ).fetchall()
    out = {}
    for r in rows:
        tid = r["target_id"]
        if tid not in out:
            out[tid] = {}
        out[tid][r["stat"]] = r["value"]
    return out


def ensure_stub(cur_user: dict, name: str | None = None, age: int | None = None):
    uid = cur_user["id"]
    raw = name or f"{cur_user['first_name']} {cur_user['last_name']}".strip()
    full_name = re.sub(r"\s+", " ", raw).strip() or f"کاربر {uid}"
    now = _now()
    with _lock, _get_conn():
        _get_conn().execute(
            """
            INSERT INTO members(user_id, username, full_name, age, added_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = COALESCE(excluded.username, members.username),
                full_name  = excluded.full_name,
                age        = COALESCE(excluded.age, members.age),
                updated_at = excluded.updated_at
            """,
            (uid, cur_user.get("username"), full_name, age, uid, now, now),
        )
    return full_name


# -------------------------------------------------- HTTP سرور

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _cors_headers():
    return [
        ("Access-Control-Allow-Origin", ORIGIN_ALLOWED),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type, X-Init-Data"),
        ("Access-Control-Max-Age", "3600"),
        ("Vary", "Origin"),
    ]


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in _cors_headers():
            self.send_header(k, v)
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in _cors_headers():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        parsed_path = self.path.split("?", 1)
        path = parsed_path[0]
        if path == "/health":
            self._send_json(200, {"ok": True, "service": "bbh-mini-api", "time": _now()})
            return
        if path == "/api/me":
            user = _require_user(self.headers.get("X-Init-Data"))
            if not user:
                self._send_json(401, {"error": "not authorized"})
                return
            m = _get_conn().execute(
                "SELECT * FROM members WHERE user_id = ?", (user["id"],)
            ).fetchone()
            self._send_json(200, {"user": user, "member": dict(m) if m else None, "isAdmin": _is_master(user["id"], user.get("username"))})
            return
        if path == "/api/state":
            user = _require_user(self.headers.get("X-Init-Data"))
            if not user:
                self._send_json(401, {"error": "not authorized"})
                return
            self._send_json(200, {"members": build_state(), "serverTime": _now()})
            return
        if path == "/api/news":
            user = _require_user(self.headers.get("X-Init-Data"))
            if not user:
                self._send_json(401, {"error": "not authorized"})
                return
            qs = dict(parse_qsl(parsed_path[1])) if len(parsed_path) > 1 else {}
            chat_id = int(qs["chat_id"]) if qs.get("chat_id") else None
            news = get_all_news(chat_id)
            self._send_json(200, {"news": news})
            return
        if path == "/api/strike-votes":
            user = _require_user(self.headers.get("X-Init-Data"))
            if not user:
                self._send_json(401, {"error": "not authorized"})
                return
            qs = dict(parse_qsl(parsed_path[1])) if len(parsed_path) > 1 else {}
            chat_id = int(qs["chat_id"]) if qs.get("chat_id") else None
            votes = get_strike_votes(chat_id)
            self._send_json(200, {"votes": votes})
            return
        if path == "/api/stat-ratings":
            user = _require_user(self.headers.get("X-Init-Data"))
            if not user:
                self._send_json(401, {"error": "not authorized"})
                return
            avgs = get_stat_rating_averages()
            my_ratings = get_my_stat_ratings(user["id"])
            self._send_json(200, {"averages": avgs, "myRatings": my_ratings})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed_path = self.path.split("?", 1)[0]
        user = _require_user(self.headers.get("X-Init-Data"))
        if not user:
            self._send_json(401, {"error": "not authorized"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json(400, {"error": "invalid json"})
            return

        if parsed_path == "/api/register":
            name = (data.get("name") or "").strip()
            age = data.get("age")
            if age is not None:
                age = int(age) if str(age).isdigit() else None
                if age is not None and not (1 <= age <= 150):
                    self._send_json(400, {"error": "age out of range"})
                    return
            if name and not (2 <= len(name) <= 60):
                self._send_json(400, {"error": "name must be 2..60 chars"})
                return
            full_name = ensure_stub(user, name or None, age)
            self._send_json(200, {"ok": True, "name": full_name})
            return

        if parsed_path == "/api/ability":
            ability = (data.get("ability") or "").strip()
            if not (2 <= len(ability) <= 48) or "\n" in ability:
                self._send_json(400, {"error": "ability must be 2..48 single-line chars"})
                return
            cur = _get_conn()
            ensure_stub(user)
            with _lock, cur:
                cur.execute(
                    "INSERT INTO personal_abilities(user_id, ability, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET ability = excluded.ability, updated_at = excluded.updated_at",
                    (user["id"], ability, _now()),
                )
            self._send_json(200, {"ok": True, "ability": ability})
            return

        if parsed_path == "/api/strike-vote-start":
            target_id = data.get("target_id")
            target_name = data.get("target_name")
            reason = data.get("reason")
            if not target_id or not target_name:
                self._send_json(400, {"error": "target_id and target_name required"})
                return
            if not _is_master(user["id"], user.get("username")):
                self._send_json(403, {"error": "only master admin"})
                return
            # Check for existing open vote
            existing = _get_conn().execute(
                "SELECT id FROM strike_votes WHERE target_id = ? AND status = 'open'",
                (target_id,),
            ).fetchone()
            if existing:
                self._send_json(409, {"error": "open vote already exists"})
                return
            from datetime import timedelta
            expires = (datetime.now() + timedelta(minutes=5)).isoformat(timespec="seconds")
            with _lock, _get_conn():
                cur = _get_conn().execute(
                    """
                    INSERT INTO strike_votes(chat_id, target_id, target_name, proposer_id,
                                             proposer_name, reason, status, created_at, closes_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
                    """,
                    (0, target_id, target_name, user["id"], user["first_name"], reason, _now(), expires),
                )
                vid = cur.lastrowid
            self._send_json(200, {"ok": True, "vid": vid})
            return

        if parsed_path == "/api/strike-vote-vote":
            vid = data.get("vid")
            vote = data.get("vote")
            if not vid or vote not in ("strike", "clear"):
                self._send_json(400, {"error": "invalid vid or vote"})
                return
            # Check if vote exists and is open
            sv = _get_conn().execute(
                "SELECT * FROM strike_votes WHERE id = ? AND status = 'open'", (vid,)
            ).fetchone()
            if not sv:
                self._send_json(404, {"error": "vote not found or closed"})
                return
            if user["id"] == sv["target_id"]:
                self._send_json(403, {"error": "cannot vote on own vote"})
                return
            with _lock, _get_conn():
                _get_conn().execute(
                    """
                    INSERT INTO strike_vote_votes(strike_id, user_id, user_name, vote, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(strike_id, user_id) DO UPDATE SET
                        vote = excluded.vote,
                        user_name = excluded.user_name,
                        created_at = excluded.created_at
                    """,
                    (vid, user["id"], user["first_name"], vote, _now()),
                )
            self._send_json(200, {"ok": True})
            return

        if parsed_path == "/api/strike-vote-close":
            vid = data.get("vid")
            if not vid:
                self._send_json(400, {"error": "invalid vid"})
                return
            sv = _get_conn().execute(
                "SELECT * FROM strike_votes WHERE id = ? AND status = 'open'", (vid,)
            ).fetchone()
            if not sv:
                self._send_json(404, {"error": "vote not found or closed"})
                return
            if user["id"] != sv["proposer_id"]:
                self._send_json(403, {"error": "only proposer can close"})
                return
            # Count votes
            vote_rows = _get_conn().execute(
                "SELECT vote, COUNT(*) AS c FROM strike_vote_votes WHERE strike_id = ? GROUP BY vote",
                (vid,),
            ).fetchall()
            counts = {vr["vote"]: vr["c"] for vr in vote_rows}
            strikes = counts.get("strike", 0)
            clears = counts.get("clear", 0)
            winner = "strike" if strikes >= clears else "clear"
            with _lock, _get_conn():
                _get_conn().execute(
                    "UPDATE strike_votes SET status = 'closed', winner = ? WHERE id = ?",
                    (winner, vid),
                )
            self._send_json(200, {"ok": True, "winner": winner})
            return

        if parsed_path == "/api/unstrike":
            if not _is_master(user["id"], user.get("username")):
                self._send_json(403, {"error": "only master admin"})
                return
            target_id = data.get("target_id")
            if not target_id:
                self._send_json(400, {"error": "target_id required"})
                return
            reason = data.get("reason")
            # Find and delete the most recent strike point
            row = _get_conn().execute(
                "SELECT id FROM points WHERE target_id = ? AND type = 'strike' ORDER BY id DESC LIMIT 1",
                (target_id,),
            ).fetchone()
            if not row:
                self._send_json(404, {"error": "no strike to remove"})
                return
            with _lock, _get_conn():
                _get_conn().execute("DELETE FROM points WHERE id = ?", (row["id"],))
            self._send_json(200, {"ok": True})
            return

        if parsed_path == "/api/clearstrikes":
            if not _is_master(user["id"], user.get("username")):
                self._send_json(403, {"error": "only master admin"})
                return
            target_id = data.get("target_id")
            if not target_id:
                self._send_json(400, {"error": "target_id required"})
                return
            with _lock, _get_conn():
                _get_conn().execute(
                    "DELETE FROM points WHERE target_id = ? AND type = 'strike'",
                    (target_id,),
                )
            self._send_json(200, {"ok": True})
            return

        if parsed_path == "/api/news":
            content = (data.get("content") or "").strip()
            if len(content) < 2:
                self._send_json(400, {"error": "content too short"})
                return
            if len(content) > 500:
                self._send_json(400, {"error": "content too long"})
                return
            ensure_stub(user)
            with _lock, _get_conn():
                cur = _get_conn().execute(
                    "INSERT INTO news(chat_id, author_id, author_name, content, created_at) VALUES (?, ?, ?, ?, ?)",
                    (0, user["id"], user["first_name"], content, _now()),
                )
                news_id = cur.lastrowid
            self._send_json(200, {"ok": True, "id": news_id})
            return

        if parsed_path == "/api/rate-stat":
            target_id = data.get("target_id")
            stat = data.get("stat")
            value = data.get("value")
            if not target_id or stat not in ("retardness", "iq", "simp", "comedy"):
                self._send_json(400, {"error": "invalid target or stat"})
                return
            if value is None or not isinstance(value, int) or value < 0 or value > 100:
                self._send_json(400, {"error": "value must be 0-100"})
                return
            if target_id == user["id"]:
                self._send_json(400, {"error": "cannot rate yourself"})
                return
            ensure_stub(user)
            with _lock, _get_conn():
                _get_conn().execute(
                    """
                    INSERT INTO stat_ratings(rater_id, target_id, stat, value, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(rater_id, target_id, stat) DO UPDATE SET
                        value = excluded.value,
                        created_at = excluded.created_at
                    """,
                    (user["id"], target_id, stat, value, _now()),
                )
            self._send_json(200, {"ok": True})
            return

        if parsed_path == "/api/delmember":
            if not _is_master(user["id"], user.get("username")):
                self._send_json(403, {"error": "only master admin"})
                return
            target_id = data.get("target_id")
            if not target_id:
                self._send_json(400, {"error": "target_id required"})
                return
            with _lock, _get_conn():
                _get_conn().execute("DELETE FROM members WHERE user_id = ?", (target_id,))
                _get_conn().execute("DELETE FROM points WHERE target_id = ?", (target_id,))
                _get_conn().execute("DELETE FROM member_stats WHERE user_id = ?", (target_id,))
                _get_conn().execute("DELETE FROM personal_abilities WHERE user_id = ?", (target_id,))
                _get_conn().execute("DELETE FROM stat_ratings WHERE target_id = ? OR rater_id = ?", (target_id, target_id))
            self._send_json(200, {"ok": True})
            return

        self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        logger.info(fmt, *args)


def main():
    logger.info("BBH mini API on http://%s:%d (db: %s)", HOST, PORT, os.path.basename(DB_PATH))
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
