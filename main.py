from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, desc
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from datetime import datetime

# ВСТАВЬ СВОЮ ССЫЛКУ ОТ NEON МЕЖДУ КАВЫЧЕК!
DATABASE_URL = "postgresql://neondb_owner:npg_StR2P5YvqGHg@ep-soft-bread-ai33v924-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserProfile(Base):
    __tablename__ = "users_final_v3"
    username = Column(String, primary_key=True, index=True)
    total_xp = Column(Integer, default=0)
    current_month_xp = Column(Integer, default=0)
    last_month_xp = Column(Integer, default=0)
    current_month = Column(String, default="")
    hp = Column(Integer, default=100)
    last_active_date = Column(String, default="")
    water_count = Column(Integer, default=0)
    completed_tasks = Column(String, default="")

class History(Base):
    __tablename__ = "history"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    event_type = Column(String) # 'gain' или 'spend'
    description = Column(String)
    amount = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def add_to_history(db, username, e_type, desc, amt):
    event = History(username=username, event_type=e_type, description=desc, amount=amt)
    db.add(event)

def get_current_month_str(): return datetime.now().strftime("%Y-%m")
def get_today_str(): return datetime.now().strftime("%Y-%m-%d")

def process_daily_updates(user, db):
    today = get_today_str()
    if user.last_active_date != today:
        if user.last_active_date:
            try:
                last_date = datetime.strptime(user.last_active_date, "%Y-%m-%d").date()
                days_missed = (datetime.now().date() - last_date).days
                if days_missed > 0:
                    loss = days_missed * 15
                    user.hp = max(0, user.hp - loss)
                    add_to_history(db, user.username, 'spend', f'Пропуск дней ({days_missed} дн.)', loss)
            except: pass
        user.last_active_date = today
        user.water_count = 0 
        user.completed_tasks = ""

@app.get("/get_hero/{username}")
def get_hero(username: str):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    cur_m = get_current_month_str()
    if not user:
        user = UserProfile(username=username, hp=100, last_active_date=get_today_str(), current_month=cur_m)
        db.add(user)
        db.commit()
    else:
        if user.current_month != cur_m:
            user.last_month_xp, user.current_month_xp, user.current_month = user.current_month_xp, 0, cur_m
        process_daily_updates(user, db)
        db.commit()
    
    # Берем последние 20 записей истории
    hist = db.query(History).filter(History.username == username).order_by(desc(History.timestamp)).limit(20).all()
    hist_data = [{"type": h.event_type, "desc": h.description, "amt": h.amount, "time": h.timestamp.strftime("%H:%M")} for h in hist]
    
    res = {"total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "hp": user.hp, "water_count": user.water_count, "completed_tasks": user.completed_tasks, "history": hist_data}
    db.close()
    return res

@app.post("/buy_reward/{username}")
def buy_reward(username: str, cost: int, name: str, qty: int = 1):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    total_cost = cost * qty
    if user.current_month_xp < total_cost:
        db.close()
        return {"error": f"Нужно {total_cost} XP!"}
    
    user.current_month_xp -= total_cost
    add_to_history(db, username, 'spend', f'Куплено: {name} (x{qty})', total_cost)
    db.commit()
    db.refresh(user)
    db.close()
    return get_hero(username)

@app.post("/add_xp/{username}")
def add_xp(username: str, amount: int, task_id: str, task_name: str):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    tasks = user.completed_tasks.split(",") if user.completed_tasks else []
    if task_id not in tasks:
        tasks.append(task_id)
        user.completed_tasks = ",".join(tasks)
        gain = amount if user.hp >= 30 else amount // 2
        user.total_xp += gain
        user.current_month_xp += gain
        user.hp = min(100, user.hp + 5)
        add_to_history(db, username, 'gain', task_name, gain)
    db.commit()
    db.close()
    return get_hero(username)

@app.post("/drink_water/{username}")
def drink_water(username: str):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user.water_count < 8:
        user.water_count += 1
        gain = 5 if user.hp >= 30 else 2
        user.total_xp += gain
        user.current_month_xp += gain
        user.hp = min(100, user.hp + 5)
        add_to_history(db, username, 'gain', f'Стакан воды ({user.water_count}/8)', gain)
    db.commit()
    db.close()
    return get_hero(username)
