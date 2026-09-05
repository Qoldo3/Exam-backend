from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def _ensure_columns(sync_conn) -> None:
    """Additive migration for columns introduced after the initial create_all.

    create_all never alters existing tables, so pre-existing dev/prod databases
    would fail at runtime ("no such column") on these new fields. Running this
    after create_all keeps old databases working without a manual reseed.
    """
    from sqlalchemy import inspect, text

    insp = inspect(sync_conn)
    existing_tables = set(insp.get_table_names())
    additions = [
        ("users", "role", "VARCHAR(20) NOT NULL DEFAULT 'user'"),
        ("users", "is_blocked", "BOOLEAN NOT NULL DEFAULT 0"),
        ("exam_sessions", "fraud_count", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for table, column, ddl in additions:
        if table not in existing_tables:
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        if column not in existing:
            try:
                sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            except Exception:
                # another app worker may have added the column between our check and
                # the ALTER; re-check instead of failing startup on a duplicate column
                recheck = {c["name"] for c in insp.get_columns(table)}
                if column not in recheck:
                    raise


async def init_db() -> None:
    from app.models import (  # noqa: F401
        Answer,
        Exam,
        ExamAccess,
        ExamSession,
        Payment,
        Question,
        SystemSetting,
        User,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_columns)
