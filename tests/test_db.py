import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, text

from db import Base, init_db, make_engine, next_request_id

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request, tmp_path):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    elif POSTGRES_URL:
        url = POSTGRES_URL
    else:
        pytest.skip("TEST_POSTGRES_URL is not set")
    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_request_ids_increase_per_year(engine):
    factory = init_db(engine)
    with factory() as session:
        first = next_request_id(session, datetime(2026, 5, 1, tzinfo=timezone.utc))
        second = next_request_id(session, datetime(2026, 5, 2, tzinfo=timezone.utc))
        next_year = next_request_id(session, datetime(2027, 1, 1, tzinfo=timezone.utc))
        session.commit()
    assert (first, second, next_year) == ("REQ-2026-001", "REQ-2026-002", "REQ-2027-001")


def test_simultaneous_intakes_get_distinct_ids(engine):
    factory = init_db(engine)
    moment = datetime(2026, 9, 29, tzinfo=timezone.utc)

    def take_id(_: int) -> str:
        with factory() as session:
            value = next_request_id(session, moment)
            session.commit()
            return value

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(take_id, range(20)))
    assert sorted(ids) == [f"REQ-2026-{n:03d}" for n in range(1, 21)]


def test_fallback_column_is_added_to_an_existing_table(engine):
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE operation_requests ("
                "request_id VARCHAR(32) PRIMARY KEY, requester VARCHAR(200), "
                "request_text TEXT, status VARCHAR(32), human_approval VARCHAR(32), "
                "created_at TIMESTAMP)"
            )
        )
    init_db(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("operation_requests")}
    assert "fallback_used" in columns
