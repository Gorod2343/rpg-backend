import os
import hmac
import hashlib
import logging
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import desc

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ✅ Конфигурация ТОЛЬКО из переменных окружения — никаких fallback с паролями
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not BOT_TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN не задана!")
if not DATABASE_URL:
    raise RuntimeError("Переменная окружения DATABASE_URL не задана!")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------------------------------------------------------------------------
# Модели
# ---------------------------------------------------------------------------
class UserProfile(Base):
    __tablename__ = "users_final_v14"
    username         = Column(String,  primary_key=True, index=True)
    total_xp         = Column(Integer, default=0)
    current_month_xp = Column(Integer, default=0)
    hp               = Column(Integer, default=100)
    last_active_date  = Column(String,  default="")
    last_active_month = Column(String,  default="")
    water_count      = Column(Integer, default=0)
    water_goal       = Column(Integer, default=8)
    completed_tasks  = Column(String,  default="")
    sleep_start      = Column(String,  default="")
    custom_habits    = Column(String,  default="")
    streak           = Column(Integer, default=0)


class History(Base):
    __tablename__ = "history_v8"
    id          = Column(Integer, primary_key=True, index=True)
    username    = Column(String,  index=True)
    event_type  = Column(String)
    description = Column(String)
    amount      = Column(Integer)
    timestamp   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(bind=engine)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------
class HabitsPayload(BaseModel):
    habits: str


@contextmanager
def get_db():
    """Контекстный менеджер сессии — утечки невозможны."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_tg_data(init_data: str) -> bool:
    """
    ✅ Правильная реализация проверки подписи Telegram WebApp.
    Документация: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Алгоритм:
      secret_key = HMAC-SHA256(key="WebAppData", msg=BOT_TOKEN)
      data_check = HMAC-SHA256(key=secret_key, msg=data_check_string)
    """
    if not init_data:
        return False
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = params.pop("hash", None)
        if not received_hash:
            return False

        # Строка проверки: отсортированные пары key=value через \n
        data_check_string = "\n".join(
            f"{k}={params[k]}" for k in sorted(params.keys())
        )

        # ✅ Правильный порядок: ключ = b"WebAppData", сообщение = BOT_TOKEN
        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode("utf-8"),
            hashlib.sha256
        ).digest()

        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        # ✅ compare_digest защищает от timing-атак
        return hmac.compare_digest(expected_hash, received_hash)

    except Exception as e:
        logger.warning(f"verify_tg_data ошибка: {e}")
        return False


def require_auth(x_tg_data: str | None) -> None:
    """Бросает 401 если подпись не прошла проверку."""
    if not verify_tg_data(x_tg_data):
        raise HTTPException(status_code=401, detail="Unauthorized")


def add_to_history(db, username: str, e_type: str, description: str, amt: int) -> None:
    db.add(History(username=username, event_type=e_type, description=description, amount=amt))


def get_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_current_month_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def build_hero_response(db, user: UserProfile) -> dict:
    """Формирует ответ в рамках уже открытой сессии — без повторного обращения к БД."""
    hist = (
        db.query(History)
        .filter(History.username == user.username)
        .order_by(desc(History.timestamp))
        .limit(20)
        .all()
    )
    hist_data = [
        {
            "type": h.event_type,
            "desc": h.description,
            "amt":  h.amount,
            # ✅ Полный ISO-timestamp — фронтенд форматирует сам
            "time": h.timestamp.isoformat() if h.timestamp else "",
        }
        for h in hist
    ]
    return {
        "total_xp":         user.total_xp,
        "current_month_xp": user.current_month_xp,
        "hp":               user.hp,
        "water_count":      user.water_count,
        "water_goal":       user.water_goal,
        "completed_tasks":  user.completed_tasks,
        "sleep_start":      user.sleep_start,
        "custom_habits":    user.custom_habits,
        "streak":           user.streak,
        "history":          hist_data,
    }


def get_or_create_user(db, username: str) -> UserProfile:
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if not user:
        user = UserProfile(
            username=username,
            hp=100,
            last_active_date=get_today_str(),
            last_active_month=get_current_month_str(),
            water_goal=8,
        )
        db.add(user)
        db.commit()
    return user


def process_daily_updates(user: UserProfile, db) -> None:
    """Сброс дневных данных и штраф за пропущенные дни."""
    today = get_today_str()
    current_month = get_current_month_str()

    # ✅ Сброс месячных XP при смене месяца
    if user.last_active_month != current_month:
        user.current_month_xp = 0
        user.last_active_month = current_month

    if user.last_active_date != today:
        # ✅ Штраф за пропущенные дни
        if user.last_active_date:
            try:
                last_date = datetime.strptime(user.last_active_date, "%Y-%m-%d").date()
                days_missed = (datetime.now(timezone.utc).date() - last_date).days
                if days_missed > 0:
                    loss = days_missed * 15
                    user.hp = max(0, user.hp - loss)
                    add_to_history(db, user.username, "spend", f"Пропуск ({days_missed} дн.)", loss)
            except Exception as e:
                logger.warning(f"Ошибка расчёта пропуска для {user.username}: {e}")

        user.last_active_date = today
        user.water_count = 0
        user.completed_tasks = ""


# ---------------------------------------------------------------------------
# Эндпоинты
# ---------------------------------------------------------------------------

@app.get("/get_hero/{username}")
def get_hero(username: str, x_tg_data: str | None = Header(None)):
    """✅ GET тоже защищён — история и данные пользователя не публичны."""
    require_auth(x_tg_data)
    with get_db() as db:
        user = get_or_create_user(db, username)
        process_daily_updates(user, db)
        db.commit()
        return build_hero_response(db, user)


@app.post("/update_habits/{username}")
def update_habits(username: str, payload: HabitsPayload, x_tg_data: str | None = Header(None)):
    require_auth(x_tg_data)
    with get_db() as db:
        user = get_or_create_user(db, username)
        user.custom_habits = payload.habits
        db.commit()
        return build_hero_response(db, user)


@app.post("/set_water_goal/{username}")
def set_water_goal(username: str, goal: int = Query(..., gt=0), x_tg_data: str | None = Header(None)):
    require_auth(x_tg_data)
    with get_db() as db:
        user = get_or_create_user(db, username)
        user.water_goal = goal
        add_to_history(db, username, "gain", f"Новая норма воды: {goal} ст.", 0)
        db.commit()
        return build_hero_response(db, user)


@app.post("/sleep_action/{username}")
def sleep_action(username: str, tz: int = 0, x_tg_data: str | None = Header(None)):
    require_auth(x_tg_data)
    with get_db() as db:
        user = get_or_create_user(db, username)

        # --- Начало трекинга ---
        if not user.sleep_start:
            user.sleep_start = datetime.now(timezone.utc).isoformat()
            db.commit()
            return build_hero_response(db, user)

        # --- Завершение трекинга ---
        try:
            start_str = user.sleep_start
            start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)

            duration_hours = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600.0

            # Меньше 30 минут — отмена
            if duration_hours < 0.5:
                user.sleep_start = ""
                db.commit()
                res = build_hero_response(db, user)
                res["sleep_report"] = "⏳ Сон отменён. Менее 30 минут — не считается."
                return res

            # ✅ Полная геймификация сна восстановлена
            local_start = start_time - timedelta(minutes=tz)
            bed_h = local_start.hour
            report, base_xp, hp_heal = [], 0, 0

            if duration_hours < 3:
                base_xp, hp_heal = 10, 5
                report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Очень короткий)")
            elif duration_hours < 5:
                base_xp, hp_heal = 15, 10
                report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Недосып)")
            elif duration_hours < 7.5:
                base_xp, hp_heal = 30, 15
                report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Средний сон)")
            else:
                base_xp, hp_heal = 50, 20
                report.append(f"⏳ Время: {round(duration_hours, 1)}ч (Оптимальный сон)")

            if duration_hours >= 3:
                if 21 <= bed_h <= 23:
                    base_xp += 30
                    report.append("🧬 Отбой: Идеально! Окно мелатонина (+30 XP).")
                elif bed_h in (0, 1):
                    base_xp += 10
                    report.append("🧬 Отбой: Допустимо (+10 XP).")
                elif 2 <= bed_h <= 5:
                    base_xp -= 10
                    report.append("🧬 Отбой: Слишком поздно (-10 XP).")

                cycle_rem = duration_hours % 1.5
                if cycle_rem < 0.35 or cycle_rem > 1.15:
                    base_xp += 20
                    hp_heal += 5
                    report.append("⏰ Фаза: Пробуждение в лёгкой фазе (+20 XP).")

            final_xp = max(0, base_xp)
            user.total_xp += final_xp
            user.current_month_xp += final_xp
            user.hp = min(100, user.hp + hp_heal)
            user.sleep_start = ""
            add_to_history(db, username, "gain", f"Сон ({round(duration_hours, 1)}ч)", final_xp)
            db.commit()

            res = build_hero_response(db, user)
            res["sleep_report"] = "\n\n".join(report) + f"\n\n🏆 ИТОГ: +{final_xp} XP | +{hp_heal} HP"
            return res

        except Exception as e:
            logger.error(f"sleep_action ошибка для {username}: {e}")
            user.sleep_start = ""
            db.commit()
            return build_hero_response(db, user)


@app.post("/drink_water/{username}")
def drink_water(username: str, x_tg_data: str | None = Header(None)):
    require_auth(x_tg_data)
    with get_db() as db:
        user = get_or_create_user(db, username)
        if user.water_count < user.water_goal:
            user.water_count += 1
            # ✅ Восстановлен штраф при низком HP и восстановление здоровья
            gain = 5 if user.hp >= 30 else 2
            user.total_xp += gain
            user.current_month_xp += gain
            user.hp = min(100, user.hp + 5)
            add_to_history(db, username, "gain", f"Вода {user.water_count}/{user.water_goal}", gain)
            db.commit()
        return build_hero_response(db, user)


@app.post("/buy_reward/{username}")
def buy_reward(
    username: str,
    cost: int = Query(...),
    name: str = Query(...),
    qty: int = Query(default=1),
    x_tg_data: str | None = Header(None),
):
    require_auth(x_tg_data)
    if cost <= 0:
        raise HTTPException(status_code=400, detail="Стоимость должна быть больше нуля")
    if qty <= 0:
        raise HTTPException(status_code=400, detail="Количество должно быть больше нуля")
    with get_db() as db:
        user = get_or_create_user(db, username)
        total_cost = cost * qty
        if user.current_month_xp < total_cost:
            return {"error": f"Недостаточно XP! Нужно {total_cost}, есть {user.current_month_xp}"}
        user.current_month_xp -= total_cost
        # ✅ История покупок восстановлена
        add_to_history(db, username, "spend", f"{name} x{qty}", total_cost)
        db.commit()
        return build_hero_response(db, user)


@app.post("/add_xp/{username}")
def add_xp(
    username: str,
    amount: int = Query(...),
    task_id: str = Query(...),
    task_name: str = Query(...),
    x_tg_data: str | None = Header(None),
):
    require_auth(x_tg_data)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Количество XP должно быть больше нуля")
    with get_db() as db:
        user = get_or_create_user(db, username)
        tasks = user.completed_tasks.split(",") if user.completed_tasks else []
        if task_id not in tasks:
            tasks.append(task_id)
            user.completed_tasks = ",".join(filter(None, tasks))
            # ✅ Восстановлен штраф при низком HP
            gain = amount if user.hp >= 30 else amount // 2
            user.total_xp += gain
            user.current_month_xp += gain
            user.hp = min(100, user.hp + 5)
            add_to_history(db, username, "gain", task_name, gain)
            db.commit()
        return build_hero_response(db, user)
