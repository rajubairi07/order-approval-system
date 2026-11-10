import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.seed import seed
from app.graph.workflow import reset_checkpoints
from app.models import Base


@pytest.fixture(autouse=True)
def _clean_checkpoints():
    """Each test gets a fresh database in which entity ids restart at 1, but
    the graph's checkpointer is process-local — clear it so a previous test's
    run for `ORDER:1` cannot be mistaken for this test's."""
    reset_checkpoints()
    yield
    reset_checkpoints()


@pytest.fixture()
def engine():
    """In-memory SQLite engine with FK enforcement, fresh schema per test.

    StaticPool + check_same_thread=False share the single in-memory database
    with FastAPI TestClient's worker threads.
    """
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _enable_fks(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine) -> Session:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def seeded_db(db) -> Session:
    seed(db)
    return db
