"""Transfer files over the restricted Azizam handover SSH channel."""
import subprocess
import sys

KEY = r"C:\Users\sabas\.ssh\azizam-transfer"
HOST = "arian@178.104.74.99"

files = {
    "upload-config": r"C:\Users\sabas\backups\azizam_bot.env",
    "upload-database": r"C:\Users\sabas\backups\azizam_primary_20261118_133018.db",
    "upload-api-database": r"C:\Users\sabas\backups\azizam_api_20261118_133018.db",
}

for command, path in files.items():
    print(f"=== {command} <- {path}")
    with open(path, "rb") as f:
        proc = subprocess.run(
            ["ssh", "-T", "-i", KEY,
             "-o", "BatchMode=yes",
             "-o", "IdentitiesOnly=yes",
             "-o", "StrictHostKeyChecking=yes",
             "-o", "ConnectTimeout=20",
             HOST, command],
            stdin=f,
            capture_output=True,
            timeout=600,
        )
    print(f"  exit={proc.returncode}")
    if proc.stdout:
        print("  stdout:", proc.stdout.decode("utf-8", errors="replace").strip())
    if proc.stderr:
        print("  stderr:", proc.stderr.decode("utf-8", errors="replace").strip())
