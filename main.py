import os
import hmac
import hashlib
import logging
import urllib.parse
import json
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, Index
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import desc, func
from sqlalchemy.exc import IntegrityError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Конфигурация — только из переменных окружения
# ---------------------------------------------------------------------------
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
DATABASE_URL   = os.environ.get("DATABASE_URL", "")
ALLOWED_ORIGIN = os.environ.get("WEBAPP_ORIGIN", "*")
INIT_DATA_TTL  = int(os.environ.get("INIT_DATA_TTL", "3600"))
HISTORY_LIMIT  = int(os.environ.get("HISTORY_LIMIT", "1000"))
HISTORY_DELETE_BATCH = int(os.environ.get("HISTORY_DELETE_BATCH", "500"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан!")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL не задан!")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ---------------------------------------------------------------------------
# Серверный справочник — XP и стоимость недоступны клиенту
# ---------------------------------------------------------------------------
TASKS: dict[str, dict] = {
    "task-run":         {"cat": "sport",  "tag": "Кардио",       "name": "Пробежка",           "xp": 150},
    "task-strength":    {"cat": "sport",  "tag": "Сила",         "name": "Силовая тренировка", "xp": 200},
    "task-cardio":      {"cat": "sport",  "tag": "Выносливость", "name": "Кардио сессия",      "xp": 150},
    "task-hiit":        {"cat": "sport",  "tag": "Интенсив",     "name": "ВИИТ",               "xp": 200},
    "task-family-time": {"cat": "family", "tag": "Связь",        "name": "Время с семьей",     "xp": 100},
}

REWARDS: dict[str, dict] = {
    "baton": {"name": "Батончик", "cost": 100, "icon": "🍫"},
    "soda":  {"name": "Газировка", "cost": 150, "icon": "🥤"},
    "fast":  {"name": "Фаст Фуд",  "cost": 600, "icon": "🍔"},
}

# ---------------------------------------------------------------------------
# БД
# ---------------------------------------------------------------------------
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserProfile(Base):
    __tablename__     = "users_v16"
    user_id           = Column(String,  primary_key=True, index=True)
    total_xp          = Column(Integer, default=0)
    current_month_xp  = Column(Integer, default=0)
    hp                = Column(Integer, default=100)
    last_active_date  = Column(String,  default="")
    last_active_month = Column(String,  default="")
    water_count       = Column(Integer, default=0)
    water_goal        = Column(Integer, default=8)
    sleep_start       = Column(String,  default="")
    custom_habits     = Column(String,  default="")
    streak            = Column(Integer, default=0)

class CompletedTask(Base):
    __tablename__ = "completed_tasks_v1"
    id      = Column(Integer, primary_key=True, index=True)
    user_id = Column(String,  nullable=False)
    task_id = Column(String,  nullable=False)
    date    = Column(String,  nullable=False)
    __table_args__ = (
        UniqueConstraint("user_id", "task_id", "date", name="uq_user_task_date"),
        Index("ix_completed_user_date", "user_id", "date"),
    )

class History(Base):
    __tablename__ = "history_v10"
    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(String,  index=True)
    event_type  = Column(String)
    description = Column(String)
    amount      = Column(Integer)
    timestamp   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("ix_history_user_ts", "user_id", "timestamp"),
    )

Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------------------------
# Авторизация: предвычисленный secret_key
# ---------------------------------------------------------------------------
_SECRET_KEY: bytes = hmac.new(
    b"WebAppData",
    BOT_TOKEN.encode("utf-8"),
    hashlib.sha256,
).digest()

def verify_and_extract_user(init_data: Optional[str]) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
    try:
        params        = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = params.pop("hash", None)
        if not received_hash:
            raise HTTPException(status_code=401, detail="Missing hash")

        auth_date = int(params.get("auth_date", "0"))
        now       = int(datetime.now(timezone.utc).timestamp())
        if now - auth_date > INIT_DATA_TTL:
            raise HTTPException(status_code=401, detail="Init data expired")

        data_check_string = "\n".join(
            f"{k}={params[k]}" for k in sorted(params.keys())
        )
        expected_hash = hmac.new(
            _SECRET_KEY,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_hash, received_hash):
            raise HTTPException(status_code=401, detail="Invalid signature")

        user = json.loads(params.get("user", "{}"))
        if not user.get("id"):
            raise HTTPException(status_code=401, detail="No user id")

        return user

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"verify_and_extract_user error: {e}")
        raise HTTPException(status_code=401, detail="Auth error")

def require_auth(init_data: Optional[str]) -> str:
    return str(verify_and_extract_user(init_data)["id"])

# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------
def rate_limit_by_user(request: Request) -> str:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        return get_remote_address(request)
    try:
        user = verify_and_extract_user(init_data)
        return f"user:{user['id']}"
    except Exception:
        return get_remote_address(request)

limiter = Limiter(key_func=rate_limit_by_user)

app = FastAPI()
