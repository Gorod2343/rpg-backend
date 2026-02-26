import os
import hmac
import hashlib
import json
import urllib.parse
from datetime import datetime, timedelta, date
from contextlib import contextmanager
from typing import Optional, List
from zoneinfo import ZoneInfo  # Python 3.9+

from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
import slowapi
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from pydantic import BaseModel, Field, validator
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Date, Float,
    ForeignKey, UniqueConstraint, Index, func, and_
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship, joinedload
from sqlalchemy.exc import IntegrityError

# ---------- Environment variables ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN must be set")
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set")

# ---------- Database setup ----------
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# ---------- Rate limiter ----------
limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(429, _rate_limit_exceeded_handler)

# ---------- SQLAlchemy Models ----------
class UserProfile(Base):
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    # Game stats
    hp = Column(Integer, default=100, nullable=False)
    xp_total = Column(Integer, default=0, nullable=False)
    current_month_xp = Column(Integer, default=0, nullable=False)
    water_count = Column(Integer, default=0, nullable=False)
    weight = Column(Float, nullable=True)
    activity = Column(String, nullable=True)  # 'low', 'medium', 'high'
    sleep_start_time = Column(DateTime(timezone=True), nullable=True)
    last_active = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(ZoneInfo("UTC")))
    last_daily_reset = Column(DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(ZoneInfo("UTC")))
    last_monthly_reset = Column(DateTime(timezone=True), nullable=False,
                                default=lambda: datetime.now(ZoneInfo("UTC")))
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(ZoneInfo("UTC")))

    custom_tasks = relationship("UserCustomTask", back_populates="user", cascade="all, delete-orphan")
    completed_tasks = relationship("CompletedTask", back_populates="user", cascade="all, delete-orphan")
    history = relationship("History", back_populates="user", cascade="all, delete-orphan")

class UserCustomTask(Base):
    __tablename__ = "user_custom_tasks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    xp_reward = Column(Integer, nullable=False)
    category = Column(String, nullable=False)  # 'activity' or 'relationships'
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(ZoneInfo("UTC")))

    user = relationship("UserProfile", back_populates="custom_tasks")

class SystemTask(Base):
    __tablename__ = "system_tasks"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    xp_reward = Column(Integer, nullable=False)
    category = Column(String, nullable=False)  # 'activity', 'relationships', etc.
    is_active = Column(Boolean, default=True)

class Reward(Base):
    __tablename__ = "rewards"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String)
    xp_cost = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)

class CompletedTask(Base):
    __tablename__ = "completed_tasks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    task_type = Column(String, nullable=False)  # 'system' or 'custom'
    task_id = Column(Integer, nullable=False)   # id from system_tasks or user_custom_tasks
    date = Column(Date, nullable=False, default=date.today)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(ZoneInfo("UTC")))

    user = relationship("UserProfile", back_populates="completed_tasks")
    __table_args__ = (
        UniqueConstraint('user_id', 'task_type', 'task_id', 'date',
                         name='unique_completed_task_per_day'),
    )

class History(Base):
    __tablename__ = "history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    action_type = Column(String, nullable=False)  # task_complete, water_sip, sleep, reward_buy, daily_penalty
    description = Column(String)
    xp_change = Column(Integer, default=0)
    hp_change = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(ZoneInfo("UTC")))

    user = relationship("UserProfile", back_populates="history")

# Create tables if not exist (for production, use migrations)
Base.metadata.create_all(bind=engine)

# ---------- Helper functions ----------
def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    """
    Validate Telegram init data according to official spec.
    Input: 'query_id=AA...&user={...}&auth_date=...&hash=...'
    """
    parsed = urllib.parse.parse_qs(init_data)
    # Ensure hash exists and remove it for data check
    hash_values = parsed.get('hash')
    if not hash_values:
        return False
    received_hash = hash_values[0]
    # Remove hash from data
    data_check_string = '\n'.join(
        f"{k}={v[0]}" for k, v in sorted(parsed.items()) if k != 'hash'
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_hash, received_hash)

def parse_user_from_init_data(init_data: str) -> dict:
    """Extract user JSON from init data"""
    parsed = urllib.parse.parse_qs(init_data)
    user_json = parsed.get('user', [None])[0]
    if not user_json:
        raise ValueError("No user field in init data")
    return json.loads(user_json)

async def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Dependency to get authenticated user from init data header."""
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
    if not verify_telegram_init_data(init_data, BOT_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid init data")
    try:
        tg_user = parse_user_from_init_data(init_data)
        telegram_id = tg_user['id']
    except (ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid user data")

    user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).first()
    if not user:
        user = UserProfile(
            telegram_id=telegram_id,
            username=tg_user.get('username'),
            first_name=tg_user.get('first_name'),
            last_name=tg_user.get('last_name')
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

def apply_daily_updates(user: UserProfile, db: Session, now: datetime):
    """
    Check and apply daily/monthly resets and inactivity penalty.
    Should be called at the beginning of each request after fetching user.
    """
    # Timezone-aware UTC
    today = now.date()

    # ----- Daily reset (water_count, completed_tasks) -----
    if user.last_daily_reset.date() < today:
        # Reset daily counters
        user.water_count = 0
        # Delete old completed tasks? They are kept for history, but daily tasks should be reset.
        # We don't delete completed tasks; they are filtered by date in queries.
        user.last_daily_reset = now

    # ----- Monthly reset (current_month_xp) -----
    # Check if last_monthly_reset is in a different month (or more than a month ago)
    if (user.last_monthly_reset.year != now.year or user.last_monthly_reset.month != now.month):
        user.current_month_xp = 0
        user.last_monthly_reset = now

    # ----- Inactivity penalty -----
    days_since_active = (now - user.last_active).days
    if days_since_active > 0:
        # For each full day missed, lose 15 HP
        penalty = days_since_active * 15
        if penalty > 0:
            old_hp = user.hp
            user.hp = max(0, user.hp - penalty)
            hp_loss = old_hp - user.hp
            if hp_loss > 0:
                history = History(
                    user_id=user.id,
                    action_type='daily_penalty',
                    description=f'Пропущено {days_since_active} дн. -{hp_loss} HP',
                    hp_change=-hp_loss
                )
                db.add(history)
    # Update last_active
    user.last_active = now

def calculate_sleep_reward(sleep_duration: timedelta, sleep_start: datetime) -> dict:
    """
    Calculate XP and HP based on sleep duration and bedtime.
    Returns dict with xp, hp, description.
    """
    hours = sleep_duration.total_seconds() / 3600
    xp = 0
    hp = 0
    desc_parts = []

    # Duration reward
    if hours < 0.5:
        # less than 0.5h - no reward (task cancelled)
        return {'xp': 0, 'hp': 0, 'description': 'Слишком короткий сон'}
    elif hours < 3:
        xp += 10
        hp += 5
        desc_parts.append(f"{hours:.1f}ч: +10 XP, +5 HP")
    elif hours < 5:
        xp += 15
        hp += 10
        desc_parts.append(f"{hours:.1f}ч: +15 XP, +10 HP")
    elif hours < 7.5:
        xp += 30
        hp += 15
        desc_parts.append(f"{hours:.1f}ч: +30 XP, +15 HP")
    else:
        xp += 50
        hp += 20
        desc_parts.append(f"{hours:.1f}ч: +50 XP, +20 HP")

    # Bedtime reward (based on start hour)
    start_hour = sleep_start.hour + sleep_start.minute/60
    if 21 <= start_hour < 23:
        xp += 30
        desc_parts.append("Отбой 21-23: +30 XP")
    elif 0 <= start_hour < 1:
        xp += 10
        desc_parts.append("Отбой 0-1: +10 XP")
    elif 2 <= start_hour < 5:
        xp -= 10
        desc_parts.append("Отбой 2-5: -10 XP")
    else:
        # other times no bonus
        pass

    # Wake phase reward (using 1.5h cycles)
    remainder = hours % 1.5
    if remainder < 0.35 or remainder > 1.15:
        xp += 20
        desc_parts.append("Пробуждение в фазе: +20 XP")

    return {'xp': xp, 'hp': hp, 'description': ', '.join(desc_parts)}

def adjust_xp_for_hp(user: UserProfile, xp: int) -> int:
    """If HP < 30, XP gain is halved (rounded down)."""
    if user.hp < 30:
        return xp // 2
    return xp

# ---------- Pydantic schemas ----------
class UserHeroResponse(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    hp: int
    xp_total: int
    current_month_xp: int
    water_count: int
    water_goal: Optional[int]  # calculated on frontend, but we can provide data for it
    weight: Optional[float]
    activity: Optional[str]
    sleep_start_time: Optional[datetime]
    custom_tasks: List[dict]  # simplified
    system_tasks: List[dict]  # simplified
    rewards: List[dict]       # simplified

    class Config:
        orm_mode = True

class CompleteTaskRequest(BaseModel):
    task_type: str  # 'system' or 'custom'
    task_id: int

    @validator('task_type')
    def valid_type(cls, v):
        if v not in ('system', 'custom'):
            raise ValueError('task_type must be system or custom')
        return v

class WaterSipResponse(BaseModel):
    water_count: int
    hp: int
    xp_total: int
    current_month_xp: int
    message: str

class SleepStartResponse(BaseModel):
    sleep_start_time: datetime
    message: str

class SleepEndResponse(BaseModel):
    xp_gained: int
    hp_gained: int
    duration_hours: float
    message: str

class HabitCreate(BaseModel):
    name: str
    xp_reward: int = Field(..., gt=0)
    category: str  # 'activity' or 'relationships'

class HabitUpdate(BaseModel):
    name: Optional[str]
    xp_reward: Optional[int] = Field(None, gt=0)
    category: Optional[str]

class BuyRewardRequest(BaseModel):
    reward_id: int

class HistoryEntry(BaseModel):
    id: int
    action_type: str
    description: Optional[str]
    xp_change: int
    hp_change: int
    created_at: datetime

    class Config:
        orm_mode = True

# ---------- API Endpoints ----------
@app.get("/api/hero", response_model=UserHeroResponse)
@limiter.limit("30/minute")
async def get_hero(request: Request, user: UserProfile = Depends(get_current_user), db: Session = Depends(get_db)):
    apply_daily_updates(user, db, datetime.now(ZoneInfo("UTC")))
    db.commit()  # save any updates from daily resets
    db.refresh(user)

    # Load system tasks and rewards (active)
    system_tasks = db.query(SystemTask).filter(SystemTask.is_active == True).all()
    rewards = db.query(Reward).filter(Reward.is_active == True).all()
    custom_tasks = user.custom_tasks

    # Prepare response (convert to dict)
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "hp": user.hp,
        "xp_total": user.xp_total,
        "current_month_xp": user.current_month_xp,
        "water_count": user.water_count,
        "water_goal": None,  # frontend will calculate based on weight/activity
        "weight": user.weight,
        "activity": user.activity,
        "sleep_start_time": user.sleep_start_time,
        "custom_tasks": [{"id": t.id, "name": t.name, "xp_reward": t.xp_reward, "category": t.category} for t in custom_tasks],
        "system_tasks": [{"id": t.id, "name": t.name, "xp_reward": t.xp_reward, "category": t.category} for t in system_tasks],
        "rewards": [{"id": r.id, "name": r.name, "description": r.description, "xp_cost": r.xp_cost} for r in rewards],
    }

@app.post("/api/task/complete")
@limiter.limit("20/minute")
async def complete_task(
    request: Request,
    task_req: CompleteTaskRequest,
    user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    apply_daily_updates(user, db, datetime.now(ZoneInfo("UTC")))

    # Check if task already completed today
    today = date.today()
    existing = db.query(CompletedTask).filter(
        CompletedTask.user_id == user.id,
        CompletedTask.task_type == task_req.task_type,
        CompletedTask.task_id == task_req.task_id,
        CompletedTask.date == today
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Task already completed today")

    # Get task details and reward
    if task_req.task_type == 'system':
        task = db.query(SystemTask).filter(SystemTask.id == task_req.task_id, SystemTask.is_active).first()
        if not task:
            raise HTTPException(status_code=404, detail="System task not found")
        xp_reward = task.xp_reward
        description = f"Задача: {task.name}"
    else:  # custom
        task = db.query(UserCustomTask).filter(
            UserCustomTask.id == task_req.task_id,
            UserCustomTask.user_id == user.id
        ).first()
        if not task:
            raise HTTPException(status_code=404, detail="Custom task not found")
        xp_reward = task.xp_reward
        description = f"Привычка: {task.name}"

    # Adjust XP if HP < 30
    xp_gained = adjust_xp_for_hp(user, xp_reward)

    # Use with_for_update to lock user row
    user = db.query(UserProfile).filter(UserProfile.id == user.id).with_for_update().first()
    user.xp_total += xp_gained
    user.current_month_xp += xp_gained
    # No HP change for regular tasks

    # Create CompletedTask record
    completed = CompletedTask(
        user_id=user.id,
        task_type=task_req.task_type,
        task_id=task_req.task_id,
        date=today
    )
    db.add(completed)

    # History entry
    history = History(
        user_id=user.id,
        action_type='task_complete',
        description=description,
        xp_change=xp_gained,
        hp_change=0
    )
    db.add(history)

    db.commit()
    return {"success": True, "xp_gained": xp_gained, "current_month_xp": user.current_month_xp}

@app.post("/api/water/sip")
@limiter.limit("10/minute")
async def water_sip(request: Request, user: UserProfile = Depends(get_current_user), db: Session = Depends(get_db)):
    apply_daily_updates(user, db, datetime.now(ZoneInfo("UTC")))

    # Use with_for_update
    user = db.query(UserProfile).filter(UserProfile.id == user.id).with_for_update().first()

    # Each sip gives +5 XP and +5 HP (but not exceeding 100 HP)
    xp_gain = 5
    hp_gain = 5
    # Adjust XP if HP < 30? Should it apply? Probably yes, but water is special. We'll apply the same rule.
    xp_gained = adjust_xp_for_hp(user, xp_gain)

    user.xp_total += xp_gained
    user.current_month_xp += xp_gained
    user.hp = min(100, user.hp + hp_gain)
    user.water_count += 1

    # History
    history = History(
        user_id=user.id,
        action_type='water_sip',
        description="Глоток воды",
        xp_change=xp_gained,
        hp_change=hp_gain
    )
    db.add(history)

    db.commit()
    return WaterSipResponse(
        water_count=user.water_count,
        hp=user.hp,
        xp_total=user.xp_total,
        current_month_xp=user.current_month_xp,
        message=f"+{xp_gained} XP, +{hp_gain} HP"
    )

@app.post("/api/sleep/start")
@limiter.limit("5/minute")
async def sleep_start(request: Request, user: UserProfile = Depends(get_current_user), db: Session = Depends(get_db)):
    apply_daily_updates(user, db, datetime.now(ZoneInfo("UTC")))

    if user.sleep_start_time is not None:
        raise HTTPException(status_code=400, detail="Already sleeping. Please wake up first.")

    user = db.query(UserProfile).filter(UserProfile.id == user.id).with_for_update().first()
    user.sleep_start_time = datetime.now(ZoneInfo("UTC"))
    db.commit()

    return SleepStartResponse(
        sleep_start_time=user.sleep_start_time,
        message="Сон начат. Спокойной ночи!"
    )

@app.post("/api/sleep/end")
@limiter.limit("5/minute")
async def sleep_end(request: Request, user: UserProfile = Depends(get_current_user), db: Session = Depends(get_db)):
    apply_daily_updates(user, db, datetime.now(ZoneInfo("UTC")))

    if user.sleep_start_time is None:
        raise HTTPException(status_code=400, detail="You are not sleeping. Start sleep first.")

    user = db.query(UserProfile).filter(UserProfile.id == user.id).with_for_update().first()
    now = datetime.now(ZoneInfo("UTC"))
    duration = now - user.sleep_start_time

    # Calculate reward
    reward = calculate_sleep_reward(duration, user.sleep_start_time)
    xp_gained = adjust_xp_for_hp(user, reward['xp'])
    hp_gained = reward['hp']

    # Apply
    user.xp_total += xp_gained
    user.current_month_xp += xp_gained
    user.hp = min(100, user.hp + hp_gained)
    # Clear sleep start
    user.sleep_start_time = None

    # History
    history = History(
        user_id=user.id,
        action_type='sleep',
        description=reward['description'],
        xp_change=xp_gained,
        hp_change=hp_gained
    )
    db.add(history)

    db.commit()
    return SleepEndResponse(
        xp_gained=xp_gained,
        hp_gained=hp_gained,
        duration_hours=duration.total_seconds() / 3600,
        message=f"Сон завершён: +{xp_gained} XP, +{hp_gained} HP"
    )

# CRUD for custom habits
@app.post("/api/habits")
@limiter.limit("10/minute")
async def create_habit(
    request: Request,
    habit: HabitCreate,
    user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # No need for with_for_update here (just insert)
    new_habit = UserCustomTask(
        user_id=user.id,
        name=habit.name,
        xp_reward=habit.xp_reward,
        category=habit.category
    )
    db.add(new_habit)
    db.commit()
    db.refresh(new_habit)
    return {"id": new_habit.id, "name": new_habit.name, "xp_reward": new_habit.xp_reward, "category": new_habit.category}

@app.put("/api/habits/{habit_id}")
@limiter.limit("10/minute")
async def update_habit(
    request: Request,
    habit_id: int,
    habit: HabitUpdate,
    user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_habit = db.query(UserCustomTask).filter(
        UserCustomTask.id == habit_id,
        UserCustomTask.user_id == user.id
    ).first()
    if not db_habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    if habit.name is not None:
        db_habit.name = habit.name
    if habit.xp_reward is not None:
        db_habit.xp_reward = habit.xp_reward
    if habit.category is not None:
        db_habit.category = habit.category
    db.commit()
    db.refresh(db_habit)
    return {"id": db_habit.id, "name": db_habit.name, "xp_reward": db_habit.xp_reward, "category": db_habit.category}

@app.delete("/api/habits/{habit_id}")
@limiter.limit("10/minute")
async def delete_habit(
    request: Request,
    habit_id: int,
    user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_habit = db.query(UserCustomTask).filter(
        UserCustomTask.id == habit_id,
        UserCustomTask.user_id == user.id
    ).first()
    if not db_habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    db.delete(db_habit)
    db.commit()
    return {"success": True}

@app.post("/api/shop/buy")
@limiter.limit("5/minute")
async def buy_reward(
    request: Request,
    buy_req: BuyRewardRequest,
    user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    apply_daily_updates(user, db, datetime.now(ZoneInfo("UTC")))

    reward = db.query(Reward).filter(Reward.id == buy_req.reward_id, Reward.is_active).first()
    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found")

    user = db.query(UserProfile).filter(UserProfile.id == user.id).with_for_update().first()
    if user.current_month_xp < reward.xp_cost:
        raise HTTPException(status_code=400, detail="Not enough XP")

    user.current_month_xp -= reward.xp_cost
    # XP total doesn't decrease (spent only monthly XP)

    history = History(
        user_id=user.id,
        action_type='reward_buy',
        description=f"Куплено: {reward.name}",
        xp_change=-reward.xp_cost,
        hp_change=0
    )
    db.add(history)
    db.commit()
    return {"success": True, "remaining_xp": user.current_month_xp}

@app.get("/api/history", response_model=List[HistoryEntry])
@limiter.limit("20/minute")
async def get_history(
    request: Request,
    user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 50
):
    entries = db.query(History).filter(History.user_id == user.id).order_by(History.created_at.desc()).limit(limit).all()
    return entries

# Optionally, an endpoint to update weight/activity
class UserMetricsUpdate(BaseModel):
    weight: float
    activity: str

@app.post("/api/user/metrics")
@limiter.limit("10/minute")
async def update_metrics(
    request: Request,
    metrics: UserMetricsUpdate,
    user: UserProfile = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user = db.query(UserProfile).filter(UserProfile.id == user.id).with_for_update().first()
    user.weight = metrics.weight
    user.activity = metrics.activity
    db.commit()
    return {"success": True}

# Health check
@app.get("/health")
async def health():
    return {"status": "ok"}
