# botapp/logging_config.py
"""
Продвинутая конфигурация логирования с ротацией файлов
На основе лучших практик из a-ulianov/OzonAPI
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def setup_logging(
    *,
    log_level: str | None = None,
    log_file: str | Path | None = None,
    log_max_bytes: int = 10485760,  # 10MB
    log_backup_count: int = 5,
    enable_console: bool = True,
    enable_file: bool = True,
    format_string: Optional[str] = None,
) -> None:
    """
    Настроить логирование с ротацией файлов
    
    Args:
        log_level: Уровень логирования (DEBUG, INFO, WARNING, ERROR)
        log_file: Путь к файлу логов
        log_max_bytes: Максимальный размер файла лога перед ротацией
        log_backup_count: Количество backup файлов
        enable_console: Включить вывод в консоль
        enable_file: Включить вывод в файл
        format_string: Кастомный формат логов
    """
    
    # Определение уровня логирования
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    
    numeric_level = getattr(logging, log_level, logging.INFO)
    
    # Определение файла логов
    # Если log_file явно None и enable_file=False, не используем файл вообще
    if log_file is None and enable_file:
        log_file = os.getenv("LOG_FILE", "logs/bot.log")
    elif log_file is None:
        log_file = None  # Явно отключаем файловое логирование
    
    log_file_path = Path(log_file) if log_file else None
    
    # Формат логов
    if format_string is None:
        format_string = os.getenv(
            "LOG_FORMAT",
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
    
    # Расширенный формат для файлов
    file_format_string = (
        "%(asctime)s - %(name)s - %(levelname)s - "
        "%(filename)s:%(lineno)d - %(funcName)s - %(message)s"
    )
    
    # Настройка корневого логгера
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Удаление существующих обработчиков (включая файловые, если они есть)
    # ВАЖНО: Удаляем ВСЕ обработчики, включая файловые, чтобы избежать дублирования
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        if hasattr(handler, 'close'):
            try:
                handler.close()
            except Exception:
                pass
    
    # Обработчик для консоли
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_formatter = logging.Formatter(format_string)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
    
    # Обработчик для файла с ротацией
    # ВАЖНО: Создаем файловый handler ТОЛЬКО если явно включен И указан путь к файлу
    if enable_file and log_file_path and log_file_path is not None:
        try:
            # Создание директории для логов
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Rotating file handler
            file_handler = logging.handlers.RotatingFileHandler(
                filename=str(log_file_path),
                maxBytes=log_max_bytes,
                backupCount=log_backup_count,
                encoding='utf-8',
            )
            file_handler.setLevel(numeric_level)
            file_formatter = logging.Formatter(file_format_string)
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)
            
            root_logger.info(
                "Логирование настроено: level=%s, file=%s, max_bytes=%s, backups=%s",
                log_level,
                log_file_path,
                log_max_bytes,
                log_backup_count,
            )
        except Exception as e:
            print(f"ОШИБКА при настройке файлового логирования: {e}", file=sys.stderr)
            # Продолжаем работу с консольным логированием
    elif enable_console:
        # Только консольное логирование
        root_logger.info("Логирование настроено: level=%s, output=console only", log_level)
    
    # Настройка уровня для сторонних библиотек
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    # Более детальное логирование для критичных компонентов
    logging.getLogger("botapp.ozon_api_client").setLevel(numeric_level)
    logging.getLogger("botapp.ozon_client").setLevel(numeric_level)
    logging.getLogger("botapp").setLevel(numeric_level)


def setup_structured_logging(
    *,
    log_level: str | None = None,
    log_file: str | Path | None = None,
    enable_json: bool = False,
) -> None:
    """
    Настроить структурированное логирование (JSON формат)
    
    Args:
        log_level: Уровень логирования
        log_file: Путь к файлу логов
        enable_json: Включить JSON формат
    """
    if not enable_json:
        setup_logging(log_level=log_level, log_file=log_file)
        return
    
    # Для JSON логирования требуется дополнительная библиотека
    try:
        import json_log_formatter
        
        if log_level is None:
            log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        
        numeric_level = getattr(logging, log_level, logging.INFO)
        
        if log_file is None:
            log_file = os.getenv("LOG_FILE", "logs/bot.json")
        
        log_file_path = Path(log_file)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # JSON formatter
        json_formatter = json_log_formatter.JSONFormatter()
        
        # File handler с ротацией
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(log_file_path),
            maxBytes=10485760,
            backupCount=5,
            encoding='utf-8',
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(json_formatter)
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        
        # Настройка корневого логгера
        root_logger = logging.getLogger()
        root_logger.setLevel(numeric_level)
        root_logger.handlers.clear()
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        
        root_logger.info("Структурированное JSON логирование настроено")
        
    except ImportError:
        # Если библиотека не установлена, используем обычное логирование
        logging.warning("json-log-formatter не установлен, используется обычное логирование")
        setup_logging(log_level=log_level, log_file=log_file)


def get_logger(name: str) -> logging.Logger:
    """
    Получить логгер с настроенным именем
    
    Args:
        name: Имя логгера (обычно __name__)
    
    Returns:
        Настроенный логгер
    """
    return logging.getLogger(name)


class PerformanceLogger:
    """Логгер для отслеживания производительности"""
    
    def __init__(self, logger: logging.Logger, operation_name: str):
        self.logger = logger
        self.operation_name = operation_name
        self.start_time = None
    
    def __enter__(self):
        import time
        self.start_time = time.time()
        self.logger.debug("Начало операции: %s", self.operation_name)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        import time
        duration = time.time() - self.start_time
        
        if exc_type is not None:
            self.logger.error(
                "Операция %s завершилась с ошибкой за %.3fs: %s",
                self.operation_name,
                duration,
                exc_val,
            )
        else:
            if duration > 5.0:
                self.logger.warning(
                    "Медленная операция %s: %.3fs",
                    self.operation_name,
                    duration,
                )
            else:
                self.logger.debug(
                    "Операция %s завершена за %.3fs",
                    self.operation_name,
                    duration,
                )
        
        return False  # Не подавлять исключения


def log_performance(operation_name: str):
    """
    Декоратор для логирования производительности функций
    
    Usage:
        @log_performance("get_product_list")
        async def get_products():
            ...
    """
    def decorator(func):
        import functools
        import time
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger = logging.getLogger(func.__module__)
            start_time = time.time()
            
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                
                if duration > 5.0:
                    logger.warning(
                        "Медленная async операция %s: %.3fs",
                        operation_name,
                        duration,
                    )
                else:
                    logger.debug(
                        "Async операция %s завершена за %.3fs",
                        operation_name,
                        duration,
                    )
                
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    "Ошибка в async операции %s за %.3fs: %s",
                    operation_name,
                    duration,
                    e,
                    exc_info=True,
                )
                raise
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger = logging.getLogger(func.__module__)
            start_time = time.time()
            
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                
                if duration > 5.0:
                    logger.warning(
                        "Медленная операция %s: %.3fs",
                        operation_name,
                        duration,
                    )
                else:
                    logger.debug(
                        "Операция %s завершена за %.3fs",
                        operation_name,
                        duration,
                    )
                
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    "Ошибка в операции %s за %.3fs: %s",
                    operation_name,
                    duration,
                    e,
                    exc_info=True,
                )
                raise
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator
