from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timezone, timedelta, date
import hmac, hashlib, urllib.parse, json

BOT_TOKEN = "8687814579:AAEcbEDRyItXDUVW3UNJd-x4vBzYrJKHTgQ"
DATABASE_URL = "postgresql://neondb_owner:npg_StR2P5YvqGHg@ep-soft-bread-ai33v924-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require"
engine = create_engine(DATABASE_URL); SessionLocal = sessionmaker(bind=engine); Base = declarative_base()

class User(Base):
    __tablename__ = "users_v13"
    u = Column(String, primary_key=True); xp = Column(Integer, default=0); mxp = Column(Integer, default=0)
    hp = Column(Integer, default=100); wc = Column(Integer, default=0); wg = Column(Integer, default=8)
    ss = Column(String, default=""); ch = Column(String, default=""); ct = Column(String, default="")
    la = Column(String, default=""); sk = Column(Integer, default=0)

Base.metadata.create_all(bind=engine); app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def vr(d):
    try:
        v = dict(urllib.parse.parse_qsl(d)); h = v.pop('hash')
        s = "\n".join([f"{k}={v[k]}" for k in sorted(v.keys())])
        sk = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        return hmac.new(sk, s.encode(), hashlib.sha256).hexdigest() == h
    except: return False

@app.get("/get_hero/{u}")
def gh(u: str, x_tg_data: str = Header(None)):
    db = SessionLocal(); usr = db.query(User).filter(User.u == u).first()
    if not usr: usr = User(u=u, la=str(date.today())); db.add(usr); db.commit()
    if usr.la != str(date.today()): usr.wc = 0; usr.ct = ""; usr.la = str(date.today()); db.commit()
    res = {"xp":usr.xp,"mxp":usr.mxp,"hp":usr.hp,"wc":usr.wc,"wg":usr.wg,"ss":usr.ss,"ch":usr.ch,"ct":usr.ct,"sk":usr.sk}
    db.close(); return res

@app.post("/set_water_goal/{u}")
def sw(u: str, goal: int, x_tg_data: str = Header(None)):
    if not vr(x_tg_data): raise HTTPException(401)
    db = SessionLocal(); usr = db.query(User).filter(User.u == u).first()
    if usr: usr.wg = goal; db.commit()
    db.close(); return gh(u, x_tg_data)

@app.post("/sleep_action/{u}")
def sl(u: str, x_tg_data: str = Header(None)):
    if not vr(x_tg_data): raise HTTPException(401)
    db = SessionLocal(); usr = db.query(User).filter(User.u == u).first()
    if not usr.ss: usr.ss = datetime.now(timezone.utc).isoformat()
    else:
        try:
            d = (datetime.now(timezone.utc) - datetime.fromisoformat(usr.ss.replace("Z","+00:00"))).total_seconds()/3600
            if d > 0.5: usr.xp += 50; usr.mxp += 50
            usr.ss = ""
        except: usr.ss = ""
    db.commit(); db.close(); return gh(u, x_tg_data)

@app.post("/drink_water/{u}")
def dw(u: str, x_tg_data: str = Header(None)):
    if not vr(x_tg_data): raise HTTPException(401)
    db = SessionLocal(); usr = db.query(User).filter(User.u == u).first()
    if usr.wc < usr.wg: usr.wc += 1; usr.xp += 5; usr.mxp += 5; db.commit()
    db.close(); return gh(u, x_tg_data)
