# botapp/notifications/config.py
"""
Конфигурация и настройки уведомлений.

Основано на паттернах из a-ulianov/OzonAPI:
- Гибкая настройка через dataclass
- Хранение состояния для каждого пользователя
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

# Путь к файлу хранения настроек
NOTIFICATIONS_FILE = Path(__file__).parent.parent.parent / "data" / "notifications.json"


@dataclass
class NotificationSettings:
    """Настройки уведомлений для пользователя."""
    
    # Включены ли уведомления
    enabled: bool = True
    
    # Типы уведомлений
    reviews_enabled: bool = True          # Новые отзывы
    questions_enabled: bool = True        # Новые вопросы
    chats_enabled: bool = True            # Новые сообщения в чатах
    orders_fbo_enabled: bool = True       # Новые FBO заказы
    orders_fbs_enabled: bool = True       # Новые FBS заказы
    
    # Интервал проверки (в секундах)
    check_interval: int = 300  # 5 минут по умолчанию
    
    # Тихие часы (не отправлять уведомления)
    quiet_hours_start: int = 23  # С 23:00
    quiet_hours_end: int = 8     # До 08:00
    quiet_hours_enabled: bool = False
    
    # Последние проверенные ID/timestamps
    last_review_id: Optional[str] = None
    last_question_id: Optional[str] = None
    last_chat_check: Optional[str] = None  # ISO timestamp
    last_order_check: Optional[str] = None  # ISO timestamp
    
    # Статистика
    total_notifications_sent: int = 0
    last_notification_at: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Сериализация в словарь."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "NotificationSettings":
        """Десериализация из словаря."""
        # Фильтруем только известные поля
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class NotificationState:
    """Состояние системы уведомлений (кеш последних данных)."""
    
    # Известные ID (чтобы не дублировать уведомления)
    known_review_ids: Set[str] = field(default_factory=set)
    known_question_ids: Set[str] = field(default_factory=set)
    known_chat_ids: Set[str] = field(default_factory=set)
    known_order_numbers: Set[str] = field(default_factory=set)
    
    # Timestamps последних проверок
    last_reviews_check: Optional[datetime] = None
    last_questions_check: Optional[datetime] = None
    last_chats_check: Optional[datetime] = None
    last_orders_check: Optional[datetime] = None


# Глобальное хранилище настроек и состояний
_user_settings: Dict[int, NotificationSettings] = {}
_user_states: Dict[int, NotificationState] = {}


def _ensure_data_dir() -> None:
    """Создаёт директорию data если её нет."""
    NOTIFICATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_settings() -> None:
    """Загружает настройки из файла."""
    global _user_settings
    
    if not NOTIFICATIONS_FILE.exists():
        return
    
    try:
        with open(NOTIFICATIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        for user_id_str, settings_dict in data.items():
            user_id = int(user_id_str)
            _user_settings[user_id] = NotificationSettings.from_dict(settings_dict)
        
        logger.info("Loaded notification settings for %d users", len(_user_settings))
    except Exception as e:
        logger.error("Failed to load notification settings: %s", e)


def _save_settings() -> None:
    """Сохраняет настройки в файл."""
    _ensure_data_dir()
    
    try:
        data = {str(user_id): settings.to_dict() for user_id, settings in _user_settings.items()}
        
        with open(NOTIFICATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.debug("Saved notification settings for %d users", len(_user_settings))
    except Exception as e:
        logger.error("Failed to save notification settings: %s", e)


def get_user_settings(user_id: int) -> NotificationSettings:
    """Получить настройки уведомлений для пользователя."""
    if not _user_settings:
        _load_settings()
    
    if user_id not in _user_settings:
        _user_settings[user_id] = NotificationSettings()
        _save_settings()
    
    return _user_settings[user_id]


def update_user_settings(user_id: int, **kwargs) -> NotificationSettings:
    """Обновить настройки уведомлений для пользователя."""
    settings = get_user_settings(user_id)
    
    for key, value in kwargs.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    
    _save_settings()
    return settings


def get_user_state(user_id: int) -> NotificationState:
    """Получить состояние уведомлений для пользователя."""
    if user_id not in _user_states:
        _user_states[user_id] = NotificationState()
    return _user_states[user_id]


def is_notifications_enabled(user_id: int) -> bool:
    """Проверить, включены ли уведомления для пользователя."""
    settings = get_user_settings(user_id)
    return settings.enabled


def is_in_quiet_hours(user_id: int) -> bool:
    """Проверить, находится ли текущее время в тихих часах."""
    settings = get_user_settings(user_id)
    
    if not settings.quiet_hours_enabled:
        return False
    
    current_hour = datetime.now().hour
    start = settings.quiet_hours_start
    end = settings.quiet_hours_end
    
    if start < end:
        # Например, с 8 до 22
        return start <= current_hour < end
    else:
        # Например, с 23 до 8 (через полночь)
        return current_hour >= start or current_hour < end


def get_all_enabled_users() -> list[int]:
    """Получить список всех пользователей с включёнными уведомлениями."""
    if not _user_settings:
        _load_settings()
    
    return [
        user_id for user_id, settings in _user_settings.items()
        if settings.enabled
    ]


__all__ = [
    "NotificationSettings",
    "NotificationState",
    "get_user_settings",
    "update_user_settings",
    "get_user_state",
    "is_notifications_enabled",
    "is_in_quiet_hours",
    "get_all_enabled_users",
]
