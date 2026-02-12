"""
FastAPI Admin панель
Управление ботом через веб-интерфейс
"""

import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Telegram Bot Admin Panel",
    description="Admin panel для управления Telegram ботом",
    version="2.0.0",
)


# === Models ===

class BotStats(BaseModel):
    """Статистика бота"""
    total_users: int
    active_chats: int
    total_questions: int
    total_reviews: int
    uptime: str
    avg_response_time: float


class BroadcastMessage(BaseModel):
    """Модель для рассылки"""
    message: str
    user_ids: Optional[list[int]] = None  # None = всем


# === Routes ===

@app.get("/", response_class=HTMLResponse)
async def admin_dashboard():
    """Главная страница админ панели"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Admin Panel</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .stat { 
                display: inline-block; 
                background: #f0f0f0; 
                padding: 20px; 
                margin: 10px; 
                border-radius: 8px;
            }
            .stat h3 { margin: 0 0 10px 0; color: #333; }
            .stat p { font-size: 24px; margin: 0; color: #007bff; }
        </style>
    </head>
    <body>
        <h1>🤖 Telegram Bot Admin Panel</h1>
        <hr>
        <div id="stats">Loading...</div>
        
        <script>
            fetch('/api/stats')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('stats').innerHTML = `
                        <div class="stat">
                            <h3>👥 Total Users</h3>
                            <p>${data.total_users}</p>
                        </div>
                        <div class="stat">
                            <h3>💬 Active Chats</h3>
                            <p>${data.active_chats}</p>
                        </div>
                        <div class="stat">
                            <h3>❓ Questions</h3>
                            <p>${data.total_questions}</p>
                        </div>
                        <div class="stat">
                            <h3>⭐ Reviews</h3>
                            <p>${data.total_reviews}</p>
                        </div>
                        <div class="stat">
                            <h3>⏱️ Avg Response</h3>
                            <p>${data.avg_response_time}ms</p>
                        </div>
                    `;
                });
        </script>
        
        <hr>
        <h2>📊 API Endpoints</h2>
        <ul>
            <li><a href="/api/stats">GET /api/stats</a> - Статистика бота</li>
            <li><a href="/api/health">GET /api/health</a> - Health check</li>
            <li><a href="/docs">GET /docs</a> - Swagger documentation</li>
        </ul>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/api/stats", response_model=BotStats)
async def get_stats():
    """Получить статистику бота"""
    # TODO: Подключить к реальной БД
    return BotStats(
        total_users=0,
        active_chats=0,
        total_questions=0,
        total_reviews=0,
        uptime="0:00:00",
        avg_response_time=0.0,
    )


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.0.0",
    }


@app.post("/api/broadcast")
async def send_broadcast(data: BroadcastMessage):
    """Отправка рассылки пользователям"""
    # TODO: Реализовать отправку через бота
    logger.info(f"Broadcast requested: {data.message}")
    
    return {
        "status": "sent",
        "message": data.message,
        "recipients": len(data.user_ids) if data.user_ids else "all",
    }


@app.get("/api/metrics")
async def get_metrics():
    """Метрики в формате Prometheus"""
    # TODO: Интеграция с MetricsMiddleware
    metrics = """
    # HELP bot_requests_total Total requests
    # TYPE bot_requests_total counter
    bot_requests_total 0
    
    # HELP bot_response_time_seconds Response time
    # TYPE bot_response_time_seconds histogram
    bot_response_time_seconds_sum 0
    bot_response_time_seconds_count 0
    """
    return Response(content=metrics, media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
