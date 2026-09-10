"""
database.py
Handles all MySQL connectivity for CampusQuery AI.

Reads connection info from environment variables only - never hardcode
credentials and never send them to the frontend.
"""

import os
import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import pymysql
from pymysql.cursors import DictCursor

load_dotenv()

logger = logging.getLogger("campusquery.database")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "college_db")

# Tables CampusQuery AI is allowed to know about / touch.
# Used both for schema introspection and as a safety allow-list.
ALLOWED_TABLES = ["students", "courses", "departments"]


class DatabaseUnavailableError(Exception):
    """Raised whenever we can't reach MySQL. Caller turns this into a
    friendly, non-technical message - never surface the raw exception
    to the end user."""
    pass


@contextmanager
def get_connection():
    """Yield a live MySQL connection, closing it afterwards.

    Raises DatabaseUnavailableError (never the raw pymysql exception)
    so callers can show a friendly message instead of a stack trace.
    """
    conn = None
    try:
        conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            cursorclass=DictCursor,
            autocommit=False,
            connect_timeout=5,
        )
        yield conn
    except pymysql.MySQLError as exc:
        logger.error("MySQL connection failed: %s", exc)
        raise DatabaseUnavailableError(str(exc)) from exc
    finally:
        if conn is not None:
            conn.close()


def check_connection() -> bool:
    """Lightweight health check used by GET /health."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except DatabaseUnavailableError:
        return False


def run_select(sql: str) -> List[Dict[str, Any]]:
    """Execute a read-only query and return rows as a list of dicts."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            return rows


def run_modification(sql: str) -> int:
    """Execute an INSERT/UPDATE/DELETE/DDL statement and commit.

    Returns the number of affected rows (0 for DDL statements, which
    is normal).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            affected = cur.rowcount
        conn.commit()
        return affected


def get_schema_info() -> List[Dict[str, Any]]:
    """Dynamically introspect the database schema via INFORMATION_SCHEMA.

    Returns a list of table descriptors:
    [
      {
        "table": "students",
        "columns": [{"name": "id", "type": "int", "key": "PRI"}, ...],
        "foreign_keys": [{"column": "course_id", "references_table": "courses", "references_column": "id"}]
      },
      ...
    ]
    Only tables in ALLOWED_TABLES are returned - this keeps the AI (and
    the schema page) scoped to the college domain even if other tables
    exist in the same database.
    """
    tables: List[Dict[str, Any]] = []

    with get_connection() as conn:
        with conn.cursor() as cur:
            placeholders = ", ".join(["%s"] * len(ALLOWED_TABLES))

            cur.execute(
                f"""
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_KEY, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ({placeholders})
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                [DB_NAME, *ALLOWED_TABLES],
            )
            columns_rows = cur.fetchall()

            cur.execute(
                f"""
                SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME IN ({placeholders})
                  AND REFERENCED_TABLE_NAME IS NOT NULL
                """,
                [DB_NAME, *ALLOWED_TABLES],
            )
            fk_rows = cur.fetchall()

    by_table: Dict[str, Dict[str, Any]] = {
        name: {"table": name, "columns": [], "foreign_keys": []} for name in ALLOWED_TABLES
    }

    for row in columns_rows:
        t = row["TABLE_NAME"]
        if t not in by_table:
            continue
        by_table[t]["columns"].append(
            {
                "name": row["COLUMN_NAME"],
                "type": row["DATA_TYPE"],
                "key": row["COLUMN_KEY"],
                "nullable": row["IS_NULLABLE"] == "YES",
            }
        )

    for row in fk_rows:
        t = row["TABLE_NAME"]
        if t not in by_table:
            continue
        by_table[t]["foreign_keys"].append(
            {
                "column": row["COLUMN_NAME"],
                "references_table": row["REFERENCED_TABLE_NAME"],
                "references_column": row["REFERENCED_COLUMN_NAME"],
            }
        )

    for name in ALLOWED_TABLES:
        if by_table[name]["columns"]:
            tables.append(by_table[name])

    return tables


def schema_as_prompt_text(schema: Optional[List[Dict[str, Any]]] = None) -> str:
    """Render the schema as compact text for the LLM prompt."""
    if schema is None:
        schema = get_schema_info()

    lines = []
    for table in schema:
        col_desc = ", ".join(f"{c['name']} {c['type']}" for c in table["columns"])
        lines.append(f"Table `{table['table']}`: {col_desc}")
        for fk in table["foreign_keys"]:
            lines.append(
                f"  - {table['table']}.{fk['column']} references "
                f"{fk['references_table']}.{fk['references_column']}"
            )
    return "\n".join(lines)
