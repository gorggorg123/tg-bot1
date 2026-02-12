"""
Admin handlers for bot management and monitoring
Requires admin permissions
"""
from __future__ import annotations

import logging
import platform
import sys
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from botapp.core.middleware.auth import Permission, require_permission

logger = logging.getLogger(__name__)
router = Router()

# Global reference to metrics middleware (will be set in run_local.py)
_metrics_middleware = None


def set_metrics_middleware(middleware):
    """Set global reference to metrics middleware"""
    global _metrics_middleware
    _metrics_middleware = middleware


@router.message(Command("metrics"))
async def cmd_metrics(message: Message, user_permissions: set[Permission] | None = None):
    """
    Показать метрики бота (только для админов)
    
    Требует Permission.VIEW_LOGS
    """
    # Проверка прав
    if not user_permissions or Permission.VIEW_LOGS not in user_permissions:
        await message.answer("❌ У вас нет прав для просмотра метрик")
        return
    
    if not _metrics_middleware:
        await message.answer("❌ Метрики недоступны (middleware не инициализирован)")
        return
    
    try:
        metrics = _metrics_middleware.get_metrics()
        
        # Форматирование uptime
        uptime = datetime.utcnow() - metrics.last_reset
        uptime_str = str(uptime).split('.')[0]  # Убираем микросекунды
        
        text = (
            f"📊 <b>Метрики бота</b>\n\n"
            f"<b>Общая статистика:</b>\n"
            f"├ Всего запросов: {metrics.total_requests}\n"
            f"├ Успешных: {metrics.successful_requests} ({metrics.success_rate:.1f}%)\n"
            f"├ Ошибок: {metrics.failed_requests} ({metrics.error_rate:.1f}%)\n"
            f"└ Среднее время ответа: {metrics.avg_response_time:.0f}ms\n\n"
        )
        
        # Топ команд
        if metrics.command_usage:
            text += "<b>Топ-5 команд:</b>\n"
            top_commands = sorted(
                metrics.command_usage.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]
            for i, (cmd, count) in enumerate(top_commands, 1):
                text += f"{i}. {cmd}: {count}\n"
            text += "\n"
        
        # Активные пользователи
        if metrics.requests_by_user:
            text += f"<b>Активных пользователей:</b> {len(metrics.requests_by_user)}\n\n"
        
        # Ошибки
        if metrics.errors_by_type:
            text += "<b>Ошибки по типам:</b>\n"
            for error_type, count in sorted(
                metrics.errors_by_type.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]:
                text += f"├ {error_type}: {count}\n"
            text += "\n"
        
        text += f"<i>Uptime: {uptime_str}</i>"
        
        await message.answer(text, parse_mode="HTML")
        
    except Exception as e:
        logger.exception("Error getting metrics")
        await message.answer(f"❌ Ошибка при получении метрик: {e}")


@router.message(Command("health"))
async def cmd_health(message: Message, user_permissions: set[Permission] | None = None):
    """
    Показать статус здоровья бота (только для админов)
    
    Требует Permission.VIEW_LOGS
    """
    # Проверка прав
    if not user_permissions or Permission.VIEW_LOGS not in user_permissions:
        await message.answer("❌ У вас нет прав для просмотра статуса")
        return
    
    try:
        # System info
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        
        # Metrics
        status_emoji = "✅"
        status_text = "Healthy"
        
        if _metrics_middleware:
            metrics = _metrics_middleware.get_metrics()
            
            # Check health conditions
            if metrics.total_requests > 100:
                if metrics.error_rate > 10:
                    status_emoji = "🔴"
                    status_text = "Unhealthy (high error rate)"
                elif metrics.error_rate > 5:
                    status_emoji = "⚠️"
                    status_text = "Degraded (elevated error rate)"
            
            if metrics.avg_response_time > 2000:
                status_emoji = "⚠️"
                status_text = "Degraded (slow response)"
        
        text = (
            f"{status_emoji} <b>Статус: {status_text}</b>\n\n"
            f"<b>Система:</b>\n"
            f"├ Python: {python_version}\n"
            f"├ Platform: {platform.system()} {platform.release()}\n"
            f"└ Architecture: {platform.machine()}\n\n"
        )
        
        # Metrics health
        if _metrics_middleware:
            metrics = _metrics_middleware.get_metrics()
            text += (
                f"<b>Производительность:</b>\n"
                f"├ Запросов: {metrics.total_requests}\n"
                f"├ Success rate: {metrics.success_rate:.1f}%\n"
                f"├ Error rate: {metrics.error_rate:.1f}%\n"
                f"└ Avg response: {metrics.avg_response_time:.0f}ms\n\n"
            )
            
            # Health indicators
            health_indicators = []
            if metrics.total_requests > 0:
                if metrics.success_rate >= 99:
                    health_indicators.append("✅ Excellent uptime")
                elif metrics.success_rate >= 95:
                    health_indicators.append("✅ Good uptime")
                else:
                    health_indicators.append("⚠️ Poor uptime")
                
                if metrics.avg_response_time < 500:
                    health_indicators.append("✅ Fast response")
                elif metrics.avg_response_time < 1000:
                    health_indicators.append("⚠️ Acceptable response")
                else:
                    health_indicators.append("🔴 Slow response")
            
            if health_indicators:
                text += "<b>Индикаторы:</b>\n"
                for indicator in health_indicators:
                    text += f"{indicator}\n"
        
        await message.answer(text, parse_mode="HTML")
        
    except Exception as e:
        logger.exception("Error getting health status")
        await message.answer(f"❌ Ошибка при получении статуса: {e}")


@router.message(Command("reset_metrics"))
async def cmd_reset_metrics(message: Message, user_permissions: set[Permission] | None = None):
    """
    Сбросить метрики (только для админов)
    
    Требует Permission.MANAGE_SETTINGS
    """
    # Проверка прав
    if not user_permissions or Permission.MANAGE_SETTINGS not in user_permissions:
        await message.answer("❌ У вас нет прав для сброса метрик")
        return
    
    if not _metrics_middleware:
        await message.answer("❌ Метрики недоступны")
        return
    
    try:
        _metrics_middleware.reset_metrics()
        await message.answer("✅ Метрики сброшены")
        logger.info("Metrics reset by admin user_id=%s", message.from_user.id)
        
    except Exception as e:
        logger.exception("Error resetting metrics")
        await message.answer(f"❌ Ошибка при сбросе метрик: {e}")


@router.message(Command("ai_stats"))
async def cmd_ai_stats(message: Message, user_permissions: set[Permission] | None = None):
    """
    Показать статистику AI-генерации (только для админов)
    
    Показывает:
    - Количество запросов (успешных/неудачных)
    - Использование токенов
    - Примерную стоимость
    - Среднее время ответа
    - Количество ретраев
    - Типы ошибок
    
    Требует Permission.VIEW_LOGS
    """
    # Проверка прав
    if not user_permissions or Permission.VIEW_LOGS not in user_permissions:
        await message.answer("❌ У вас нет прав для просмотра статистики AI")
        return
    
    try:
        from botapp.ai_client import get_ai_metrics
        
        metrics = get_ai_metrics()
        stats = metrics.as_dict()
        
        text = (
            f"🤖 <b>Статистика AI-генерации</b>\n\n"
            f"<b>Запросы:</b>\n"
            f"├ Всего: {stats['total_requests']}\n"
            f"├ Успешных: {stats['successful_requests']}\n"
            f"├ Ошибок: {stats['failed_requests']}\n"
            f"└ Success rate: {stats['success_rate']}\n\n"
            f"<b>Токены:</b>\n"
            f"├ Input: {stats['total_input_tokens']:,}\n"
            f"├ Output: {stats['total_output_tokens']:,}\n"
            f"└ Всего: {stats['total_input_tokens'] + stats['total_output_tokens']:,}\n\n"
            f"<b>Производительность:</b>\n"
            f"├ Avg latency: {stats['avg_latency_ms']:.0f}ms\n"
            f"├ Retries: {stats['total_retries']}\n"
            f"└ Стоимость: ${stats['estimated_cost_usd']:.4f}\n"
        )
        
        # Ошибки по типам
        if stats['errors_by_type']:
            text += "\n<b>Ошибки по типам:</b>\n"
            for error_type, count in sorted(
                stats['errors_by_type'].items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]:
                text += f"├ {error_type}: {count}\n"
        
        if stats['last_request_at']:
            text += f"\n<i>Последний запрос: {stats['last_request_at'][:19]}</i>"
        
        await message.answer(text, parse_mode="HTML")
        
    except ImportError:
        await message.answer("❌ Модуль AI-клиента не загружен")
    except Exception as e:
        logger.exception("Error getting AI stats")
        await message.answer(f"❌ Ошибка при получении статистики AI: {e}")


@router.message(Command("ai_reset"))
async def cmd_ai_reset(message: Message, user_permissions: set[Permission] | None = None):
    """
    Сбросить статистику AI (только для админов)
    
    Требует Permission.MANAGE_SETTINGS
    """
    # Проверка прав
    if not user_permissions or Permission.MANAGE_SETTINGS not in user_permissions:
        await message.answer("❌ У вас нет прав для сброса статистики AI")
        return
    
    try:
        from botapp.ai_client import reset_ai_metrics
        
        reset_ai_metrics()
        await message.answer("✅ Статистика AI сброшена")
        logger.info("AI metrics reset by admin user_id=%s", message.from_user.id)
        
    except ImportError:
        await message.answer("❌ Модуль AI-клиента не загружен")
    except Exception as e:
        logger.exception("Error resetting AI stats")
        await message.answer(f"❌ Ошибка при сбросе статистики AI: {e}")
