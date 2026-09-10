"""
main.py
CampusQuery AI - FastAPI backend.

Endpoints:
    GET  /            -> API status info (JSON)
    GET  /health       -> database + AI service connectivity check
    GET  /schema       -> friendly table/column/relationship info for the UI
    GET  /app          -> serves the frontend (index.html)
    POST /chat         -> natural language in, answer or confirmation request out
    POST /confirm      -> apply or cancel a pending modification

Nothing in this file ever sends SQL, JSON error bodies from MySQL, Python
tracebacks, or credentials to the client. Every exception is caught and
converted to a short, friendly message.
"""

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Support both invocation styles:
#   from the project root:  uvicorn backend.main:app   (package import)
#   from inside backend/:   uvicorn main:app           (plain script import)
try:
    from . import database, ollama_service, sql_service
    from .database import DatabaseUnavailableError
    from .ollama_service import AIServiceUnavailableError
    from .sql_service import QueryType, SqlSafetyError
except ImportError:
    import database
    import ollama_service
    import sql_service
    from database import DatabaseUnavailableError
    from ollama_service import AIServiceUnavailableError
    from sql_service import QueryType, SqlSafetyError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("campusquery.main")

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(title="CampusQuery AI", description="AI assistant for a college database")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# ---------------------------------------------------------------------------
# In-memory store for pending confirmations.
# Keyed by a random confirmation_id - the actual SQL never leaves the server.
# ---------------------------------------------------------------------------
PENDING: Dict[str, Dict[str, Any]] = {}
PENDING_TTL_SECONDS = 5 * 60


def _cleanup_pending() -> None:
    now = time.time()
    expired = [cid for cid, entry in PENDING.items() if now - entry["created_at"] > PENDING_TTL_SECONDS]
    for cid in expired:
        PENDING.pop(cid, None)


GENERIC_ERROR_MESSAGE = "Something went wrong on my end. Please try again in a moment."
DB_DOWN_MESSAGE = "I can't reach the college database right now. Please try again shortly."
AI_DOWN_MESSAGE = "The AI service is temporarily unavailable. Please try again shortly."
UNSUPPORTED_ENTITY_MESSAGE = (
    "I can't answer that because the requested information is not available "
    "in the college database. I can currently answer questions about "
    "students, courses, and departments."
)

UNSUPPORTED_ENTITIES = (
    "employee",
    "employees",
    "faculty",
    "faculties",
    "teacher",
    "teachers",
    "professor",
    "professors",
    "lecturer",
    "lecturers",
    "staff",
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[ChatTurn] = Field(default_factory=list)


class ChatResponse(BaseModel):
    type: str  # "answer" | "confirm" | "error"
    message: str
    data: Optional[List[Dict[str, Any]]] = None
    confirmation_id: Optional[str] = None
    sql: Optional[str] = None


class ConfirmRequest(BaseModel):
    confirmation_id: str
    confirmed: bool


class ConfirmResponse(BaseModel):
    type: str  # "answer" | "cancelled" | "error"
    message: str
    data: Optional[List[Dict[str, Any]]] = None


# ---------------------------------------------------------------------------
# Basic endpoints
# ---------------------------------------------------------------------------
@app.get("/")
def read_root():
    return {"status": "CampusQuery AI API is running", "docs": "/docs", "app": "/app"}


@app.get("/health")
def health_check():
    db_ok = database.check_connection()
    ai_ok = ollama_service.check_connection()
    return {
        "status": "ok" if (db_ok and ai_ok) else "degraded",
        "database": "connected" if db_ok else "unavailable",
        "ai_service": "connected" if ai_ok else "unavailable",
    }


@app.get("/app")
def serve_app():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        return JSONResponse(status_code=404, content={"detail": "Frontend not found"})
    return FileResponse(str(index_path))


@app.get("/schema")
def get_schema():
    """Friendly schema overview for the Schema page in the UI.
    Table/column names only - no data, no credentials."""
    try:
        tables = database.get_schema_info()
        return {"tables": tables}
    except DatabaseUnavailableError:
        return JSONResponse(status_code=503, content={"detail": DB_DOWN_MESSAGE})
    except Exception:
        logger.exception("Unexpected error building schema")
        return JSONResponse(status_code=500, content={"detail": GENERIC_ERROR_MESSAGE})


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
def _history_to_text(history: List[ChatTurn], max_turns: int = 6) -> str:
    recent = history[-max_turns:]
    lines = []
    for turn in recent:
        role = "User" if turn.role == "user" else "Assistant"
        lines.append(f"{role}: {turn.content}")
    return "\n".join(lines)

def _requests_unsupported_entity(user_message: str) -> bool:
    """Return True when the user asks for an entity not present in the schema."""
    text = user_message.lower()
    return any(
        entity in text
        for entity in UNSUPPORTED_ENTITIES
    )

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    _cleanup_pending()

    user_message = (req.message or "").strip()
    if not user_message:
        return ChatResponse(type="error", message="Please type a question about the college database.")

    # 1. Schema
    try:
        schema_text = database.schema_as_prompt_text()
    except DatabaseUnavailableError:
        return ChatResponse(type="error", message=DB_DOWN_MESSAGE)
    except Exception:
        logger.exception("Unexpected error loading schema")
        return ChatResponse(type="error", message=GENERIC_ERROR_MESSAGE)

    # 2. Reject requests for entities that do not exist in this database.
    if _requests_unsupported_entity(user_message):
        return ChatResponse(
            type="answer",
            message=UNSUPPORTED_ENTITY_MESSAGE,
        )

    # 3. Natural language -> SQL
    history_text = _history_to_text(req.history)
    try:
        sql = ollama_service.generate_sql(user_message, schema_text, history_text)
    except AIServiceUnavailableError:
        return ChatResponse(type="error", message=AI_DOWN_MESSAGE)
    except Exception:
        logger.exception("Unexpected error generating SQL")
        return ChatResponse(type="error", message=GENERIC_ERROR_MESSAGE)
    if not sql:
        return ChatResponse(
            type="answer",
            message=(
                "I can't answer that because the requested information is not "
                "available in the college database. I can currently answer "
                "questions about students, courses, and departments."
            ),
        )

    # 4. Validate / classify
    try:
        query_type = sql_service.classify_and_validate(sql)
    except SqlSafetyError as exc:
        return ChatResponse(type="error", message=str(exc))
    except Exception:
        logger.exception("Unexpected error validating SQL")
        return ChatResponse(type="error", message=GENERIC_ERROR_MESSAGE)

    # 4a. Read-only -> run immediately
    if query_type == QueryType.SELECT:
        try:
            rows = database.run_select(sql)
        except DatabaseUnavailableError:
            return ChatResponse(type="error", message=DB_DOWN_MESSAGE)
        except Exception:
            logger.exception("Unexpected error running SELECT")
            return ChatResponse(
                type="error",
                message="I couldn't complete that request against the college database. Could you rephrase it?",
            )

        try:
            answer = ollama_service.generate_natural_answer(user_message, rows)
        except AIServiceUnavailableError:
            return ChatResponse(type="error", message=AI_DOWN_MESSAGE)
        except Exception:
            logger.exception("Unexpected error generating answer")
            return ChatResponse(type="error", message=GENERIC_ERROR_MESSAGE)

        return ChatResponse(
        type="answer",
        message=answer,
        data=rows if rows else [],
        sql=sql,
        )

    # 4b. Modification -> hold for confirmation, never execute yet
    try:
        confirm_message = ollama_service.generate_confirmation_message(user_message, query_type.value)
        if not confirm_message or "this will" not in confirm_message.lower():
            confirm_message = sql_service.describe_modification(sql, query_type)
    except AIServiceUnavailableError:
        confirm_message = sql_service.describe_modification(sql, query_type)
    except Exception:
        logger.exception("Unexpected error generating confirmation message")
        confirm_message = sql_service.describe_modification(sql, query_type)

    confirmation_id = str(uuid.uuid4())
    PENDING[confirmation_id] = {
        "sql": sql,
        "query_type": query_type,
        "created_at": time.time(),
    }

    return ChatResponse(
        type="confirm",
        message=f"{confirm_message} Would you like me to continue?",
        confirmation_id=confirmation_id,
        sql=sql,
    )


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------
@app.post("/confirm", response_model=ConfirmResponse)
def confirm(req: ConfirmRequest):
    _cleanup_pending()

    entry = PENDING.pop(req.confirmation_id, None)
    if entry is None:
        return ConfirmResponse(
            type="error",
            message="That confirmation has expired or was already handled. Please ask again.",
        )

    if not req.confirmed:
        return ConfirmResponse(type="cancelled", message="Okay, I won't make that change.")

    sql = entry["sql"]

    # Re-validate right before executing - defense in depth.
    try:
        query_type = sql_service.classify_and_validate(sql)
    except SqlSafetyError as exc:
        return ConfirmResponse(type="error", message=str(exc))

    if query_type != QueryType.MODIFY:
        return ConfirmResponse(type="error", message=GENERIC_ERROR_MESSAGE)

    try:
        affected = database.run_modification(sql)
    except DatabaseUnavailableError:
        return ConfirmResponse(type="error", message=DB_DOWN_MESSAGE)
    except Exception:
        logger.exception("Unexpected error applying modification")
        return ConfirmResponse(
            type="error",
            message="I couldn't complete that change. The record may not exist, or the request may conflict with existing data.",
        )

    if affected and affected > 0:
        message = "Done! The college database has been updated."
    else:
        message = "That's done, though it looks like nothing matched to change."

    return ConfirmResponse(type="answer", message=message)
