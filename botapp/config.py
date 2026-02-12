# botapp/config.py
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(*names: str, default: str = "") -> str:
    """
    Берём первое непустое значение из списка переменных окружения.
    """
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return (default or "").strip()


@dataclass(frozen=True, slots=True)
class OzonConfig:
    base_url: str
    client_id: str
    api_key: str
    write_client_id: str
    write_api_key: str
    timeout_s: float


@dataclass(frozen=True, slots=True)
class CdekConfig:
    """Конфигурация CDEK API v2"""
    client_id: str
    client_secret: str
    base_url: str  # https://api.cdek.ru или https://api.edu.cdek.ru
    sender_city: str  # "Ижевск"
    sender_name: str
    sender_phone: str
    default_tariff_name: str  # "Магистральный экспресс склад-склад"
    order_type: int = 1  # 1 = e-shop, 2 = delivery
    sender_pvz: str | None = None  # опционально
    timeout_s: float = 30.0


@dataclass(frozen=True, slots=True)
class Config:
    """Полная конфигурация бота"""
    
    # Telegram Bot
    tg_bot_token: str
    admin_ids: list[int]
    
    # Ozon API
    ozon_client_id: str
    ozon_api_key: str
    ozon_write_client_id: str
    ozon_write_api_key: str
    
    # PostgreSQL
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    use_postgres: bool
    
    # Redis
    redis_host: str
    redis_port: int
    use_redis: bool
    
    # Webhook
    webhook_url: str
    webhook_secret: str
    use_webhook: bool
    
    # Features
    enable_prefetch: bool
    enable_inline_query: bool
    
    # Logging
    log_level: str
    log_file: str


def load_ozon_config() -> OzonConfig:
    base_url = _env("OZON_API_BASE_URL", default="https://api-seller.ozon.ru").rstrip("/")

    # Совместимость: старые имена переменных (OZON_SELLER_*) + новые (OZON_*)
    client_id = _env("OZON_CLIENT_ID", "OZON_SELLER_CLIENT_ID")
    api_key = _env("OZON_API_KEY", "OZON_SELLER_API_KEY")

    write_client_id = _env(
        "OZON_WRITE_CLIENT_ID",
        "OZON_SELLER_WRITE_CLIENT_ID",
        default=client_id,
    )
    write_api_key = _env(
        "OZON_WRITE_API_KEY",
        "OZON_SELLER_WRITE_API_KEY",
        default=api_key,
    )

    try:
        timeout_s = float(_env("OZON_HTTP_TIMEOUT_S", default="35"))
    except Exception:
        timeout_s = 35.0

    return OzonConfig(
        base_url=base_url,
        client_id=client_id,
        api_key=api_key,
        write_client_id=write_client_id,
        write_api_key=write_api_key,
        timeout_s=timeout_s,
    )


def load_cdek_config() -> CdekConfig:
    """Загрузка конфигурации CDEK API"""
    client_id = _env("CDEK_CLIENT_ID", default="")
    client_secret = _env("CDEK_CLIENT_SECRET", default="")
    base_url = _env("CDEK_BASE_URL", default="https://api.cdek.ru").rstrip("/")
    sender_city = _env("CDEK_SENDER_CITY", default="Ижевск")
    sender_name = _env("CDEK_SENDER_NAME", default="")
    sender_phone = _env("CDEK_SENDER_PHONE", default="")
    default_tariff_name = _env("CDEK_DEFAULT_TARIFF_NAME", default="Магистральный экспресс склад-склад")
    order_type_raw = _env("CDEK_ORDER_TYPE", default="1")
    sender_pvz = _env("CDEK_SENDER_PVZ", default="") or None
    
    # Валидация обязательных полей
    if not client_id:
        raise ValueError("CDEK_CLIENT_ID не задан в переменных окружения. Укажите его в .env файле.")
    if not client_secret:
        raise ValueError("CDEK_CLIENT_SECRET не задан в переменных окружения. Укажите его в .env файле.")
    if not sender_name:
        raise ValueError("CDEK_SENDER_NAME не задан в переменных окружения. Укажите имя отправителя в .env файле.")
    if not sender_phone:
        raise ValueError("CDEK_SENDER_PHONE не задан в переменных окружения. Укажите телефон отправителя в .env файле.")
    
    try:
        timeout_s = float(_env("CDEK_TIMEOUT_S", default="30"))
    except Exception:
        timeout_s = 30.0

    try:
        order_type = int(order_type_raw)
    except Exception:
        order_type = 1
    if order_type not in (1, 2):
        order_type = 1
    
    return CdekConfig(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        sender_city=sender_city,
        sender_name=sender_name,
        sender_phone=sender_phone,
        default_tariff_name=default_tariff_name,
        order_type=order_type,
        sender_pvz=sender_pvz,
        timeout_s=timeout_s,
    )


def get_config() -> Config:
    """Загрузка полной конфигурации"""
    
    # Admin IDs
    admin_ids_str = _env("ADMIN_IDS", default="")
    admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]
    
    # PostgreSQL
    postgres_host = _env("POSTGRES_HOST", default="localhost")
    postgres_port = int(_env("POSTGRES_PORT", default="5432"))
    postgres_db = _env("POSTGRES_DB", default="telegram_bot")
    postgres_user = _env("POSTGRES_USER", default="bot_user")
    postgres_password = _env("POSTGRES_PASSWORD", default="")
    use_postgres = _env("USE_POSTGRES", default="false").lower() == "true"
    
    # Redis
    redis_host = _env("REDIS_HOST", default="localhost")
    redis_port = int(_env("REDIS_PORT", default="6379"))
    use_redis = _env("USE_REDIS", default="false").lower() == "true"
    
    # Webhook
    webhook_url = _env("WEBHOOK_URL", default="")
    webhook_secret = _env("WEBHOOK_SECRET", default="")
    use_webhook = _env("USE_WEBHOOK", default="false").lower() == "true"
    
    # Features
    enable_prefetch = _env("ENABLE_PREFETCH", default="true").lower() == "true"
    enable_inline_query = _env("ENABLE_INLINE_QUERY", default="true").lower() == "true"
    
    # Ozon
    ozon_config = load_ozon_config()
    
    return Config(
        tg_bot_token=_env("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", default=""),
        admin_ids=admin_ids,
        ozon_client_id=ozon_config.client_id,
        ozon_api_key=ozon_config.api_key,
        ozon_write_client_id=ozon_config.write_client_id,
        ozon_write_api_key=ozon_config.write_api_key,
        postgres_host=postgres_host,
        postgres_port=postgres_port,
        postgres_db=postgres_db,
        postgres_user=postgres_user,
        postgres_password=postgres_password,
        use_postgres=use_postgres,
        redis_host=redis_host,
        redis_port=redis_port,
        use_redis=use_redis,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        use_webhook=use_webhook,
        enable_prefetch=enable_prefetch,
        enable_inline_query=enable_inline_query,
        log_level=_env("LOG_LEVEL", default="INFO"),
        log_file=_env("LOG_FILE", default="logs/bot_local.log"),
    )
