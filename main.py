import os
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Index, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла (если есть)
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Конфигурация из переменных окружения ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/dbname")
# Автоматически заменяем postgres:// на postgresql:// (для совместимости с Neon)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Настройки CORS: по умолчанию разрешаем всё для разработки, но можно задать список через запятую
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

# --- Подключение к БД ---
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # проверка соединения перед использованием
    echo=False,          # можно включить для отладки SQL
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Модели SQLAlchemy ---

class UserProfile(Base):
    __tablename__ = "users_final_v9"

    username = Column(String, primary_key=True, index=True)
    total_xp = Column(Integer, nullable=False, default=0)
    current_month_xp = Column(Integer, nullable=False, default=0)
    hp = Column(Integer, nullable=False, default=100)
    last_active_date = Column(String, nullable=False, default="")  # YYYY-MM-DD в UTC
    water_count = Column(Integer, nullable=False, default=0)
    water_goal = Column(Integer, nullable=False, default=8)
    completed_tasks = Column(ARRAY(String), nullable=False, default=[])  # массив task_id
    sleep_start = Column(String, nullable=False, default="")  # ISO формат с таймзоной
    custom_habits = Column(String, nullable=False, default="")

class History(Base):
    __tablename__ = "history_v7"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)  # 'gain' или 'spend'
    description = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_history_username_timestamp", "username", "timestamp"),
    )

# Создаём таблицы (в production лучше использовать миграции, но для простоты оставим)
Base.metadata.create_all(bind=engine)

# --- Pydantic схемы (запросы и ответы) ---

class HabitsPayload(BaseModel):
    habits: str

class SetWaterGoalRequest(BaseModel):
    goal: int = Field(..., gt=0, le=20, description="Цель по воде (стаканы в день, 1-20)")

class AddXpRequest(BaseModel):
    amount: int = Field(..., gt=0, le=1000, description="Количество опыта")
    task_id: str = Field(..., min_length=1, max_length=100, description="Идентификатор задачи")
    task_name: str = Field(..., min_length=1, max_length=200, description="Название задачи")

    @validator("task_id")
    def task_id_no_commas(cls, v):
        if "," in v:
            raise ValueError("task_id не может содержать запятую")
        return v

class HistoryEntry(BaseModel):
    type: str
    desc: str
    amt: int
    time: str  # формат "HH:MM"

    class Config:
        orm_mode = True

class HeroResponse(BaseModel):
    total_xp: int
    current_month_xp: int
    hp: int
    water_count: int
    water_goal: int
    completed_tasks: List[str]
    sleep_start: str
    custom_habits: str
    history: List[HistoryEntry]

    class Config:
        orm_mode = True

# --- Зависимости ---

def get_db() -> Session:
    """Зависимость для получения сессии БД с автоматическим закрытием."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Вспомогательные функции ---

def get_today_str() -> str:
    """Возвращает сегодняшнюю дату в формате YYYY-MM-DD (UTC)."""
    return datetime.now(timezone.utc).date().isoformat()

def add_to_history(db: Session, username: str, event_type: str, description: str, amount: int):
    """Добавляет запись в историю."""
    event = History(
        username=username,
        event_type=event_type,
        description=description,
        amount=amount,
    )
    db.add(event)

def process_daily_updates(user: UserProfile, db: Session):
    """
    Обновляет пользователя в соответствии с новым днём:
    - Штраф за пропущенные дни (макс. 50 HP)
    - Сброс water_count и completed_tasks
    """
    today = get_today_str()
    if user.last_active_date == today:
        return

    # Если дата последней активности установлена, считаем пропущенные дни
    if user.last_active_date:
        try:
            last_date = datetime.strptime(user.last_active_date, "%Y-%m-%d").date()
            current_date = datetime.now(timezone.utc).date()
            days_missed = (current_date - last_date).days
            if days_missed > 0:
                loss = min(days_missed * 15, 50)  # не более 50 HP за раз
                user.hp = max(0, user.hp - loss)
                add_to_history(db, user.username, 'spend', f'Пропуск ({days_missed} дн.)', loss)
                logger.info(f"User {user.username} lost {loss} HP due to {days_missed} missed days")
        except Exception as e:
            logger.error(f"Error parsing last_active_date for {user.username}: {e}")
            # Если ошибка парсинга, просто сбрасываем дату без штрафа
    else:
        # Если даты не было, устанавливаем сегодня и не штрафуем
        pass

    # Сброс ежедневных счётчиков
    user.last_active_date = today
    user.water_count = 0
    user.completed_tasks = []
    # water_goal не сбрасывается

def get_user_or_create(username: str, db: Session) -> UserProfile:
    """Возвращает пользователя, создавая нового при отсутствии."""
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if not user:
        user = UserProfile(
            username=username,
            hp=100,
            last_active_date=get_today_str(),
            water_goal=8,
            completed_tasks=[]
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"Created new user: {username}")
    return user

def build_hero_response(user: UserProfile, db: Session) -> HeroResponse:
    """Формирует ответ с данными героя и последними 20 записями истории."""
    history = db.query(History).filter(History.username == user.username)\
               .order_by(History.timestamp.desc()).limit(20).all()
    history_data = [
        HistoryEntry(
            type=h.event_type,
            desc=h.description,
            amt=h.amount,
            time=h.timestamp.strftime("%H:%M")  # время в UTC
        ) for h in history
    ]
    return HeroResponse(
        total_xp=user.total_xp,
        current_month_xp=user.current_month_xp,
        hp=user.hp,
        water_count=user.water_count,
        water_goal=user.water_goal,
        completed_tasks=user.completed_tasks,
        sleep_start=user.sleep_start,
        custom_habits=user.custom_habits,
        history=history_data,
    )

# --- Инициализация FastAPI ---

app = FastAPI(title="Hero API", version="1.0.0")

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Эндпоинты ---

@app.get("/get_hero/{username}", response_model=HeroResponse)
def get_hero(username: str, db: Session = Depends(get_db)):
    """Получить профиль героя и историю."""
    user = get_user_or_create(username, db)
    process_daily_updates(user, db)
    db.commit()
    return build_hero_response(user, db)

@app.post("/set_water_goal/{username}", response_model=HeroResponse)
def set_water_goal(username: str, request: SetWaterGoalRequest, db: Session = Depends(get_db)):
    """Установить новую цель по воде."""
    user = get_user_or_create(username, db)
    user.water_goal = request.goal
    add_to_history(db, username, 'gain', f'Новая цель: {request.goal} ст.', 0)
    db.commit()
    return build_hero_response(user, db)

@app.post("/sleep_action/{username}", response_model=HeroResponse)
def sleep_action(username: str, tz: int = 0, db: Session = Depends(get_db)):
    """
    Начать/закончить сон.
    - Если sleep_start пуст: запоминаем время начала.
    - Если sleep_start не пуст: считаем длительность, начисляем XP и HP, очищаем sleep_start.
    Параметр tz пока не используется, всё считается в UTC.
    """
    user = get_user_or_create(username, db)
    process_daily_updates(user, db)

    now = datetime.now(timezone.utc)

    if not user.sleep_start:
        # Начинаем сон
        user.sleep_start = now.isoformat()
        db.commit()
        return build_hero_response(user, db)

    # Завершаем сон
    try:
        start_time = datetime.fromisoformat(user.sleep_start)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        duration_hours = (now - start_time).total_seconds() / 3600.0

        # Проверка на слишком короткий сон (меньше 0.5 часа)
        if duration_hours < 0.5:
            user.sleep_start = ""
            db.commit()
            response = build_hero_response(user, db)
            # Добавляем дополнительное поле через расширение response (не нарушая схему)
            # Для простоты вернём как есть, но можно добавить кастомный заголовок
            return response

        # Ограничиваем максимальную длительность 16 часами (чтобы избежать накрутки)
        effective_hours = min(duration_hours, 16.0)
        xp_gain = 50  # фиксированная награда
        hp_gain = 15

        user.total_xp += xp_gain
        user.current_month_xp += xp_gain
        user.hp = min(100, user.hp + hp_gain)

        add_to_history(db, username, 'gain', f'Сон ({effective_hours:.1f} ч)', xp_gain)

        user.sleep_start = ""
        db.commit()
        logger.info(f"User {username} slept for {duration_hours:.1f} hours, gained {xp_gain} XP")
    except Exception as e:
        logger.error(f"Error in sleep_action for {username}: {e}")
        user.sleep_start = ""
        db.commit()
        # Можно вернуть ответ без начисления, но ошибка уже залогирована

    return build_hero_response(user, db)

@app.post("/drink_water/{username}", response_model=HeroResponse)
def drink_water(username: str, db: Session = Depends(get_db)):
    """Выпить стакан воды. Начисляет XP, если не превышена цель."""
    user = get_user_or_create(username, db)
    process_daily_updates(user, db)

    if user.water_count < user.water_goal:
        user.water_count += 1
        xp_gain = 5
        user.total_xp += xp_gain
        user.current_month_xp += xp_gain
        user.hp = min(100, user.hp + 5)
        add_to_history(db, username, 'gain', f'Вода {user.water_count}/{user.water_goal}', xp_gain)
        db.commit()
    # Если цель уже достигнута, ничего не делаем (можно вернуть сообщение, но оставим как есть)
    return build_hero_response(user, db)

@app.post("/add_xp/{username}", response_model=HeroResponse)
def add_xp(username: str, request: AddXpRequest, db: Session = Depends(get_db)):
    """Добавить опыт за выполненную задачу, если она ещё не выполнялась сегодня."""
    user = get_user_or_create(username, db)
    process_daily_updates(user, db)

    # Проверяем, не выполнялась ли задача сегодня
    if request.task_id not in user.completed_tasks:
        user.completed_tasks.append(request.task_id)
        user.total_xp += request.amount
        user.current_month_xp += request.amount
        user.hp = min(100, user.hp + 5)  # небольшое восстановление HP
        add_to_history(db, username, 'gain', request.task_name, request.amount)
        db.commit()
        logger.info(f"User {username} completed task {request.task_id}, gained {request.amount} XP")
    # Иначе игнорируем (задача уже выполнена)
    return build_hero_response(user, db)

@app.post("/update_habits/{username}", response_model=HeroResponse)
def update_habits(username: str, payload: HabitsPayload, db: Session = Depends(get_db)):
    """Обновить строку с пользовательскими привычками."""
    user = get_user_or_create(username, db)
    user.custom_habits = payload.habits
    db.commit()
    return build_hero_response(user, db)

# Запуск: uvicorn main:app --reload