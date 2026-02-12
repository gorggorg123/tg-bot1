# botapp/ozon_api_config.py
"""
Продвинутая конфигурация для OzonAPI на основе a-ulianov/OzonAPI
Поддерживает множественные аккаунты, OAuth, настраиваемые лимиты и ретраи.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _env(*names: str, default: str = "") -> str:
    """Возвращает первое непустое значение из списка переменных окружения."""
    for name in names:
        if not name:
            continue
        value = os.getenv(name)
        if value is not None:
            value = str(value).strip()
            if value:
                return value
    return default


@dataclass
class OzonAPIConfigProfile:
    """Профиль конфигурации для одного аккаунта Ozon"""
    
    # Идентификатор профиля (для множественных аккаунтов)
    profile_name: str = "default"
    
    # Авторизация (классическая или OAuth)
    client_id: str = ""
    api_key: str = ""
    oauth_token: str | None = None
    
    # Rate limiting
    max_requests_per_second: int = 27  # Рекомендованное значение из a-ulianov/OzonAPI
    connector_limit: int = 100  # Максимум одновременных соединений
    connector_limit_per_host: int = 30
    
    # Retry механизм
    max_retries: int = 5
    retry_min_wait: float = 1.0  # Минимальная задержка между ретраями (сек)
    retry_max_wait: float = 10.0  # Максимальная задержка (сек)
    retry_multiplier: float = 2.0  # Множитель для экспоненциального backoff
    
    # Timeouts
    total_timeout: float = 60.0  # Общий таймаут для запроса
    connect_timeout: float = 10.0  # Таймаут на подключение
    sock_read_timeout: float = 30.0  # Таймаут на чтение
    
    # Логирование
    enable_request_logging: bool = True
    log_level: str = "INFO"
    log_file: str | None = None
    log_max_bytes: int = 10485760  # 10MB
    log_backup_files_count: int = 5
    
    # Кеширование
    enable_cache: bool = True
    cache_ttl_seconds: int = 3600  # 1 час для статичных данных
    cache_dir: Path | None = None
    
    # Дополнительные параметры
    user_agent: str | None = None
    extra_headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class OzonAPIConfig:
    """Глобальная конфигурация для всех профилей Ozon API"""
    
    # Основной профиль (для обратной совместимости)
    default_profile: OzonAPIConfigProfile = field(default_factory=OzonAPIConfigProfile)
    
    # Дополнительные профили (для множественных аккаунтов)
    profiles: Dict[str, OzonAPIConfigProfile] = field(default_factory=dict)
    
    # Глобальные настройки
    enable_metrics: bool = False  # Сбор метрик производительности
    metrics_file: Path | None = None
    
    # Режим работы
    debug_mode: bool = False
    
    @classmethod
    def from_env(cls, env_prefix: str = "OZON") -> OzonAPIConfig:
        """Создать конфигурацию из переменных окружения"""

        env_prefix = (env_prefix or "OZON").upper()
        fallback_prefix = "OZON_SELLER" if env_prefix == "OZON" else ""

        def pick(name: str, alt: str | None = None, default: str = "") -> str:
            keys = [f"{env_prefix}_{name}"]
            if alt:
                keys.append(f"{env_prefix}_{alt}")
            if fallback_prefix:
                keys.append(f"{fallback_prefix}_{name}")
                if alt:
                    keys.append(f"{fallback_prefix}_{alt}")
            return _env(*keys, default=default)

        # Основной профиль
        default_profile = OzonAPIConfigProfile(
            profile_name="default",
            client_id=pick("CLIENT_ID"),
            api_key=pick("API_KEY"),
            oauth_token=pick("OAUTH_TOKEN") or None,
            max_requests_per_second=int(pick("MAX_RPS", "MAX_REQUESTS_PER_SECOND", default="27")),
            connector_limit=int(pick("CONNECTOR_LIMIT", default="100")),
            connector_limit_per_host=int(pick("CONNECTOR_LIMIT_PER_HOST", default="30")),
            max_retries=int(pick("MAX_RETRIES", default="5")),
            retry_min_wait=float(pick("RETRY_MIN_WAIT", default="1.0")),
            retry_max_wait=float(pick("RETRY_MAX_WAIT", default="10.0")),
            retry_multiplier=float(pick("RETRY_MULTIPLIER", default="2.0")),
            total_timeout=float(pick("TOTAL_TIMEOUT", "TIMEOUT", default="60.0")),
            connect_timeout=float(pick("CONNECT_TIMEOUT", default="10.0")),
            sock_read_timeout=float(pick("SOCK_READ_TIMEOUT", default="30.0")),
            enable_request_logging=pick("ENABLE_REQUEST_LOGGING", default="true").lower() == "true",
            log_level=pick("LOG_LEVEL", default="INFO").upper(),
            log_file=pick("LOG_FILE") or None,
            log_max_bytes=int(pick("LOG_MAX_BYTES", default="10485760")),
            log_backup_files_count=int(pick("LOG_BACKUP_FILES_COUNT", default="5")),
            enable_cache=pick("ENABLE_CACHE", default="true").lower() == "true",
            cache_ttl_seconds=int(pick("CACHE_TTL_SECONDS", default="3600")),
            cache_dir=Path(d) if (d := pick("CACHE_DIR")) else None,
            user_agent=pick("USER_AGENT") or None,
        )
        
        # Поддержка write credentials (для обратной совместимости)
        write_client_id = pick("WRITE_CLIENT_ID")
        write_api_key = pick("WRITE_API_KEY")
        
        profiles = {}
        if write_client_id and write_api_key:
            profiles["write"] = OzonAPIConfigProfile(
                profile_name="write",
                client_id=write_client_id,
                api_key=write_api_key,
                max_requests_per_second=default_profile.max_requests_per_second,
                max_retries=default_profile.max_retries,
            )
        
        return cls(
            default_profile=default_profile,
            profiles=profiles,
            enable_metrics=pick("ENABLE_METRICS", default="false").lower() == "true",
            metrics_file=Path(m) if (m := pick("METRICS_FILE")) else None,
            debug_mode=pick("DEBUG_MODE", default="false").lower() == "true",
        )
    
    def get_profile(self, profile_name: str = "default") -> OzonAPIConfigProfile:
        """Получить профиль по имени"""
        if profile_name == "default":
            return self.default_profile
        return self.profiles.get(profile_name, self.default_profile)
    
    def validate(self) -> list[str]:
        """Валидация конфигурации, возвращает список ошибок"""
        errors = []
        
        # Проверка основного профиля
        if not self.default_profile.client_id:
            errors.append("OZON_CLIENT_ID не установлен")
        if not self.default_profile.api_key and not self.default_profile.oauth_token:
            errors.append("OZON_API_KEY или OZON_OAUTH_TOKEN должен быть установлен")
        
        # Проверка лимитов
        if self.default_profile.max_requests_per_second < 1:
            errors.append(f"max_requests_per_second должен быть >= 1, получено: {self.default_profile.max_requests_per_second}")
        if self.default_profile.max_requests_per_second > 100:
            logger.warning("max_requests_per_second > 100 может привести к блокировке API")
        
        # Проверка retry параметров
        if self.default_profile.max_retries < 0:
            errors.append(f"max_retries должен быть >= 0, получено: {self.default_profile.max_retries}")
        if self.default_profile.retry_min_wait <= 0:
            errors.append(f"retry_min_wait должен быть > 0, получено: {self.default_profile.retry_min_wait}")
        if self.default_profile.retry_max_wait < self.default_profile.retry_min_wait:
            errors.append(f"retry_max_wait должен быть >= retry_min_wait")
        
        # Проверка timeout параметров
        if self.default_profile.total_timeout <= 0:
            errors.append(f"total_timeout должен быть > 0, получено: {self.default_profile.total_timeout}")
        if self.default_profile.connect_timeout <= 0:
            errors.append(f"connect_timeout должен быть > 0, получено: {self.default_profile.connect_timeout}")
        
        return errors


# Глобальный инстанс конфигурации
_global_config: OzonAPIConfig | None = None


def get_ozon_config() -> OzonAPIConfig:
    """Получить глобальную конфигурацию OzonAPI"""
    global _global_config
    if _global_config is None:
        _global_config = OzonAPIConfig.from_env()
        errors = _global_config.validate()
        if errors:
            logger.error("Ошибки валидации конфигурации OzonAPI:")
            for error in errors:
                logger.error("  - %s", error)
            raise ValueError(f"Неверная конфигурация OzonAPI: {'; '.join(errors)}")
        logger.info("OzonAPI конфигурация загружена успешно")
        logger.debug("  - max_requests_per_second: %s", _global_config.default_profile.max_requests_per_second)
        logger.debug("  - max_retries: %s", _global_config.default_profile.max_retries)
        logger.debug("  - enable_cache: %s", _global_config.default_profile.enable_cache)
    return _global_config


def reload_ozon_config() -> OzonAPIConfig:
    """Перезагрузить конфигурацию из переменных окружения"""
    global _global_config
    _global_config = None
    return get_ozon_config()
