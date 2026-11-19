import os
from pathlib import Path

import asyncpg
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@pytest.fixture
async def db_tx():
    """A connection inside a transaction that is always rolled back.

    Tests using this leave no data behind in the shared Supabase database.
    Skipped when DATABASE_URL is not configured.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set (backend/.env)")
    try:
        conn = await asyncpg.connect(url, statement_cache_size=0)
    except ValueError:
        # Hide the parser message: it can contain parts of the password.
        pytest.fail("DATABASE_URL could not be parsed (percent-encode special chars in the password)", pytrace=False)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()
