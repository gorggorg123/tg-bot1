"""
Web Dashboard for Monitoring
============================
FastAPI веб-дашборд для мониторинга бота

Эндпоинты:
- GET /dashboard - главная страница
- GET /api/logs - получить логи
- WS /ws/logs - WebSocket стрим логов
- GET /api/metrics - получить метрики
- GET /api/health - проверка здоровья
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse

from botapp.monitoring import get_live_logger, get_metrics_collector, get_health_checker

logger = logging.getLogger(__name__)


def create_monitoring_app() -> FastAPI:
    """Создать FastAPI приложение для мониторинга"""
    app = FastAPI(
        title="Ozon Bot Monitoring",
        description="Real-time monitoring dashboard",
        version="2.0",
    )
    
    live_logger = get_live_logger()
    metrics = get_metrics_collector()
    health = get_health_checker()
    
    # ===== Главная страница =====
    
    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        """Главная страница дашборда"""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Ozon Bot Monitoring</title>
            <style>
                body {
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    margin: 0;
                    padding: 20px;
                    background: #f5f5f5;
                }
                .container {
                    max-width: 1400px;
                    margin: 0 auto;
                }
                h1 {
                    color: #333;
                    margin-bottom: 30px;
                }
                .grid {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                    gap: 20px;
                    margin-bottom: 30px;
                }
                .card {
                    background: white;
                    border-radius: 8px;
                    padding: 20px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }
                .card h2 {
                    margin-top: 0;
                    color: #555;
                    font-size: 18px;
                }
                .metric {
                    font-size: 32px;
                    font-weight: bold;
                    color: #007bff;
                    margin: 10px 0;
                }
                .log-container {
                    background: #1e1e1e;
                    color: #d4d4d4;
                    border-radius: 8px;
                    padding: 20px;
                    height: 500px;
                    overflow-y: auto;
                    font-family: 'Consolas', 'Monaco', monospace;
                    font-size: 13px;
                }
                .log-entry {
                    margin-bottom: 8px;
                    padding: 8px;
                    border-left: 3px solid #007bff;
                    background: rgba(255,255,255,0.05);
                }
                .log-entry.error {
                    border-left-color: #dc3545;
                }
                .log-entry.warning {
                    border-left-color: #ffc107;
                }
                .timestamp {
                    color: #888;
                    font-size: 11px;
                }
                .status-indicator {
                    display: inline-block;
                    width: 12px;
                    height: 12px;
                    border-radius: 50%;
                    margin-right: 8px;
                }
                .status-healthy {
                    background: #28a745;
                }
                .status-unhealthy {
                    background: #dc3545;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🚀 Ozon Bot Monitoring Dashboard</h1>
                
                <div class="grid">
                    <div class="card">
                        <h2>📊 Requests</h2>
                        <div class="metric" id="requests">-</div>
                        <div>Total requests processed</div>
                    </div>
                    <div class="card">
                        <h2>⚡ Avg Response</h2>
                        <div class="metric" id="response-time">-</div>
                        <div>Average response time</div>
                    </div>
                    <div class="card">
                        <h2>✅ Success Rate</h2>
                        <div class="metric" id="success-rate">-</div>
                        <div>Successful requests</div>
                    </div>
                    <div class="card">
                        <h2>💾 Memory</h2>
                        <div class="metric" id="memory">-</div>
                        <div>Memory usage</div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>📝 Live Logs</h2>
                    <div class="log-container" id="logs"></div>
                </div>
            </div>
            
            <script>
                // WebSocket для live логов
                const ws = new WebSocket('ws://' + location.host + '/ws/logs');
                const logsContainer = document.getElementById('logs');
                
                ws.onmessage = (event) => {
                    const log = JSON.parse(event.data);
                    const entry = document.createElement('div');
                    entry.className = 'log-entry ' + log.level.toLowerCase();
                    entry.innerHTML = `
                        <span class="timestamp">[${new Date(log.timestamp).toLocaleTimeString()}]</span>
                        <strong>${log.level}</strong> ${log.message}
                    `;
                    logsContainer.insertBefore(entry, logsContainer.firstChild);
                    
                    // Ограничение количества логов
                    if (logsContainer.children.length > 100) {
                        logsContainer.removeChild(logsContainer.lastChild);
                    }
                };
                
                // Обновление метрик каждые 3 секунды
                setInterval(async () => {
                    const response = await fetch('/api/metrics/summary');
                    const data = await response.json();
                    
                    document.getElementById('requests').textContent = 
                        data.totals?.requests || 0;
                    document.getElementById('response-time').textContent = 
                        '~0.5s';  // TODO: calc from metrics
                    document.getElementById('success-rate').textContent = 
                        '99%';  // TODO: calc from metrics
                    document.getElementById('memory').textContent = 
                        (data.system?.memory_mb || 0).toFixed(0) + ' MB';
                }, 3000);
            </script>
        </body>
        </html>
        """
    
    # ===== API Endpoints =====
    
    @app.get("/api/logs")
    async def get_logs(
        limit: int = Query(100, ge=1, le=1000),
        level: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Получить последние логи"""
        logs = live_logger.get_recent_logs(limit=limit, level=level)
        stats = live_logger.get_statistics()
        
        return {
            "logs": logs,
            "statistics": stats,
        }
    
    @app.get("/api/metrics")
    async def get_metrics() -> Dict[str, Any]:
        """Получить все метрики"""
        return {
            "system": metrics.get_system_metrics(),
            "handlers": metrics.get_handler_metrics(),
        }
    
    @app.get("/api/metrics/summary")
    async def get_metrics_summary() -> Dict[str, Any]:
        """Получить сводку метрик"""
        return metrics.get_summary()
    
    @app.get("/api/health")
    async def get_health() -> Dict[str, Any]:
        """Проверка здоровья"""
        await health.check_all()
        return health.get_summary()
    
    # ===== WebSocket для live логов =====
    
    @app.websocket("/ws/logs")
    async def websocket_logs(websocket: WebSocket):
        """WebSocket endpoint для стриминга логов"""
        await websocket.accept()
        live_logger.register_ws_client(websocket)
        
        try:
            # Держим соединение открытым
            while True:
                # Ожидаем сообщения от клиента (ping/pong)
                await websocket.receive_text()
        except WebSocketDisconnect:
            live_logger.unregister_ws_client(websocket)
            logger.info("WebSocket client disconnected")
    
    return app


__all__ = ["create_monitoring_app"]
