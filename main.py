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

BOT_TOKEN = "8687814579:AAEcbEDRyItXDUVW3UNJd-x4vBzYrJKHTgQ" 

DATABASE_URL = "postgresql://neondb_owner:npg_StR2P5YvqGHg@ep-soft-bread-ai33v924-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserProfile(Base):
    __tablename__ = "users_final_v11"
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
    streak = Column(Integer, default=0)
    last_streak_date = Column(String, default="")

class History(Base):
    __tablename__ = "history_v9"
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

def verify_tg_data(init_data: str):
    if not init_data: return False
    try:
        vals = dict(urllib.parse.parse_qsl(init_data))
        auth_hash = vals.pop('hash')
        data_check_string = "\n".join([f"{k}={vals[k]}" for k in sorted(vals.keys())])
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return expected_hash == auth_hash
    except: return False

@app.get("/get_hero/{username}")
def get_hero(username: str, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if not user:
        user = UserProfile(username=username, last_active_date=str(date.today()))
        db.add(user)
        db.commit()
    today_str = str(date.today())
    if user.last_active_date != today_str:
        user.water_count = 0
        user.completed_tasks = ""
        user.last_active_date = today_str
        db.commit()
    hist = db.query(History).filter(History.username == username).order_by(desc(History.timestamp)).limit(20).all()
    hist_data = [{"type": h.event_type, "desc": h.description, "amt": h.amount, "time": h.timestamp.strftime("%H:%M")} for h in hist]
    res = { "total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "hp": user.hp, "water_count": user.water_count, "water_goal": user.water_goal, "completed_tasks": user.completed_tasks, "sleep_start": user.sleep_start, "custom_habits": user.custom_habits, "streak": user.streak, "history": hist_data }
    db.close()
    return res

@app.post("/add_xp/{username}")
def add_xp(username: str, task_id: str, x_tg_data: str = Header(None)):
    if not verify_tg_data(x_tg_data): raise HTTPException(status_code=401)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    # Восстанавливаем поиск реального XP задачи
    default_habits = [
        {"id": "task-run", "name": "Пробежка", "xp": 150},
        {"id": "task-strength", "name": "Силовая тренировка", "xp": 200},
        {"id": "task-family-time", "name": "Время с семьей", "xp": 100}
    ]
    habits = json.loads(user.custom_habits) if user.custom_habits else default_habits
    task = next((h for h in habits if h["id"] == task_id), None)
    
    if task and task_id not in (user.completed_tasks or "").split(","):
        user.completed_tasks = f"{user.completed_tasks},{task_id}" if user.completed_tasks else task_id
        user.total_xp += task["xp"]
        user.current_month_xp += task["xp"]
        db.add(History(username=username, event_type='gain', description=task["name"], amount=task["xp"]))
        db.commit()
    db.close()
    return get_hero(username, x_tg_data)

@app.post("/drink_water/{username}")
def drink_water(username: str, x_tg_data: str = Header(None)):
    if not verify_tg_data(x_tg_data): raise HTTPException(status_code=401)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user and user.water_count < user.water_goal:
        user.water_count += 1
        user.total_xp += 5; user.current_month_xp += 5
        today = date.today(); yesterday = today - timedelta(days=1)
        if user.water_count >= user.water_goal:
            if user.last_streak_date == str(yesterday): user.streak += 1
            elif user.last_streak_date != str(today): user.streak = 1
            user.last_streak_date = str(today)
        db.add(History(username=username, event_type='gain', description=f'Вода {user.water_count}/{user.water_goal}', amount=5))
        db.commit()
    db.close()
    return get_hero(username, x_tg_data)

@app.post("/update_habits/{username}")
def update_habits(username: str, payload: HabitsPayload, x_tg_data: str = Header(None)):
    if not verify_tg_data(x_tg_data): raise HTTPException(status_code=401)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user: user.custom_habits = payload.habits; db.commit()
    db.close()
    return get_hero(username, x_tg_data)

@app.post("/set_water_goal/{username}")
def set_water_goal(username: str, goal: int, x_tg_data: str = Header(None)):
    if not verify_tg_data(x_tg_data): raise HTTPException(status_code=401)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user: user.water_goal = goal; db.commit()
    db.close()
    return get_hero(username, x_tg_data)
