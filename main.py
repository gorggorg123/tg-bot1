from fastapi import FastAPI
from botapp.tg import router as tg_router  # <--- тут новое имя пакета

app = FastAPI()


@app.get("/")
async def root():
    return {"status": "ok", "message": "Ozon bot is alive"}


# Подключаем Telegram-вебхук
app.include_router(tg_router)
