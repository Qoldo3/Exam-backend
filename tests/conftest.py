"""Pytest fixtures.

DATABASE_URL is redirected to a throwaway per-process SQLite file *before* the
app modules are imported, so the test suite never touches the development
database (backend/exam.db).
"""
import os
import tempfile

# Only redirect if the operator did not explicitly set a DATABASE_URL.
os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{tempfile.gettempdir()}/exam_test_{os.getpid()}.db",
)
# Tests hit the auth/admin endpoints many times per second; slowapi's limiter
# reads this at import time, so disable it here (before app.core.config loads).
os.environ["RATE_LIMIT_ENABLED"] = "false"
# A dev/prod backend/.env may arm the license gate (ENVIRONMENT=production,
# LICENSE_ENABLED=true); tests must never be locked, so force it off explicitly
# before app.core.config is imported.
os.environ["LICENSE_ENABLED"] = "false"
os.environ["LICENSE_HASH"] = ""
os.environ["ENVIRONMENT"] = "test"
