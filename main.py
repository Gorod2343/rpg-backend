from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timezone, timedelta, date
import hmac
import hashlib
import urllib.parse
import json

# ТВОЙ ТОКЕН УЖЕ ЗДЕСЬ
BOT_TOKEN = "8687814579:AAEcbEDRyItXDUVW3UNJd-x4vBzYrJKHTgQ" 

DATABASE_URL = "postgresql://neondb_owner:npg_StR2P5YvqGHg@ep-soft-bread-ai33v924-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserProfile(Base):
    __tablename__ = "users_final_v10" # Новая версия для стриков
    username = Column(String, primary_key=True, index=True)
    total_xp = Column(Integer, default=0)
    current_month_xp = Column(Integer, default=0)
    hp = Column(Integer, default=100)
    last_active_date = Column(String, default="")
    water_count = Column(Integer, default=0)
    water_goal = Column(Integer, default=8)
    completed_tasks = Column(String, default="")
    sleep_start = Column(String, default="") 
    custom_habits = Column(String, default="")
    streak = Column(Integer, default=0) # СЧЕТЧИК ОГОНЬКОВ
    last_streak_date = Column(String, default="") # ДАТА ПОСЛЕДНЕГО ОГОНЬКА

class History(Base):
    __tablename__ = "history_v8"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    event_type = Column(String)
    description = Column(String)
    amount = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class HabitsPayload(BaseModel):
    habits: str

DEFAULT_HABITS = [
    {"id": "task-run", "cat": "sport", "tag": "Кардио", "name": "Пробежка", "xp": 150},
    {"id": "task-strength", "cat": "sport", "tag": "Сила", "name": "Силовая тренировка", "xp": 200},
    {"id": "task-cardio", "cat": "sport", "tag": "Выносливость", "name": "Кардио сессия", "xp": 150},
    {"id": "task-hiit", "cat": "sport", "tag": "Интенсив", "name": "ВИИТ", "xp": 200},
    {"id": "task-family-time", "cat": "family", "tag": "Связь", "name": "Время с семьей", "xp": 100}
]

def verify_tg_data(init_data: str, expected_user_id: str):
    if not init_data: raise HTTPException(status_code=401, detail="Запустите через Telegram!")
    vals = dict(urllib.parse.parse_qsl(init_data))
    hash_val = vals.pop("hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(vals.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if calc_hash != hash_val: raise HTTPException(status_code=401, detail="Ошибка безопасности!")
    user_data = json.loads(vals.get("user", "{}"))
    if str(user_data.get("id")) != expected_user_id: raise HTTPException(status_code=401, detail="ID mismatch")

def add_to_history(db, username, e_type, desc, amt):
    event = History(username=username, event_type=e_type, description=desc, amount=amt)
    db.add(event)

def process_streak(user, db):
    today = date.today()
    yesterday = today - timedelta(days=1)
    
    if user.last_streak_date:
        last_date = datetime.strptime(user.last_streak_date, "%Y-%m-%d").date()
        if last_date < yesterday:
            user.streak = 0 # Стрик сгорел, если пропустил больше дня
    
    # Если норма воды выполнена сегодня и мы еще не обновляли стрик сегодня
    if user.water_count >= user.water_goal and user.last_streak_date != str(today):
        if not user.last_streak_date or datetime.strptime(user.last_streak_date, "%Y-%m-%d").date() == yesterday:
            user.streak += 1
        else:
            user.streak = 1
        user.last_streak_date = str(today)

@app.get("/get_hero/{username}")
def get_hero(username: str, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if not user:
        user = UserProfile(username=username, last_active_date=str(date.today()))
        db.add(user)
    else:
        # Сброс воды каждый день
        today_str = str(date.today())
        if user.last_active_date != today_str:
            user.water_count = 0
            user.completed_tasks = ""
            user.last_active_date = today_str
    
    process_streak(user, db)
    db.commit()
    
    hist = db.query(History).filter(History.username == username).order_by(desc(History.timestamp)).limit(20).all()
    hist_data = [{"type": h.event_type, "desc": h.description, "amt": h.amount, "time": h.timestamp.strftime("%H:%M")} for h in hist]
    res = { "total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "hp": user.hp, "water_count": user.water_count, "water_goal": user.water_goal, "completed_tasks": user.completed_tasks, "sleep_start": user.sleep_start, "custom_habits": user.custom_habits, "streak": user.streak, "history": hist_data }
    db.close()
    return res

@app.post("/drink_water/{username}")
def drink_water(username: str, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user.water_count < user.water_goal:
        user.water_count += 1
        user.total_xp += 5; user.current_month_xp += 5
        add_to_history(db, username, 'gain', f'Вода {user.water_count}/{user.water_goal}', 5)
        process_streak(user, db)
        db.commit()
    db.close()
    return get_hero(username=username, x_tg_data=x_tg_data)

@app.post("/add_xp/{username}")
def add_xp(username: str, task_id: str, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    habits = json.loads(user.custom_habits) if user.custom_habits else DEFAULT_HABITS
    task = next((h for h in habits if h["id"] == task_id), None)
    if task and task_id not in (user.completed_tasks or "").split(","):
        user.completed_tasks = f"{user.completed_tasks},{task_id}" if user.completed_tasks else task_id
        user.total_xp += task["xp"]; user.current_month_xp += task["xp"]
        add_to_history(db, user.username, 'gain', task["name"], task["xp"])
        db.commit()
    db.close()
    return get_hero(username=username, x_tg_data=x_tg_data)

@app.post("/update_habits/{username}")
def update_habits(username: str, payload: HabitsPayload, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user: user.custom_habits = payload.habits; db.commit()
    db.close()
    return get_hero(username=username, x_tg_data=x_tg_data)

@app.post("/buy_reward/{username}")
def buy_reward(username: str, reward_id: str, qty: int = 1, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    prices = {"baton": 100, "soda": 150, "fast": 600}
    total_cost = prices.get(reward_id, 999999) * qty
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user.current_month_xp >= total_cost:
        user.current_month_xp -= total_cost
        add_to_history(db, username, 'spend', f'Покупка {reward_id}', total_cost)
        db.commit()
    db.close()
    return get_hero(username=username, x_tg_data=x_tg_data)

@app.post("/set_water_goal/{username}")
def set_water_goal(username: str, goal: int, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user: user.water_goal = goal; db.commit()
    db.close()
    return get_hero(username=username, x_tg_data=x_tg_data)
