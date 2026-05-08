# api.py
"""
AgentKit Studio API
Production-grade local backend for AgentKit Studio.

Run:
    pip install fastapi uvicorn pydantic requests python-multipart
    uvicorn api:app --reload --host 127.0.0.1 --port 8000

Optional frontend:
    Put frontend files inside ./static
    Open: http://127.0.0.1:8000

Expected project files:
    agentkit.py
    tools.py
    api.py
    input/
    output/
    static/                 optional frontend

Core features:
- Secure local authentication and registration
- SQLite persistence plus JSON mirrors/backups
- Auto-discovers @tool functions from tools.py
- Creates, searches, sorts, updates, duplicates, deletes automations
- Stores automation versions and run history
- Generates Python code from visual automation config
- Best-effort parses generated AgentKit code back into visual automation config
- Runs automations locally through AgentKit
- Streams live run events using Server-Sent Events
- Provides simple/detailed/debug run event views
- Lists input and output files safely
- Serves the frontend from ./static at /, /app, /studio

Environment variables:
    AGENTKIT_MODEL=gemma4
    OLLAMA_BASE_URL=http://localhost:11434
    AGENTKIT_AUTH_DISABLED=false
    AGENTKIT_ALLOW_REGISTRATION=true
    AGENTKIT_SESSION_DAYS=14
    AGENTKIT_CORS_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
    AGENTKIT_SECRET_KEY=<random secret>

Security notes:
- This is designed for local-first usage.
- Do not expose it directly to the public internet without HTTPS and a reverse proxy.
- Auth is token-based; tokens are stored hashed in SQLite.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import importlib
import inspect
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import sqlite3
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple
from analyzer.engine import analyze_project
from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, validator

from agentkit import Agent, AgentResult, Supervisor, Tool, Workflow, doctor


# ---------------------------------------------------------------------
# Constants and directories
# ---------------------------------------------------------------------


APP_NAME = "AgentKit Studio"
APP_VERSION = "2.0.0"

PROJECT_ROOT = Path.cwd().resolve()
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
STATIC_DIR = PROJECT_ROOT / "static"

AGENTKIT_DIR = PROJECT_ROOT / ".agentkit"
DB_PATH = AGENTKIT_DIR / "studio.sqlite3"
AUTOMATIONS_DIR = AGENTKIT_DIR / "automations"
RUNS_DIR = AGENTKIT_DIR / "runs"
EXPORTS_DIR = AGENTKIT_DIR / "exports"
UPLOADS_DIR = INPUT_DIR

DEFAULT_MODEL = os.getenv("AGENTKIT_MODEL", "gemma4")
DEFAULT_HOST = os.getenv("AGENTKIT_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("AGENTKIT_PORT", "8000"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

AUTH_DISABLED = os.getenv("AGENTKIT_AUTH_DISABLED", "false").strip().lower() in {"1", "true", "yes", "y"}
ALLOW_REGISTRATION = os.getenv("AGENTKIT_ALLOW_REGISTRATION", "true").strip().lower() in {"1", "true", "yes", "y"}
SESSION_DAYS = int(os.getenv("AGENTKIT_SESSION_DAYS", "14"))
SECRET_KEY = os.getenv("AGENTKIT_SECRET_KEY") or secrets.token_urlsafe(48)

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "AGENTKIT_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
]

SAFE_INPUT_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".html", ".xml", ".log", ".py", ".yaml", ".yml",
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf", ".xlsx", ".xls", ".docx",
}

SAFE_OUTPUT_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".html", ".xml", ".log", ".py", ".yaml", ".yml",
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf", ".xlsx",
}

for directory in [
    INPUT_DIR,
    OUTPUT_DIR,
    DATA_DIR,
    REPORTS_DIR,
    AGENTKIT_DIR,
    AUTOMATIONS_DIR,
    RUNS_DIR,
    EXPORTS_DIR,
    UPLOADS_DIR,
]:
    directory.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
# FastAPI setup
# ---------------------------------------------------------------------


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Local-first API for visually building and running AgentKit automations.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------
# Type aliases and Pydantic models
# ---------------------------------------------------------------------


AutomationType = Literal["agent", "workflow"]
RunStatus = Literal["queued", "running", "success", "failed", "cancelled"]
EventType = Literal[
    "AGENT", "MODEL", "PLAN", "ACTION", "OBSERVATION", "ERROR", "RETRY", "DONE", "WORKFLOW", "SYSTEM"
]
UserRole = Literal["admin", "user"]
SortField = Literal["name", "created_at", "updated_at", "last_run_at"]
SortDirection = Literal["asc", "desc"]
RunEventView = Literal["simple", "detailed", "debug"]


class UserPublicModel(BaseModel):
    id: str
    email: str
    name: str
    role: UserRole
    created_at: str
    last_login_at: Optional[str] = None


class RegisterRequestModel(BaseModel):
    email: str
    password: str
    name: str = ""

    @validator("email")
    def clean_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
            raise ValueError("Invalid email address.")
        return value

    @validator("password")
    def strong_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("Password must contain at least one letter and one number.")
        return value


class LoginRequestModel(BaseModel):
    email: str
    password: str

    @validator("email")
    def clean_email(cls, value: str) -> str:
        return value.strip().lower()


class AuthResponseModel(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_at: str
    user: UserPublicModel


class ToolParameterModel(BaseModel):
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""


class ToolModel(BaseModel):
    name: str
    description: str
    category: str = "Other"
    mode: Literal["read", "write", "memory", "compute", "system", "other"] = "other"
    parameters: List[ToolParameterModel] = Field(default_factory=list)


class WorkflowStepModel(BaseModel):
    agent_name: str = "Step Agent"
    goal: str = ""
    task: str
    tools: List[str] = Field(default_factory=list)
    max_steps: int = 10


class AutomationModel(BaseModel):
    id: Optional[str] = None
    name: str = "Untitled Automation"
    type: AutomationType = "agent"
    model: str = DEFAULT_MODEL
    goal: str = ""
    task: str = ""
    tools: List[str] = Field(default_factory=list)
    max_steps: int = 12
    safe_mode: bool = True
    temperature: float = 0.2
    base_url: str = OLLAMA_BASE_URL
    steps: List[WorkflowStepModel] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AutomationSummaryModel(BaseModel):
    id: str
    name: str
    type: AutomationType
    model: str
    goal: str = ""
    tools: List[str]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_run_status: Optional[RunStatus] = None
    last_run_at: Optional[str] = None
    run_count: int = 0
    owner_id: Optional[str] = None


class AutomationListResponseModel(BaseModel):
    items: List[AutomationSummaryModel]
    total: int
    page: int
    page_size: int


class RunRequestModel(BaseModel):
    automation_id: Optional[str] = None
    automation: Optional[AutomationModel] = None


class RunStartResponseModel(BaseModel):
    run_id: str
    status: RunStatus
    stream_url: str
    details_url: str


class RunEventModel(BaseModel):
    id: str
    run_id: str
    type: EventType
    message: str
    timestamp: str
    step: Optional[int] = None
    duration_ms: Optional[int] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    level: Literal["primary", "secondary", "debug"] = "primary"
    summary: Optional[str] = None


class RunInfoModel(BaseModel):
    run_id: str
    automation_id: Optional[str] = None
    automation_name: str
    status: RunStatus
    started_at: str
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    output_files: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)


class CodeRequestModel(BaseModel):
    automation: AutomationModel


class CodeParseRequestModel(BaseModel):
    code: str
    fallback: Optional[AutomationModel] = None


class SyncResponseModel(BaseModel):
    automation: AutomationModel
    code: str
    warnings: List[str] = Field(default_factory=list)


class UploadResponseModel(BaseModel):
    filename: str
    path: str
    size_bytes: int


class SettingUpdateModel(BaseModel):
    key: str
    value: Any


# ---------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------


@contextmanager
def db() -> Iterable[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen_at TEXT,
                user_agent TEXT,
                ip_address TEXT
            );

            CREATE TABLE IF NOT EXISTS automations (
                id TEXT PRIMARY KEY,
                owner_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                model TEXT NOT NULL,
                goal TEXT DEFAULT '',
                task TEXT DEFAULT '',
                tools_json TEXT NOT NULL DEFAULT '[]',
                config_json TEXT NOT NULL,
                code TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS automation_versions (
                id TEXT PRIMARY KEY,
                automation_id TEXT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
                owner_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                config_json TEXT NOT NULL,
                code TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                note TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                automation_id TEXT,
                owner_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                automation_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER,
                result_json TEXT,
                error TEXT,
                output_files_json TEXT DEFAULT '[]',
                stats_json TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS run_events (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                summary TEXT,
                timestamp TEXT NOT NULL,
                step INTEGER,
                duration_ms INTEGER,
                data_json TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_automations_owner_updated ON automations(owner_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_runs_automation_started ON runs(automation_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_run_events_run_timestamp ON run_events(run_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);
            """
        )


_init_db()


# ---------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _local_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value)
    value = value.strip("-")
    return value or "automation"


def _safe_join(base: Path, relative_path: str) -> Path:
    candidate = (base / relative_path).resolve()
    base_resolved = base.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsafe path outside allowed directory.")
    return candidate


def _json_dump_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _json_load_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _model_dump(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _automation_path(automation_id: str) -> Path:
    return AUTOMATIONS_DIR / f"{automation_id}.json"


def _run_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def _run_events_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.jsonl"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _sign_value(value: str) -> str:
    return hmac.new(SECRET_KEY.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def _password_hash(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000)
    return digest.hex()


def _verify_password(password: str, salt: str, password_hash: str) -> bool:
    actual = _password_hash(password, salt)
    return hmac.compare_digest(actual, password_hash)


def _public_user(row: sqlite3.Row) -> UserPublicModel:
    return UserPublicModel(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        role=row["role"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


def _user_count() -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def _automation_from_row(row: sqlite3.Row) -> AutomationModel:
    data = json.loads(row["config_json"])
    data["id"] = row["id"]
    data["created_at"] = row["created_at"]
    data["updated_at"] = row["updated_at"]
    return AutomationModel(**data)


def _save_automation_json_mirror(automation: AutomationModel, owner_id: Optional[str] = None) -> None:
    payload = _model_dump(automation)
    payload["owner_id"] = owner_id
    _json_dump_file(_automation_path(automation.id or _slugify(automation.name)), payload)


def _append_run_event(run_id: str, event: Dict[str, Any]) -> None:
    path = _run_events_path(run_id)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    with db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO run_events
            (id, run_id, type, level, message, summary, timestamp, step, duration_ms, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                run_id,
                event["type"],
                event.get("level", "primary"),
                event["message"],
                event.get("summary"),
                event["timestamp"],
                event.get("step"),
                event.get("duration_ms"),
                json.dumps(event.get("data", {}), ensure_ascii=False),
            ),
        )


# ---------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------


class OptionalUser(BaseModel):
    id: str = "local"
    email: str = "local@agentkit"
    name: str = "Local User"
    role: UserRole = "admin"
    created_at: str = Field(default_factory=_now)
    last_login_at: Optional[str] = None


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> UserPublicModel:
    if AUTH_DISABLED:
        local = OptionalUser()
        return UserPublicModel(**_model_dump(local))

    token = None
    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get("agentkit_session")

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    token_hash = _hash_token(token)
    now = _now()

    with db() as conn:
        row = conn.execute(
            """
            SELECT users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session.")
        conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash))
        return _public_user(row)


async def get_optional_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Optional[UserPublicModel]:
    try:
        return await get_current_user(request, authorization)
    except HTTPException:
        return None


def require_admin(user: UserPublicModel = Depends(get_current_user)) -> UserPublicModel:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    return user


def _create_session(user_id: str, request: Request) -> Tuple[str, str]:
    raw_token = secrets.token_urlsafe(48)
    token_hash = _hash_token(raw_token)
    session_id = str(uuid.uuid4())
    created_at = _now()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    user_agent = request.headers.get("user-agent", "")[:500]
    ip_address = request.client.host if request.client else ""

    with db() as conn:
        conn.execute(
            """
            INSERT INTO sessions
            (id, user_id, token_hash, created_at, expires_at, last_seen_at, user_agent, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, user_id, token_hash, created_at, expires_at, created_at, user_agent, ip_address),
        )
    return raw_token, expires_at


@app.post("/api/auth/register", response_model=AuthResponseModel)
def register(payload: RegisterRequestModel, request: Request, response: Response):
    if not AUTH_DISABLED:
        count = _user_count()
        if count > 0 and not ALLOW_REGISTRATION:
            raise HTTPException(status_code=403, detail="Registration is disabled.")

    user_id = str(uuid.uuid4())
    salt = secrets.token_hex(16)
    password_hash = _password_hash(payload.password, salt)
    now = _now()
    role: UserRole = "admin" if _user_count() == 0 else "user"
    name = payload.name.strip() or payload.email.split("@")[0]

    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO users
                (id, email, name, role, password_hash, password_salt, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, payload.email, name, role, password_hash, salt, now, now),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    token, expires_at = _create_session(user_id, request)
    response.set_cookie(
        "agentkit_session",
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_DAYS * 24 * 60 * 60,
    )

    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return AuthResponseModel(token=token, expires_at=expires_at, user=_public_user(row))


@app.post("/api/auth/login", response_model=AuthResponseModel)
def login(payload: LoginRequestModel, request: Request, response: Response):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (payload.email,)).fetchone()
    if not row or not _verify_password(payload.password, row["password_salt"], row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    now = _now()
    with db() as conn:
        conn.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (now, now, row["id"]))
    token, expires_at = _create_session(row["id"], request)
    response.set_cookie(
        "agentkit_session",
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_DAYS * 24 * 60 * 60,
    )
    with db() as conn:
        fresh = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    return AuthResponseModel(token=token, expires_at=expires_at, user=_public_user(fresh))


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, authorization: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get("agentkit_session")
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
    response.delete_cookie("agentkit_session")
    return {"ok": True}


@app.get("/api/auth/me", response_model=UserPublicModel)
def me(user: UserPublicModel = Depends(get_current_user)):
    return user


@app.get("/api/auth/status")
def auth_status(user: Optional[UserPublicModel] = Depends(get_optional_user)):
    return {
        "auth_disabled": AUTH_DISABLED,
        "registration_allowed": ALLOW_REGISTRATION or _user_count() == 0,
        "has_users": _user_count() > 0,
        "user": _model_dump(user) if user else None,
    }


# ---------------------------------------------------------------------
# Runtime state and run event model
# ---------------------------------------------------------------------


@dataclass
class RunState:
    run_id: str
    automation: AutomationModel
    owner_id: Optional[str]
    status: RunStatus = "queued"
    started_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    result: Optional[AgentResult] = None
    error: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    event_queue: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)
    thread: Optional[threading.Thread] = None
    cancel_requested: bool = False

    def push_event(
        self,
        event_type: EventType,
        message: str,
        *,
        step: Optional[int] = None,
        duration_ms: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        level = _event_level(event_type, message)
        event = {
            "id": str(uuid.uuid4()),
            "run_id": self.run_id,
            "type": event_type,
            "level": level,
            "message": str(message),
            "summary": _summarize_event(event_type, str(message), data or {}),
            "timestamp": _now(),
            "step": step,
            "duration_ms": duration_ms,
            "data": data or {},
        }
        self.events.append(event)
        self.event_queue.put(event)
        _append_run_event(self.run_id, event)
        return event


RUNS: Dict[str, RunState] = {}
RUNS_LOCK = threading.Lock()


def _event_level(event_type: str, message: str) -> Literal["primary", "secondary", "debug"]:
    if event_type in {"MODEL", "SYSTEM"}:
        return "debug"
    if event_type in {"OBSERVATION"} and len(message) > 1200:
        return "secondary"
    if event_type in {"PLAN", "ACTION", "DONE", "ERROR", "RETRY", "WORKFLOW"}:
        return "primary"
    return "secondary"


def _summarize_event(event_type: str, message: str, data: Dict[str, Any]) -> str:
    text = re.sub(r"\s+", " ", message).strip()
    if event_type == "ACTION":
        match = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\((.*)\)", text)
        if match:
            return match.group(1)
    if event_type == "OBSERVATION":
        if "Markdown report created" in text:
            return text
        if "FILE |" in text:
            return "Listed files"
        if len(text) > 140:
            return text[:140] + "..."
    if len(text) > 120:
        return text[:120] + "..."
    return text


# ---------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------


def _load_tools_module(reload_module: bool = True):
    try:
        import tools as tools_module
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not import tools.py: {type(exc).__name__}: {exc}")
    if reload_module:
        try:
            tools_module = importlib.reload(tools_module)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not reload tools.py: {type(exc).__name__}: {exc}")
    return tools_module


def get_tool_registry(reload_module: bool = True) -> Dict[str, Tool]:
    tools_module = _load_tools_module(reload_module=reload_module)
    discovered: Dict[str, Tool] = {}
    all_tools = getattr(tools_module, "ALL_TOOLS", None)
    if isinstance(all_tools, Iterable):
        for item in all_tools:
            if isinstance(item, Tool):
                discovered[item.name] = item
    for _, value in inspect.getmembers(tools_module):
        if isinstance(value, Tool):
            discovered[value.name] = value
    return dict(sorted(discovered.items(), key=lambda kv: kv[0].lower()))


def _tool_category(tool_name: str) -> str:
    name = tool_name.lower()
    if any(x in name for x in ["file", "folder", "rename", "copy", "move", "list"]):
        return "Files"
    if any(x in name for x in ["csv", "chart", "stats", "data", "excel"]):
        return "Data"
    if "pdf" in name:
        return "PDF"
    if any(x in name for x in ["text", "keyword", "word", "clean", "compare"]):
        return "Text"
    if any(x in name for x in ["report", "markdown", "table", "todo"]):
        return "Reports"
    if "memory" in name:
        return "Memory"
    if any(x in name for x in ["calculate", "math"]):
        return "Math"
    return "Other"


def _tool_mode(tool_name: str) -> Literal["read", "write", "memory", "compute", "system", "other"]:
    name = tool_name.lower()
    if name.startswith(("read", "list", "search", "summarize", "extract", "compare", "count", "file_info", "pdf_info")):
        return "read"
    if name.startswith(("write", "create", "append", "move", "copy", "rename", "filter", "convert")):
        return "write"
    if name.startswith("memory"):
        return "memory"
    if name in {"calculate", "basic_stats"} or "stats" in name:
        return "compute"
    if "folder" in name:
        return "system"
    return "other"


def _tool_to_model(tool: Tool) -> ToolModel:
    schema = tool.parameters or {}
    properties = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    params = []
    for name, meta in properties.items():
        params.append(
            ToolParameterModel(
                name=name,
                type=str(meta.get("type", "string")),
                required=name in required,
                description=str(meta.get("description", "")),
            )
        )
    return ToolModel(
        name=tool.name,
        description=tool.description,
        category=_tool_category(tool.name),
        mode=_tool_mode(tool.name),
        parameters=params,
    )


def _get_tools_by_names(tool_names: List[str]) -> List[Tool]:
    registry = get_tool_registry(reload_module=True)
    selected = []
    for name in tool_names:
        tool_obj = registry.get(name)
        if not tool_obj:
            raise HTTPException(status_code=400, detail=f"Tool not found: {name}. Refresh tool library and try again.")
        selected.append(tool_obj)
    return selected


# ---------------------------------------------------------------------
# Python code generation and parsing
# ---------------------------------------------------------------------


def generate_python_code(automation: AutomationModel) -> str:
    if automation.type == "workflow":
        return _generate_workflow_python_code(automation)
    return _generate_agent_python_code(automation)


def _generate_agent_python_code(automation: AutomationModel) -> str:
    tool_names = sorted(dict.fromkeys(automation.tools))
    import_line = f"from tools import {', '.join(tool_names)}" if tool_names else "# from tools import your_tool_here"
    tools_expr = ",\n        ".join(tool_names) if tool_names else ""
    return f'''# app.py
# Generated by AgentKit Studio
# Automation: {automation.name}

from agentkit import Agent
{import_line}


agent = Agent(
    name={automation.name!r},
    model={automation.model!r},
    goal={automation.goal!r},
    tools=[
        {tools_expr}
    ],
    max_steps={automation.max_steps},
    safe_mode={automation.safe_mode},
    temperature={automation.temperature},
)

agent.run("""
{automation.task.strip()}
""")
'''


def _generate_workflow_python_code(automation: AutomationModel) -> str:
    tool_names = sorted({tool for step in automation.steps for tool in step.tools})
    import_line = f"from tools import {', '.join(tool_names)}" if tool_names else "# from tools import your_tool_here"
    agent_blocks = []
    for index, step in enumerate(automation.steps, start=1):
        variable_name = f"agent_{index}"
        tools_expr = ",\n        ".join(step.tools)
        agent_blocks.append(
            f'''{variable_name} = Agent(
    name={step.agent_name!r},
    model={automation.model!r},
    goal={step.goal!r},
    tools=[
        {tools_expr}
    ],
    max_steps={step.max_steps},
    safe_mode={automation.safe_mode},
    temperature={automation.temperature},
)'''
        )
    workflow_steps = []
    for index, step in enumerate(automation.steps, start=1):
        workflow_steps.append(
            f'''workflow.add_step(
    agent_{index},
    """
{step.task.strip()}
    """,
)'''
        )
    return f'''# workflow_app.py
# Generated by AgentKit Studio
# Automation: {automation.name}

from agentkit import Agent, Workflow
{import_line}


{chr(10).join(agent_blocks)}

workflow = Workflow({automation.name!r})

{chr(10).join(workflow_steps)}

workflow.run()
'''


def parse_python_code_to_automation(code: str, fallback: Optional[AutomationModel] = None) -> Tuple[AutomationModel, List[str]]:
    warnings: List[str] = []
    base = fallback or AutomationModel(name="Parsed Automation")
    data = _model_dump(base)

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        warnings.append(f"Could not parse Python: {exc}")
        return AutomationModel(**data), warnings

    imported_tools: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "tools":
            for alias in node.names:
                imported_tools.append(alias.asname or alias.name)

    agent_call: Optional[ast.Call] = None
    run_task: Optional[str] = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn_name = _ast_call_name(node.func)
            if fn_name == "Agent":
                agent_call = node
            if fn_name and fn_name.endswith(".run") and node.args:
                run_task = _safe_literal(node.args[0])

    if agent_call:
        for keyword in agent_call.keywords:
            key = keyword.arg
            if not key:
                continue
            value = _safe_literal(keyword.value)
            if key in {"name", "model", "goal", "max_steps", "safe_mode", "temperature", "base_url"}:
                data[key] = value
            elif key == "tools":
                names = _extract_tool_names_from_ast(keyword.value)
                if names:
                    data["tools"] = names

    if imported_tools and not data.get("tools"):
        data["tools"] = imported_tools
    if run_task is not None:
        data["task"] = run_task.strip()

    data["type"] = "agent"
    data["updated_at"] = _now()
    return AutomationModel(**data), warnings


def _ast_call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _ast_call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _safe_literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except Exception:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return node.value
        return None


def _extract_tool_names_from_ast(node: ast.AST) -> List[str]:
    names: List[str] = []
    if isinstance(node, ast.List):
        for elt in node.elts:
            if isinstance(elt, ast.Name):
                names.append(elt.id)
            elif isinstance(elt, ast.Call):
                name = _ast_call_name(elt.func)
                if name:
                    names.append(name.split(".")[-1])
    return names


# ---------------------------------------------------------------------
# Run logger and execution
# ---------------------------------------------------------------------


class RunEventLogger:
    def __init__(self, run: RunState, also_print: bool = True):
        self.run = run
        self.also_print = also_print

    def _emit(self, event_type: EventType, message: str):
        if self.also_print:
            print(f"[{event_type}] {message}")
        self.run.push_event(event_type, message)

    def agent(self, message: str): self._emit("AGENT", message)
    def model(self, message: str): self._emit("MODEL", message)
    def plan(self, message: str): self._emit("PLAN", message)
    def action(self, message: str): self._emit("ACTION", message)
    def observation(self, message: str): self._emit("OBSERVATION", message)
    def error(self, message: str): self._emit("ERROR", message)
    def retry(self, message: str): self._emit("RETRY", message)
    def done(self, message: str): self._emit("DONE", message)
    def workflow(self, message: str): self._emit("WORKFLOW", message)


def _create_agent_from_automation(automation: AutomationModel, run: Optional[RunState] = None) -> Agent:
    selected_tools = _get_tools_by_names(automation.tools)
    agent = Agent(
        name=automation.name,
        model=automation.model,
        goal=automation.goal,
        tools=selected_tools,
        base_url=automation.base_url or OLLAMA_BASE_URL,
        temperature=automation.temperature,
        max_steps=automation.max_steps,
        safe_mode=automation.safe_mode,
        verbose=True,
    )
    if run:
        agent.logger = RunEventLogger(run)
    return agent


def _create_workflow_from_automation(automation: AutomationModel, run: Optional[RunState] = None) -> Workflow:
    workflow = Workflow(name=automation.name, verbose=True)
    if run:
        workflow.logger = RunEventLogger(run)
    for step in automation.steps:
        step_tools = _get_tools_by_names(step.tools)
        agent = Agent(
            name=step.agent_name,
            model=automation.model,
            goal=step.goal,
            tools=step_tools,
            base_url=automation.base_url or OLLAMA_BASE_URL,
            temperature=automation.temperature,
            max_steps=step.max_steps,
            safe_mode=automation.safe_mode,
            verbose=True,
        )
        if run:
            agent.logger = RunEventLogger(run)
        workflow.add_step(agent, step.task)
    return workflow


def _save_run_state(run: RunState) -> None:
    info = _run_info_dict(run)
    _json_dump_file(_run_path(run.run_id), info)
    with db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs
            (id, automation_id, owner_id, automation_name, status, started_at, finished_at, duration_ms,
             result_json, error, output_files_json, stats_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                info["run_id"],
                info.get("automation_id"),
                run.owner_id,
                info["automation_name"],
                info["status"],
                info["started_at"],
                info.get("finished_at"),
                info.get("duration_ms"),
                json.dumps(info.get("result"), ensure_ascii=False),
                info.get("error"),
                json.dumps(info.get("output_files", []), ensure_ascii=False),
                json.dumps(info.get("stats", {}), ensure_ascii=False),
            ),
        )


def _run_info_dict(run: RunState) -> Dict[str, Any]:
    started = datetime.fromisoformat(run.started_at)
    finished = datetime.fromisoformat(run.finished_at) if run.finished_at else None
    duration_ms = int((finished - started).total_seconds() * 1000) if finished else None
    result_payload = None
    if run.result is not None:
        result_payload = {
            "answer": run.result.answer,
            "steps": run.result.steps,
            "success": getattr(run.result, "success", run.status == "success"),
            "error": getattr(run.result, "error", None),
        }
    return {
        "run_id": run.run_id,
        "automation_id": run.automation.id,
        "automation_name": run.automation.name,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_ms": duration_ms,
        "result": result_payload,
        "error": run.error,
        "output_files": _list_output_files_dict(limit=100),
        "stats": _run_stats_from_events(run.events, duration_ms),
    }


def _run_stats_from_events(events: List[Dict[str, Any]], duration_ms: Optional[int]) -> Dict[str, Any]:
    return {
        "duration_ms": duration_ms,
        "total_events": len(events),
        "steps": len([e for e in events if e.get("type") == "ACTION"]),
        "retries": len([e for e in events if e.get("type") == "RETRY"]),
        "errors": len([e for e in events if e.get("type") == "ERROR"]),
        "outputs": len(_list_output_files_dict(limit=100)),
    }


def _execute_run(run: RunState) -> None:
    start = time.time()
    run.status = "running"
    run.push_event("SYSTEM", f"Run started for automation: {run.automation.name}", data={"automation": _model_dump(run.automation)})
    _save_run_state(run)
    try:
        before_outputs = _snapshot_output_files()
        if run.automation.type == "workflow":
            if not run.automation.steps:
                raise ValueError("Workflow automation has no steps.")
            workflow = _create_workflow_from_automation(run.automation, run=run)
            results = workflow.run()
            answer = "\n\n".join([result.answer for result in results])
            success = all(getattr(result, "success", True) for result in results)
            try:
                result = AgentResult(
                    answer=answer,
                    steps=sum(result.steps for result in results),
                    history=[{"workflow_results": [r.history for r in results]}],
                    success=success,
                    error=None if success else "One or more workflow steps may have failed.",
                )
            except TypeError:
                result = AgentResult(answer=answer, steps=sum(result.steps for result in results), history=[])
        else:
            if not run.automation.task.strip():
                raise ValueError("Automation task cannot be empty.")
            agent = _create_agent_from_automation(run.automation, run=run)
            result = agent.run(run.automation.task)

        run.result = result
        run.status = "success" if getattr(result, "success", True) else "failed"
        run.error = getattr(result, "error", None)
        after_outputs = _snapshot_output_files()
        new_outputs = sorted(list(after_outputs - before_outputs))
        if new_outputs:
            run.push_event("SYSTEM", f"{len(new_outputs)} new output file(s) created.", data={"new_outputs": new_outputs})
        if run.status == "success":
            run.push_event("DONE", result.answer or "Automation completed successfully.")
        else:
            run.push_event("ERROR", run.error or result.answer or "Automation failed.")
    except Exception as exc:
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.push_event("ERROR", run.error, data={"traceback": traceback.format_exc(limit=8)})
    finally:
        run.finished_at = _now()
        duration_ms = int((time.time() - start) * 1000)
        run.push_event("SYSTEM", f"Run finished with status: {run.status}", duration_ms=duration_ms)
        _save_run_state(run)
        run.event_queue.put({"type": "__END__"})


# ---------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------


def _snapshot_output_files() -> set[str]:
    if not OUTPUT_DIR.exists():
        return set()
    return {str(path.relative_to(OUTPUT_DIR)).replace("\\", "/") for path in OUTPUT_DIR.rglob("*") if path.is_file()}


def _list_output_files_dict(limit: int = 200) -> List[Dict[str, Any]]:
    if not OUTPUT_DIR.exists():
        return []
    items = []
    for path in OUTPUT_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(OUTPUT_DIR)).replace("\\", "/")
        stat = path.stat()
        items.append({
            "name": path.name,
            "path": rel,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "extension": path.suffix.lower(),
            "mime_type": mimetypes.guess_type(str(path))[0] or "application/octet-stream",
            "previewable": path.suffix.lower() in {".txt", ".md", ".json", ".csv", ".py", ".html", ".log", ".yaml", ".yml"},
        })
    items.sort(key=lambda item: item["modified_at"], reverse=True)
    return items[:limit]


def _read_file_preview(base: Path, relative_path: str, allowed_exts: set[str], max_chars: int = 50000) -> Dict[str, Any]:
    target = _safe_join(base, relative_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    suffix = target.suffix.lower()
    if suffix not in allowed_exts:
        raise HTTPException(status_code=400, detail="Unsupported file type.")
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf", ".xlsx", ".xls", ".docx"}:
        return {
            "path": relative_path,
            "binary": True,
            "download_url": f"/api/outputs/download?path={relative_path}" if base == OUTPUT_DIR else f"/api/input/download?path={relative_path}",
            "mime_type": mimetypes.guess_type(str(target))[0] or "application/octet-stream",
        }
    content = target.read_text(encoding="utf-8", errors="replace")
    return {
        "path": relative_path,
        "binary": False,
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
        "mime_type": mimetypes.guess_type(str(target))[0] or "text/plain",
    }


# ---------------------------------------------------------------------
# Frontend routes
# ---------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def home():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(
        """
<!doctype html>
<html>
<head>
  <title>AgentKit Studio API</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 40px; background: #fbfaf7; color: #17211a; }
    code { background: #f1efe7; padding: 2px 6px; border-radius: 6px; }
    .card { border: 1px solid #e5e1d6; border-radius: 16px; padding: 24px; max-width: 760px; background: white; }
    a { color: #166534; }
  </style>
</head>
<body>
  <div class="card">
    <h1>AgentKit Studio API</h1>
    <p>Backend is running locally.</p>
    <p>Frontend not found. Put frontend files inside <code>./static</code>.</p>
    <p>Expected: <code>./static/index.html</code></p>
    <p>Docs: <a href="/docs">/docs</a></p>
    <p>Auth: <a href="/api/auth/status">/api/auth/status</a></p>
    <p>Tools: <a href="/api/tools">/api/tools</a></p>
    <p>Automations: <a href="/api/automations">/api/automations</a></p>
    <p>Outputs: <a href="/api/outputs">/api/outputs</a></p>
  </div>
</body>
</html>
        """,
        status_code=200,
    )


@app.get("/app", response_class=HTMLResponse)
def app_home():
    return home()


@app.get("/studio", response_class=HTMLResponse)
def studio_home():
    return home()


# ---------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "project_root": str(PROJECT_ROOT),
        "ollama_base_url": OLLAMA_BASE_URL,
        "default_model": DEFAULT_MODEL,
        "auth_disabled": AUTH_DISABLED,
        "persistent": True,
        "database": str(DB_PATH),
        "time": _now(),
    }


@app.get("/api/doctor")
def api_doctor(model: str = Query(DEFAULT_MODEL), user: UserPublicModel = Depends(get_current_user)):
    ok = doctor(model=model, base_url=OLLAMA_BASE_URL)
    return {"ok": ok, "model": model, "ollama_base_url": OLLAMA_BASE_URL}


@app.get("/api/workspace")
def workspace(user: UserPublicModel = Depends(get_current_user)):
    return {
        "project_root": str(PROJECT_ROOT),
        "input_dir": str(INPUT_DIR),
        "output_dir": str(OUTPUT_DIR),
        "data_dir": str(DATA_DIR),
        "reports_dir": str(REPORTS_DIR),
        "automations_dir": str(AUTOMATIONS_DIR),
        "runs_dir": str(RUNS_DIR),
        "db_path": str(DB_PATH),
    }


# ---------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------


@app.get("/api/settings")
def get_settings(user: UserPublicModel = Depends(get_current_user)):
    with db() as conn:
        rows = conn.execute("SELECT key, value_json FROM settings ORDER BY key").fetchall()
    return {row["key"]: json.loads(row["value_json"]) for row in rows}


@app.put("/api/settings")
def set_setting(payload: SettingUpdateModel, user: UserPublicModel = Depends(get_current_user)):
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)",
            (payload.key, json.dumps(payload.value, ensure_ascii=False), _now()),
        )
    return {"ok": True, "key": payload.key, "value": payload.value}


# ---------------------------------------------------------------------
# Tool endpoints
# ---------------------------------------------------------------------


@app.get("/api/tools", response_model=List[ToolModel])
def list_tools(
    category: Optional[str] = None,
    mode: Optional[str] = None,
    q: str = "",
    user: UserPublicModel = Depends(get_current_user),
):
    registry = get_tool_registry(reload_module=True)
    tools = [_tool_to_model(tool) for tool in registry.values()]
    if category:
        tools = [tool for tool in tools if tool.category.lower() == category.lower()]
    if mode:
        tools = [tool for tool in tools if tool.mode == mode]
    if q.strip():
        needle = q.strip().lower()
        tools = [tool for tool in tools if needle in tool.name.lower() or needle in tool.description.lower()]
    return tools


@app.get("/api/tools/grouped")
def list_tools_grouped(user: UserPublicModel = Depends(get_current_user)):
    registry = get_tool_registry(reload_module=True)
    grouped: Dict[str, List[ToolModel]] = {}
    for tool in registry.values():
        model = _tool_to_model(tool)
        grouped.setdefault(model.category, []).append(model)
    return {
        "groups": {
            category: [_model_dump(item) for item in items]
            for category, items in sorted(grouped.items())
        },
        "counts": {category: len(items) for category, items in sorted(grouped.items())},
    }


@app.get("/api/tools/{tool_name}", response_model=ToolModel)
def get_tool(tool_name: str, user: UserPublicModel = Depends(get_current_user)):
    registry = get_tool_registry(reload_module=True)
    tool_obj = registry.get(tool_name)
    if not tool_obj:
        raise HTTPException(status_code=404, detail="Tool not found.")
    return _tool_to_model(tool_obj)


@app.post("/api/tools/{tool_name}/test")
def test_tool(tool_name: str, args: Dict[str, Any], user: UserPublicModel = Depends(get_current_user)):
    registry = get_tool_registry(reload_module=True)
    tool_obj = registry.get(tool_name)
    if not tool_obj:
        raise HTTPException(status_code=404, detail="Tool not found.")
    try:
        result = tool_obj.run(**args)
        return {"ok": True, "tool": tool_name, "result": str(result)}
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "tool": tool_name, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=5)},
        )


# ---------------------------------------------------------------------
# Automation endpoints
# ---------------------------------------------------------------------


@app.get("/api/automations", response_model=AutomationListResponseModel)
def list_automations(
    q: str = "",
    sort_by: SortField = "updated_at",
    sort_dir: SortDirection = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_deleted: bool = False,
    user: UserPublicModel = Depends(get_current_user),
):
    where = [] if include_deleted else ["deleted_at IS NULL"]
    params: List[Any] = []
    if not AUTH_DISABLED and user.role != "admin":
        where.append("owner_id = ?")
        params.append(user.id)
    if q.strip():
        where.append("(LOWER(name) LIKE ? OR LOWER(goal) LIKE ? OR LOWER(task) LIKE ?)")
        needle = f"%{q.strip().lower()}%"
        params.extend([needle, needle, needle])
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    order = "ASC" if sort_dir == "asc" else "DESC"
    offset = (page - 1) * page_size

    with db() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM automations {where_sql}", params).fetchone()[0])
        rows = conn.execute(
            f"""
            SELECT * FROM automations
            {where_sql}
            ORDER BY {sort_by} {order}
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset],
        ).fetchall()

        items = []
        for row in rows:
            last_run = conn.execute(
                "SELECT status, started_at FROM runs WHERE automation_id = ? ORDER BY started_at DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            run_count = int(conn.execute("SELECT COUNT(*) FROM runs WHERE automation_id = ?", (row["id"],)).fetchone()[0])
            tools = json.loads(row["tools_json"] or "[]")
            items.append(
                AutomationSummaryModel(
                    id=row["id"],
                    name=row["name"],
                    type=row["type"],
                    model=row["model"],
                    goal=row["goal"] or "",
                    tools=tools,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    last_run_status=last_run["status"] if last_run else None,
                    last_run_at=last_run["started_at"] if last_run else None,
                    run_count=run_count,
                    owner_id=row["owner_id"],
                )
            )
    return AutomationListResponseModel(items=items, total=total, page=page, page_size=page_size)


@app.post("/api/automations", response_model=AutomationModel)
def create_automation(automation: AutomationModel, user: UserPublicModel = Depends(get_current_user)):
    data = _model_dump(automation)
    automation_id = automation.id or _slugify(automation.name)
    base_id = automation_id
    counter = 2
    with db() as conn:
        while conn.execute("SELECT 1 FROM automations WHERE id = ?", (automation_id,)).fetchone():
            automation_id = f"{base_id}-{counter}"
            counter += 1
    now = _now()
    data["id"] = automation_id
    data["created_at"] = now
    data["updated_at"] = now
    saved = AutomationModel(**data)
    code = generate_python_code(saved)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO automations
            (id, owner_id, name, type, model, goal, task, tools_json, config_json, code, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                saved.id,
                None if AUTH_DISABLED else user.id,
                saved.name,
                saved.type,
                saved.model,
                saved.goal,
                saved.task,
                json.dumps(saved.tools, ensure_ascii=False),
                json.dumps(_model_dump(saved), ensure_ascii=False),
                code,
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO automation_versions (id, automation_id, owner_id, config_json, code, created_at, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), saved.id, None if AUTH_DISABLED else user.id, json.dumps(_model_dump(saved), ensure_ascii=False), code, now, "created"),
        )
    _save_automation_json_mirror(saved, owner_id=None if AUTH_DISABLED else user.id)
    return saved


def _get_automation_row_or_404(automation_id: str, user: UserPublicModel) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT * FROM automations WHERE id = ? AND deleted_at IS NULL", (automation_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Automation not found.")
    if not AUTH_DISABLED and user.role != "admin" and row["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied.")
    return row


@app.get("/api/automations/{automation_id}", response_model=AutomationModel)
def get_automation(automation_id: str, user: UserPublicModel = Depends(get_current_user)):
    row = _get_automation_row_or_404(automation_id, user)
    return _automation_from_row(row)


@app.put("/api/automations/{automation_id}", response_model=AutomationModel)
def update_automation(automation_id: str, automation: AutomationModel, user: UserPublicModel = Depends(get_current_user)):
    old_row = _get_automation_row_or_404(automation_id, user)
    old = _automation_from_row(old_row)
    data = _model_dump(automation)
    data["id"] = automation_id
    data["created_at"] = old.created_at or _now()
    data["updated_at"] = _now()
    saved = AutomationModel(**data)
    code = generate_python_code(saved)
    with db() as conn:
        conn.execute(
            """
            UPDATE automations
            SET name = ?, type = ?, model = ?, goal = ?, task = ?, tools_json = ?, config_json = ?, code = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                saved.name,
                saved.type,
                saved.model,
                saved.goal,
                saved.task,
                json.dumps(saved.tools, ensure_ascii=False),
                json.dumps(_model_dump(saved), ensure_ascii=False),
                code,
                data["updated_at"],
                automation_id,
            ),
        )
        conn.execute(
            "INSERT INTO automation_versions (id, automation_id, owner_id, config_json, code, created_at, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), automation_id, None if AUTH_DISABLED else user.id, json.dumps(_model_dump(saved), ensure_ascii=False), code, data["updated_at"], "updated"),
        )
    _save_automation_json_mirror(saved, owner_id=old_row["owner_id"])
    return saved


@app.patch("/api/automations/{automation_id}/code", response_model=SyncResponseModel)
def update_automation_from_code(automation_id: str, payload: CodeParseRequestModel, user: UserPublicModel = Depends(get_current_user)):
    row = _get_automation_row_or_404(automation_id, user)
    fallback = _automation_from_row(row)
    automation, warnings = parse_python_code_to_automation(payload.code, fallback=fallback)
    automation.id = automation_id
    automation.created_at = fallback.created_at
    automation.updated_at = _now()
    # Persist visual config and user-edited code together.
    with db() as conn:
        conn.execute(
            """
            UPDATE automations
            SET name = ?, type = ?, model = ?, goal = ?, task = ?, tools_json = ?, config_json = ?, code = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                automation.name,
                automation.type,
                automation.model,
                automation.goal,
                automation.task,
                json.dumps(automation.tools, ensure_ascii=False),
                json.dumps(_model_dump(automation), ensure_ascii=False),
                payload.code,
                automation.updated_at,
                automation_id,
            ),
        )
        conn.execute(
            "INSERT INTO automation_versions (id, automation_id, owner_id, config_json, code, created_at, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), automation_id, None if AUTH_DISABLED else user.id, json.dumps(_model_dump(automation), ensure_ascii=False), payload.code, automation.updated_at, "code-sync"),
        )
    _save_automation_json_mirror(automation, owner_id=row["owner_id"])
    return SyncResponseModel(automation=automation, code=payload.code, warnings=warnings)


@app.delete("/api/automations/{automation_id}")
def delete_automation(automation_id: str, hard: bool = False, user: UserPublicModel = Depends(get_current_user)):
    _get_automation_row_or_404(automation_id, user)
    with db() as conn:
        if hard:
            conn.execute("DELETE FROM automations WHERE id = ?", (automation_id,))
        else:
            conn.execute("UPDATE automations SET deleted_at = ?, updated_at = ? WHERE id = ?", (_now(), _now(), automation_id))
    path = _automation_path(automation_id)
    if hard and path.exists():
        path.unlink()
    return {"ok": True, "deleted": automation_id, "hard": hard}


@app.post("/api/automations/{automation_id}/duplicate", response_model=AutomationModel)
def duplicate_automation(automation_id: str, user: UserPublicModel = Depends(get_current_user)):
    original = get_automation(automation_id, user)
    duplicate = AutomationModel(**_model_dump(original))
    duplicate.id = None
    duplicate.name = f"{original.name} Copy"
    return create_automation(duplicate, user)


@app.get("/api/automations/{automation_id}/code")
def get_automation_code(automation_id: str, user: UserPublicModel = Depends(get_current_user)):
    row = _get_automation_row_or_404(automation_id, user)
    code = row["code"] or generate_python_code(_automation_from_row(row))
    return {"automation_id": automation_id, "language": "python", "code": code}


@app.get("/api/automations/{automation_id}/versions")
def get_automation_versions(automation_id: str, user: UserPublicModel = Depends(get_current_user)):
    _get_automation_row_or_404(automation_id, user)
    with db() as conn:
        rows = conn.execute(
            "SELECT id, automation_id, created_at, note FROM automation_versions WHERE automation_id = ? ORDER BY created_at DESC LIMIT 100",
            (automation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/generate-code")
def generate_code(request: CodeRequestModel, user: UserPublicModel = Depends(get_current_user)):
    return {"language": "python", "code": generate_python_code(request.automation)}


@app.post("/api/parse-code", response_model=SyncResponseModel)
def parse_code(request: CodeParseRequestModel, user: UserPublicModel = Depends(get_current_user)):
    automation, warnings = parse_python_code_to_automation(request.code, fallback=request.fallback)
    return SyncResponseModel(automation=automation, code=generate_python_code(automation), warnings=warnings)


# ---------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------


@app.post("/api/run", response_model=RunStartResponseModel)
def start_run(request: RunRequestModel, user: UserPublicModel = Depends(get_current_user)):
    if request.automation_id:
        automation = get_automation(request.automation_id, user)
    elif request.automation:
        automation = request.automation
    else:
        raise HTTPException(status_code=400, detail="Provide either automation_id or automation.")
    if not automation.id:
        automation.id = f"adhoc-{uuid.uuid4().hex[:8]}"
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    run = RunState(run_id=run_id, automation=automation, owner_id=None if AUTH_DISABLED else user.id)
    with RUNS_LOCK:
        RUNS[run_id] = run

    _save_run_state(run)
    run.push_event("SYSTEM", "Run queued.")
    _save_run_state(run)
    thread = threading.Thread(target=_execute_run, args=(run,), daemon=True, name=f"agentkit-run-{run_id}")
    run.thread = thread
    thread.start()
    return RunStartResponseModel(run_id=run_id, status="queued", stream_url=f"/api/runs/{run_id}/stream", details_url=f"/api/runs/{run_id}")


@app.post("/api/automations/{automation_id}/run", response_model=RunStartResponseModel)
def start_saved_automation_run(automation_id: str, user: UserPublicModel = Depends(get_current_user)):
    return start_run(RunRequestModel(automation_id=automation_id), user)


@app.get("/api/runs")
def list_runs(
    limit: int = Query(50, ge=1, le=500),
    automation_id: Optional[str] = None,
    status_filter: Optional[RunStatus] = Query(None, alias="status"),
    user: UserPublicModel = Depends(get_current_user),
):
    where = []
    params: List[Any] = []
    if not AUTH_DISABLED and user.role != "admin":
        where.append("owner_id = ?")
        params.append(user.id)
    if automation_id:
        where.append("automation_id = ?")
        params.append(automation_id)
    if status_filter:
        where.append("status = ?")
        params.append(status_filter)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    with db() as conn:
        rows = conn.execute(f"SELECT * FROM runs {where_sql} ORDER BY started_at DESC LIMIT ?", params + [limit]).fetchall()
    items = []
    for row in rows:
        items.append({
            "run_id": row["id"],
            "automation_id": row["automation_id"],
            "automation_name": row["automation_name"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "duration_ms": row["duration_ms"],
            "result": json.loads(row["result_json"] or "null"),
            "error": row["error"],
            "output_files": json.loads(row["output_files_json"] or "[]"),
            "stats": json.loads(row["stats_json"] or "{}"),
        })
    return items


@app.get("/api/runs/{run_id}", response_model=RunInfoModel)
def get_run(run_id: str, user: UserPublicModel = Depends(get_current_user)):
    with RUNS_LOCK:
        run = RUNS.get(run_id)
    if run:
        if not AUTH_DISABLED and user.role != "admin" and run.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied.")
        return RunInfoModel(**_run_info_dict(run))
    with db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Run not found.")
    if not AUTH_DISABLED and user.role != "admin" and row["owner_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied.")
    return RunInfoModel(
        run_id=row["id"],
        automation_id=row["automation_id"],
        automation_name=row["automation_name"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        duration_ms=row["duration_ms"],
        result=json.loads(row["result_json"] or "null"),
        error=row["error"],
        output_files=json.loads(row["output_files_json"] or "[]"),
        stats=json.loads(row["stats_json"] or "{}"),
    )


def _filter_events_for_view(events: List[Dict[str, Any]], view: RunEventView) -> List[Dict[str, Any]]:
    if view == "debug":
        return events
    if view == "detailed":
        return [event for event in events if event.get("level") in {"primary", "secondary"}]
    # simple
    simplified = []
    for event in events:
        if event.get("type") in {"PLAN", "ACTION", "DONE", "ERROR", "RETRY"}:
            item = dict(event)
            item["message"] = item.get("summary") or item.get("message", "")
            item["data"] = {}
            simplified.append(item)
    return simplified


@app.get("/api/runs/{run_id}/events")
def get_run_events(run_id: str, view: RunEventView = "debug", user: UserPublicModel = Depends(get_current_user)):
    _ = get_run(run_id, user)
    with RUNS_LOCK:
        run = RUNS.get(run_id)
    if run:
        return _filter_events_for_view(run.events, view)
    with db() as conn:
        rows = conn.execute("SELECT * FROM run_events WHERE run_id = ? ORDER BY timestamp ASC", (run_id,)).fetchall()
    events = []
    for row in rows:
        events.append({
            "id": row["id"],
            "run_id": row["run_id"],
            "type": row["type"],
            "level": row["level"],
            "message": row["message"],
            "summary": row["summary"],
            "timestamp": row["timestamp"],
            "step": row["step"],
            "duration_ms": row["duration_ms"],
            "data": json.loads(row["data_json"] or "{}"),
        })
    return _filter_events_for_view(events, view)


@app.get("/api/runs/{run_id}/stream")
async def stream_run_events(
    run_id: str,
    request: Request,
    view: RunEventView = "simple",
    token: Optional[str] = Query(None),
):
    # EventSource cannot easily set Authorization headers in browsers.
    # Accept token query param or cookie. Validate manually if auth is enabled.
    if not AUTH_DISABLED:
        if token:
            token_hash = _hash_token(token)
            with db() as conn:
                user_row = conn.execute(
                    "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
                    (token_hash, _now()),
                ).fetchone()
            if not user_row:
                raise HTTPException(status_code=401, detail="Invalid stream token.")
            stream_user = _public_user(user_row)
        else:
            stream_user = await get_current_user(request, authorization=None)
    else:
        stream_user = UserPublicModel(id="local", email="local@agentkit", name="Local User", role="admin", created_at=_now())

    _ = get_run(run_id, stream_user)

    with RUNS_LOCK:
        run = RUNS.get(run_id)

    if not run:
        async def completed_stream():
            events = get_run_events(run_id, view=view, user=stream_user)
            for event in events:
                yield f"event: message\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "event: end\ndata: {}\n\n"
        return StreamingResponse(completed_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    async def event_generator():
        sent_ids = set()
        for event in _filter_events_for_view(run.events, view):
            sent_ids.add(event["id"])
            yield f"event: message\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.to_thread(run.event_queue.get, True, 1.0)
            except queue.Empty:
                yield "event: ping\ndata: {}\n\n"
                continue
            if event.get("type") == "__END__":
                yield "event: end\ndata: {}\n\n"
                break
            filtered = _filter_events_for_view([event], view)
            for item in filtered:
                if item["id"] not in sent_ids:
                    sent_ids.add(item["id"])
                    yield f"event: message\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str, user: UserPublicModel = Depends(get_current_user)):
    with RUNS_LOCK:
        run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found or already completed.")
    if not AUTH_DISABLED and user.role != "admin" and run.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied.")
    run.cancel_requested = True
    run.push_event("SYSTEM", "Cancel requested. Current model/tool call may finish before stopping.")
    return {"ok": True, "run_id": run_id, "message": "Cancel requested."}


# ---------------------------------------------------------------------
# Input and output endpoints
# ---------------------------------------------------------------------


@app.post("/api/input/upload", response_model=List[UploadResponseModel])
async def upload_input_files(files: List[UploadFile] = File(...), user: UserPublicModel = Depends(get_current_user)):
    uploaded: List[UploadResponseModel] = []
    for file in files:
        clean_name = Path(file.filename or f"upload_{uuid.uuid4().hex}").name
        suffix = Path(clean_name).suffix.lower()
        if suffix and suffix not in SAFE_INPUT_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported input file type: {suffix}")
        target = UPLOADS_DIR / clean_name
        with target.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        uploaded.append(UploadResponseModel(filename=clean_name, path=str(target.relative_to(PROJECT_ROOT)).replace("\\", "/"), size_bytes=target.stat().st_size))
    return uploaded


@app.get("/api/input/files")
def list_input_files(user: UserPublicModel = Depends(get_current_user)):
    files = []
    for path in INPUT_DIR.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append({
            "name": path.name,
            "path": str(path.relative_to(INPUT_DIR)).replace("\\", "/"),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "extension": path.suffix.lower(),
            "mime_type": mimetypes.guess_type(str(path))[0] or "application/octet-stream",
        })
    files.sort(key=lambda item: item["modified_at"], reverse=True)
    return files


@app.get("/api/input/preview")
def preview_input(path: str = Query(...), max_chars: int = Query(50000, ge=100, le=500000), user: UserPublicModel = Depends(get_current_user)):
    return _read_file_preview(INPUT_DIR, path, SAFE_INPUT_EXTENSIONS, max_chars=max_chars)


@app.get("/api/input/download")
def download_input(path: str = Query(...), user: UserPublicModel = Depends(get_current_user)):
    target = _safe_join(INPUT_DIR, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Input file not found.")
    return FileResponse(path=str(target), filename=target.name, media_type=mimetypes.guess_type(str(target))[0] or "application/octet-stream")


@app.delete("/api/input/files")
def delete_input_file(path: str = Query(...), user: UserPublicModel = Depends(get_current_user)):
    target = _safe_join(INPUT_DIR, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Input file not found.")
    target.unlink()
    return {"ok": True, "deleted": path}


@app.get("/api/outputs")
def list_outputs(limit: int = Query(200, ge=1, le=1000), user: UserPublicModel = Depends(get_current_user)):
    return _list_output_files_dict(limit=limit)


@app.get("/api/outputs/preview")
def preview_output(path: str = Query(...), max_chars: int = Query(50000, ge=100, le=500000), user: UserPublicModel = Depends(get_current_user)):
    return _read_file_preview(OUTPUT_DIR, path, SAFE_OUTPUT_EXTENSIONS, max_chars=max_chars)


@app.get("/api/outputs/download")
def download_output(path: str = Query(...), user: UserPublicModel = Depends(get_current_user)):
    target = _safe_join(OUTPUT_DIR, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Output file not found.")
    if target.suffix.lower() not in SAFE_OUTPUT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type.")
    return FileResponse(path=str(target), filename=target.name, media_type=mimetypes.guess_type(str(target))[0] or "application/octet-stream")


@app.delete("/api/outputs")
def delete_output(path: str = Query(...), user: UserPublicModel = Depends(get_current_user)):
    target = _safe_join(OUTPUT_DIR, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Output file not found.")
    target.unlink()
    return {"ok": True, "deleted": path}


@app.delete("/api/outputs/all")
def clear_outputs(user: UserPublicModel = Depends(get_current_user)):
    deleted = 0
    for path in OUTPUT_DIR.rglob("*"):
        if path.is_file():
            path.unlink()
            deleted += 1
    return {"ok": True, "deleted_files": deleted}


# ---------------------------------------------------------------------
# Import / export / templates
# ---------------------------------------------------------------------


@app.get("/api/export/automations")
def export_automations(user: UserPublicModel = Depends(get_current_user)):
    automations = []
    with db() as conn:
        if AUTH_DISABLED or user.role == "admin":
            rows = conn.execute("SELECT * FROM automations WHERE deleted_at IS NULL ORDER BY updated_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM automations WHERE owner_id = ? AND deleted_at IS NULL ORDER BY updated_at DESC", (user.id,)).fetchall()
    for row in rows:
        automations.append(json.loads(row["config_json"]))
    payload = {"app": APP_NAME, "version": APP_VERSION, "exported_at": _now(), "automations": automations}
    export_path = EXPORTS_DIR / f"automations_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    _json_dump_file(export_path, payload)
    return payload


@app.post("/api/import/automations")
def import_automations(payload: Dict[str, Any], user: UserPublicModel = Depends(get_current_user)):
    automations = payload.get("automations")
    if not isinstance(automations, list):
        raise HTTPException(status_code=400, detail="Expected payload.automations to be a list.")
    imported = []
    for item in automations:
        automation = AutomationModel(**item)
        automation.id = None
        saved = create_automation(automation, user)
        imported.append(saved.id)
    return {"ok": True, "imported": imported}


@app.get("/api/templates")
def templates(user: UserPublicModel = Depends(get_current_user)):
    return [
        {
            "id": "study-planner",
            "name": "Study Planner",
            "description": "Create a study plan from local syllabus and notes.",
            "automation": _model_dump(AutomationModel(
                name="Study Planner",
                model=DEFAULT_MODEL,
                goal="Create a personalized study plan from class materials, notes, and syllabus.",
                task="Read files from ./input. Extract key topics, deadlines, and project requirements. Create ./output/study_plan.md with daily topics, practice tasks, revision checkpoints, important keywords, and a final checklist.",
                tools=["list_files", "read_file", "read_pdf", "extract_keywords", "create_schedule_markdown", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "csv-insight",
            "name": "CSV Insight Agent",
            "description": "Analyze CSV data and generate insights.",
            "automation": _model_dump(AutomationModel(
                name="CSV Insight Agent",
                model=DEFAULT_MODEL,
                goal="Analyze CSV files and produce useful insights.",
                task="Find CSV files in ./input. Analyze the most relevant CSV. Create ./output/csv_insight_report.md with summary, key findings, risks, and recommendations.",
                tools=["list_files", "read_csv", "summarize_csv", "csv_missing_report", "csv_value_counts", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "file-organizer",
            "name": "File Organizer",
            "description": "Organize local files and create a summary report.",
            "automation": _model_dump(AutomationModel(
                name="File Organizer",
                model=DEFAULT_MODEL,
                goal="Organize local files safely and document what changed.",
                task="Look inside ./input. Organize files into folders inside ./output/organized. Do not delete anything. Copy files safely and create ./output/file_organization_report.md explaining the organization.",
                tools=["list_files", "get_folder_tree", "create_folder", "copy_file", "make_file_manifest", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "resume-ranker",
            "name": "Resume Ranker",
            "description": "Compare resumes against a job description and create a ranking report.",
            "automation": _model_dump(AutomationModel(
                name="Resume Ranker",
                model=DEFAULT_MODEL,
                goal="Rank resumes against a job description using transparent criteria.",
                task="Read resume files and job description files from ./input. Compare candidate fit, strengths, gaps, and risks. Create ./output/resume_ranking_report.md with ranking, evidence, and recommendation.",
                tools=["list_files", "read_file", "read_pdf", "compare_texts", "extract_keywords", "create_markdown_report"],
                max_steps=16,
            )),
        },

        # -----------------------------------------------------------------
        # Education / classroom templates
        # -----------------------------------------------------------------

        {
            "id": "assignment-feedback",
            "name": "Assignment Feedback Generator",
            "description": "Review student submissions and create constructive feedback.",
            "automation": _model_dump(AutomationModel(
                name="Assignment Feedback Generator",
                model=DEFAULT_MODEL,
                goal="Generate useful, structured feedback for student assignments.",
                task="Read assignment submissions from ./input. Identify strengths, missing sections, improvement areas, and suggestions. Create ./output/assignment_feedback.md with clear, student-friendly feedback.",
                tools=["list_files", "read_file", "read_pdf", "check_required_sections", "rubric_score", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "rubric-grader",
            "name": "Rubric Grader",
            "description": "Score a submission against rubric criteria using local rule-based evidence.",
            "automation": _model_dump(AutomationModel(
                name="Rubric Grader",
                model=DEFAULT_MODEL,
                goal="Evaluate a submission against rubric criteria and produce a transparent grading report.",
                task="Read rubric and submission files from ./input. Score the submission against the rubric using evidence from the text. Create ./output/rubric_grading_report.md with scores, matched evidence, and improvement suggestions.",
                tools=["list_files", "read_file", "read_pdf", "rubric_score", "check_required_sections", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "quiz-generator",
            "name": "Quiz Generator",
            "description": "Create quiz questions from notes or syllabus files.",
            "automation": _model_dump(AutomationModel(
                name="Quiz Generator",
                model=DEFAULT_MODEL,
                goal="Generate a useful quiz from learning materials.",
                task="Read notes, syllabus, or study material from ./input. Extract key concepts and create ./output/quiz.md with multiple-choice questions, short-answer questions, and an answer key.",
                tools=["list_files", "read_file", "read_pdf", "extract_keywords", "text_to_bullets", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "lesson-plan-builder",
            "name": "Lesson Plan Builder",
            "description": "Turn topic notes into a structured lesson plan.",
            "automation": _model_dump(AutomationModel(
                name="Lesson Plan Builder",
                model=DEFAULT_MODEL,
                goal="Create a practical lesson plan from teaching materials.",
                task="Read teaching materials from ./input. Create ./output/lesson_plan.md with learning objectives, required materials, lecture flow, activities, practice tasks, and assessment ideas.",
                tools=["list_files", "read_file", "read_pdf", "extract_keywords", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "attendance-risk-detector",
            "name": "Attendance Risk Detector",
            "description": "Analyze attendance CSV data and flag students at risk.",
            "automation": _model_dump(AutomationModel(
                name="Attendance Risk Detector",
                model=DEFAULT_MODEL,
                goal="Identify attendance risk from local CSV files.",
                task="Find attendance CSV files in ./input. Analyze attendance patterns and identify students with low attendance or repeated absence. Create ./output/attendance_risk_report.md with risk categories and recommendations.",
                tools=["list_files", "read_csv", "summarize_csv", "csv_column_names", "csv_groupby_summary", "filter_csv", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "marks-analyzer",
            "name": "Marks Analyzer",
            "description": "Analyze marks CSV and generate student performance insights.",
            "automation": _model_dump(AutomationModel(
                name="Marks Analyzer",
                model=DEFAULT_MODEL,
                goal="Analyze student marks and produce useful academic insights.",
                task="Find marks or grades CSV files in ./input. Analyze performance, top scores, low scores, missing values, and improvement areas. Create ./output/marks_analysis_report.md.",
                tools=["list_files", "read_csv", "summarize_csv", "csv_shape", "csv_missing_report", "csv_value_counts", "basic_stats", "create_chart_from_csv", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "student-progress-report",
            "name": "Student Progress Report",
            "description": "Create a progress report from attendance, marks, and notes.",
            "automation": _model_dump(AutomationModel(
                name="Student Progress Report",
                model=DEFAULT_MODEL,
                goal="Create a complete student progress report from local files.",
                task="Read available attendance, marks, and notes files from ./input. Combine the information into ./output/student_progress_report.md with strengths, risks, and action items.",
                tools=["list_files", "read_file", "read_pdf", "read_csv", "summarize_csv", "csv_groupby_summary", "create_markdown_report"],
                max_steps=18,
            )),
        },

        # -----------------------------------------------------------------
        # Research / knowledge-work templates
        # -----------------------------------------------------------------

        {
            "id": "research-folder-summarizer",
            "name": "Research Folder Summarizer",
            "description": "Summarize a folder of papers, notes, PDFs, and text files.",
            "automation": _model_dump(AutomationModel(
                name="Research Folder Summarizer",
                model=DEFAULT_MODEL,
                goal="Summarize research materials into a concise brief.",
                task="Read research files from ./input, including text and PDFs. Create ./output/research_summary.md with key themes, findings, open questions, and useful references.",
                tools=["list_files", "get_folder_tree", "read_file", "read_pdf", "pdf_info", "extract_keywords", "create_brief_from_text", "create_markdown_report"],
                max_steps=18,
            )),
        },
        {
            "id": "literature-review-builder",
            "name": "Literature Review Builder",
            "description": "Create a literature review draft from PDFs and notes.",
            "automation": _model_dump(AutomationModel(
                name="Literature Review Builder",
                model=DEFAULT_MODEL,
                goal="Create a structured literature review from local research documents.",
                task="Read research papers, abstracts, and notes from ./input. Create ./output/literature_review.md with themes, paper summaries, comparison points, gaps, and future work.",
                tools=["list_files", "read_pdf", "extract_pdf_pages_text", "read_file", "extract_keywords", "compare_texts", "create_markdown_report"],
                max_steps=20,
            )),
        },
        {
            "id": "paper-keyword-indexer",
            "name": "Paper Keyword Indexer",
            "description": "Extract keywords from PDFs and create an index.",
            "automation": _model_dump(AutomationModel(
                name="Paper Keyword Indexer",
                model=DEFAULT_MODEL,
                goal="Create a keyword index for research files.",
                task="Read PDFs and notes from ./input. Extract important keywords per file and create ./output/keyword_index.md.",
                tools=["list_files", "read_pdf", "read_file", "extract_keywords", "create_table_markdown", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "notes-to-brief",
            "name": "Notes to Brief",
            "description": "Turn raw notes into a clean decision brief.",
            "automation": _model_dump(AutomationModel(
                name="Notes to Brief",
                model=DEFAULT_MODEL,
                goal="Convert messy notes into a clear brief.",
                task="Read note files from ./input. Create ./output/brief.md with summary bullets, decisions, risks, questions, and next actions.",
                tools=["list_files", "read_file", "clean_text", "text_to_bullets", "extract_dates", "create_brief_from_text", "create_markdown_report"],
                max_steps=14,
            )),
        },

        # -----------------------------------------------------------------
        # Meeting / productivity templates
        # -----------------------------------------------------------------

        {
            "id": "meeting-notes-to-action-items",
            "name": "Meeting Notes to Action Items",
            "description": "Convert meeting notes into action items and minutes.",
            "automation": _model_dump(AutomationModel(
                name="Meeting Notes to Action Items",
                model=DEFAULT_MODEL,
                goal="Turn meeting notes into useful minutes and action items.",
                task="Read meeting notes from ./input. Extract decisions, action items, owners, dates, and unresolved questions. Create ./output/meeting_minutes.md.",
                tools=["list_files", "read_file", "extract_dates", "extract_emails", "create_minutes_from_notes", "create_todo_file", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "daily-planner",
            "name": "Daily Planner",
            "description": "Create a daily plan from tasks and notes.",
            "automation": _model_dump(AutomationModel(
                name="Daily Planner",
                model=DEFAULT_MODEL,
                goal="Create a practical daily plan from local task notes.",
                task="Read task lists and notes from ./input. Create ./output/daily_plan.md with prioritized tasks, estimated schedule, reminders, and checklist.",
                tools=["list_files", "read_file", "extract_dates", "create_todo_file", "create_schedule_markdown", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "weekly-review",
            "name": "Weekly Review Generator",
            "description": "Generate a weekly review from notes and outputs.",
            "automation": _model_dump(AutomationModel(
                name="Weekly Review Generator",
                model=DEFAULT_MODEL,
                goal="Create a weekly review from local notes and work logs.",
                task="Read files from ./input and ./output if relevant. Create ./output/weekly_review.md with wins, blockers, completed tasks, pending items, and next week priorities.",
                tools=["list_files", "read_file", "search_text_in_files", "extract_dates", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "email-draft-from-notes",
            "name": "Email Draft from Notes",
            "description": "Create polished email drafts from raw notes.",
            "automation": _model_dump(AutomationModel(
                name="Email Draft from Notes",
                model=DEFAULT_MODEL,
                goal="Turn raw notes into clear email drafts.",
                task="Read notes from ./input. Create ./output/email_drafts.md with polished email drafts, suggested subject lines, and follow-up checklist.",
                tools=["list_files", "read_file", "clean_text", "extract_emails", "create_markdown_report"],
                max_steps=12,
            )),
        },

        # -----------------------------------------------------------------
        # Data / analytics templates
        # -----------------------------------------------------------------

        {
            "id": "data-cleaning-assistant",
            "name": "Data Cleaning Assistant",
            "description": "Inspect a CSV and create a cleaned version plus report.",
            "automation": _model_dump(AutomationModel(
                name="Data Cleaning Assistant",
                model=DEFAULT_MODEL,
                goal="Clean and document a CSV dataset.",
                task="Find CSV files in ./input. Inspect missing values, duplicates, and column quality. Create a cleaned CSV in ./output and create ./output/data_cleaning_report.md explaining what was done.",
                tools=["list_files", "read_csv", "summarize_csv", "csv_missing_report", "deduplicate_csv", "sample_csv", "create_markdown_report"],
                max_steps=18,
            )),
        },
        {
            "id": "csv-dashboard-assets",
            "name": "CSV Dashboard Assets",
            "description": "Create chart assets and a markdown dashboard report from CSV.",
            "automation": _model_dump(AutomationModel(
                name="CSV Dashboard Assets",
                model=DEFAULT_MODEL,
                goal="Generate simple dashboard assets from CSV data.",
                task="Find the most relevant CSV in ./input. Summarize it, identify useful chart columns, create chart image files in ./output, and create ./output/dashboard_report.md.",
                tools=["list_files", "read_csv", "summarize_csv", "csv_column_names", "csv_value_counts", "create_chart_from_csv", "create_markdown_report"],
                max_steps=18,
            )),
        },
        {
            "id": "excel-to-report",
            "name": "Excel to Report",
            "description": "Analyze an Excel file and create a report.",
            "automation": _model_dump(AutomationModel(
                name="Excel to Report",
                model=DEFAULT_MODEL,
                goal="Analyze Excel sheets and produce a readable report.",
                task="Find Excel files in ./input. List sheet names, inspect the most relevant sheet, convert it to CSV if useful, and create ./output/excel_report.md.",
                tools=["list_files", "excel_sheet_names", "read_excel", "excel_to_csv", "summarize_csv", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "lead-list-cleaner",
            "name": "Lead List Cleaner",
            "description": "Clean a contacts CSV and extract emails/phone numbers.",
            "automation": _model_dump(AutomationModel(
                name="Lead List Cleaner",
                model=DEFAULT_MODEL,
                goal="Clean contact or lead list data.",
                task="Find contact CSV or text files in ./input. Extract emails and phone numbers, identify missing fields, remove duplicate records if possible, and create ./output/lead_list_report.md.",
                tools=["list_files", "read_file", "read_csv", "extract_emails", "extract_phone_numbers", "csv_missing_report", "deduplicate_csv", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "inventory-insight",
            "name": "Inventory Insight Agent",
            "description": "Analyze inventory CSV data and identify issues.",
            "automation": _model_dump(AutomationModel(
                name="Inventory Insight Agent",
                model=DEFAULT_MODEL,
                goal="Analyze inventory data and identify stock risks.",
                task="Find inventory CSV files in ./input. Analyze stock levels, missing data, repeated items, and category summaries. Create ./output/inventory_insight_report.md.",
                tools=["list_files", "read_csv", "summarize_csv", "csv_groupby_summary", "csv_value_counts", "filter_csv", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "expense-analyzer",
            "name": "Expense Analyzer",
            "description": "Analyze expense CSV files and create spending insights.",
            "automation": _model_dump(AutomationModel(
                name="Expense Analyzer",
                model=DEFAULT_MODEL,
                goal="Analyze expense records and generate spending insights.",
                task="Find expense CSV files in ./input. Analyze categories, totals, outliers, and missing information. Create ./output/expense_analysis_report.md.",
                tools=["list_files", "read_csv", "summarize_csv", "csv_groupby_summary", "sort_csv", "create_chart_from_csv", "create_markdown_report"],
                max_steps=16,
            )),
        },

        # -----------------------------------------------------------------
        # File / document operations templates
        # -----------------------------------------------------------------

        {
            "id": "file-audit",
            "name": "File Audit",
            "description": "Create a file inventory and audit report.",
            "automation": _model_dump(AutomationModel(
                name="File Audit",
                model=DEFAULT_MODEL,
                goal="Audit local project files safely.",
                task="Scan ./input. Create a file tree, recent file list, duplicate file report, and JSON manifest. Create ./output/file_audit_report.md.",
                tools=["list_files", "get_folder_tree", "list_recent_files", "find_duplicate_files", "make_file_manifest", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "pdf-toolkit",
            "name": "PDF Toolkit",
            "description": "Inspect, extract, split, or merge PDFs locally.",
            "automation": _model_dump(AutomationModel(
                name="PDF Toolkit",
                model=DEFAULT_MODEL,
                goal="Process PDF files locally and safely.",
                task="Find PDF files in ./input. Inspect PDF metadata and extract useful text. If multiple PDFs are relevant, create a combined report. Create ./output/pdf_toolkit_report.md.",
                tools=["list_files", "list_files_by_extension", "pdf_info", "read_pdf", "extract_pdf_pages_text", "split_pdf_pages", "merge_pdfs", "create_markdown_report"],
                max_steps=18,
            )),
        },
        {
            "id": "image-asset-processor",
            "name": "Image Asset Processor",
            "description": "Inspect, resize, convert, and thumbnail local images.",
            "automation": _model_dump(AutomationModel(
                name="Image Asset Processor",
                model=DEFAULT_MODEL,
                goal="Prepare image assets for projects or reports.",
                task="Find image files in ./input. Inspect image metadata, create thumbnails or resized versions in ./output, and create ./output/image_asset_report.md.",
                tools=["list_files", "list_files_by_extension", "image_info", "create_thumbnail", "resize_image", "convert_image_format", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "zip-archive-builder",
            "name": "Zip Archive Builder",
            "description": "Create a zip archive from a local folder.",
            "automation": _model_dump(AutomationModel(
                name="Zip Archive Builder",
                model=DEFAULT_MODEL,
                goal="Archive local project files safely.",
                task="Inspect ./input and create ./output/input_archive.zip. Also create ./output/archive_manifest.md explaining what was archived.",
                tools=["list_files", "get_folder_tree", "make_file_manifest", "zip_folder", "create_markdown_report"],
                max_steps=12,
            )),
        },
        {
            "id": "local-search-agent",
            "name": "Local Search Agent",
            "description": "Search local files for keywords and create a report.",
            "automation": _model_dump(AutomationModel(
                name="Local Search Agent",
                model=DEFAULT_MODEL,
                goal="Search local documents for important terms.",
                task="Search files inside ./input for important terms found in the task or available notes. Create ./output/local_search_report.md with matches, filenames, and recommendations.",
                tools=["list_files", "search_text_in_files", "read_file", "extract_keywords", "create_markdown_report"],
                max_steps=14,
            )),
        },

        # -----------------------------------------------------------------
        # Developer / code templates
        # -----------------------------------------------------------------

        {
            "id": "codebase-overview",
            "name": "Codebase Overview",
            "description": "Analyze Python files and create a codebase summary.",
            "automation": _model_dump(AutomationModel(
                name="Codebase Overview",
                model=DEFAULT_MODEL,
                goal="Create a clear overview of a Python codebase.",
                task="Inspect Python files in the current project or ./input. List functions, imports, line counts, and notable files. Create ./output/codebase_overview.md.",
                tools=["list_files", "get_folder_tree", "list_python_functions", "python_imports_report", "count_lines_of_code", "grep_code", "create_markdown_report"],
                max_steps=18,
            )),
        },
        {
            "id": "bug-report-summarizer",
            "name": "Bug Report Summarizer",
            "description": "Turn logs and bug notes into a clear bug report.",
            "automation": _model_dump(AutomationModel(
                name="Bug Report Summarizer",
                model=DEFAULT_MODEL,
                goal="Create a clear bug report from logs and notes.",
                task="Read logs, traces, or bug notes from ./input. Identify errors, possible causes, reproduction hints, and next debugging steps. Create ./output/bug_report.md.",
                tools=["list_files", "read_file", "search_text_in_files", "extract_keywords", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "python-project-scaffold",
            "name": "Python Project Scaffold",
            "description": "Generate a simple local AgentKit project scaffold.",
            "automation": _model_dump(AutomationModel(
                name="Python Project Scaffold",
                model=DEFAULT_MODEL,
                goal="Create a starter local AgentKit automation project.",
                task="Create a small project scaffold in ./output/project_scaffold with README, app.py, input folder, and output folder. Create a report explaining the scaffold.",
                tools=["create_project_scaffold", "create_readme", "create_agent_app_file", "create_markdown_report"],
                max_steps=10,
            )),
        },
        {
            "id": "agent-app-generator",
            "name": "Agent App Generator",
            "description": "Generate an app.py file for a custom AgentKit automation.",
            "automation": _model_dump(AutomationModel(
                name="Agent App Generator",
                model=DEFAULT_MODEL,
                goal="Generate a clean app.py file for a local AgentKit automation.",
                task="Read requirements from ./input if available. Generate ./output/app.py for an AgentKit automation, including a useful agent name, model, tools, and task. Also create ./output/app_generation_notes.md.",
                tools=["list_files", "read_file", "create_agent_app_file", "create_markdown_report"],
                max_steps=12,
            )),
        },

        # -----------------------------------------------------------------
        # Business / operations templates
        # -----------------------------------------------------------------

        {
            "id": "invoice-folder-summarizer",
            "name": "Invoice Folder Summarizer",
            "description": "Summarize invoice or receipt files from a folder.",
            "automation": _model_dump(AutomationModel(
                name="Invoice Folder Summarizer",
                model=DEFAULT_MODEL,
                goal="Summarize invoice or receipt files into an accounting-friendly report.",
                task="Read invoice, receipt, or expense files from ./input. Extract dates, totals if visible, vendors if visible, and create ./output/invoice_summary.md. Do not invent missing values.",
                tools=["list_files", "read_file", "read_pdf", "extract_dates", "extract_emails", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "contract-review-helper",
            "name": "Contract Review Helper",
            "description": "Summarize contract documents and flag important clauses.",
            "automation": _model_dump(AutomationModel(
                name="Contract Review Helper",
                model=DEFAULT_MODEL,
                goal="Summarize contracts and flag important clauses for human review.",
                task="Read contract files from ./input. Create ./output/contract_review.md with parties, dates, obligations, payment terms, renewal terms, risks, and questions for legal review. Do not provide legal advice.",
                tools=["list_files", "read_file", "read_pdf", "extract_dates", "extract_keywords", "search_text_in_files", "create_markdown_report"],
                max_steps=18,
            )),
        },
        {
            "id": "customer-feedback-analyzer",
            "name": "Customer Feedback Analyzer",
            "description": "Analyze feedback text or CSV files and find themes.",
            "automation": _model_dump(AutomationModel(
                name="Customer Feedback Analyzer",
                model=DEFAULT_MODEL,
                goal="Analyze customer feedback and identify useful themes.",
                task="Read customer feedback files from ./input. Analyze sentiment, repeated issues, positive feedback, feature requests, and create ./output/customer_feedback_report.md.",
                tools=["list_files", "read_file", "read_csv", "csv_value_counts", "simple_sentiment", "extract_keywords", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "sales-call-notes-analyzer",
            "name": "Sales Call Notes Analyzer",
            "description": "Summarize sales notes into opportunities and next steps.",
            "automation": _model_dump(AutomationModel(
                name="Sales Call Notes Analyzer",
                model=DEFAULT_MODEL,
                goal="Turn sales call notes into structured CRM-style summaries.",
                task="Read sales call notes from ./input. Extract customer needs, objections, follow-up items, dates, emails, and create ./output/sales_call_summary.md.",
                tools=["list_files", "read_file", "extract_dates", "extract_emails", "text_to_bullets", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "risk-register-builder",
            "name": "Risk Register Builder",
            "description": "Create a risk register from notes, plans, or reports.",
            "automation": _model_dump(AutomationModel(
                name="Risk Register Builder",
                model=DEFAULT_MODEL,
                goal="Create a structured risk register from project materials.",
                task="Read project notes and reports from ./input. Identify risks, likelihood, impact, mitigation ideas, and create ./output/risk_register.md.",
                tools=["list_files", "read_file", "read_pdf", "extract_keywords", "create_table_markdown", "create_markdown_report"],
                max_steps=16,
            )),
        },

        # -----------------------------------------------------------------
        # Web/local HTML templates
        # -----------------------------------------------------------------

        {
            "id": "html-content-extractor",
            "name": "HTML Content Extractor",
            "description": "Extract readable text and links from local HTML files.",
            "automation": _model_dump(AutomationModel(
                name="HTML Content Extractor",
                model=DEFAULT_MODEL,
                goal="Extract useful content from local HTML files.",
                task="Find HTML files in ./input. Extract readable text and links. Create ./output/html_content_report.md.",
                tools=["list_files", "list_files_by_extension", "html_to_text", "extract_html_links", "extract_urls", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "markdown-publisher",
            "name": "Markdown Publisher",
            "description": "Convert markdown reports into simple HTML pages.",
            "automation": _model_dump(AutomationModel(
                name="Markdown Publisher",
                model=DEFAULT_MODEL,
                goal="Convert markdown files into readable HTML pages.",
                task="Find markdown files in ./input or ./output. Convert the most relevant markdown file to HTML and create ./output/publishing_report.md.",
                tools=["list_files", "list_files_by_extension", "read_file", "markdown_to_html", "create_markdown_report"],
                max_steps=12,
            )),
        },

        # -----------------------------------------------------------------
        # Utility / system templates
        # -----------------------------------------------------------------

        {
            "id": "json-config-auditor",
            "name": "JSON Config Auditor",
            "description": "Validate and inspect local JSON config files.",
            "automation": _model_dump(AutomationModel(
                name="JSON Config Auditor",
                model=DEFAULT_MODEL,
                goal="Inspect JSON files and create a configuration audit report.",
                task="Find JSON files in ./input. Validate them, list keys, inspect important values when appropriate, and create ./output/json_config_audit.md.",
                tools=["list_files", "list_files_by_extension", "read_json", "validate_json_text", "json_keys", "json_get_value", "create_markdown_report"],
                max_steps=16,
            )),
        },
        {
            "id": "memory-backed-notes",
            "name": "Memory-backed Notes",
            "description": "Store and retrieve simple local memory values.",
            "automation": _model_dump(AutomationModel(
                name="Memory-backed Notes",
                model=DEFAULT_MODEL,
                goal="Use local memory.json to persist useful notes or decisions.",
                task="Read notes from ./input. Save important facts or preferences to memory when useful, list memory keys, and create ./output/memory_notes_report.md.",
                tools=["list_files", "read_file", "memory_set", "memory_get", "memory_list", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "project-readme-generator",
            "name": "Project README Generator",
            "description": "Create a README from project files and notes.",
            "automation": _model_dump(AutomationModel(
                name="Project README Generator",
                model=DEFAULT_MODEL,
                goal="Generate a useful README for a local project.",
                task="Inspect project files and notes from ./input or the current workspace. Create ./output/README.md with project name, problem, solution, setup, usage, and demo notes.",
                tools=["list_files", "get_folder_tree", "read_file", "create_readme", "create_markdown_report"],
                max_steps=14,
            )),
        },
        {
            "id": "hackathon-submission-packager",
            "name": "Hackathon Submission Packager",
            "description": "Prepare a hackathon submission folder and zip.",
            "automation": _model_dump(AutomationModel(
                name="Hackathon Submission Packager",
                model=DEFAULT_MODEL,
                goal="Prepare a clean hackathon submission package.",
                task="Inspect project files. Create a README, final report, manifest, and zip archive under ./output for hackathon submission. Do not delete files.",
                tools=["list_files", "get_folder_tree", "make_file_manifest", "create_readme", "create_markdown_report", "zip_folder"],
                max_steps=18,
            )),
        },
    ]


# ---------------------------------------------------------------------
# Admin / development convenience
# ---------------------------------------------------------------------


@app.get("/api/admin/users", response_model=List[UserPublicModel])
def list_users(user: UserPublicModel = Depends(require_admin)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [_public_user(row) for row in rows]


@app.post("/api/dev/reset")
def dev_reset(confirm: bool = False, user: UserPublicModel = Depends(require_admin)):
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass confirm=true to reset local AgentKit Studio data.")
    with db() as conn:
        conn.executescript(
            """
            DELETE FROM run_events;
            DELETE FROM runs;
            DELETE FROM automation_versions;
            DELETE FROM automations;
            DELETE FROM settings;
            """
        )
    if AUTOMATIONS_DIR.exists():
        shutil.rmtree(AUTOMATIONS_DIR)
    if RUNS_DIR.exists():
        shutil.rmtree(RUNS_DIR)
    AUTOMATIONS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with RUNS_LOCK:
        RUNS.clear()
    return {"ok": True, "message": "Reset complete. Users and sessions were preserved."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host=DEFAULT_HOST, port=DEFAULT_PORT, reload=True)


@app.post("/analyze-project")
async def analyze_project_api(data: dict):

    project_path = data.get("project_path")

    result = analyze_project(project_path)

    return {
        "success": True,
        "results": result
    }

@app.get("/test-analysis")
async def test_analysis():

    from analyzer.engine import analyze_project

    result = analyze_project("./input")

    return result