"""Start the API and the web app together.  Run from the repo root:

    python run.py

The API (FastAPI on http://127.0.0.1:8000) starts first, then the web app
(http://localhost:5080, which opens in your browser). Output from both appears in
one window, tagged [api] and [web]. Press Ctrl+C once to stop both. If either one
exits on its own, the other is stopped too.
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API_DIR = ROOT / "api"
WEB_DIR = ROOT / "web"
API_HOST, API_PORT = "127.0.0.1", 8000
IS_WINDOWS = os.name == "nt"


def stream(proc: subprocess.Popen, tag: str) -> None:
    """Copy a process's output to ours, one tagged line at a time."""
    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"[{tag}] {line.rstrip()}", flush=True)


def launch(tag: str, cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd, cwd=cwd, env={**os.environ, **(env or {})},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        # Own process group, so Ctrl+C in this window doesn't hit the children directly
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0,
        start_new_session=not IS_WINDOWS,
    )
    threading.Thread(target=stream, args=(proc, tag), daemon=True).start()
    return proc


def stop(proc: subprocess.Popen) -> None:
    """Stop a process and everything it started (dotnet run starts a child)."""
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def wait_for_port(host: str, port: int, proc: subprocess.Popen, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline and proc.poll() is None:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def main() -> int:
    if shutil.which("dotnet") is None:
        print("Can't find 'dotnet'. Install the .NET SDK, then run this again.")
        return 1

    # HEADLESS and the other settings in api/config.py still come from your environment
    api = launch("api", [sys.executable, "-u", "-m", "uvicorn", "main:app",
                         "--host", API_HOST, "--port", str(API_PORT)], API_DIR,
                 {"PYTHONUNBUFFERED": "1"})
    procs = [api]
    try:
        if not wait_for_port(API_HOST, API_PORT, api):
            if api.poll() is not None:
                print(f"The API exited with code {api.returncode}. Is everything in "
                      "api/requirements.txt installed?")
            else:
                print(f"The API didn't start listening on port {API_PORT} within 30 seconds.")
            return 1

        web = launch("web", ["dotnet", "run", "--project", str(WEB_DIR)], ROOT)
        procs.append(web)
        print("\nBoth running. Press Ctrl+C to stop.\n", flush=True)

        while all(p.poll() is None for p in procs):
            time.sleep(0.5)

        for tag, p in (("api", api), ("web", web)):
            if p.poll() is not None:
                print(f"\nThe {tag} stopped (exit code {p.returncode}); stopping the other one.")
        return 0
    except KeyboardInterrupt:
        print("\nStopping...")
        return 0
    finally:
        for p in reversed(procs):
            stop(p)


if __name__ == "__main__":
    sys.exit(main())