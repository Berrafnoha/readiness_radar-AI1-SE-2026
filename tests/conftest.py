import os
import uuid

import pytest

from radar import auth, db, services


def database_url_for_tests():
    return os.environ.get("TEST_DATABASE_URL") or db.database_url()


@pytest.fixture(autouse=True)
def _reset_throttle():
    auth._FAILS.clear()


@pytest.fixture
def conn():
    """A fresh, isolated PostgreSQL schema per test, migrated and loaded with the demo project."""
    url = database_url_for_tests()
    try:
        admin = db.connect(url)
    except Exception as e:                                       # noqa: BLE001
        pytest.skip(f"PostgreSQL not reachable ({type(e).__name__}). Start it with: docker compose up -d")
    schema = "t_" + uuid.uuid4().hex[:10]
    admin.execute(f"CREATE SCHEMA {schema}")
    admin.commit()
    c = db.connect(url, schema=schema)
    db.migrate(c)
    db.reset_demo(c)
    services.recalc(c)
    yield c
    c.close()
    admin.execute(f"DROP SCHEMA {schema} CASCADE")
    admin.commit()
    admin.close()
