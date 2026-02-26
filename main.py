import os
from datetime import datetime, date
from contextlib import contextmanager
import hmac
import hashlib
import json
from typing import Dict, Optional
from urllib.parse import parse_qsl
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, Boolean, UniqueConstraint, func
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError("BOT_TOKEN и DATABASE_URL обязательны в окружении")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=20, max_overflow=30, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Модели (без изменений)
class UserProfile(Base):
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), nullable=True)
    hp = Column(Integer, default=100)
    total_xp = Column(Integer, default=0)
    current_month_xp = Column(Integer, default=0)
    water_count = Column(Integer, default=0)
    water_goal = Column(Integer, default=2000)
    sleep_start = Column(DateTime(timezone=True), nullable=True)
    last_login = Column(DateTime(timezone=True), default=func.now())
    last_login_date = Column(Date, default=date.today)

class UserHabit(Base):
    __tablename__ = "user_habits"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False)
    xp_reward = Column(Integer, nullable=False)
    category = Column(String(50), default="Активность")

class CompletedTask(Base):
    __tablename__ = "completed_tasks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    habit_id = Column(Integer, nullable=False)
    completed_date = Column(Date, default=date.today)
    __table_args__ = (UniqueConstraint('user_id', 'habit_id', 'completed_date', name='uix_user_habit_date'),)

class History(Base):
    __tablename__ = "history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    action = Column(String(100))
    details = Column(String(255), nullable=True)
    xp_change = Column(Integer, default=0)
    hp_change = Column(Integer, default=0)
    timestamp = Column(DateTime(timezone=True), default=func.now())

Base.metadata.create_all(bind=engine)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Life RPG")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@contextmanager
def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# === TG AUTH (обновлённый парсинг + docs-compliant) ===
def validate_init_data(init_data_str: str) -> dict:
    if not init_data_str:
        raise HTTPException(401, "Нет initData")
    try:
        params = dict(parse_qsl(init_data_str))  # правильный разбор query-string
        received_hash = params.pop("hash", None)
        if not received_hash:
            raise ValueError("Нет hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            raise HTTPException(403, "Неверная подпись")
        auth_date = int(params.get("auth_date", 0))
        if (datetime.now().timestamp() - auth_date) > 43200:  # 12 часов (строже)
            raise HTTPException(403, "Данные устарели")
        user = json.loads(params.get("user", "{}"))
        return {"user_id": user.get("id"), "username": user.get("username")}
    except Exception as e:
        raise HTTPException(401, f"Auth error: {str(e)}")

# Остальной код main.py без изменений (модели, process_daily_updates, apply_sleep, эндпоинты — всё осталось как было)

# ... (все эндпоинты /load-profile, /drink-water и т.д. — идентичны предыдущей версии)

@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/health")
async def health():
    return {"status": "ok"}
