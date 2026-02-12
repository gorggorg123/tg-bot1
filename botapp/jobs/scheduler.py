# botapp/jobs/scheduler.py
"""
JobQueue на основе APScheduler (адаптировано из python-telegram-bot).

Управление периодическими и отложенными задачами.
"""

from __future__ import annotations

import logging
from typing import Callable, Any, Optional
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor

logger = logging.getLogger(__name__)

# Глобальный экземпляр scheduler
_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Получить глобальный экземпляр scheduler."""
    global _scheduler
    if _scheduler is None:
        jobstores = {
            'default': MemoryJobStore()
        }
        executors = {
            'default': AsyncIOExecutor()
        }
        job_defaults = {
            'coalesce': True,  # Объединять несколько пропущенных запусков в один
            'max_instances': 3,  # Максимум параллельных экземпляров задачи
            'misfire_grace_time': 30  # Время ожидания перед пропуском задачи
        }
        
        _scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone='UTC'
        )
        logger.info("JobQueue scheduler created")
    return _scheduler


def start_scheduler() -> None:
    """Запустить scheduler."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("JobQueue scheduler started")


def stop_scheduler() -> None:
    """Остановить scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=True)
        logger.info("JobQueue scheduler stopped")


def add_interval_job(
    func: Callable,
    seconds: int,
    job_id: Optional[str] = None,
    **kwargs
) -> str:
    """
    Добавить периодическую задачу.
    
    Args:
        func: Асинхронная функция для выполнения
        seconds: Интервал в секундах
        job_id: Уникальный ID задачи (если не указан, генерируется автоматически)
        **kwargs: Дополнительные аргументы для функции
    
    Returns:
        ID задачи
    """
    scheduler = get_scheduler()
    trigger = IntervalTrigger(seconds=seconds)
    
    if job_id is None:
        job_id = f"{func.__name__}_{id(func)}"
    
    scheduler.add_job(
        func,
        trigger=trigger,
        id=job_id,
        replace_existing=True,
        **kwargs
    )
    logger.info("Added interval job: %s (every %s seconds)", job_id, seconds)
    return job_id


def add_cron_job(
    func: Callable,
    hour: Optional[int] = None,
    minute: Optional[int] = None,
    day_of_week: Optional[str] = None,
    job_id: Optional[str] = None,
    **kwargs
) -> str:
    """
    Добавить задачу по расписанию (cron).
    
    Args:
        func: Асинхронная функция для выполнения
        hour: Час (0-23)
        minute: Минута (0-59)
        day_of_week: День недели ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
        job_id: Уникальный ID задачи
        **kwargs: Дополнительные аргументы для функции
    
    Returns:
        ID задачи
    """
    scheduler = get_scheduler()
    trigger = CronTrigger(hour=hour, minute=minute, day_of_week=day_of_week)
    
    if job_id is None:
        job_id = f"{func.__name__}_cron_{id(func)}"
    
    scheduler.add_job(
        func,
        trigger=trigger,
        id=job_id,
        replace_existing=True,
        **kwargs
    )
    logger.info("Added cron job: %s (hour=%s, minute=%s)", job_id, hour, minute)
    return job_id


def add_delayed_job(
    func: Callable,
    run_date: datetime,
    job_id: Optional[str] = None,
    **kwargs
) -> str:
    """
    Добавить отложенную задачу (one-time).
    
    Args:
        func: Асинхронная функция для выполнения
        run_date: Дата и время запуска
        job_id: Уникальный ID задачи
        **kwargs: Дополнительные аргументы для функции
    
    Returns:
        ID задачи
    """
    scheduler = get_scheduler()
    trigger = DateTrigger(run_date=run_date)
    
    if job_id is None:
        job_id = f"{func.__name__}_delayed_{id(func)}"
    
    scheduler.add_job(
        func,
        trigger=trigger,
        id=job_id,
        replace_existing=True,
        **kwargs
    )
    logger.info("Added delayed job: %s (run_date=%s)", job_id, run_date)
    return job_id


def remove_job(job_id: str) -> bool:
    """Удалить задачу по ID."""
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(job_id)
        logger.info("Removed job: %s", job_id)
        return True
    except Exception as e:
        logger.warning("Failed to remove job %s: %s", job_id, e)
        return False


def get_job(job_id: str) -> Optional[Any]:
    """Получить задачу по ID."""
    scheduler = get_scheduler()
    return scheduler.get_job(job_id)


def list_jobs() -> list[dict]:
    """Получить список всех задач."""
    scheduler = get_scheduler()
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            'id': job.id,
            'name': job.name,
            'next_run_time': job.next_run_time,
            'trigger': str(job.trigger),
        })
    return jobs


__all__ = [
    "get_scheduler",
    "start_scheduler",
    "stop_scheduler",
    "add_interval_job",
    "add_cron_job",
    "add_delayed_job",
    "remove_job",
    "get_job",
    "list_jobs",
]
