#!/usr/bin/env python3
"""
Suraj AI — root launcher.

Handles Python path setup so you can run from anywhere:
    python3 /path/to/phonecontrol/server.py
    python3 server.py            # from inside the project dir
"""
import os
import sys

# Ensure the project root (this file's directory) is on sys.path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Load .env if present
try:
    from pathlib import Path
    env_file = Path(ROOT) / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())
except Exception:
    pass

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    # On Termux/Android, file-watching reload is flaky — disable via env.
    no_reload = os.environ.get("SURAJ_NO_RELOAD", "").lower() in ("1", "true", "yes")
    print(f"\n📱 Suraj AI starting on http://{host}:{port}")
    print(f"   Open this URL in your browser.\n")
    if no_reload:
        uvicorn.run("backend.main:app", host=host, port=port)
    else:
        uvicorn.run(
            "backend.main:app",
            host=host,
            port=port,
            reload=True,
            reload_dirs=[ROOT],
        )
