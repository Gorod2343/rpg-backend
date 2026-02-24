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

# Создаем новую таблицу для ежемесячной статистики
class UserMonth(Base):
    __tablename__ = "users_monthly"
    username = Column(String, primary_key=True, index=True)
    total_xp = Column(Integer, default=0)
    current_month_xp = Column(Integer, default=0)
    last_month_xp = Column(Integer, default=0)
    current_month = Column(String, default="")

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Функция для получения текущего месяца (например, "2026-02")
def get_current_month_str():
    return datetime.now().strftime("%Y-%m")

@app.get("/")
def read_root():
    return {"status": "Сервер работает в режиме 'Ежемесячных сезонов'!"}

@app.get("/get_hero/{username}")
def get_hero(username: str):
    db = SessionLocal()
    user = db.query(UserMonth).filter(UserMonth.username == username).first()
    current_month = get_current_month_str()
    
    if not user:
        user = UserMonth(username=username, total_xp=0, current_month_xp=0, last_month_xp=0, current_month=current_month)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif user.current_month != current_month:
        # МАГИЯ: Наступил новый месяц! Сохраняем результат и обнуляем счетчик
        user.last_month_xp = user.current_month_xp
        user.current_month_xp = 0
        user.current_month = current_month
        db.commit()
        db.refresh(user)
        
    data = {"total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "last_month_xp": user.last_month_xp}
    db.close()
    return data

@app.post("/add_xp/{username}")
def add_xp(username: str, amount: int):
    db = SessionLocal()
    user = db.query(UserMonth).filter(UserMonth.username == username).first()
    current_month = get_current_month_str()
    
    if not user:
        user = UserMonth(username=username, total_xp=0, current_month_xp=0, last_month_xp=0, current_month=current_month)
        db.add(user)
    elif user.current_month != current_month:
        user.last_month_xp = user.current_month_xp
        user.current_month_xp = 0
        user.current_month = current_month
    
    user.total_xp += amount
    user.current_month_xp += amount
    
    db.commit()
    db.refresh(user)
    
    data = {"total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "last_month_xp": user.last_month_xp}
    db.close()
    return data
