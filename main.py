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
# Авторизация
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

        data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params.keys()))
        expected_hash = hmac.new(_SECRET_KEY, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

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
    if not init_data: return get_remote_address(request)
    try: return f"user:{verify_and_extract_user(init_data)['id']}"
    except Exception: return get_remote_address(request)

limiter = Limiter(key_func=rate_limit_by_user)

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Telegram-Init-Data"],
)

# ---------------------------------------------------------------------------
# Pydantic-схемы
# ---------------------------------------------------------------------------
class TaskCompletePayload(BaseModel):
    task_id: str = Field(..., max_length=64)
    custom_name: Optional[str] = Field(default=None, max_length=100)
    custom_xp: Optional[int] = Field(default=None, ge=1, le=10000)

class RewardBuyPayload(BaseModel):
    reward_id: str = Field(..., max_length=64)
    qty: int       = Field(default=1, ge=1, le=10)

class WaterGoalPayload(BaseModel):
    goal: int = Field(..., ge=1, le=20)

class HabitsPayload(BaseModel):
    habits: str = Field(..., max_length=16384)

class SleepPayload(BaseModel):
    tz: int = Field(default=0, ge=-720, le=840)

# ---------------------------------------------------------------------------
# Утилиты БД
# ---------------------------------------------------------------------------
@contextmanager
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def get_today_str() -> str: return datetime.now(timezone.utc).strftime("%Y-%m-%d")
def get_current_month_str() -> str: return datetime.now(timezone.utc).strftime("%Y-%m")

def add_to_history(db, user_id: str, e_type: str, description: str, amt: int) -> None:
    db.add(History(user_id=user_id, event_type=e_type, description=description, amount=amt))

def trim_history_background(user_id: str) -> None:
    try:
        with get_db() as db:
            count = db.query(func.count(History.id)).filter(History.user_id == user_id).scalar() or 0
            if count <= HISTORY_LIMIT: return
            to_delete = min(count - HISTORY_LIMIT, HISTORY_DELETE_BATCH)
            oldest_ids = db.query(History.id).filter(History.user_id == user_id).order_by(History.timestamp.asc()).limit(to_delete).subquery()
            db.query(History).filter(History.id.in_(oldest_ids)).delete(synchronize_session=False)
            db.commit()
    except Exception as e: logger.warning(f"trim_history error: {e}")

def get_or_create_user_in_tx(db, user_id: str) -> UserProfile:
    user = db.query(UserProfile).filter(UserProfile.user_id == user_id).with_for_update().first()
    if not user:
        user = UserProfile(user_id=user_id, hp=100, last_active_date=get_today_str(), last_active_month=get_current_month_str(), water_goal=8)
        db.add(user)
        db.flush()
    return user

def process_daily_updates(user: UserProfile, db) -> None:
    today = get_today_str()
    current_month = get_current_month_str()

    if user.last_active_month != current_month:
        user.current_month_xp  = 0
        user.last_active_month = current_month

    if user.last_active_date != today:
        if user.last_active_date:
            try:
                last_date   = datetime.strptime(user.last_active_date, "%Y-%m-%d").date()
                days_missed = (datetime.now(timezone.utc).date() - last_date).days
                if days_missed > 0:
                    loss       = min(days_missed * 15, user.hp)
                    user.hp    = max(0, user.hp - loss)
                    user.streak = 0
                    add_to_history(db, user.user_id, "spend", f"Пропуск ({days_missed} дн.)", loss)
            except Exception as e: pass
        user.last_active_date = today
        user.water_count      = 0

def get_completed_today(db, user_id: str) -> set[str]:
    today = get_today_str()
    rows  = db.query(CompletedTask.task_id).filter(CompletedTask.user_id == user_id, CompletedTask.date == today).all()
    return {r.task_id for r in rows}

def build_hero_response(db, user: UserProfile) -> dict:
    hist = db.query(History).filter(History.user_id == user.user_id).order_by(desc(History.timestamp)).limit(20).all()
    hist_data = [{"type": h.event_type, "desc": h.description, "amt": h.amount, "time": h.timestamp.isoformat() if h.timestamp else ""} for h in hist]
    return {
        "total_xp":         user.total_xp,
        "current_month_xp": user.current_month_xp,
        "hp":               user.hp,
        "water_count":      user.water_count,
        "water_goal":       user.water_goal,
        "completed_tasks":  ",".join(get_completed_today(db, user.user_id)),
        "sleep_start":      user.sleep_start,
        "custom_habits":    user.custom_habits,
        "streak":           user.streak,
        "history":          hist_data,
        "tasks":            [{"id": tid, **tdata} for tid, tdata in TASKS.items()],
        "rewards":          [{"id": rid, **rdata} for rid, rdata in REWARDS.items()],
    }

# ---------------------------------------------------------------------------
# Эндпоинты
# ---------------------------------------------------------------------------
@app.get("/hero")
@limiter.limit("60/minute")
def get_hero(request: Request, x_telegram_init_data: Optional[str] = Header(None)):
    user_id = require_auth(x_telegram_init_data)
    with get_db() as db:
        user = get_or_create_user_in_tx(db, user_id)
        process_daily_updates(user, db)
        db.commit()
        return build_hero_response(db, user)

@app.post("/tasks/complete")
@limiter.limit("30/minute")
def complete_task(request: Request, payload: TaskCompletePayload, background_tasks: BackgroundTasks, x_telegram_init_data: Optional[str] = Header(None)):
    user_id = require_auth(x_telegram_init_data)
    
    task_name = ""
    task_xp = 0

    if payload.task_id.startswith("task-custom-"):
        if not payload.custom_name or not payload.custom_xp:
            raise HTTPException(status_code=400, detail="Missing custom task data")
        task_name = payload.custom_name
        task_xp = payload.custom_xp
    else:
        task = TASKS.get(payload.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        task_name = task["name"]
        task_xp = task["xp"]

    with get_db() as db:
        user = get_or_create_user_in_tx(db, user_id)
        process_daily_updates(user, db)
        today = get_today_str()

        if db.query(CompletedTask).filter(CompletedTask.user_id == user_id, CompletedTask.task_id == payload.task_id, CompletedTask.date == today).first():
            db.commit()
            return build_hero_response(db, user)

        try:
            db.add(CompletedTask(user_id=user_id, task_id=payload.task_id, date=today))
            db.flush()
        except IntegrityError:
            db.rollback()
            with get_db() as db2:
                user2 = get_or_create_user_in_tx(db2, user_id)
                db2.commit()
                return build_hero_response(db2, user2)

        gain                   = task_xp if user.hp >= 30 else task_xp // 2
        user.total_xp         += gain
        user.current_month_xp += gain
        user.hp                = min(100, user.hp + 5)
        add_to_history(db, user_id, "gain", task_name, gain)
        db.commit()

        background_tasks.add_task(trim_history_background, user_id)
        return build_hero_response(db, user)

@app.post("/rewards/buy")
@limiter.limit("20/minute")
def buy_reward(request: Request, payload: RewardBuyPayload, background_tasks: BackgroundTasks, x_telegram_init_data: Optional[str] = Header(None)):
    user_id = require_auth(x_telegram_init_data)
    reward = REWARDS.get(payload.reward_id)
    if not reward: raise HTTPException(status_code=404, detail="Reward not found")
    total_cost = reward["cost"] * payload.qty

    with get_db() as db:
        user = get_or_create_user_in_tx(db, user_id)
        if user.current_month_xp < total_cost:
            db.commit()
            return {"error": f"Недостаточно XP! Нужно {total_cost}, есть {user.current_month_xp}"}

        user.current_month_xp -= total_cost
        add_to_history(db, user_id, "spend", f"{reward['name']} x{payload.qty}", total_cost)
        db.commit()
        background_tasks.add_task(trim_history_background, user_id)
        return build_hero_response(db, user)

@app.post("/health/drink")
@limiter.limit("30/minute")
def drink_water(request: Request, background_tasks: BackgroundTasks, x_telegram_init_data: Optional[str] = Header(None)):
    user_id = require_auth(x_telegram_init_data)
    with get_db() as db:
        user = get_or_create_user_in_tx(db, user_id)
        process_daily_updates(user, db)

        if user.water_count < user.water_goal:
            user.water_count      += 1
            gain                   = 5 if user.hp >= 30 else 2
            user.total_xp         += gain
            user.current_month_xp += gain
            user.hp                = min(100, user.hp + 5)
            add_to_history(db, user_id, "gain", f"Вода {user.water_count}/{user.water_goal}", gain)
            background_tasks.add_task(trim_history_background, user_id)
        db.commit()
        return build_hero_response(db, user)

@app.post("/health/set-goal")
@limiter.limit("10/minute")
def set_water_goal(request: Request, payload: WaterGoalPayload, x_telegram_init_data: Optional[str] = Header(None)):
    user_id = require_auth(x_telegram_init_data)
    with get_db() as db:
        user = get_or_create_user_in_tx(db, user_id)
        user.water_goal = payload.goal
        db.commit()
        return build_hero_response(db, user)

@app.post("/sleep/action")
@limiter.limit("10/minute")
def sleep_action(request: Request, payload: SleepPayload, background_tasks: BackgroundTasks, x_telegram_init_data: Optional[str] = Header(None)):
    user_id = require_auth(x_telegram_init_data)
    with get_db() as db:
        user = get_or_create_user_in_tx(db, user_id)

        if not user.sleep_start:
            user.sleep_start = datetime.now(timezone.utc).isoformat()
            db.commit()
            return build_hero_response(db, user)

        try:
            start_time = datetime.fromisoformat(user.sleep_start.replace("Z", "+00:00"))
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)

            duration_hours = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600.0

            if duration_hours < 0.5:
                user.sleep_start = ""
                db.commit()
                res = build_hero_response(db, user)
                res["sleep_report"] = "⏳ Сон отменён. Менее 30 минут — не считается."
                return res

            local_start = start_time - timedelta(minutes=payload.tz)
            bed_h       = local_start.hour
            report, base_xp, hp_heal = [], 0, 0

            if duration_hours < 3: base_xp, hp_heal = 10, 5;  report.append(f"⏳ {round(duration_hours,1)}ч (Очень короткий)")
            elif duration_hours < 5: base_xp, hp_heal = 15, 10; report.append(f"⏳ {round(duration_hours,1)}ч (Недосып)")
            elif duration_hours < 7.5: base_xp, hp_heal = 30, 15; report.append(f"⏳ {round(duration_hours,1)}ч (Средний сон)")
            else: base_xp, hp_heal = 50, 20; report.append(f"⏳ {round(duration_hours,1)}ч (Оптимальный сон)")

            if duration_hours >= 3:
                if 21 <= bed_h <= 23: base_xp += 30; report.append("🧬 Идеальное время засыпания (+30 XP).")
                elif bed_h in (0, 1): base_xp += 10; report.append("🧬 Допустимое время засыпания (+10 XP).")
                elif 2 <= bed_h <= 5: base_xp -= 10; report.append("🧬 Слишком поздно (-10 XP).")
                if (duration_hours % 1.5) < 0.35 or (duration_hours % 1.5) > 1.15:
                    base_xp += 20; hp_heal += 5
                    report.append("⏰ Пробуждение в лёгкой фазе (+20 XP).")

            final_xp               = max(0, base_xp)
            user.total_xp         += final_xp
            user.current_month_xp += final_xp
            user.hp                = min(100, user.hp + hp_heal)
            user.sleep_start       = ""
            add_to_history(db, user_id, "gain", f"Сон ({round(duration_hours,1)}ч)", final_xp)
            db.commit()

            background_tasks.add_task(trim_history_background, user_id)
            res = build_hero_response(db, user)
            res["sleep_report"] = "\n\n".join(report) + f"\n\n🏆 ИТОГ: +{final_xp} XP | +{hp_heal} HP"
            return res

        except Exception as e:
            logger.error(f"sleep_action error: {e}")
            user.sleep_start = ""
            db.commit()
            return build_hero_response(db, user)

@app.post("/habits/update")
@limiter.limit("10/minute")
def update_habits(request: Request, payload: HabitsPayload, x_telegram_init_data: Optional[str] = Header(None)):
    user_id = require_auth(x_telegram_init_data)
    with get_db() as db:
        user = get_or_create_user_in_tx(db, user_id)
        user.custom_habits = payload.habits
        db.commit()
        return build_hero_response(db, user)
