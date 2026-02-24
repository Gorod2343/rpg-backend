from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Разрешаем нашему сайту из Telegram общаться с этим сервером
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Наша стартовая база данных в памяти
users_db = {
    "Александр": {"xp": 450, "level": 12}
}

@app.get("/")
def read_root():
    return {"status": "Сервер Life RPG запущен и ждет команд!"}

@app.get("/get_hero/{username}")
def get_hero(username: str):
    if username not in users_db:
        users_db[username] = {"xp": 0, "level": 1}
    return users_db[username]

@app.post("/add_xp/{username}")
def add_xp(username: str, amount: int):
    if username not in users_db:
        users_db[username] = {"xp": 0, "level": 1}
    
    users_db[username]["xp"] += amount
    
    if users_db[username]["xp"] >= 1000:
        users_db[username]["level"] += 1
        users_db[username]["xp"] -= 1000
        
    return users_db[username]
