from fastapi import FastAPI
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
    __tablename__ = "users_with_hp_v1"
    username = Column(String, primary_key=True, index=True)
    total_xp = Column(Integer, default=0)
    current_month_xp = Column(Integer, default=0)
    last_month_xp = Column(Integer, default=0)
    current_month = Column(String, default="")
    hp = Column(Integer, default=100)
    last_active_date = Column(String, default="")

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

def process_daily_hp(user):
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
                if user.hp < 0:
                    user.hp = 0
        except Exception:
            pass
        user.last_active_date = today

@app.get("/")
def read_root():
    return {"status": "Сервер работает! Дебафф Истощения активирован!"}

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
        
        process_daily_hp(user)
        db.commit()
        db.refresh(user)
        
    data = {"total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "last_month_xp": user.last_month_xp, "hp": user.hp}
    db.close()
    return data

@app.post("/add_xp/{username}")
def add_xp(username: str, amount: int):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    current_month = get_current_month_str()
    
    if not user:
        user = UserProfile(username=username, hp=100, last_active_date=get_today_str(), current_month=current_month)
        db.add(user)
    else:
        if user.current_month != current_month:
            user.last_month_xp = user.current_month_xp
            user.current_month_xp = 0
            user.current_month = current_month
        process_daily_hp(user)
    
    # === МАГИЯ ДЕБАФФА ===
    # Если Здоровье меньше 30, режем получаемый опыт пополам!
    actual_amount = amount
    if user.hp < 30:
        actual_amount = amount // 2
        
    user.total_xp += actual_amount
    user.current_month_xp += actual_amount
    
    # ЛЕЧЕНИЕ: +5 HP за каждое выполненное задание
    user.hp += 5
    if user.hp > 100:
        user.hp = 100
        
    user.last_active_date = get_today_str() 
    
    db.commit()
    db.refresh(user)
    
    data = {"total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "last_month_xp": user.last_month_xp, "hp": user.hp}
    db.close()
    return data
