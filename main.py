from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timezone, timedelta
import hmac
import hashlib
import urllib.parse
import json

# ==========================================
# ТОКЕН ТВОЕГО БОТА (ДЛЯ АНТИ-ЧИТА)
BOT_TOKEN = "8687814579:AAEcbEDRyItXDUVW3UNJd-x4vBzYrJKHTgQ" 
# ==========================================

# БАЗА ДАННЫХ NEON
DATABASE_URL = "postgresql://neondb_owner:npg_StR2P5YvqGHg@ep-soft-bread-ai33v924-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class HabitsPayload(BaseModel):
    habits: str

# БАЗОВЫЕ НАСТРОЙКИ (ЕСЛИ У ПОЛЬЗОВАТЕЛЯ НЕТ СВОИХ)
DEFAULT_HABITS = [
    {"id": "task-run", "cat": "sport", "tag": "Кардио", "name": "Пробежка", "xp": 150},
    {"id": "task-strength", "cat": "sport", "tag": "Сила", "name": "Силовая тренировка", "xp": 200},
    {"id": "task-cardio", "cat": "sport", "tag": "Выносливость", "name": "Кардио сессия", "xp": 150},
    {"id": "task-hiit", "cat": "sport", "tag": "Интенсив", "name": "ВИИТ", "xp": 200},
    {"id": "task-family-time", "cat": "family", "tag": "Связь", "name": "Время с семьей", "xp": 100}
]

REWARDS_DB = {
    "baton": {"name": "Батончик", "cost": 100},
    "soda": {"name": "Газировка", "cost": 150},
    "fast": {"name": "Фаст Фуд", "cost": 600}
}

# --- СИСТЕМА БЕЗОПАСНОСТИ ---
def verify_tg_data(init_data: str, expected_user_id: str):
    if not init_data: 
        raise HTTPException(status_code=401, detail="Запустите приложение через Telegram!")
    
    vals = dict(urllib.parse.parse_qsl(init_data))
    if "hash" not in vals: 
        raise HTTPException(status_code=401, detail="Ошибка безопасности: нет хэша")
        
    hash_val = vals.pop("hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(vals.items()))
    
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    if calc_hash != hash_val: 
        raise HTTPException(status_code=401, detail="Подпись не совпадает! Атака отклонена.")
        
    user_data = json.loads(vals.get("user", "{}"))
    if str(user_data.get("id")) != expected_user_id:
        raise HTTPException(status_code=401, detail="Попытка взлома чужого аккаунта!")

def add_to_history(db, username, e_type, desc, amt):
    event = History(username=username, event_type=e_type, description=desc, amount=amt)
    db.add(event)

def get_today_str(): 
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def process_daily_updates(user, db):
    today = get_today_str()
    if user.last_active_date != today:
        if user.last_active_date:
            try:
                last_date = datetime.strptime(user.last_active_date, "%Y-%m-%d").date()
                days_missed = (datetime.now(timezone.utc).date() - last_date).days
                if days_missed > 0:
                    loss = days_missed * 15; user.hp = max(0, user.hp - loss)
                    add_to_history(db, user.username, 'spend', f'Пропуск ({days_missed} дн.)', loss)
            except: pass
        user.last_active_date, user.water_count, user.completed_tasks = today, 0, ""

@app.get("/get_hero/{username}")
def get_hero(username: str, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if not user:
        user = UserProfile(username=username, hp=100, last_active_date=get_today_str(), water_goal=8)
        db.add(user); db.commit()
    else: process_daily_updates(user, db); db.commit()
    
    hist = db.query(History).filter(History.username == username).order_by(desc(History.timestamp)).limit(20).all()
    hist_data = [{"type": h.event_type, "desc": h.description, "amt": h.amount, "time": h.timestamp.strftime("%H:%M")} for h in hist]
    res = { "total_xp": user.total_xp, "current_month_xp": user.current_month_xp, "hp": user.hp, "water_count": user.water_count, "water_goal": user.water_goal, "completed_tasks": user.completed_tasks, "sleep_start": user.sleep_start, "custom_habits": user.custom_habits, "history": hist_data }
    db.close()
    return res

@app.post("/update_habits/{username}")
def update_habits(username: str, payload: HabitsPayload, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user: user.custom_habits = payload.habits; db.commit()
    db.close()
    return get_hero(username=username, x_tg_data=x_tg_data)

@app.post("/set_water_goal/{username}")
def set_water_goal(username: str, goal: int, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user: user.water_goal = goal; add_to_history(db, username, 'gain', f'Новая норма: {goal} ст.', 0); db.commit()
    db.close()
    return get_hero(username=username, x_tg_data=x_tg_data)

@app.post("/sleep_action/{username}")
def sleep_action(username: str, tz: int = 0, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if not user.sleep_start:
        user.sleep_start = datetime.now(timezone.utc).isoformat(); db.commit()
        res = get_hero(username=username, x_tg_data=x_tg_data)
    else:
        try:
            start_str = user.sleep_start
            if start_str.endswith("Z"): start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            else:
                start_time = datetime.fromisoformat(start_str)
                if start_time.tzinfo is None: start_time = start_time.replace(tzinfo=timezone.utc)
            duration_hours = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600.0
            
            if duration_hours < 0.5:
                user.sleep_start = ""; db.commit(); res = get_hero(username=username, x_tg_data=x_tg_data)
                res["sleep_report"] = "⏳ Сон отменен. Вы спали меньше 30 минут."
                return res

            local_start_time = start_time - timedelta(minutes=tz); bed_h = local_start_time.hour
            report, base_xp, hp_heal = [], 0, 0
            if duration_hours < 3: base_xp, hp_heal = 10, 5; report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Короткий)")
            elif 3 <= duration_hours < 5: base_xp, hp_heal = 15, 10; report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Недосып)")
            elif 5 <= duration_hours < 7.5: base_xp, hp_heal = 30, 15; report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Средний сон)")
            else: base_xp, hp_heal = 50, 20; report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Оптимальный)")
            if duration_hours >= 3:
                if 21 <= bed_h <= 23: base_xp += 30; report.append("🧬 Отбой: Окно мелатонина (+30 XP).")
                elif bed_h == 0 or bed_h == 1: base_xp += 10; report.append("🧬 Отбой: Допустимо (+10 XP).")
                elif 2 <= bed_h <= 5: base_xp -= 10; report.append("🧬 Отбой: Поздно (-10 XP).")
                cycle_rem = duration_hours % 1.5
                if cycle_rem < 0.35 or cycle_rem > 1.15: base_xp += 20; hp_heal += 5; report.append("⏰ Фаза: Легкая фаза (+20 XP).")

            final_xp = max(0, base_xp); user.total_xp += final_xp; user.current_month_xp += final_xp; user.hp = min(100, user.hp + hp_heal)
            add_to_history(db, username, 'gain', f'Сон ({round(duration_hours, 1)}ч)', final_xp)
            user.sleep_start = ""; db.commit()
            res = get_hero(username=username, x_tg_data=x_tg_data)
            res["sleep_report"] = "\n\n".join(report) + f"\n\n🏆 ИТОГ: +{final_xp} XP | +{hp_heal} HP"
        except Exception:
            user.sleep_start = ""; db.commit(); res = get_hero(username=username, x_tg_data=x_tg_data)
    db.close()
    return res

@app.post("/drink_water/{username}")
def drink_water(username: str, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user.water_count < user.water_goal:
        user.water_count += 1
        gain = 5 if user.hp >= 30 else 2
        user.total_xp += gain; user.current_month_xp += gain; user.hp = min(100, user.hp + 5)
        add_to_history(db, username, 'gain', f'Вода {user.water_count}/{user.water_goal}', gain)
        db.commit()
    db.close()
    return get_hero(username=username, x_tg_data=x_tg_data)

@app.post("/buy_reward/{username}")
def buy_reward(username: str, reward_id: str, qty: int = 1, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username) 
    if reward_id not in REWARDS_DB: raise HTTPException(status_code=400, detail="Товар не найден")
    
    cost = REWARDS_DB[reward_id]["cost"]
    name = REWARDS_DB[reward_id]["name"]
    total_cost = cost * qty
    
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user.current_month_xp < total_cost:
        db.close(); return {"error": f"Недостаточно XP! Нужно {total_cost}"}
    user.current_month_xp -= total_cost
    add_to_history(db, username, 'spend', f'{name} x{qty}', total_cost)
    db.commit(); db.close()
    return get_hero(username=username, x_tg_data=x_tg_data)

@app.post("/add_xp/{username}")
def add_xp(username: str, task_id: str, x_tg_data: str = Header(None)):
    verify_tg_data(x_tg_data, username)
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    
    # Сервер сам ищет задачу и начисляет очки (защита от накрутки)
    habits = json.loads(user.custom_habits) if user.custom_habits else DEFAULT_HABITS
    task_name, amount = None, 0
    for h in habits:
        if h["id"] == task_id:
            task_name, amount = h["name"], h["xp"]
            break
            
    if not task_name: 
        db.close()
        raise HTTPException(status_code=400, detail="Задача не найдена в вашем профиле")

    tasks = user.completed_tasks.split(",") if user.completed_tasks else []
    if task_id not in tasks:
        tasks.append(task_id)
        user.completed_tasks = ",".join(tasks)
        gain = amount if user.hp >= 30 else amount // 2
        user.total_xp += gain; user.current_month_xp += gain; user.hp = min(100, user.hp + 5)
        add_to_history(db, username, 'gain', task_name, gain)
        db.commit()
    db.close()
    return get_hero(username=username, x_tg_data=x_tg_data)
