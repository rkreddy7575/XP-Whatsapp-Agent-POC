import os
import sys

# Ensure backend root directory is on sys.path for service and data resolution in Vercel Python runtime
backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

# If running in Vercel without an explicit DATABASE_PATH, default ephemeral SQLite fallback to /tmp/app.db
if not os.getenv("DATABASE_PATH") and os.getenv("VERCEL"):
    os.environ["DATABASE_PATH"] = "/tmp/app.db"

# Expose existing FastAPI application from main.py
from main import app  # noqa: E402
