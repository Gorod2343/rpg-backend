from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timezone, timedelta, date
import hmac, hashlib, urllib.parse, json

BOT_TOKEN = "8687814579:AAEcbEDRyItXDUVW3UNJd-x4vBzYrJKHTgQ"
DATABASE_URL = "postgresql://neondb_owner:npg_StR2P5YvqGHg@ep-soft-bread-ai33v924-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserProfile(Base):
    __tablename__ = "users_final_v12" # Новая таблица для чистого теста
    username = Column(String, primary_key=True)
    total_xp = Column(Integer, default=0); current_month_xp = Column(Integer, default=0)
    hp = Column(Integer, default=100); water_count = Column(Integer, default=0)
    water_goal = Column(Integer, default=8); sleep_start = Column(String, default="")
    custom_habits = Column(String, default=""); completed_tasks = Column(String, default="")
    last_active_date = Column(String, default=""); streak = Column(Integer, default=0)

Base.metadata.create_all(bind=engine)
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def verify(d):
    if not d: return False
    try:
        v = dict(urllib.parse.parse_qsl(d))
        h = v.pop('hash')
        s = "\n".join([f"{k}={v[k]}" for k in sorted(v.keys())])
        sk = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        return hmac.new(sk, s.encode(), hashlib.sha256).hexdigest() == h
    except: return False

@app.get("/get_hero/{u}")
def get_hero(u: str, x_tg_data: str = Header(None)):
    db = SessionLocal(); user = db.query(UserProfile).filter(UserProfile.username == u).first()
    if not user: user = UserProfile(username=u, last_active_date=str(date.today())); db.add(user); db.commit()
    if user.last_active_date != str(date.today()): user.water_count = 0; user.completed_tasks = ""; user.last_active_date = str(date.today()); db.commit()
    res = {"total_xp":user.total_xp,"current_month_xp":user.current_month_xp,"hp":user.hp,"water_count":user.water_count,"water_goal":user.water_goal,"sleep_start":user.sleep_start,"custom_habits":user.custom_habits,"completed_tasks":user.completed_tasks,"streak":user.streak}
    db.close(); return res

@app.post("/set_water_goal/{u}")
def set_goal(u: str, goal: int, x_tg_data: str = Header(None)):
    if not verify(x_tg_data): raise HTTPException(401)
    db = SessionLocal(); user = db.query(UserProfile).filter(UserProfile.username == u).first()
    if user: user.water_goal = goal; db.commit()
    db.close(); return get_hero(u, x_tg_data)

@app.post("/sleep_action/{u}")
def sleep(u: str, tz: int = 0, x_tg_data: str = Header(None)):
    if not verify(x_tg_data): raise HTTPException(401)
    db = SessionLocal(); user = db.query(UserProfile).filter(UserProfile.username == u).first()
    if not user.sleep_start: user.sleep_start = datetime.now(timezone.utc).isoformat()
    else:
        dur = (datetime.now(timezone.utc) - datetime.fromisoformat(user.sleep_start.replace("Z","+00:00"))).total_seconds()/3600
        if dur > 0.5: user.total_xp += 50; user.current_month_xp += 50
        user.sleep_start = ""
    db.commit(); db.close(); return get_hero(u, x_tg_data)

@app.post("/drink_water/{u}")
def drink(u: str, x_tg_data: str = Header(None)):
    if not verify(x_tg_data): raise HTTPException(401)
    db = SessionLocal(); user = db.query(UserProfile).filter(UserProfile.username == u).first()
    if user.water_count < user.water_goal: user.water_count += 1; user.total_xp += 5; user.current_month_xp += 5; db.commit()
    db.close(); return get_hero(u, x_tg_data)
