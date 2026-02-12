"""
Health Checker
=============
Проверка состояния всех компонентов системы
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Статус здоровья компонента"""
    name: str
    healthy: bool
    message: str
    latency_ms: Optional[float] = None
    last_check: Optional[datetime] = None
    details: Dict[str, Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "healthy": self.healthy,
            "message": self.message,
            "latency_ms": round(self.latency_ms, 2) if self.latency_ms else None,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "details": self.details or {},
        }


class HealthChecker:
    """Проверка здоровья компонентов системы"""
    
    def __init__(self):
        self._checks: Dict[str, Callable[[], Awaitable[HealthStatus]]] = {}
        self._last_results: Dict[str, HealthStatus] = {}
    
    def register_check(
        self,
        name: str,
        check_func: Callable[[], Awaitable[HealthStatus]],
    ) -> None:
        """Зарегистрировать проверку здоровья"""
        self._checks[name] = check_func
        logger.info("Health check registered: %s", name)
    
    async def check(self, name: str) -> HealthStatus:
        """Выполнить конкретную проверку"""
        if name not in self._checks:
            return HealthStatus(
                name=name,
                healthy=False,
                message=f"Check {name} not found"
            )
        
        try:
            result = await self._checks[name]()
            self._last_results[name] = result
            return result
        except Exception as e:
            logger.error("Health check %s failed: %s", name, e)
            result = HealthStatus(
                name=name,
                healthy=False,
                message=f"Check failed: {e}"
            )
            self._last_results[name] = result
            return result
    
    async def check_all(self) -> Dict[str, HealthStatus]:
        """Выполнить все проверки"""
        results = await asyncio.gather(
            *[self.check(name) for name in self._checks.keys()],
            return_exceptions=True
        )
        
        return {
            name: result
            for name, result in zip(self._checks.keys(), results)
            if isinstance(result, HealthStatus)
        }
    
    def get_last_results(self) -> Dict[str, HealthStatus]:
        """Получить результаты последних проверок"""
        return self._last_results.copy()
    
    def get_summary(self) -> Dict[str, Any]:
        """Получить сводку здоровья"""
        all_healthy = all(
            status.healthy
            for status in self._last_results.values()
        )
        
        unhealthy = [
            status.name
            for status in self._last_results.values()
            if not status.healthy
        ]
        
        return {
            "healthy": all_healthy,
            "total_checks": len(self._checks),
            "passing": len(self._last_results) - len(unhealthy),
            "failing": len(unhealthy),
            "unhealthy_components": unhealthy,
            "details": {
                name: status.to_dict()
                for name, status in self._last_results.items()
            }
        }


# ===== Глобальный экземпляр =====

_health_checker: Optional[HealthChecker] = None


def get_health_checker() -> HealthChecker:
    """Получить глобальный health checker"""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker


__all__ = ["HealthChecker", "HealthStatus", "get_health_checker"]
