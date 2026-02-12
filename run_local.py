#!/usr/bin/env python3
"""Локальный запуск бота без FastAPI сервера.
Используется для разработки и отладки на локальной машине.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress

try:
    import psutil
except ImportError:
    psutil = None

from dotenv import load_dotenv
from aiohttp import web

# Загружаем переменные окружения из .env
load_dotenv()

# Инициализация улучшенного логирования - ВСЕ ЛОГИ В ТЕРМИНАЛ
# ВАЖНО: Настраиваем логирование ПЕРВЫМ, до импорта других модулей
from botapp.logging_config import setup_logging
import logging

# Очищаем все существующие обработчики перед настройкой
root_logger = logging.getLogger()
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
    if hasattr(handler, 'close'):
        try:
            handler.close()
        except Exception:
            pass

# Настраиваем логирование ТОЛЬКО для консоли
setup_logging(
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    log_file=None,  # Не пишем в файл
    enable_console=True,
    enable_file=False,  # Только терминал!
)

from botapp.config import load_ozon_config
from botapp.jobs.chat_autoreply import chat_autoreply_loop
from botapp.ozon_client import close_clients, get_client, has_write_credentials
from botapp.router import router as root_router
from botapp.storage import ROOT as STORAGE_ROOT, flush_storage
from botapp.tg_session import create_bot_session_from_env
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

logger = logging.getLogger("run_local")


def _env(*names: str, default: str = "") -> str:
    """Получить переменную окружения."""
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return (default or "").strip()


def _check_already_running():
    """Проверяет, не запущен ли уже бот."""
    if psutil is None:
        logger.debug("psutil не установлен, пропускаем проверку запущенных экземпляров")
        return
    
    current_pid = os.getpid()
    bot_processes = []
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] == current_pid:
                continue
            
            if proc.info['name'] and 'python' in proc.info['name'].lower():
                cmdline = proc.info.get('cmdline') or []
                cmdline_str = ' '.join(cmdline)
                
                if 'run_local.py' in cmdline_str:
                    bot_processes.append({
                        'pid': proc.info['pid'],
                        'cmdline': cmdline_str
                    })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    
    if bot_processes:
        logger.error("=" * 60)
        logger.error("[!] ОШИБКА: Обнаружены запущенные экземпляры бота!")
        for proc in bot_processes:
            logger.error("  PID %s - %s", proc['pid'], proc['cmdline'])
        logger.error("")
        logger.error("Невозможно запустить бот - это вызовет TelegramConflictError.")
        logger.error("")
        logger.error("Остановите запущенные экземпляры:")
        logger.error("  Windows CMD: taskkill /F /PID %s", ' /PID '.join(str(p['pid']) for p in bot_processes))
        logger.error("  PowerShell: Stop-Process -Id %s -Force", ','.join(str(p['pid']) for p in bot_processes))
        logger.error("  Или закройте окна с запущенным ботом вручную")
        logger.error("=" * 60)
        sys.exit(1)


async def _start_health_server(port: int) -> web.AppRunner:
    """Поднять простой HTTP health server для Render Web Service."""

    app = web.Application()

    async def _health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "ozon-tg-bot"})

    app.router.add_get("/", _health)
    app.router.add_get("/healthz", _health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(port))
    await site.start()
    logger.info("Health server started on 0.0.0.0:%s", port)
    return runner


async def main():
    """Основная функция запуска бота."""
    # Проверка, не запущен ли уже бот
    _check_already_running()
    
    # Проверка токена Telegram
    token = _env("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Не задан TG_BOT_TOKEN (или TELEGRAM_BOT_TOKEN).")
        logger.error("Создайте файл .env и укажите токен бота.")
        sys.exit(1)
    
    # Проверка Ozon credentials
    try:
        cfg = load_ozon_config()
        if not cfg.client_id or not cfg.api_key:
            logger.error("Не заданы OZON credentials (OZON_CLIENT_ID/OZON_API_KEY).")
            logger.error("Добавьте их в файл .env")
            sys.exit(1)
    except Exception as e:
        logger.error("Ошибка при загрузке конфигурации Ozon: %s", e)
        sys.exit(1)
    
    # Предупреждения
    if not _env("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY не задан — ИИ-генерация будет недоступна.")
    
    # ФИНАЛЬНАЯ проверка: убеждаемся, что файловых обработчиков нет
    # (на случай, если какой-то импортированный модуль переопределил логирование)
    file_handlers = [h for h in logging.getLogger().handlers 
                     if isinstance(h, (logging.FileHandler, logging.handlers.RotatingFileHandler))]
    if file_handlers:
        logger.warning("Обнаружены файловые обработчики логирования после импортов, удаляем их...")
        for handler in file_handlers:
            logging.getLogger().removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        logger.info("Файловые обработчики удалены. Логирование только в консоль.")
    
    # Проверка write credentials (после загрузки конфигурации Ozon)
    try:
        if not has_write_credentials():
            logger.warning(
                "Write-доступ к Ozon не задан (OZON_WRITE_*). "
                "Отправка ответов/сообщений будет недоступна."
            )
    except Exception as e:
        logger.debug("Не удалось проверить write credentials: %s", e)
    
    # Логирование storage
    logger.info("Storage ROOT: %s", STORAGE_ROOT)
    try:
        STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
        probe_path = STORAGE_ROOT / ".rw_probe"
        probe_path.write_text("ok", encoding="utf-8")
        content = probe_path.read_text(encoding="utf-8").strip()
        if content == "ok":
            logger.info("Storage доступен для чтения/записи: %s", STORAGE_ROOT)
        probe_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Проблема с storage: %s", exc)
    
    # Создание бота и диспетчера
    logger.info("Инициализация бота...")
    try:
        # Создаем кастомную сессию с настройкой SSL (для работы через прокси/корп. сети)
        session = create_bot_session_from_env()
        bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            session=session,
        )
        logger.info("Бот создан с кастомной сессией (SSL настроен из env)")
    except Exception as e:
        logger.warning("Не удалось создать кастомную сессию, используем default: %s", e)
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    
    dp = Dispatcher(storage=MemoryStorage())
    
    # ========== MIDDLEWARE INTEGRATION (Phase 1 - Foundation) ==========
    # Порядок middleware критичен! Не меняйте без необходимости.
    
    # 1. Логирование (первым, чтобы логировать все события)
    from botapp.core.middleware import LoggingMiddleware
    logging_middleware = LoggingMiddleware(log_text=False, log_data=False)
    dp.message.middleware(logging_middleware)
    dp.callback_query.middleware(logging_middleware)
    logger.info("[+] LoggingMiddleware активирован")
    
    # 2. Метрики (рано, чтобы собирать статистику по всем запросам)
    from botapp.core.middleware import MetricsMiddleware
    metrics_middleware = MetricsMiddleware()
    dp.message.middleware(metrics_middleware)
    dp.callback_query.middleware(metrics_middleware)
    logger.info("[+] MetricsMiddleware активирован")
    
    # 3. Аутентификация и авторизация
    from botapp.core.middleware import AuthMiddleware
    from botapp.core.middleware.auth import Role
    
    # Получаем admin ID из переменной окружения
    admin_ids = []
    admin_ids_str = _env("ADMIN_IDS", "BOT_ADMIN_IDS")
    if admin_ids_str:
        admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]
    
    if not admin_ids:
        logger.warning("[!] ADMIN_IDS не заданы в .env - используется базовая защита")
    
    auth_middleware = AuthMiddleware(
        admins=admin_ids,
        moderators=[],
        blocked_users=[],
        default_role=Role.USER,
    )
    dp.message.middleware(auth_middleware)
    dp.callback_query.middleware(auth_middleware)
    logger.info("[+] AuthMiddleware активирован (admins: %s)", len(admin_ids))
    
    # 4. Rate Limiting (после auth, чтобы можно было исключить админов)
    from botapp.core.middleware import RateLimitMiddleware
    rate_limit_middleware = RateLimitMiddleware(
        rate=20,  # 20 запросов
        per=60,   # за 60 секунд
        exclude_commands=["/start", "/help"],
        exclude_users=admin_ids,  # Админы не ограничиваются
    )
    dp.message.middleware(rate_limit_middleware)
    dp.callback_query.middleware(rate_limit_middleware)
    logger.info("[+] RateLimitMiddleware активирован (20 req/60s)")
    
    # 5. Обработка ошибок (последним, чтобы ловить все ошибки)
    from botapp.core.middleware import ErrorHandlerMiddleware
    error_handler_middleware = ErrorHandlerMiddleware(
        send_to_user=True,
        notify_admin=True,
        admin_chat_id=admin_ids[0] if admin_ids else None,
    )
    dp.message.middleware(error_handler_middleware)
    dp.callback_query.middleware(error_handler_middleware)
    logger.info("[+] ErrorHandlerMiddleware активирован")
    
    logger.info("=" * 60)
    logger.info("[*] ВСЕ MIDDLEWARE АКТИВИРОВАНЫ (Production-Ready)")
    logger.info("=" * 60)
    
    # ========== END MIDDLEWARE INTEGRATION ==========
    
    # Передаем ссылку на metrics middleware в admin handlers
    from botapp.admin_handlers import set_metrics_middleware
    set_metrics_middleware(metrics_middleware)
    logger.info("[+] Admin handlers настроены")
    
    dp.include_router(root_router)
    
    # Инициализация Ozon клиента
    logger.info("Инициализация Ozon клиента...")
    get_client()  # Старый клиент (для обратной совместимости)
    
    # Проверка Ozon API клиентов (используется проверенный OzonClient из ozon_client.py)
    try:
        from botapp.ozon_client import get_write_client
        default_client = get_client()
        logger.info("[+] Ozon API клиент инициализирован (default)")
        
        # Проверка write клиента
        if has_write_credentials():
            write_client = get_write_client()
            logger.info("[+] Write клиент инициализирован")
        else:
            logger.info("[i] Write клиент не настроен (OZON_WRITE_* переменные не заданы)")
            
    except Exception as e:
        logger.error("[!] Ошибка инициализации Ozon API клиентов: %s", e)
        logger.error("Проверьте OZON_CLIENT_ID и OZON_API_KEY в .env файле")
        sys.exit(1)
    
    # Запуск chat autoreply loop
    autoreply_stop = asyncio.Event()
    autoreply_task = None
    try:
        autoreply_task = asyncio.create_task(chat_autoreply_loop(autoreply_stop))
        logger.info("Chat autoreply loop запущен")
    except Exception as e:
        logger.warning("Не удалось запустить chat autoreply loop: %s", e)
    
    # Инициализация системы уведомлений
    try:
        from botapp.notifications import (
            set_bot as set_notification_bot,
            start_notification_checker,
            router as notifications_router,
        )
        set_notification_bot(bot)
        dp.include_router(notifications_router)
        start_notification_checker()
        logger.info("[+] Система уведомлений запущена")
    except Exception as e:
        logger.warning("Не удалось запустить систему уведомлений: %s", e)
    
    # Render Web Service требует открытый HTTP порт.
    # Если переменная PORT задана, поднимаем минимальный health endpoint.
    health_runner = None
    port_raw = _env("PORT")
    if port_raw:
        try:
            health_runner = await _start_health_server(int(port_raw))
        except Exception as e:
            logger.error("Не удалось запустить health server на PORT=%s: %s", port_raw, e)
            raise

    # На Windows Ctrl+C обрабатывается через KeyboardInterrupt
    
    try:
        logger.info("=" * 60)
        logger.info("Бот запущен локально!")
        logger.info("Нажмите Ctrl+C для остановки")
        logger.info("=" * 60)
        
        # Запуск polling
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=_env("DROP_PENDING_UPDATES", default="1") == "1",
        )
    except KeyboardInterrupt:
        logger.info("Получен сигнал KeyboardInterrupt (Ctrl+C)...")
    except asyncio.CancelledError:
        logger.info("Операция отменена...")
    except Exception as e:
        error_msg = str(e)
        if "SSL" in error_msg or "ssl" in error_msg or "WRONG_VERSION_NUMBER" in error_msg:
            logger.error("=" * 60)
            logger.error("ОШИБКА SSL ПОДКЛЮЧЕНИЯ")
            logger.error("=" * 60)
            logger.error("Не удалось установить SSL соединение с api.telegram.org")
            logger.error("Это часто происходит при работе через прокси или корпоративный файрвол.")
            logger.error("")
            logger.error("РЕШЕНИЕ:")
            logger.error("1. Добавьте в файл .env строку: TG_DISABLE_SSL_VERIFY=1")
            logger.error("   (ВНИМАНИЕ: это небезопасно, используйте только для разработки!)")
            logger.error("")
            logger.error("2. Или укажите путь к CA сертификату: TG_SSL_CA_FILE=/path/to/ca.pem")
            logger.error("=" * 60)
        raise
    finally:
        logger.info("Завершение работы бота...")
        
        # Остановка chat autoreply loop
        if autoreply_task:
            autoreply_stop.set()
            autoreply_task.cancel()
            try:
                await autoreply_task
            except asyncio.CancelledError:
                pass
            logger.info("Chat autoreply loop остановлен")
        
        # Остановка системы уведомлений
        try:
            from botapp.notifications import stop_notification_checker
            stop_notification_checker()
            logger.info("Система уведомлений остановлена")
        except Exception as e:
            logger.warning("Ошибка при остановке уведомлений: %s", e)
        
        # Сохранение данных
        try:
            flush_storage()
            logger.info("Данные сохранены")
        except Exception as e:
            logger.warning("Ошибка при сохранении данных: %s", e)
        
        # Вывод метрик middleware
        try:
            logger.info("=" * 60)
            logger.info("[*] МЕТРИКИ БОТА")
            logger.info("=" * 60)
            metrics = metrics_middleware.get_metrics()
            logger.info("Total requests: %s", metrics.total_requests)
            logger.info("Successful: %s (%.1f%%)", metrics.successful_requests, metrics.success_rate)
            logger.info("Failed: %s (%.1f%%)", metrics.failed_requests, metrics.error_rate)
            logger.info("Avg response time: %.0fms", metrics.avg_response_time)
            
            if metrics.command_usage:
                logger.info("")
                logger.info("Top commands:")
                top_commands = sorted(metrics.command_usage.items(), key=lambda x: x[1], reverse=True)[:5]
                for cmd, count in top_commands:
                    logger.info("  %s: %s", cmd, count)
            
            if metrics.errors_by_type:
                logger.info("")
                logger.info("Errors by type:")
                for error_type, count in metrics.errors_by_type.items():
                    logger.info("  %s: %s", error_type, count)
            
            logger.info("=" * 60)
        except Exception as e:
            logger.debug("Не удалось получить метрики middleware: %s", e)
        
        # Остановка health server (если запускался)
        if health_runner:
            try:
                await health_runner.cleanup()
                logger.info("Health server остановлен")
            except Exception as e:
                logger.warning("Ошибка при остановке health server: %s", e)

        # Закрытие Ozon клиентов
        try:
            await close_clients()
            logger.info("Ozon клиенты закрыты")
        except Exception as e:
            logger.warning("Ошибка при закрытии Ozon клиентов: %s", e)
        
        # Закрытие сессии бота
        try:
            await bot.session.close()
            logger.info("Сессия бота закрыта")
        except Exception as e:
            logger.warning("Ошибка при закрытии сессии бота: %s", e)
        
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Завершение по Ctrl+C")
    except Exception as e:
        logger.exception("Критическая ошибка: %s", e)
        sys.exit(1)
