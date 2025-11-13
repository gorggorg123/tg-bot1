from fastapi import FastAPI
from botapp.tg import router as tg_router

app = FastAPI(title="Ozon Telegram Bot")

@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "Ozon Telegram Bot на FastAPI + Render работает 🚀",
    }

# Подключаем обработчик Telegram
app.include_router(tg_router)
