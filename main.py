from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timezone, timedelta

# Подключение к твоей базе данных Neon
DATABASE_URL = "postgresql://neondb_owner:npg_StR2P5YvqGHg@ep-soft-bread-ai33v924-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Модель профиля (используем твою рабочую таблицу v9)
class UserProfile(Base):
    __tablename__ = "users_final_v9"
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

# Модель истории событий
class History(Base):
    __tablename__ = "history_v7"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    event_type = Column(String)
    description = Column(String)
    amount = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

class HabitsPayload(BaseModel):
    habits: str

def add_to_history(db, username, e_type, desc, amt):
    event = History(username=username, event_type=e_type, description=desc, amount=amt)
    db.add(event)

def get_today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# Функция ежедневного обновления (сбрасывает только прогресс, но не цель)
def process_daily_updates(user, db):
    today = get_today_str()
    if user.last_active_date != today:
        if user.last_active_date:
            try:
                last_date = datetime.strptime(user.last_active_date, "%Y-%m-%d").date()
                days_missed = (datetime.now(timezone.utc).date() - last_date).days
                if days_missed > 0:
                    loss = days_missed * 15
                    user.hp = max(0, user.hp - loss)
                    add_to_history(db, user.username, 'spend', f'Пропуск ({days_missed} дн.)', loss)
            except:
                pass
        # Сбрасываем только выпитое за сегодня и список задач. water_goal ОСТАЕТСЯ.
        user.last_active_date = today
        user.water_count = 0
        user.completed_tasks = ""

@app.get("/get_hero/{username}")
def get_hero(username: str):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if not user:
        user = UserProfile(username=username, hp=100, last_active_date=get_today_str(), water_goal=8)
        db.add(user)
        db.commit()
    else:
        process_daily_updates(user, db)
        db.commit()
    
    hist = db.query(History).filter(History.username == username).order_by(desc(History.timestamp)).limit(20).all()
    hist_data = [{"type": h.event_type, "desc": h.description, "amt": h.amount, "time": h.timestamp.strftime("%H:%M")} for h in hist]
    
    res = {
        "total_xp": user.total_xp,
        "current_month_xp": user.current_month_xp, 
        "hp": user.hp,
        "water_count": user.water_count,
        "water_goal": user.water_goal,
        "completed_tasks": user.completed_tasks,
        "sleep_start": user.sleep_start, 
        "custom_habits": user.custom_habits,
        "history": hist_data
    }
    db.close()
    return res

@app.post("/set_water_goal/{username}")
def set_water_goal(username: str, goal: int):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user:
        user.water_goal = goal
        add_to_history(db, username, 'gain', f'Новая цель: {goal} ст.', 0)
        db.commit()
    db.close()
    return get_hero(username)

@app.post("/sleep_action/{username}")
def sleep_action(username: str, tz: int = 0):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    
    if not user.sleep_start:
        user.sleep_start = datetime.now(timezone.utc).isoformat()
        db.commit()
        res = get_hero(username)
    else:
        try:
            start_str = user.sleep_start
            start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            
            end_time = datetime.now(timezone.utc)
            duration_hours = (end_time - start_time).total_seconds() / 3600.0
            
            if duration_hours < 0.5:
                user.sleep_start = ""
                db.commit()
                res = get_hero(username)
                res["sleep_report"] = "⏳ Сон отменен. Вы спали слишком мало."
                return res

            final_xp = 50
            user.total_xp += final_xp
            user.current_month_xp += final_xp
            user.hp = min(100, user.hp + 15)
            add_to_history(db, username, 'gain', f'Сон ({round(duration_hours, 1)}ч)', final_xp)
            user.sleep_start = ""
            db.commit()
            res = get_hero(username)
        except Exception:
            user.sleep_start = ""
            db.commit()
            res = get_hero(username)
    db.close()
    return res

@app.post("/drink_water/{username}")
def drink_water(username: str):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user.water_count < user.water_goal:
        user.water_count += 1
        gain = 5
        user.total_xp += gain
        user.current_month_xp += gain
        user.hp = min(100, user.hp + 5)
        add_to_history(db, username, 'gain', f'Вода {user.water_count}/{user.water_goal}', gain)
        db.commit()
    db.close()
    return get_hero(username)

@app.post("/add_xp/{username}")
def add_xp(username: str, amount: int, task_id: str, task_name: str):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    tasks = (user.completed_tasks or "").split(",")
    if task_id not in tasks:
        new_tasks = f"{user.completed_tasks},{task_id}" if user.completed_tasks else task_id
        user.completed_tasks = new_tasks
        user.total_xp += amount
        user.current_month_xp += amount
        user.hp = min(100, user.hp + 5)
        add_to_history(db, username, 'gain', task_name, amount)
        db.commit()
    db.close()
    return get_hero(username)

@app.post("/update_habits/{username}")
def update_habits(username: str, payload: HabitsPayload):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user:
        user.custom_habits = payload.habits
        db.commit()
    db.close()
    return get_hero(username)
