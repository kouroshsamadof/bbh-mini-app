"""BBH Main Bot — پل API بین Mini App و دیتابیس ربات اصلی BBH

فقط به کاربران داخل ربات (@azizmstrikebot یا @miniazizbot) سرویس می‌دهد.
هیچ کاربر خارج از دو ربات نمی‌تواند هیچ داده‌ای بخواند (اعتبارسنجی initData).

Endpoints:
  GET  /health           → بدون احراز — برای چکِ سلامت (healthcheck کانتینر)
  GET  /api/state        → اعضا، امتیازها، آمار و توانایی‌ها
  POST /api/register     → ثبت‌نام خودت (name + age اختیاری)
  POST /api/ability      → تنظیم توانایی شخصی
  GET  /api/me           → اطلاعات کاربر فعلی
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
    """اعتبارسنجی initData برای یک توکن مشخص (الگوریتم رسمی Telegram)."""
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
    """initData را با هرکدام از توکن‌های پشتیبانی‌شده اعتبارسنجی می‌کند."""
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


def get_active_news(chat_id: int | None = None):
    """آخرین خبر فعال (نامنتهی به انقضا) را برمی‌گرداند."""
    if chat_id is not None:
        row = _get_conn().execute(
            """
            SELECT * FROM news
            WHERE chat_id = ? AND expires_at > ?
            ORDER BY id DESC LIMIT 1
            """,
            (chat_id, _now()),
        ).fetchone()
    else:
        row = _get_conn().execute(
            """
            SELECT * FROM news
            WHERE expires_at > ?
            ORDER BY id DESC LIMIT 1
            """,
            (_now(),),
        ).fetchone()
    return dict(row) if row else None


def judgments_history(limit: int = 30) -> list[dict]:
    rows = _get_conn().execute(
        """
        SELECT id, target_id, target_name, reason, status, winner, admin_name, created_at, closes_at
        FROM judgments
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


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
            self._send_json(200, {"user": user, "member": dict(m) if m else None})
            return
        if path == "/api/state":
            user = _require_user(self.headers.get("X-Init-Data"))
            if not user:
                self._send_json(401, {"error": "not authorized"})
                return
            self._send_json(200, {"members": build_state(), "history": judgments_history(), "serverTime": _now()})
            return
        if path == "/api/history":
            user = _require_user(self.headers.get("X-Init-Data"))
            if not user:
                self._send_json(401, {"error": "not authorized"})
                return
            self._send_json(200, {"history": judgments_history()})
            return
        if path == "/api/news":
            user = _require_user(self.headers.get("X-Init-Data"))
            if not user:
                self._send_json(401, {"error": "not authorized"})
                return
            qs = dict(parse_qsl(parsed_path[1])) if len(parsed_path) > 1 else {}
            chat_id = int(qs["chat_id"]) if qs.get("chat_id") else None
            news = get_active_news(chat_id)
            self._send_json(200, {"news": news})
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
