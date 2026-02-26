import os
import hmac
import hashlib
import json
from datetime import datetime, date, timedelta, timezone
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from sqlalchemy import (
    create_engine, Column, Integer, String, Date, DateTime,
    ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

# ================= ENV =================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError("Missing required environment variables")

# ================= DB =================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ================= MODELS =================

class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)

    level = Column(Integer, default=1)
    xp = Column(Integer, default=0)
    current_month_xp = Column(Integer, default=0)

    hp = Column(Integer, default=100)

    water_count = Column(Integer, default=0)
    water_goal = Column(Integer, default=8)

    last_login = Column(Date, default=date.today)

    completed_tasks = relationship("CompletedTask", cascade="all, delete")
    history = relationship("History", cascade="all, delete")


class CompletedTask(Base):
    __tablename__ = "completed_tasks"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user_profiles.id"))
    task_id = Column(String, nullable=False)
    date = Column(Date, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "task_id", "date"),
    )


class History(Base):
    __tablename__ = "history"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user_profiles.id"))
    action = Column(String)
    xp = Column(Integer)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(bind=engine)

# ================= FASTAPI =================

app = FastAPI()

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://telegram.org"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================= TELEGRAM AUTH =================

def validate_telegram(init_data: str):
    try:
        parsed = dict(
            item.split("=")
            for item in init_data.split("&")
            if not item.startswith("hash=")
        )
        received_hash = dict(
            item.split("=")
            for item in init_data.split("&")
            if item.startswith("hash=")
        )["hash"]

        data_check_string = "\n".join(
            f"{k}={parsed[k]}" for k in sorted(parsed)
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            raise HTTPException(status_code=403)

        user_data = json.loads(parsed["user"])
        return user_data

    except Exception:
        raise HTTPException(status_code=403)


# ================= GAME LOGIC =================

def process_daily_updates(user: UserProfile):
    today = date.today()
    delta = (today - user.last_login).days

    if delta > 0:
        user.water_count = 0
        user.completed_tasks.clear()

        if delta > 1:
            penalty = delta * 15
            user.hp = max(0, user.hp - penalty)

        if today.month != user.last_login.month:
            user.current_month_xp = 0

        user.last_login = today


def apply_xp(user: UserProfile, amount: int):
    if amount <= 0:
        raise HTTPException(status_code=400)

    if user.hp < 30:
        amount = amount // 2

    user.xp += amount
    user.current_month_xp += amount

    user.level = user.xp // 100 + 1


# ================= ENDPOINTS =================

@app.get("/hero")
@limiter.limit("10/minute")
def load_hero(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data")
    user_data = validate_telegram(init_data)

    with get_db() as db:
        user = db.query(UserProfile).filter_by(
            telegram_id=user_data["id"]
        ).with_for_update().first()

        if not user:
            user = UserProfile(
                telegram_id=user_data["id"],
                username=user_data.get("username")
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        process_daily_updates(user)
        db.commit()

        return {
            "level": user.level,
            "xp": user.xp,
            "month_xp": user.current_month_xp,
            "hp": user.hp,
            "water": user.water_count,
            "goal": user.water_goal
        }


@app.post("/water")
@limiter.limit("20/minute")
def drink_water(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data")
    user_data = validate_telegram(init_data)

    with get_db() as db:
        user = db.query(UserProfile).filter_by(
            telegram_id=user_data["id"]
        ).with_for_update().first()

        if not user:
            raise HTTPException(status_code=404)

        user.water_count += 1
        user.hp = min(100, user.hp + 5)
        apply_xp(user, 5)

        db.commit()
        return {"ok": True}


@app.post("/sleep")
@limiter.limit("10/minute")
def sleep_action(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data")
    user_data = validate_telegram(init_data)

    body = json.loads(request.body().decode())
    duration = float(body.get("duration", 0))
    bedtime_hour = int(body.get("bedtime_hour", 0))

    if duration <= 0:
        raise HTTPException(status_code=400)

    xp = 0
    hp = 0

    if duration < 0.5:
        xp = 0
    elif duration < 3:
        xp += 10; hp += 5
    elif duration < 5:
        xp += 15; hp += 10
    elif duration < 7.5:
        xp += 30; hp += 15
    else:
        xp += 50; hp += 20

    if 21 <= bedtime_hour <= 23:
        xp += 30
    elif 0 <= bedtime_hour <= 1:
        xp += 10
    elif 2 <= bedtime_hour <= 5:
        xp -= 10

    remainder = duration % 1.5
    if remainder < 0.35 or remainder > 1.15:
        xp += 20

    with get_db() as db:
        user = db.query(UserProfile).filter_by(
            telegram_id=user_data["id"]
        ).with_for_update().first()

        user.hp = min(100, user.hp + hp)
        apply_xp(user, xp)

        db.commit()
        return {"ok": True}
