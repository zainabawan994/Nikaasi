"""Start the Nikaasi FastAPI server:  python run.py   (PORT / HOST env, default 127.0.0.1:3000)."""
import os
import sys
from pathlib import Path

import uvicorn

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from app.env import load_dotenv

    load_dotenv()
    port = int(os.environ.get("PORT", "3000"))
    prod = os.environ.get("APP_ENV", "development").lower() == "production"
    print(f"Nikaasi (FastAPI, {'production' if prod else 'development'}) -> http://localhost:{port}"
          + ("" if prod else f"  |  API docs -> http://localhost:{port}/docs"), flush=True)
    uvicorn.run("app.main:app", host=os.environ.get("HOST", "127.0.0.1"), port=port, log_level="info",
                server_header=False,  # don't advertise the server software
                limit_concurrency=int(os.environ.get("MAX_CONNECTIONS", "300")),  # beyond this: 503, not a crash
                timeout_keep_alive=5,  # drop idle keep-alive connections quickly (slow-client protection)
                proxy_headers=os.environ.get("TRUST_PROXY") == "1", forwarded_allow_ips="*" if os.environ.get("TRUST_PROXY") == "1" else None)
