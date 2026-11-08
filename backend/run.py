"""Start the Nikaasi FastAPI server:  python run.py   (PORT env, default 3000)."""
import os
import sys
from pathlib import Path

import uvicorn

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    port = int(os.environ.get("PORT", "3000"))
    print(f"Nikaasi (FastAPI) -> http://localhost:{port}  |  API docs -> http://localhost:{port}/docs", flush=True)
    uvicorn.run("app.main:app", host=os.environ.get("HOST", "127.0.0.1"), port=port, log_level="info")
