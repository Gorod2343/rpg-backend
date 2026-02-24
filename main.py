from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, declarative_base

# ВСТАВЬ СВОЮ ССЫЛКУ ОТ NEON МЕЖДУ КАВЫЧЕК!
DATABASE_URL = "postgresql://neondb_owner:npg_StR2P5YvqGHg@ep-soft-bread-ai33v924-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# Настройка подключения к базе
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Создаем структуру таблицы пользователей
class User(Base):
    __tablename__ = "users"
    username = Column(String, primary_key=True, index=True)
    xp = Column(Integer, default=450)
    level = Column(Integer, default=12)

# Создаем таблицу в базе данных (если ее еще нет)
Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "Сервер работает с вечной базой данных Neon!"}

@app.get("/get_hero/{username}")
def get_hero(username: str):
    db = SessionLocal()
    # Ищем героя в базе
    user = db.query(User).filter(User.username == username).first()
    if not user:
        # Если нет, создаем нового с твоими текущими статами
        user = User(username=username, xp=450, level=12)
        db.add(user)
        db.commit()
        db.refresh(user)
    
    xp = user.xp
    level = user.level
    db.close()
    return {"xp": xp, "level": level}

@app.post("/add_xp/{username}")
def add_xp(username: str, amount: int):
    db = SessionLocal()
    user = db.query(User).filter(User.username == username).first()
    if not user:
        user = User(username=username, xp=450, level=12)
        db.add(user)
    
    user.xp += amount
    
    if user.xp >= 1000:
        user.level += 1
        user.xp -= 1000
        
    db.commit()
    db.refresh(user)
    
    xp = user.xp
    level = user.level
    db.close()
    
    return {"xp": xp, "level": level}
