import os
import hmac
import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, validator
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Date, Text, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args={"sslmode": "require"} if "neon" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Life RPG API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://web.telegram.org", "https://telegram.org"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

class UserProfile(Base):
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    hp = Column(Integer, default=100)
    xp = Column(Integer, default=0)
    level = Column(Integer, default=1)
    current_month_xp = Column(Integer, default=0)
    water_count = Column(Integer, default=0)
    water_goal = Column(Integer, default=8)
    weight = Column(Float, default=70.0)
    activity_factor = Column(Float, default=1.0)
    completed_tasks = Column(Text, default="[]")
    sleep_start = Column(DateTime(timezone=True), nullable=True)
    coins = Column(Integer, default=0)
    streak = Column(Integer, default=0)
    last_seen_date = Column(Date, nullable=True)
    last_month_reset = Column(Date, nullable=True)
    custom_habits = Column(Text, default="[]")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CompletedTask(Base):
    __tablename__ = "completed_tasks"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    task_id = Column(String, nullable=False)
    completed_date = Column(Date, nullable=False)
    xp_gained = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("user_id", "task_id", "completed_date", name="uq_task_per_day"),)


class History(Base):
    __tablename__ = "history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    xp_delta = Column(Integer, default=0)
    hp_delta = Column(Integer, default=0)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ShopPurchase(Base):
    __tablename__ = "shop_purchases"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    reward_id = Column(String, nullable=False)
    purchased_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(bind=engine)

# ─────────────────────────────────────────────
# SERVER DICTIONARIES
# ─────────────────────────────────────────────

TASKS = {
    "workout_light": {"name": "Лёгкая тренировка", "xp": 20, "category": "activity"},
    "workout_medium": {"name": "Средняя тренировка", "xp": 35, "category": "activity"},
    "workout_hard": {"name": "Интенсивная тренировка", "xp": 50, "category": "activity"},
    "meditation": {"name": "Медитация 10 мин", "xp": 15, "category": "activity"},
    "reading": {"name": "Чтение 30 мин", "xp": 15, "category": "activity"},
    "walk": {"name": "Прогулка на свежем воздухе", "xp": 20, "category": "activity"},
    "friend_call": {"name": "Позвонить другу", "xp": 20, "category": "relations"},
    "family_time": {"name": "Провести время с семьёй", "xp": 25, "category": "relations"},
    "gratitude": {"name": "Написать благодарность", "xp": 10, "category": "relations"},
    "social_event": {"name": "Социальное мероприятие", "xp": 30, "category": "relations"},
}

REWARDS = {
    "coffee": {"name": "☕ Кофе с собой", "cost": 50, "description": "Заслуженный кофе"},
    "movie": {"name": "🎬 Кино", "cost": 100, "description": "Поход в кино"},
    "game_hour": {"name": "🎮 Час игр", "cost": 75, "description": "Час любимых игр"},
    "cheat_meal": {"name": "🍕 Читмил", "cost": 120, "description": "Читмил без угрызений совести"},
    "spa": {"name": "💆 Спа-день", "cost": 300, "description": "День отдыха и восстановления"},
    "new_game": {"name": "🕹️ Новая игра", "cost": 500, "description": "Купить новую игру"},
}

# ─────────────────────────────────────────────
# DB SESSION
# ─────────────────────────────────────────────

@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

def validate_telegram_init_data(init_data: str) -> dict:
    """Validate Telegram WebApp initData per official docs."""
    try:
        parsed = {}
        pairs = init_data.split("&")
        data_check_string_parts = []
        hash_value = None

        for pair in pairs:
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            key = unquote(key)
            value = unquote(value)
            if key == "hash":
                hash_value = value
            else:
                data_check_string_parts.append(f"{key}={value}")
                parsed[key] = value

        if not hash_value:
            raise ValueError("No hash in init data")

        data_check_string_parts.sort()
        data_check_string = "\n".join(data_check_string_parts)

        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_hash, hash_value):
            raise ValueError("Invalid hash")

        if "user" in parsed:
            parsed["user"] = json.loads(parsed["user"])

        # Check timestamp (max 1 hour old)
        if "auth_date" in parsed:
            auth_date = int(parsed["auth_date"])
            now = int(datetime.now(timezone.utc).timestamp())
            if now - auth_date > 3600:
                raise ValueError("Init data expired")

        return parsed
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")
    except Exception as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_current_user(x_telegram_init_data: str = Header(None)) -> dict:
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing X-Telegram-Init-Data header")
    return validate_telegram_init_data(x_telegram_init_data)


# ─────────────────────────────────────────────
# GAME LOGIC
# ─────────────────────────────────────────────

def xp_to_level(xp: int) -> int:
    level = 1
    required = 100
    while xp >= required:
        xp -= required
        level += 1
        required = int(required * 1.15)
    return level


def xp_for_next_level(current_xp: int) -> tuple:
    xp = current_xp
    required = 100
    while xp >= required:
        xp -= required
        required = int(required * 1.15)
    return xp, required


def apply_xp_gain(user: UserProfile, xp_gain: int) -> int:
    """Apply XP gain considering HP penalty."""
    if user.hp < 30:
        xp_gain = xp_gain // 2
    if xp_gain < 0:
        xp_gain = 0
    user.xp += xp_gain
    user.current_month_xp += xp_gain
    user.level = xp_to_level(user.xp)
    return xp_gain


def clamp_hp(user: UserProfile):
    user.hp = max(0, min(100, user.hp))


def process_daily_updates(user: UserProfile, db: Session):
    """Handle daily/monthly resets and streak/penalty logic."""
    today = date.today()
    now = datetime.now(timezone.utc)

    # Monthly reset of current_month_xp
    if user.last_month_reset is None or (
        today.year != user.last_month_reset.year or today.month != user.last_month_reset.month
    ):
        user.current_month_xp = 0
        user.last_month_reset = today

    # Daily reset + streak + HP penalty
    if user.last_seen_date is None:
        user.last_seen_date = today
        user.streak = 1
    elif user.last_seen_date < today:
        days_missed = (today - user.last_seen_date).days

        if days_missed == 1:
            user.streak += 1
        else:
            # Penalty: N * 15 HP for missed days
            penalty = days_missed * 15
            user.hp = max(0, user.hp - penalty)
            user.streak = 1
            # Log penalty
            hist = History(
                user_id=user.telegram_id,
                event_type="penalty",
                description=f"Штраф за {days_missed} пропущенных дней",
                xp_delta=0,
                hp_delta=-penalty,
                timestamp=now,
            )
            db.add(hist)

        # Reset daily fields
        user.water_count = 0
        user.completed_tasks = "[]"
        user.last_seen_date = today

    db.flush()


# ─────────────────────────────────────────────
# PYDANTIC SCHEMAS
# ─────────────────────────────────────────────

class WaterRequest(BaseModel):
    amount: int = 1

    @validator("amount")
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("amount must be positive")
        return v


class SleepStartRequest(BaseModel):
    pass


class SleepEndRequest(BaseModel):
    pass


class TaskCompleteRequest(BaseModel):
    task_id: str


class ShopBuyRequest(BaseModel):
    reward_id: str


class UpdateBioRequest(BaseModel):
    weight: float
    activity_factor: float

    @validator("weight")
    def weight_valid(cls, v):
        if v <= 0 or v > 500:
            raise ValueError("Invalid weight")
        return v

    @validator("activity_factor")
    def activity_valid(cls, v):
        if v <= 0 or v > 10:
            raise ValueError("Invalid activity_factor")
        return v


class AddHabitRequest(BaseModel):
    name: str
    xp: int
    category: str

    @validator("xp")
    def xp_positive(cls, v):
        if v <= 0:
            raise ValueError("XP must be positive")
        return v

    @validator("name")
    def name_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        if len(v) > 100:
            raise ValueError("Name too long")
        return v

    @validator("category")
    def category_valid(cls, v):
        if v not in ("activity", "relations", "custom"):
            raise ValueError("Invalid category")
        return v


class EditHabitRequest(BaseModel):
    habit_id: str
    name: str
    xp: int

    @validator("xp")
    def xp_positive(cls, v):
        if v <= 0:
            raise ValueError("XP must be positive")
        return v

    @validator("name")
    def name_not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        if len(v) > 100:
            raise ValueError("Name too long")
        return v


class DeleteHabitRequest(BaseModel):
    habit_id: str


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_or_create_user(telegram_id: str, username: str, first_name: str, db: Session) -> UserProfile:
    user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).with_for_update().first()
    if not user:
        user = UserProfile(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
        )
        db.add(user)
        db.flush()
    else:
        if username:
            user.username = username
        if first_name:
            user.first_name = first_name
    return user


def build_hero_response(user: UserProfile) -> dict:
    xp_current, xp_needed = xp_for_next_level(user.xp)
    completed = json.loads(user.completed_tasks or "[]")
    custom_habits = json.loads(user.custom_habits or "[]")
    return {
        "telegram_id": user.telegram_id,
        "first_name": user.first_name,
        "username": user.username,
        "hp": user.hp,
        "xp": user.xp,
        "level": user.level,
        "current_month_xp": user.current_month_xp,
        "xp_current": xp_current,
        "xp_needed": xp_needed,
        "water_count": user.water_count,
        "water_goal": user.water_goal,
        "weight": user.weight,
        "activity_factor": user.activity_factor,
        "completed_tasks": completed,
        "sleep_start": user.sleep_start.isoformat() if user.sleep_start else None,
        "coins": user.coins,
        "streak": user.streak,
        "custom_habits": custom_habits,
        "tasks": TASKS,
        "rewards": REWARDS,
    }


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.get("/api/hero")
@limiter.limit("60/minute")
async def get_hero(request: Request, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))
    if not telegram_id:
        raise HTTPException(status_code=400, detail="No user id in init data")

    with get_db() as db:
        user = get_or_create_user(
            telegram_id=telegram_id,
            username=user_data.get("username", ""),
            first_name=user_data.get("first_name", ""),
            db=db,
        )
        process_daily_updates(user, db)
        db.commit()
        return build_hero_response(user)


@app.post("/api/water")
@limiter.limit("30/minute")
async def add_water(request: Request, body: WaterRequest, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))

    with get_db() as db:
        user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        process_daily_updates(user, db)

        xp_gain = body.amount * 5
        hp_gain = body.amount * 5
        actual_xp = apply_xp_gain(user, xp_gain)
        user.hp = min(100, user.hp + hp_gain)
        user.water_count += body.amount

        hist = History(
            user_id=telegram_id,
            event_type="water",
            description=f"Выпито {body.amount} стакан(ов) воды",
            xp_delta=actual_xp,
            hp_delta=hp_gain,
            timestamp=datetime.now(timezone.utc),
        )
        db.add(hist)
        db.commit()
        return {"ok": True, "xp_gained": actual_xp, "hp_gained": hp_gain, "hero": build_hero_response(user)}


@app.post("/api/sleep/start")
@limiter.limit("10/minute")
async def sleep_start(request: Request, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))

    with get_db() as db:
        user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user.sleep_start is not None:
            raise HTTPException(status_code=400, detail="Already sleeping")

        user.sleep_start = datetime.now(timezone.utc)
        db.commit()
        return {"ok": True, "sleep_start": user.sleep_start.isoformat()}


@app.post("/api/sleep/end")
@limiter.limit("10/minute")
async def sleep_end(request: Request, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))
    now = datetime.now(timezone.utc)

    with get_db() as db:
        user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user.sleep_start is None:
            raise HTTPException(status_code=400, detail="Not sleeping")

        sleep_start_dt = user.sleep_start
        duration_hours = (now - sleep_start_dt).total_seconds() / 3600.0

        xp_gain = 0
        hp_gain = 0
        message = ""

        if duration_hours < 0.5:
            message = "Слишком мало — сон не засчитан"
        else:
            # Duration XP
            if duration_hours < 3:
                xp_gain += 10
                hp_gain += 5
            elif duration_hours < 5:
                xp_gain += 15
                hp_gain += 10
            elif duration_hours < 7.5:
                xp_gain += 30
                hp_gain += 15
            else:
                xp_gain += 50
                hp_gain += 20

            # Bedtime bonus
            bedtime_hour = sleep_start_dt.astimezone(timezone.utc).hour
            if 21 <= bedtime_hour <= 23:
                xp_gain += 30
            elif bedtime_hour in (0, 1):
                xp_gain += 10
            elif 2 <= bedtime_hour <= 5:
                xp_gain -= 10

            # Wake phase bonus
            phase_remainder = (duration_hours % 1.5)
            if phase_remainder < 0.35 or phase_remainder > 1.15:
                xp_gain += 20

            message = f"Сон {duration_hours:.1f}ч засчитан"

        actual_xp = 0
        if xp_gain > 0:
            actual_xp = apply_xp_gain(user, xp_gain)
        elif xp_gain < 0:
            user.current_month_xp = max(0, user.current_month_xp + xp_gain)

        if hp_gain > 0:
            user.hp = min(100, user.hp + hp_gain)

        clamp_hp(user)
        user.sleep_start = None

        hist = History(
            user_id=telegram_id,
            event_type="sleep",
            description=message,
            xp_delta=actual_xp,
            hp_delta=hp_gain,
            timestamp=now,
        )
        db.add(hist)
        db.commit()
        return {
            "ok": True,
            "xp_gained": actual_xp,
            "hp_gained": hp_gain,
            "duration_hours": round(duration_hours, 2),
            "message": message,
            "hero": build_hero_response(user),
        }


@app.post("/api/task/complete")
@limiter.limit("30/minute")
async def complete_task(request: Request, body: TaskCompleteRequest, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))
    today = date.today()

    with get_db() as db:
        user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        process_daily_updates(user, db)

        # Determine XP from built-in or custom habits
        xp_value = None
        task_name = None
        if body.task_id in TASKS:
            xp_value = TASKS[body.task_id]["xp"]
            task_name = TASKS[body.task_id]["name"]
        else:
            custom_habits = json.loads(user.custom_habits or "[]")
            for habit in custom_habits:
                if habit.get("id") == body.task_id:
                    xp_value = habit["xp"]
                    task_name = habit["name"]
                    break

        if xp_value is None:
            raise HTTPException(status_code=404, detail="Task not found")

        # Check uniqueness for today
        existing = db.query(CompletedTask).filter(
            CompletedTask.user_id == telegram_id,
            CompletedTask.task_id == body.task_id,
            CompletedTask.completed_date == today,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="Task already completed today")

        actual_xp = apply_xp_gain(user, xp_value)

        ct = CompletedTask(
            user_id=telegram_id,
            task_id=body.task_id,
            completed_date=today,
            xp_gained=actual_xp,
        )
        db.add(ct)

        completed = json.loads(user.completed_tasks or "[]")
        if body.task_id not in completed:
            completed.append(body.task_id)
        user.completed_tasks = json.dumps(completed)

        hist = History(
            user_id=telegram_id,
            event_type="task",
            description=f"Задача выполнена: {task_name}",
            xp_delta=actual_xp,
            hp_delta=0,
            timestamp=datetime.now(timezone.utc),
        )
        db.add(hist)
        db.commit()
        return {"ok": True, "xp_gained": actual_xp, "hero": build_hero_response(user)}


@app.post("/api/shop/buy")
@limiter.limit("20/minute")
async def shop_buy(request: Request, body: ShopBuyRequest, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))

    if body.reward_id not in REWARDS:
        raise HTTPException(status_code=404, detail="Reward not found")

    reward = REWARDS[body.reward_id]
    cost = reward["cost"]

    if cost <= 0:
        raise HTTPException(status_code=400, detail="Invalid reward cost")

    with get_db() as db:
        user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if user.current_month_xp < cost:
            raise HTTPException(status_code=400, detail="Not enough monthly XP")

        user.current_month_xp -= cost

        purchase = ShopPurchase(
            user_id=telegram_id,
            reward_id=body.reward_id,
            purchased_at=datetime.now(timezone.utc),
        )
        db.add(purchase)

        hist = History(
            user_id=telegram_id,
            event_type="shop",
            description=f"Куплено: {reward['name']}",
            xp_delta=-cost,
            hp_delta=0,
            timestamp=datetime.now(timezone.utc),
        )
        db.add(hist)
        db.commit()
        return {"ok": True, "purchased": reward["name"], "hero": build_hero_response(user)}


@app.post("/api/bio/update")
@limiter.limit("10/minute")
async def update_bio(request: Request, body: UpdateBioRequest, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))

    with get_db() as db:
        user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.weight = body.weight
        user.activity_factor = body.activity_factor
        user.water_goal = max(1, int(body.weight * body.activity_factor / 250))
        db.commit()
        return {"ok": True, "water_goal": user.water_goal, "hero": build_hero_response(user)}


@app.post("/api/habit/add")
@limiter.limit("20/minute")
async def add_habit(request: Request, body: AddHabitRequest, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))

    with get_db() as db:
        user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        habits = json.loads(user.custom_habits or "[]")
        if len(habits) >= 20:
            raise HTTPException(status_code=400, detail="Max 20 custom habits")

        import uuid
        habit_id = f"custom_{uuid.uuid4().hex[:8]}"
        habits.append({"id": habit_id, "name": body.name, "xp": body.xp, "category": body.category})
        user.custom_habits = json.dumps(habits)
        db.commit()
        return {"ok": True, "habit_id": habit_id, "hero": build_hero_response(user)}


@app.post("/api/habit/edit")
@limiter.limit("20/minute")
async def edit_habit(request: Request, body: EditHabitRequest, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))

    with get_db() as db:
        user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        habits = json.loads(user.custom_habits or "[]")
        found = False
        for habit in habits:
            if habit.get("id") == body.habit_id:
                habit["name"] = body.name
                habit["xp"] = body.xp
                found = True
                break

        if not found:
            raise HTTPException(status_code=404, detail="Habit not found")

        user.custom_habits = json.dumps(habits)
        db.commit()
        return {"ok": True, "hero": build_hero_response(user)}


@app.post("/api/habit/delete")
@limiter.limit("20/minute")
async def delete_habit(request: Request, body: DeleteHabitRequest, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))

    with get_db() as db:
        user = db.query(UserProfile).filter(UserProfile.telegram_id == telegram_id).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        habits = json.loads(user.custom_habits or "[]")
        new_habits = [h for h in habits if h.get("id") != body.habit_id]
        if len(new_habits) == len(habits):
            raise HTTPException(status_code=404, detail="Habit not found")

        user.custom_habits = json.dumps(new_habits)
        db.commit()
        return {"ok": True, "hero": build_hero_response(user)}


@app.get("/api/history")
@limiter.limit("20/minute")
async def get_history(request: Request, auth: dict = Depends(get_current_user)):
    user_data = auth.get("user", {})
    telegram_id = str(user_data.get("id", ""))

    with get_db() as db:
        records = (
            db.query(History)
            .filter(History.user_id == telegram_id)
            .order_by(History.timestamp.desc())
            .limit(50)
            .all()
        )
        return {
            "history": [
                {
                    "id": r.id,
                    "event_type": r.event_type,
                    "description": r.description,
                    "xp_delta": r.xp_delta,
                    "hp_delta": r.hp_delta,
                    "timestamp": r.timestamp.isoformat(),
                }
                for r in records
            ]
        }


@app.get("/health")
async def health():
    return {"status": "ok"}
