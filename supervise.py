#!/usr/bin/env python3
"""Supervises the Telegram bot + HTTP API in a single container.

Spawns both child processes. If either dies, the whole container exits and
Docker's restart policy brings it back up.
"""
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))

children: list[subprocess.Popen] = []


def _term(signum, _frame):
    for p in children:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass


def _start(description, args):
    print(f"launching {description}: {' '.join(args)}", flush=True)
    p = subprocess.Popen(args)
    children.append(p)
    return p


def main():
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    bot = _start("telegram bot", [sys.executable, os.path.join(ROOT, "bot", "bot.py")])
    api = _start("http api", [sys.executable, os.path.join(ROOT, "bot", "api_server.py")])

    # مراقب سلامت — اگر یکی از بچه‌ها بمیرد، بقیه را می‌کشیم و خود خارج می‌شویم
    while True:
        dead_rc = None
        for p in list(children):
            rc = p.poll()
            if rc is not None:
                children.remove(p)
                print(f"child {p.args} exited with {rc}", flush=True)
                dead_rc = rc
        if dead_rc is not None:
            print("supervisor: shutting down remaining children", flush=True)
            for p in children:
                try:
                    p.terminate()
                except Exception:
                    pass
            return dead_rc
        time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
