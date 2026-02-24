import os
import json
import sqlite3
from datetime import datetime, date, timedelta
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import hmac
import hashlib

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "rpg_database.db"
BOT_TOKEN = "ТВОЙ_ТОКЕН_БОТА" # ОСТАВЬ СВОЙ ТОКЕН ТУТ

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            total_xp INTEGER DEFAULT 0,
            current_month_xp INTEGER DEFAULT 0,
            hp INTEGER DEFAULT 100,
            water_count INTEGER DEFAULT 0,
            water_goal INTEGER DEFAULT 8,
            sleep_start TEXT,
            history TEXT DEFAULT '[]',
            completed_tasks TEXT DEFAULT '',
            custom_habits TEXT DEFAULT '',
            streak INTEGER DEFAULT 0,
            last_streak_date TEXT
        )''')
init_db()

def verify_tg_data(init_data: str):
    if not init_data: return False
    try:
        vals = {k: v for k, v in [s.split('=') for s in init_data.split('&')]}
        hash_msg = vals.pop('hash')
        data_check_string = "\n".join([f"{k}={vals[k]}" for k in sorted(vals.keys())])
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return h == hash_msg
    except: return False

def update_streak(user_id, conn):
    user = conn.execute("SELECT water_count, water_goal, streak, last_streak_date FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user: return
    
    today = date.today()
    yesterday = today - timedelta(days=1)
    last_date_str = user['last_streak_date']
    current_streak = user['streak']
    
    # Если норма воды выполнена
    if user['water_count'] >= user['water_goal']:
        if last_date_str:
            last_date = datetime.strptime(last_date_str, '%Y-%m-%d').date()
            if last_date == yesterday:
                current_streak += 1
                conn.execute("UPDATE users SET streak = ?, last_streak_date = ? WHERE user_id = ?", (current_streak, today.isoformat(), user_id))
            elif last_date < yesterday:
                conn.execute("UPDATE users SET streak = 1, last_streak_date = ? WHERE user_id = ?", (today.isoformat(), user_id))
        else:
            conn.execute("UPDATE users SET streak = 1, last_streak_date = ? WHERE user_id = ?", (today.isoformat(), user_id))

@app.get("/get_hero/{user_id}")
async def get_hero(user_id: str, x_tg_data: str = Header(None)):
    if not verify_tg_data(x_tg_data): raise HTTPException(status_code=403)
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not user:
            conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        
        # Проверка на сгорание стрика (если вчера не заходил и не пил)
        today = date.today()
        yesterday = today - timedelta(days=1)
        if user['last_streak_date']:
            last_date = datetime.strptime(user['last_streak_date'], '%Y-%m-%d').date()
            if last_date < yesterday:
                conn.execute("UPDATE users SET streak = 0 WHERE user_id = ?", (user_id,))
                
        res = dict(user)
        res['history'] = json.loads(user['history'])
        return res

@app.post("/drink_water/{user_id}")
async def drink_water(user_id: str, x_tg_data: str = Header(None)):
    if not verify_tg_data(x_tg_data): raise HTTPException(status_code=403)
    with get_db() as conn:
        conn.execute("UPDATE users SET water_count = water_count + 1 WHERE user_id = ?", (user_id,))
        update_streak(user_id, conn) # Проверяем стрик при каждом глотке
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return {**dict(user), "history": json.loads(user['history'])}

# ... (оставь остальные методы add_xp, sleep_action, update_habits как были в прошлом коде)
