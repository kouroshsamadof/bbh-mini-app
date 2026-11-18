"""MINI AZIZ watchdog â€” Ø±ÙˆÛŒ Ù‡Ø± Ø±ÙˆØ´Ù† Ø´Ø¯Ù† Ø³ÛŒØ³ØªÙ… Ø§Ø¬Ø±Ø§ Ù…ÛŒâ€ŒØ´ÙˆØ¯:
  Û±) Ø³Ø±ÙˆØ± API Ø±Ø§ Ø¨ÛŒØ¯Ø§Ø± Ù…ÛŒâ€ŒÚ©Ù†Ø¯
  Û²) ØªÙˆÙ†Ù„ TryCloudflare Ø±Ø§ Ø¨Ø±Ø§ÛŒ Ø¯Ø³ØªØ±Ø³ÛŒ Ø§ÛŒÙ†ØªØ±Ù†Øª Ø§Ø¬Ø±Ø§ Ù…ÛŒâ€ŒÚ©Ù†Ø¯
  Û³) Ø¢Ø¯Ø±Ø³ Ù„Ø­Ø¸Ù‡â€ŒØ§ÛŒ ØªÙˆÙ†Ù„ Ø±Ø§ Ø¯Ø± `js/config.js` Ø§Ù¾ Ù…ÛŒâ€ŒÙ†ÙˆÛŒØ³Ø¯
  Û´) Ø§Ú¯Ø± Ø¢Ø¯Ø±Ø³ Ø¹ÙˆØ¶ Ø´Ø¯Ù‡ Ø¨Ø§Ø´Ø¯ØŒ Ø¨Ø§ Ù¾ÙˆØ´ Ú©Ø±Ø¯Ù† Ù‡Ù…ÛŒÙ† ÛŒÚ© ØªØºÛŒÛŒØ±ØŒ Ø§Ù¾ Ù‡Ù…ÛŒØ´Ù‡ Ø²Ù†Ø¯Ù‡ Ù…ÛŒâ€ŒÙ…Ø§Ù†Ø¯
"""
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = r"C:\Users\sabas"
API_SCRIPT = r"C:\Users\sabas\source\repos\bbh-mini-app\bot\api_server.py"
APP_REPO = r"C:\Users\sabas\source\repos\bbh-mini-app"
CONFIG = os.path.join(APP_REPO, "js", "config.js")
STATE_FILE = os.path.join(ROOT, ".tunnel_last_url")
HEALTH = "http://127.0.0.1:8787/health"

GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()
if not GH_TOKEN:
    import sys as _sys
    _sys.stderr.write("GH_TOKEN not set in environment\n")


def http_json(url, timeout=8):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode()
    except Exception:
        return None


def wait_health(seconds=45):
    for _ in range(seconds):
        if http_json(HEALTH, 3):
            return True
        time.sleep(1)
    return False


def start_api_if_needed():
    if http_json(HEALTH, 3):
        return True
    subprocess.Popen(
        [sys.executable, API_SCRIPT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return wait_health(30)


def git(*args, cwd=APP_REPO):
    return subprocess.run(
        [r"C:\Program Files\Git\cmd\git.exe", *args],
        cwd=cwd, capture_output=True, text=True,
        env={**os.environ, "GH_TOKEN": GH_TOKEN},
    )


def current_tunnel_url() -> str | None:
    out = subprocess.run(
        ["C:\\Users\\sabas\\cloudflared.exe", "tunnel", "--url", "http://127.0.0.1:8787", "--no-autoupdate"],
        capture_output=True, text=True, timeout=60,
    )
    for line in (out.stdout + out.stderr).splitlines():
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            # ØªÙˆÙ†Ù„ Ø¨Ø§ÛŒØ¯ ØªØ§ Ø§Ø¨Ø¯ ÙØ¹Ø§Ù„ Ø¨Ù…Ø§Ù†Ø¯ â€” Ø¯ÙˆØ¨Ø§Ø±Ù‡ Ø¨Ø§Ø² Ù…ÛŒâ€ŒÚ©Ù†ÛŒÙ… Ø§Ø² Ø§ÛŒÙ†â€ŒØ¬Ø§
            return m.group(0)
    return None


def ensure_daemon_tunnel():
    """Ú©Ù„Ø§ÛŒÙ†Øª ØªÙˆÙ†Ù„ Ø±Ø§ Ø¯Ø± Ù¾Ø³â€ŒØ²Ù…ÛŒÙ†Ù‡ Ø¨Ø§Ù„Ø§ Ù†Ú¯Ù‡ Ù…ÛŒâ€ŒØ¯Ø§Ø±Ø¯ Ùˆ ØªÙ„Ø§Ø´ Ù…ÛŒâ€ŒÚ©Ù†Ø¯ URL Ø¨Ú¯ÛŒØ±ÛŒØ¯."""
    log_path = r"C:\Users\sabas\tunnel.log"
    subprocess.Popen(
        ["C:\\Users\\sabas\\cloudflared.exe", "tunnel", "--url", "http://127.0.0.1:8787", "--no-autoupdate"],
        stdout=open(log_path, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for _ in range(60):
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", content)
            if m:
                return m.group(0)
        except FileNotFoundError:
            pass
        time.sleep(2)
    return None


def write_config(url: str):
    with open(CONFIG, "w", encoding="utf-8") as f:
        f.write(
            "// Ø¢Ø¯Ø±Ø³ Ù„Ø­Ø¸Ù‡â€ŒØ§ÛŒ API Ù…Ø´ØªØ±Ú© Ø±Ø¨Ø§Øª â€” Ø±ÙˆÛŒ Ù‡Ø± Ø±ÙˆØ´Ù† Ø´Ø¯Ù† Ø³ÛŒØ³ØªÙ… Ø¨Ù‡â€ŒØ±ÙˆØ² Ù…ÛŒâ€ŒØ´ÙˆØ¯.\n"
            f"window.BBH_API_BASE = \"{url}\";\n"
        )


def publish_if_changed(url: str):
    try:
        with open(STATE_FILE) as f:
            last = f.read().strip()
    except FileNotFoundError:
        last = ""

    write_config(url)

    if last == url:
        return False

    git("add", "js/config.js")
    commit = git("-c", "user.name=kouroshsamadof",
                 "-c", "user.email=345356499+kouroshsamadof@users.noreply.github.com",
                 "commit", "-q", "-m", f"watchdog: point app at live tunnel {url}")
    push = git("push", "origin", "main")
    ok = push.returncode == 0
    if ok:
        try:
            with open(STATE_FILE, "w") as f:
                f.write(url)
        except OSError:
            pass
    return ok


def main():
    print("watchdog: waiting for APIâ€¦")
    if not start_api_if_needed():
        print("watchdog: API failed to start", file=sys.stderr)
        return 1

    url = ensure_daemon_tunnel()
    if not url:
        print("watchdog: tunnel URL not found in log", file=sys.stderr)
        return 1

    print(f"watchdog: tunnel URL = {url}")
    published = publish_if_changed(url)
    print("watchdog:", "published to GitHub Pages" if published else "URL unchanged â€” nothing to push")
    return 0


if __name__ == "__main__":
    sys.exit(main())

