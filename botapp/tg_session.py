"""Утилита для создания кастомной aiohttp сессии для aiogram с настройкой SSL.

Позволяет обойти проблемы с SSL при работе через прокси или в корпоративных сетях.
"""
from __future__ import annotations

import logging
import os
import ssl
from typing import Any

try:
    import certifi
except ImportError:
    certifi = None

try:
    from aiohttp import TCPConnector
except ImportError:
    TCPConnector = None

from aiogram.client.session.aiohttp import AiohttpSession

logger = logging.getLogger(__name__)


class CustomAiohttpSession(AiohttpSession):
    """Кастомная сессия с настройкой SSL через переопределение connector."""
    
    def __init__(self, *args, custom_ssl: ssl.SSLContext | None = None, disable_ssl: bool = False, **kwargs):
        # Сохраняем настройки SSL перед вызовом super()
        self._custom_ssl = custom_ssl
        self._disable_ssl = disable_ssl
        super().__init__(*args, **kwargs)
        # Переопределяем connector настройки после super()
        if TCPConnector:
            self._connector_type = TCPConnector
            # Получаем текущие настройки или создаем новые
            init = {}
            if hasattr(self, "_connector_init") and self._connector_init:
                init = dict(self._connector_init)
            
            if self._disable_ssl:
                # Полностью отключаем SSL проверку
                init["ssl"] = False
                logger.info("CustomAiohttpSession: SSL verification DISABLED (ssl=False)")
            elif self._custom_ssl is not None:
                init["ssl"] = self._custom_ssl
                logger.info("CustomAiohttpSession: Using custom SSL context")
            else:
                # Используем default SSL context с certifi
                try:
                    if certifi:
                        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                        logger.debug("Using SSL context with certifi CA bundle")
                    else:
                        ssl_ctx = ssl.create_default_context()
                        logger.debug("Using default SSL context (certifi not available)")
                    
                    # Настройки для совместимости
                    ssl_ctx.check_hostname = True
                    ssl_ctx.verify_mode = ssl.CERT_REQUIRED
                    init["ssl"] = ssl_ctx
                except Exception as e:
                    logger.warning("Failed to create SSL context: %s", e)
                    # Если не удалось создать SSL context, отключаем проверку
                    init["ssl"] = False
                    logger.warning("Falling back to SSL verification disabled")
            
            self._connector_init = init
            self._should_reset_connector = True
            logger.info("CustomAiohttpSession: _connector_init ssl=%s, _should_reset_connector=%s", init.get("ssl"), self._should_reset_connector)
            # Принудительно закрываем существующую сессию, если она есть, чтобы применить новые настройки
            if hasattr(self, "_session") and self._session is not None:
                logger.debug("CustomAiohttpSession: Closing existing session to apply new SSL settings")
                # Закрываем сессию асинхронно, но это может быть проблемой в __init__
                # Поэтому просто помечаем, что нужно пересоздать
                self._session = None
        else:
            # Fallback для старых версий
            if self._disable_ssl:
                self._connector_init = {"ssl": False}
            elif self._custom_ssl is not None:
                self._connector_init = {"ssl": self._custom_ssl}
            self._should_reset_connector = True
    
    async def create_session(self):
        """Переопределяем create_session для гарантированного применения SSL настроек."""
        from aiohttp import ClientSession
        import ssl as ssl_module
        
        if self._should_reset_connector:
            await self.close()
        
        if self._session is None or (hasattr(self._session, "closed") and self._session.closed):
            # Принудительно применяем настройки из _connector_init
            connector_kwargs = {}
            
            # Убеждаемся, что SSL настройки применяются
            if self._disable_ssl:
                # Создаём SSL context который полностью отключает проверку
                ssl_ctx = ssl_module.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl_module.CERT_NONE
                connector_kwargs["ssl"] = ssl_ctx
                logger.info("CustomAiohttpSession.create_session: SSL verification DISABLED (insecure SSLContext)")
            elif self._custom_ssl is not None:
                connector_kwargs["ssl"] = self._custom_ssl
                logger.debug("CustomAiohttpSession.create_session: using custom SSL context")
            elif self._connector_init:
                connector_kwargs = dict(self._connector_init)
                logger.debug("CustomAiohttpSession.create_session: using connector_init settings")
            
            connector = self._connector_type(**connector_kwargs) if self._connector_type and connector_kwargs else None
            logger.info("CustomAiohttpSession.create_session: connector created with ssl=%s", 
                       type(connector_kwargs.get("ssl")).__name__ if connector_kwargs.get("ssl") is not None else "default")
            
            # Создаем сессию с connector, отключаем использование системных прокси
            # trust_env=False предотвращает использование HTTP_PROXY/HTTPS_PROXY
            self._session = ClientSession(connector=connector, trust_env=False)
            self._should_reset_connector = False
        
        return self._session


def create_bot_session(
    disable_ssl_verify: bool = False,
    ssl_context: ssl.SSLContext | None = None,
) -> AiohttpSession:
    """
    Создает кастомную aiohttp сессию для aiogram с настройкой SSL.
    
    Args:
        disable_ssl_verify: Если True, отключает проверку SSL сертификатов (небезопасно!)
        ssl_context: Кастомный SSL контекст. Если не указан, используется default.
    
    Returns:
        AiohttpSession с настроенным connector.
    """
    if disable_ssl_verify:
        logger.warning(
            "SSL verification disabled! This is insecure and should only be used "
            "for development or behind corporate proxies."
        )
        return CustomAiohttpSession(disable_ssl=True)
    elif ssl_context is not None:
        logger.info("Using custom SSL context")
        return CustomAiohttpSession(custom_ssl=ssl_context)
    else:
        # Используем default SSL context с certifi
        logger.debug("Using default SSL context")
        return CustomAiohttpSession()


def create_bot_session_from_env() -> AiohttpSession:
    """
    Создает сессию на основе переменных окружения.
    
    Переменные:
        TG_DISABLE_SSL_VERIFY: если "1" или "true", отключает проверку SSL (по умолчанию отключено)
        TG_SSL_CA_FILE: путь к файлу с CA сертификатами (опционально)
        TG_ENABLE_SSL_VERIFY: если "1" или "true", включает проверку SSL (по умолчанию выключено)
    
    Returns:
        AiohttpSession с настроенным connector.
    """
    disable_ssl_env = os.getenv("TG_DISABLE_SSL_VERIFY", "").strip()
    enable_ssl_env = os.getenv("TG_ENABLE_SSL_VERIFY", "").strip()
    ca_file = os.getenv("TG_SSL_CA_FILE", "").strip()
    
    # Логика определения SSL:
    # 1. Если TG_ENABLE_SSL_VERIFY=1 -> включаем SSL
    # 2. Если TG_DISABLE_SSL_VERIFY=1 -> отключаем SSL  
    # 3. По умолчанию для разработки отключаем SSL (можно изменить на True для production)
    
    if enable_ssl_env.lower() in ("1", "true", "yes"):
        disable_ssl = False
        logger.info("[+] TG_ENABLE_SSL_VERIFY is set, SSL verification will be ENABLED")
    elif disable_ssl_env.lower() in ("1", "true", "yes"):
        disable_ssl = True
        logger.warning("[!] TG_DISABLE_SSL_VERIFY is set, SSL verification will be DISABLED (insecure!)")
    else:
        # По умолчанию отключаем SSL проверку для удобства разработки
        # Для production рекомендуется установить TG_ENABLE_SSL_VERIFY=1
        disable_ssl = True
        logger.info("[i] No SSL env vars set, using default: SSL verification DISABLED (development mode)")
    
    logger.info(
        "Creating bot session: disable_ssl=%s (TG_DISABLE_SSL_VERIFY=%r, TG_ENABLE_SSL_VERIFY=%r), TG_SSL_CA_FILE=%s",
        disable_ssl,
        disable_ssl_env or "(not set)",
        enable_ssl_env or "(not set)",
        ca_file or "(not set)",
    )
    
    if disable_ssl:
        logger.warning("[!] SSL verification is DISABLED (insecure, only for development!)")
        logger.warning("[!] For production, set TG_ENABLE_SSL_VERIFY=1 in .env file")
        session = create_bot_session(disable_ssl_verify=True)
        logger.info("Created session with SSL disabled: %s", type(session).__name__)
        return session
    
    ssl_ctx = None
    if ca_file:
        try:
            ssl_ctx = ssl.create_default_context(cafile=ca_file)
            logger.info("Using custom CA file: %s", ca_file)
        except Exception as e:
            logger.warning("Failed to load CA file %s: %s", ca_file, e)
            ssl_ctx = None
    
    return create_bot_session(ssl_context=ssl_ctx)
