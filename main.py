from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timezone, timedelta

# ТВОЯ БАЗА ДАННЫХ
DATABASE_URL = "postgresql://neondb_owner:npg_StR2P5YvqGHg@ep-soft-bread-ai33v924-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserProfile(Base):
    __tablename__ = "users_final_v8"
    username = Column(String, primary_key=True, index=True)
    total_xp = Column(Integer, default=0)
    current_month_xp = Column(Integer, default=0)
    hp = Column(Integer, default=100)
    last_active_date = Column(String, default="")
    water_count = Column(Integer, default=0)
    water_goal = Column(Integer, default=8)
    completed_tasks = Column(String, default="")
    sleep_start = Column(String, default="") 

class History(Base):
    __tablename__ = "history_v6"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    event_type = Column(String)
    description = Column(String)
    amount = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def add_to_history(db, username, e_type, desc, amt):
    event = History(username=username, event_type=e_type, description=desc, amount=amt)
    db.add(event)

def get_today_str(): return datetime.now(timezone.utc).strftime("%Y-%m-%d")

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
            except: pass
        user.last_active_date, user.water_count, user.completed_tasks = today, 0, ""

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
        "total_xp": user.total_xp, "current_month_xp": user.current_month_xp, 
        "hp": user.hp, "water_count": user.water_count, "water_goal": user.water_goal,
        "completed_tasks": user.completed_tasks, "sleep_start": user.sleep_start, "history": hist_data
    }
    db.close()
    return res

@app.post("/set_water_goal/{username}")
def set_water_goal(username: str, goal: int):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if user:
        user.water_goal = goal
        add_to_history(db, username, 'gain', f'Новая норма: {goal} ст.', 0)
        db.commit()
    db.close()
    return get_hero(username)

@app.post("/sleep_action/{username}")
def sleep_action(username: str, tz: int = 0):
    # tz - это смещение часового пояса в минутах, переданное с телефона
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    
    if not user.sleep_start:
        # УСНУЛ: Сохраняем строго в UTC с правильным форматом времени
        user.sleep_start = datetime.now(timezone.utc).isoformat()
        db.commit()
        res = get_hero(username)
    else:
        # ПРОСНУЛСЯ
        try:
            start_str = user.sleep_start
            # Защита от старых данных и парсинг в UTC
            if start_str.endswith("Z"):
                start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            else:
                start_time = datetime.fromisoformat(start_str)
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)
            
            end_time = datetime.now(timezone.utc)
            duration_hours = (end_time - start_time).total_seconds() / 3600.0
            
            # Определяем твое МЕСТНОЕ время засыпания для анализа "Окна мелатонина"
            local_start_time = start_time - timedelta(minutes=tz)
            bed_h = local_start_time.hour
            
            report = []
            base_xp = 0
            hp_heal = 0
            
            if duration_hours < 5:
                base_xp = 10; hp_heal = 5
                report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Критический недосып, мало циклов)")
            elif 5 <= duration_hours < 7.5:
                base_xp = 30; hp_heal = 10
                report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Средний сон, ~4 цикла)")
            else:
                base_xp = 50; hp_heal = 20
                report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Оптимально, 5-6 циклов)")
            
            if 21 <= bed_h <= 23:
                base_xp += 30
                report.append("🧬 Отбой: Идеально! Окно мелатонина поймано.")
            elif bed_h == 0 or bed_h == 1:
                base_xp += 10
                report.append("🧬 Отбой: Допустимо, но часть глубокого сна упущена.")
            elif 2 <= bed_h <= 5:
                base_xp -= 10
                report.append("🧬 Отбой: Слишком поздно. Преобладал быстрый сон.")
            else:
                report.append("🧬 Отбой: Дневной сон (сбиты циркадные ритмы).")
            
            cycle_rem = duration_hours % 1.5
            if cycle_rem < 0.35 or cycle_rem > 1.15:
                base_xp += 20; hp_heal += 5
                report.append("⏰ Фаза: Пробуждение в легкой фазе. Правило 90 минут сработало!")
            else:
                report.append("⏰ Фаза: Пробуждение посреди глубокого цикла.")
            
            final_xp = max(0, base_xp)
            user.total_xp += final_xp
            user.current_month_xp += final_xp
            user.hp = min(100, user.hp + hp_heal)
            
            add_to_history(db, username, 'gain', f'Сон ({round(duration_hours, 1)}ч)', final_xp)
            
            user.sleep_start = ""
            db.commit()
            
            res = get_hero(username)
            res["sleep_report"] = "\n\n".join(report) + f"\n\n🏆 ИТОГ: +{final_xp} XP | +{hp_heal} HP"
        except Exception as e:
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
        gain = 5 if user.hp >= 30 else 2
        user.total_xp += gain
        user.current_month_xp += gain
        user.hp = min(100, user.hp + 5)
        add_to_history(db, username, 'gain', f'Вода {user.water_count}/{user.water_goal}', gain)
        db.commit()
    db.close()
    return get_hero(username)

@app.post("/buy_reward/{username}")
def buy_reward(username: str, cost: int, name: str, qty: int = 1):
    db = SessionLocal()
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    total_cost = cost * qty
    if user.current_month_xp < total_cost:
        db.close()
        return {"error": f"Недостаточно XP! Нужно {total_cost}"}
    user.current_month_xp -= total_cost
    add_to_history(db, username, 'spend', f'{name} x{qty}', total_cost)
    db.commit()
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
