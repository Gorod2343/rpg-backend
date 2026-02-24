from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

# ВСТАВЬ СВОЮ ССЫЛКУ ОТ NEON МЕЖДУ КАВЫЧЕК!
DATABASE_URL = "postgresql://neondb_owner:npg_StR2P5YvqGHg@ep-soft-bread-ai33v924-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserProfile(Base):
    __tablename__ = "users_final_v2"
    username = Column(String, primary_key=True, index=True)
    total_xp = Column(Integer, default=0)
    current_month_xp = Column(Integer, default=0)
    last_month_xp = Column(Integer, default=0)
    current_month = Column(String, default="")
    hp = Column(Integer, default=100)
    last_active_date = Column(String, default="")
    water_count = Column(Integer, default=0)
    completed_tasks = Column(String, default="")

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_current_month_str():
    return datetime.now().strftime("%Y-%m")

def get_today_str():
    return datetime.now().strftime("%Y-%m-%d")

def process_daily_updates(user):
    today = get_today_str()
    if not user.last_active_date:
        user.last_active_date = today
        return
    
    if user.last_active_date != today:
        try:
            last_date = datetime.strptime(user.last_active_date, "%Y-%m-%d").date()
            curr_date = datetime.strptime(today, "%Y-%m-%d").date()
            days_missed = (curr_date - last_date).days
            if days_missed > 0:
                user.hp -= days_missed * 15 
                if user.hp < 0: user.hp = 0
        except: pass
        user.last_active_date = today
        user.water_count = 0 
        user.completed_tasks = ""

@app.get("/get_hero/{username}")
def get_hero(username: str):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    current_month = get_current_month_str()
    if not user:
        user = UserProfile(username=username, hp=100, last_active_date=get_today_str(), current_month=current_month)
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if user.current_month != current_month:
            user.last_month_xp = user.current_month_xp
            user.current_month_xp = 0
            user.current_month = current_month
        process_daily_updates(user)
        db.commit()
        db.refresh(user)
    
    res = {"total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "last_month_xp": user.last_month_xp, "hp": user.hp, "water_count": user.water_count, "completed_tasks": user.completed_tasks}
    db.close()
    return res

@app.post("/buy_reward/{username}")
def buy_reward(username: str, cost: int):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.current_month_xp < cost:
        db.close()
        return {"error": "Недостаточно очков в этом месяце!"}
    
    user.current_month_xp -= cost
    db.commit()
    db.refresh(user)
    res = {"total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "last_month_xp": user.last_month_xp, "hp": user.hp, "water_count": user.water_count, "completed_tasks": user.completed_tasks}
    db.close()
    return res

@app.post("/drink_water/{username}")
def drink_water(username: str):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    process_daily_updates(user)
    if user.water_count < 8:
        user.water_count += 1
        gain = 5 if user.hp >= 30 else 2
        user.total_xp += gain
        user.current_month_xp += gain
        user.hp = min(100, user.hp + 5)
    db.commit()
    res = {"total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "last_month_xp": user.last_month_xp, "hp": user.hp, "water_count": user.water_count, "completed_tasks": user.completed_tasks}
    db.close()
    return res

@app.post("/add_xp/{username}")
def add_xp(username: str, amount: int, task_id: str = ""):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    process_daily_updates(user)
    tasks = user.completed_tasks.split(",") if user.completed_tasks else []
    if not (task_id and task_id in tasks):
        if task_id:
            tasks.append(task_id)
            user.completed_tasks = ",".join(tasks)
        gain = amount if user.hp >= 30 else amount // 2
        user.total_xp += gain
        user.current_month_xp += gain
        user.hp = min(100, user.hp + 5)
    db.commit()
    res = {"total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "last_month_xp": user.last_month_xp, "hp": user.hp, "water_count": user.water_count, "completed_tasks": user.completed_tasks}
    db.close()
    return res
