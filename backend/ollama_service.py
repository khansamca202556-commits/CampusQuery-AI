"""
ollama_service.py
All communication with the local Ollama (llama3.2) model.

Two jobs only:
  1. Turn a natural-language request + schema into a single MySQL statement.
  2. Turn a natural-language request + query results into a friendly answer.

The model never talks to the user directly - main.py always routes its
output through sql_service for safety checks before anything happens,
and the raw SQL is never sent back to the frontend.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("campusquery.ollama")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
REQUEST_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "30"))


class AIServiceUnavailableError(Exception):
    """Raised whenever Ollama can't be reached or returns something
    unusable. Caller turns this into a friendly, non-technical message."""
    pass


def _call_ollama(prompt: str, system: Optional[str] = None) -> str:
    payload: Dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0},
    }
    if system:
        payload["system"] = system

    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("response") or "").strip()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Ollama call failed: %s", exc)
        raise AIServiceUnavailableError(str(exc)) from exc


def check_connection() -> bool:
    """Lightweight health check used by GET /health."""
    try:
        resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


_SQL_SYSTEM_PROMPT = """You are the SQL generator for CampusQuery AI, a college database assistant.

Your job is to convert the user's natural-language request into ONE valid MySQL statement.

STRICT RULES:
- Output ONLY the SQL statement.
- No explanation, markdown, code fences, comments, or extra text.
- Always end the statement with a semicolon.
- Use ONLY tables and columns that actually exist in the provided database schema.
- NEVER invent, guess, rename, or move a column from one table to another.
- Carefully check which table owns every column before writing SQL.
- Use JOINs whenever information is requested from related tables.
- Follow the foreign-key relationships shown in the schema.
- For questions involving students and their course, JOIN students with courses using students.course_id = courses.id.
- For questions involving courses and departments, JOIN courses with departments using courses.department_id = departments.id.
- If a requested value is stored in another related table, JOIN that table instead of selecting the value directly from the current table.
- Prefer SELECT for questions and information requests.
- Use INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, or TRUNCATE only when the user clearly requests a database change.
- Never use multiple SQL statements.
- Never use administrative commands such as GRANT, REVOKE, CREATE USER, SET GLOBAL, SHUTDOWN, OUTFILE, LOAD_FILE, etc.
- If a modification is ambiguous, prefer matching by name.
- For ranking questions such as highest/lowest marks, return both the person's name and the ranked value.
- Return exactly ONE SQL statement.
- Prefer SELECT for questions and information requests.

IMPORTANT DATABASE RELATIONSHIPS:
students.course_id -> courses.id
courses.department_id -> departments.id

COMMON EXAMPLES:
User: "Show each student's name and their course"
SQL:
SELECT s.name, c.course_name FROM students AS s JOIN courses AS c ON s.course_id = c.id;

User: "Show students with their department"
SQL:
SELECT s.name, d.department_name
FROM students AS s
JOIN courses AS c ON s.course_id = c.id
JOIN departments AS d ON c.department_id = d.id;

User: "Which student has the highest marks?"
SQL:
SELECT name, marks FROM students ORDER BY marks DESC LIMIT 1;

Before returning SQL, mentally verify:
1. Every table exists.
2. Every selected column exists in the table being referenced.
3. Every JOIN uses the correct foreign-key relationship.
4. The SQL is valid MySQL.
5. There is only one statement.
"""


def _build_sql_prompt(user_message: str, schema_text: str, history_text: str) -> str:
    parts = [
        "DATABASE SCHEMA - THIS IS THE SOURCE OF TRUTH:",
        schema_text,
        "",
        "IMPORTANT SQL INSTRUCTIONS:",
        "- Use only tables and columns explicitly listed above.",
        "- Never invent a column.",
        "- Check which table owns every column before selecting it.",
        "- Use JOIN when the requested information belongs to another table.",
        "- students.course_id references courses.id.",
        "- courses.department_id references departments.id.",
        "- course_name belongs to courses.",
        "- department_name belongs to departments.",
        "",
    ]

    if history_text:
        parts.append("RECENT CONVERSATION (context only):")
        parts.append(history_text)
        parts.append("")

    parts.append(f"USER REQUEST:\n{user_message}")
    parts.append("")
    parts.append("Return ONE valid MySQL statement only:")

    return "\n".join(parts)


_CODE_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_LEADING_LABEL_RE = re.compile(r"^\s*(sql|query|statement)\s*:\s*", re.IGNORECASE)


def _extract_sql(raw: str) -> str:
    """Clean up common LLM formatting artifacts around the SQL statement."""
    text = raw.strip()

    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    text = _LEADING_LABEL_RE.sub("", text).strip()

    # The model uses this marker when the requested entity/information
    # does not exist in the database schema.
    if text.upper().rstrip(";").strip() == "UNSUPPORTED_REQUEST":
        return ""

    # If the model added prose after the statement, keep only the first
    semi_index = text.find(";")
    if semi_index != -1:
        text = text[: semi_index + 1]

    return text.strip()


def generate_sql(user_message: str, schema_text: str, history_text: str = "") -> str:
    """Ask the model for a single SQL statement. Returns the cleaned
    statement text - NOT validated yet, that's sql_service's job."""
    prompt = _build_sql_prompt(user_message, schema_text, history_text)
    raw = _call_ollama(prompt, system=_SQL_SYSTEM_PROMPT)
    return _extract_sql(raw)


_ANSWER_SYSTEM_PROMPT = """You are CampusQuery AI, a friendly assistant that helps people explore a college database in plain English.

Rules:
- Answer ONLY using the data provided below. Never invent students, marks, courses, or departments.
- Never mention SQL, tables, columns, databases, JSON, or anything technical.
- Be concise and conversational, like you're speaking to a colleague.
- If the data is empty, say plainly that nothing matched, in plain English.
- Do not use markdown tables in your answer - a table will be shown separately if useful. Just summarize in words.
- Never claim that information is missing if the provided data contains the answer.
- When a value is present in the returned data, explicitly use that value in the answer.
"""


def generate_natural_answer(user_message: str, rows: List[Dict[str, Any]]) -> str:
    """Turn query results into a short, friendly natural-language answer."""
    preview = rows[:50]  # keep prompt bounded for large result sets
    prompt = (
        f"User asked: {user_message}\n\n"
        f"Data returned ({len(rows)} row(s), showing up to 50):\n"
        f"{json.dumps(preview, default=str)}\n\n"
        "Write a short, friendly answer in plain English:"
    )
    try:
        return _call_ollama(prompt, system=_ANSWER_SYSTEM_PROMPT)
    except AIServiceUnavailableError:
        raise
    except Exception as exc:  # defensive - never let formatting crash the answer
        logger.error("Failed to generate natural answer: %s", exc)
        raise AIServiceUnavailableError(str(exc)) from exc


_CONFIRM_SYSTEM_PROMPT = """You are CampusQuery AI. You are about to make a change to a college database
and must describe it to a non-technical user in ONE short plain-English sentence,
starting with "This will ...". Never mention SQL, tables, or columns by their technical names -
describe the change in terms of students, courses, or departments.
"""


def generate_confirmation_message(user_message: str, query_type: str) -> str:
    """Ask the model for a plain-language description of a pending
    modification, to show alongside Confirm/Cancel."""
    prompt = (
        f"The user asked: {user_message}\n"
        f"This is a {query_type} operation on the college database.\n"
        "Describe the change in one short sentence starting with \"This will\":"
    )
    return _call_ollama(prompt, system=_CONFIRM_SYSTEM_PROMPT)
