"""Regenerate schema.sql from the SQLAlchemy models.

The models in app/models/ are the source of truth. schema.sql exists so the
schema can be read (and applied by hand) without running the application;
this script keeps the two in step.

Usage:  python scripts/dump_schema.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app.models import Base  # noqa: E402

HEADER = """\
-- Database schema for the Agentic Order & Quote Approval System.
--
-- GENERATED FILE - do not edit. The SQLAlchemy models in app/models/ are the
-- source of truth; this is the PostgreSQL DDL they produce, provided so the
-- schema can be read without running the application.
--
-- Regenerate:  python scripts/dump_schema.py
-- Apply:       python -m app.database.seed   (create_all + idempotent seed data)
"""


def render() -> str:
    pg = postgresql.dialect()
    parts = [HEADER]
    for table in Base.metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=pg)).strip() + ";")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            parts.append(str(CreateIndex(index).compile(dialect=pg)).strip() + ";")
        parts.append("")
    return "\n".join(parts)


def main() -> None:
    target = Path(__file__).resolve().parent.parent / "schema.sql"
    target.write_text(render(), encoding="utf-8")
    print(f"wrote {target} ({len(Base.metadata.sorted_tables)} tables)")


if __name__ == "__main__":
    main()
