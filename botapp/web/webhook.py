"""
Webhook server на aiohttp
Заменяет polling на instant delivery
"""

import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

logger = logging.getLogger(__name__)


async def on_startup(app: web.Application, bot: Bot, webhook_url: str):
    """Установка webhook при старте"""
    logger.info(f"Setting webhook: {webhook_url}")
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    logger.info("✅ Webhook установлен успешно")


async def on_shutdown(app: web.Application, bot: Bot):
    """Удаление webhook при остановке"""
    logger.info("Deleting webhook...")
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Webhook удалён")


def setup_webhook_server(
    dispatcher: Dispatcher,
    bot: Bot,
    webhook_url: str,
    webhook_path: str = "/webhook",
    host: str = "0.0.0.0",
    port: int = 8080,
) -> web.Application:
    """
    Создание aiohttp приложения с webhook
    
    Args:
        dispatcher: Aiogram Dispatcher
        bot: Aiogram Bot
        webhook_url: Внешний URL webhook (https://your-domain.com/webhook)
        webhook_path: Путь для webhook на сервере
        host: Host для привязки
        port: Port для привязки
    
    Returns:
        aiohttp Application
    """
    app = web.Application()
    
    # Webhook handler
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=webhook_path)
    
    # Setup application
    setup_application(app, dispatcher, bot=bot)
    
    # Startup/shutdown hooks
    app.on_startup.append(lambda app: on_startup(app, bot, webhook_url))
    app.on_shutdown.append(lambda app: on_shutdown(app, bot))
    
    logger.info(f"Webhook server настроен: {host}:{port}{webhook_path}")
    
    return app


async def start_webhook_server(app: web.Application, host: str = "0.0.0.0", port: int = 8080):
    """
    Запуск webhook сервера
    
    Usage:
        app = setup_webhook_server(dp, bot, webhook_url)
        await start_webhook_server(app)
    """
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    logger.info(f"🚀 Webhook server запущен на http://{host}:{port}")
    
    # Keep running
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
