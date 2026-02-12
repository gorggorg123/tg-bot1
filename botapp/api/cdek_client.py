# botapp/api/cdek_client.py
"""
CDEK REST API v2 клиент.

Асинхронный клиент для работы с CDEK API v2, построенный по аналогии с OzonClient.
Поддерживает OAuth2 авторизацию, кэширование токенов, retry с экспоненциальным backoff,
rate limiting, метрики производительности (как в a-ulianov/OzonAPI).
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic
from typing import Any, Dict, List, Optional

import httpx
from botapp.config import CdekConfig, load_cdek_config

logger = logging.getLogger(__name__)

# Глобальный кэш токенов (thread-safe)
_token_cache: Dict[str, Dict[str, Any]] = {}
_token_cache_lock = Lock()

# Глобальный экземпляр клиента (singleton)
_cdek_client: Optional["CdekClient"] = None
_cdek_client_lock = Lock()


class _SimpleRateLimiter:
    """
    Простой rate limiter для контроля частоты запросов (как в a-ulianov/OzonAPI).
    
    Использует sliding window алгоритм для ограничения количества запросов
    в заданном временном окне.
    """
    def __init__(self, *, rate: int, per_seconds: float):
        self.rate = max(1, rate)
        self.per_seconds = max(per_seconds, 0.001)
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Дождаться возможности выполнить запрос."""
        async with self._lock:
            now = monotonic()
            window_start = now - self.per_seconds
            while self._calls and self._calls[0] < window_start:
                self._calls.popleft()

            if len(self._calls) < self.rate:
                self._calls.append(now)
                return

            sleep_for = self._calls[0] + self.per_seconds - now
            await asyncio.sleep(max(sleep_for, 0))
            self._calls.append(monotonic())


# Rate limiters для различных эндпоинтов CDEK API
_cdek_global_rate_limiter = _SimpleRateLimiter(rate=50, per_seconds=1.0)
_cdek_cities_rate_limiter = _SimpleRateLimiter(rate=10, per_seconds=1.0)
_cdek_delivery_points_rate_limiter = _SimpleRateLimiter(rate=10, per_seconds=1.0)
_cdek_calculator_rate_limiter = _SimpleRateLimiter(rate=5, per_seconds=1.0)
_cdek_orders_rate_limiter = _SimpleRateLimiter(rate=5, per_seconds=1.0)

_PATH_RATE_LIMITERS: Dict[str, _SimpleRateLimiter] = {
    "/v2/location/cities": _cdek_cities_rate_limiter,
    "/v2/deliverypoints": _cdek_delivery_points_rate_limiter,
    "/v2/calculator/tariff": _cdek_calculator_rate_limiter,
    "/v2/calculator/tarifflist": _cdek_calculator_rate_limiter,
    "/v2/orders": _cdek_orders_rate_limiter,
}


@dataclass
class CdekClientMetrics:
    """Метрики производительности CDEK API клиента (аналогично OzonClientMetrics)."""
    
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_response_time: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    rate_limit_hits: int = 0
    retries_count: int = 0
    
    @property
    def average_response_time(self) -> float:
        return self.total_response_time / self.total_requests if self.total_requests > 0 else 0.0
    
    @property
    def success_rate(self) -> float:
        return (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0.0
    
    @property
    def cache_hit_rate(self) -> float:
        total_cache = self.cache_hits + self.cache_misses
        return (self.cache_hits / total_cache * 100) if total_cache > 0 else 0.0
    
    def reset(self) -> None:
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_response_time = 0.0
        self.cache_hits = 0
        self.cache_misses = 0
        self.rate_limit_hits = 0
        self.retries_count = 0


class CdekAuthError(Exception):
    """Ошибка авторизации CDEK API."""
    pass


class CdekAPIError(Exception):
    """Ошибка CDEK API."""
    pass


@dataclass
class CdekClient:
    """
    Асинхронный клиент CDEK REST API v2.
    
    Поддерживает:
    - OAuth2 авторизацию с автоматическим обновлением токенов
    - Retry с экспоненциальным backoff
    - Метрики производительности
    - Контекстный менеджер для корректного закрытия
    """
    
    config: CdekConfig
    total_timeout: float = 30.0
    connect_timeout: float = 10.0
    max_retries: int = 5
    retry_min_wait: float = 1.0
    retry_max_wait: float = 10.0
    retry_multiplier: float = 2.0
    enable_request_logging: bool = True
    enable_metrics: bool = False
    
    def __post_init__(self) -> None:
        timeout = httpx.Timeout(
            timeout=self.total_timeout,
            connect=self.connect_timeout,
        )
        self._http_client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self._metrics = CdekClientMetrics()
        self._global_rate_limiter = _cdek_global_rate_limiter
        self._closed = False
    
    @property
    def metrics(self) -> CdekClientMetrics:
        """Получить метрики производительности."""
        return self._metrics
    
    def get_metrics_summary(self) -> str:
        """Форматированная сводка метрик (как в a-ulianov/OzonAPI)."""
        m = self._metrics
        return (
            f"📊 Метрики CdekClient:\n"
            f"  • Всего запросов: {m.total_requests}\n"
            f"  • Успешных: {m.successful_requests} ({m.success_rate:.1f}%)\n"
            f"  • Ошибок: {m.failed_requests}\n"
            f"  • Среднее время: {m.average_response_time:.3f}s\n"
            f"  • Rate limit hits: {m.rate_limit_hits}\n"
            f"  • Retries: {m.retries_count}\n"
            f"  • Cache hits: {m.cache_hits} ({m.cache_hit_rate:.1f}%)"
        )
    
    async def aclose(self) -> None:
        """Закрыть HTTP клиент и освободить ресурсы."""
        if self._closed:
            return
        self._closed = True
        await self._http_client.aclose()
    
    async def __aenter__(self) -> "CdekClient":
        """Контекстный менеджер для async with."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Автоматическое закрытие при выходе из контекста."""
        await self.aclose()
    
    async def _get_access_token(self) -> str:
        """
        Получить access token через OAuth2 client_credentials.
        Токены кэшируются в памяти с автоматическим обновлением.
        """
        cache_key = f"{self.config.client_id}:{self.config.base_url}"
        
        # Проверяем кэш
        with _token_cache_lock:
            cached = _token_cache.get(cache_key)
            if cached:
                expires_at = cached.get("expires_at", 0)
                # Обновляем за 60 секунд до истечения
                if datetime.now(timezone.utc).timestamp() < expires_at - 60:
                    token = cached.get("access_token")
                    if token:
                        logger.debug("CDEK: Using cached access token")
                        return token
        
        # Получаем новый токен
        # CDEK API v2 использует endpoint /v2/oauth/token для prod и /oauth/token для test
        # Проверяем, какой сервер используется
        if "edu.cdek.ru" in self.config.base_url:
            # Тестовый сервер может использовать другой endpoint
            token_url = f"{self.config.base_url}/oauth/token"
        else:
            # Продакшн сервер
            token_url = f"{self.config.base_url}/v2/oauth/token"
        
        auth_data = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        
        try:
            logger.debug("CDEK: Requesting access token from %s", token_url)
            response = await self._http_client.post(
                token_url,
                data=auth_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            
            # Если получили 410, пробуем альтернативный endpoint
            if response.status_code == 410 and "edu.cdek.ru" not in self.config.base_url:
                logger.warning("CDEK: Got 410 on /v2/oauth/token, trying /oauth/token")
                token_url = f"{self.config.base_url}/oauth/token"
                response = await self._http_client.post(
                    token_url,
                    data=auth_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            
            response.raise_for_status()
            data = response.json()
            
            access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)  # По умолчанию 1 час
            
            if not access_token:
                raise CdekAuthError("Access token not found in response")
            
            # Сохраняем в кэш
            expires_at = datetime.now(timezone.utc).timestamp() + expires_in
            with _token_cache_lock:
                _token_cache[cache_key] = {
                    "access_token": access_token,
                    "expires_at": expires_at,
                    "expires_in": expires_in,
                }
            
            logger.info("CDEK: Obtained new access token (expires in %ss)", expires_in)
            return access_token
            
        except httpx.HTTPStatusError as e:
            error_text = ""
            try:
                error_text = e.response.text[:500] if hasattr(e.response, 'text') else str(e.response.content)[:500]
            except Exception:
                pass
            logger.error(
                "CDEK auth failed: HTTP %s from %s: %s",
                e.response.status_code,
                token_url,
                error_text,
            )
            raise CdekAuthError(f"Failed to obtain access token: HTTP {e.response.status_code} from {token_url}")
        except Exception as e:
            logger.error("CDEK auth error: %s", e, exc_info=True)
            raise CdekAuthError(f"Failed to obtain access token: {e}")
    
    def _calculate_backoff(self, attempt: int) -> float:
        """Экспоненциальный backoff."""
        delay = self.retry_min_wait * (self.retry_multiplier ** attempt)
        return min(delay, self.retry_max_wait) + random.uniform(0, 0.25)
    
    async def _request_with_retries(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """
        Выполнить запрос с retry, rate limiting и автоматической авторизацией (как в a-ulianov/OzonAPI).
        """
        url = f"{self.config.base_url}{path}"
        suffix = path if path.startswith("/") else f"/{path}"
        attempts_429 = 0
        attempts_other = 0
        start_time = monotonic()
        
        # Rate limiters: сначала глобальный, потом по пути
        limiter = _PATH_RATE_LIMITERS.get(suffix)
        
        while True:
            try:
                # Глобальный rate limiter
                await self._global_rate_limiter.wait()
                # Специфичный для эндпоинта rate limiter
                if limiter:
                    await limiter.wait()
                
                # Получаем токен для каждого запроса (он кэшируется внутри)
                token = await self._get_access_token()
                
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                
                if method.upper() == "GET":
                    response = await self._http_client.get(url, headers=headers, params=params)
                elif method.upper() == "POST":
                    response = await self._http_client.post(url, headers=headers, json=json, params=params)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                
                elapsed = monotonic() - start_time
                
                # Обновляем метрики
                if self.enable_metrics:
                    self._metrics.total_requests += 1
                    self._metrics.total_response_time += elapsed
                
                # Улучшенное логирование (как в OzonClient)
                if self.enable_request_logging:
                    request_id = response.headers.get("X-Request-Id") or response.headers.get("Request-Id") or "-"
                    
                    if response.status_code < 400:
                        # Успешные запросы - логируем на DEBUG
                        logger.debug(
                            "CDEK %s%s -> HTTP %s (request_id=%s, %.3fs)",
                            self.config.base_url,
                            suffix,
                            response.status_code,
                            request_id,
                            elapsed,
                        )
                    else:
                        # Ошибки - логируем на WARNING/ERROR с деталями
                        try:
                            error_body = response.json()
                            error_summary = str(error_body)[:200] if error_body else response.text[:200]
                        except Exception:
                            error_summary = response.text[:200] if hasattr(response, 'text') else "N/A"
                        
                        log_level = logger.error if response.status_code >= 500 else logger.warning
                        log_level(
                            "CDEK %s%s -> HTTP %s (request_id=%s, %.3fs): %s",
                            self.config.base_url,
                            suffix,
                            response.status_code,
                            request_id,
                            elapsed,
                            error_summary,
                        )
                
                # Обработка ошибок
                if response.status_code == 401:
                    # Токен истек, очищаем кэш и повторяем
                    cache_key = f"{self.config.client_id}:{self.config.base_url}"
                    with _token_cache_lock:
                        _token_cache.pop(cache_key, None)
                    if attempts_other < self.max_retries:
                        attempts_other += 1
                        if self.enable_metrics:
                            self._metrics.retries_count += 1
                        logger.debug("CDEK: Token expired, retrying...")
                        continue
                
                if response.status_code == 429:
                    if self.enable_metrics:
                        self._metrics.rate_limit_hits += 1
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and attempts_429 < self.max_retries:
                        try:
                            delay = float(retry_after)
                            await asyncio.sleep(min(delay, 30.0))
                            attempts_429 += 1
                            if self.enable_metrics:
                                self._metrics.retries_count += 1
                            logger.warning(
                                "CDEK %s%s -> HTTP 429, retrying after %.1fs (%d/%d)",
                                self.config.base_url,
                                suffix,
                                delay,
                                attempts_429,
                                self.max_retries,
                            )
                            continue
                        except ValueError:
                            pass
                
                if response.status_code >= 500 and attempts_other < self.max_retries:
                    # Retry для серверных ошибок
                    attempts_other += 1
                    if self.enable_metrics:
                        self._metrics.retries_count += 1
                    delay = self._calculate_backoff(attempts_other - 1)
                    logger.warning(
                        "CDEK %s%s -> HTTP %s, retrying in %.2fs (%d/%d)",
                        self.config.base_url,
                        suffix,
                        response.status_code,
                        delay,
                        attempts_other,
                        self.max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
                
                # Успешный запрос или не-retryable ошибка
                if response.status_code < 400:
                    if self.enable_metrics:
                        self._metrics.successful_requests += 1
                else:
                    if self.enable_metrics:
                        self._metrics.failed_requests += 1
                
                return response
                
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
                attempts_other += 1
                if self.enable_metrics:
                    self._metrics.retries_count += 1
                
                if attempts_other > self.max_retries:
                    if self.enable_metrics:
                        self._metrics.failed_requests += 1
                        self._metrics.total_requests += 1
                    raise
                
                delay = self._calculate_backoff(attempts_other - 1)
                logger.warning(
                    "CDEK %s%s -> Network error, retrying in %.2fs (%d/%d): %s",
                    self.config.base_url,
                    suffix,
                    delay,
                    attempts_other,
                    self.max_retries,
                    e,
                )
                await asyncio.sleep(delay)
        
        # Все попытки исчерпаны
        if self.enable_metrics:
            self._metrics.failed_requests += 1
            self._metrics.total_requests += 1
        raise CdekAPIError(f"Failed after {self.max_retries} retries")
    
    async def get_cities(self, city: Optional[str] = None, country_code: str = "RU", use_cache: bool = True) -> List[Dict[str, Any]]:
        """
        Получить список городов (с кэшированием и TTL, как в OzonClient).
        
        Args:
            city: Название города для поиска (опционально)
            country_code: Код страны (по умолчанию RU)
            use_cache: Использовать кэш (по умолчанию True)
        
        Returns:
            Список городов
        """
        cache_key = f"{country_code}:{city or 'all'}"
        now = datetime.now(timezone.utc).timestamp()
        
        # Проверяем кэш с TTL
        if use_cache:
            with _cities_cache_lock:
                _prune_cache(_cities_cache, _cities_cache_ttl)
                cached = _cities_cache.get(cache_key)
                if cached:
                    expires_at = cached.get("expires_at", 0)
                    if now < expires_at:
                        data = cached.get("data")
                        if data:
                            if self.enable_metrics:
                                self._metrics.cache_hits += 1
                            logger.debug("CDEK: Using cached cities for %s (expires in %.0fs)", cache_key, expires_at - now)
                            return data
                    else:
                        # Удаляем устаревшую запись
                        _cities_cache.pop(cache_key, None)
        
        params = {"country_codes": country_code}
        if city:
            params["city"] = city
        
        response = await self._request_with_retries("GET", "/v2/location/cities", params=params)
        response.raise_for_status()
        data = response.json()
        
        # CDEK API может возвращать данные в разных форматах
        if isinstance(data, list):
            cities = data
        elif isinstance(data, dict):
            # Проверяем различные возможные ключи
            cities = data.get("cities") or data.get("items") or data.get("result", [])
        else:
            cities = []
        
        # Сохраняем в кэш с TTL
        if use_cache and cities:
            expires_at = now + _cities_cache_ttl.total_seconds()
            with _cities_cache_lock:
                _cities_cache[cache_key] = {
                    "data": cities,
                    "expires_at": expires_at,
                }
                if self.enable_metrics:
                    self._metrics.cache_misses += 1
        
        return cities
    
    async def get_delivery_points(self, city_code: Optional[int] = None, city: Optional[str] = None, use_cache: bool = True) -> List[Dict[str, Any]]:
        """
        Получить список ПВЗ (пунктов выдачи заказов) с кэшированием и TTL.
        
        Args:
            city_code: Код города
            city: Название города
            use_cache: Использовать кэш (по умолчанию True)
        
        Returns:
            Список ПВЗ
        """
        cache_key = f"{city_code or 0}:{city or 'all'}"
        now = datetime.now(timezone.utc).timestamp()
        
        # Проверяем кэш с TTL
        if use_cache:
            with _pvz_cache_lock:
                _prune_cache(_pvz_cache, _pvz_cache_ttl)
                cached = _pvz_cache.get(cache_key)
                if cached:
                    expires_at = cached.get("expires_at", 0)
                    if now < expires_at:
                        data = cached.get("data")
                        if data:
                            if self.enable_metrics:
                                self._metrics.cache_hits += 1
                            logger.debug("CDEK: Using cached PVZ for %s (expires in %.0fs)", cache_key, expires_at - now)
                            return data
                    else:
                        # Удаляем устаревшую запись
                        _pvz_cache.pop(cache_key, None)
        
        params = {}
        if city_code:
            params["city_code"] = city_code
        if city:
            params["city"] = city
        
        response = await self._request_with_retries("GET", "/v2/deliverypoints", params=params)
        response.raise_for_status()
        data = response.json()
        
        # CDEK API может возвращать данные в разных форматах
        if isinstance(data, list):
            pvz_list = data
        elif isinstance(data, dict):
            pvz_list = data.get("delivery_points") or data.get("items") or data.get("result", [])
        else:
            pvz_list = []
        
        # Сохраняем в кэш с TTL
        if use_cache and pvz_list:
            expires_at = now + _pvz_cache_ttl.total_seconds()
            with _pvz_cache_lock:
                _pvz_cache[cache_key] = {
                    "data": pvz_list,
                    "expires_at": expires_at,
                }
                if self.enable_metrics:
                    self._metrics.cache_misses += 1
        
        return pvz_list
    
    async def calculate_tariff(
        self,
        from_location: Dict[str, Any],
        to_location: Dict[str, Any],
        packages: List[Dict[str, Any]],
        tariff_code: Optional[int] = None,
        order_type: int = 1,
    ) -> Dict[str, Any]:
        """
        Рассчитать стоимость доставки.
        
        Args:
            from_location: Локация отправителя (dict с code или address)
            to_location: Локация получателя (dict с code или address)
            packages: Список посылок (каждая с weight, length, width, height)
            tariff_code: Код тарифа (опционально)
        
        Returns:
            Результат расчета
        """
        payload = {
            "type": int(order_type),
            "from_location": from_location,
            "to_location": to_location,
            "packages": packages,
        }
        if tariff_code:
            payload["tariff_code"] = tariff_code
        
        response = await self._request_with_retries("POST", "/v2/calculator/tariff", json=payload)
        response.raise_for_status()
        return response.json()
    
    async def get_available_tariffs(
        self,
        from_location: Dict[str, Any],
        to_location: Dict[str, Any],
        packages: List[Dict[str, Any]],
        order_type: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Получить список доступных тарифов.
        
        Args:
            from_location: Локация отправителя
            to_location: Локация получателя
            packages: Список посылок
        
        Returns:
            Список доступных тарифов
        """
        payload = {
            "type": int(order_type),
            "from_location": from_location,
            "to_location": to_location,
            "packages": packages,
        }
        
        response = await self._request_with_retries("POST", "/v2/calculator/tarifflist", json=payload)
        response.raise_for_status()
        data = response.json()
        
        # CDEK API может возвращать данные в разных форматах
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return (
                data.get("tariff_codes")
                or data.get("tariffs")
                or data.get("items")
                or data.get("result", [])
            )
        return []
    
    async def create_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Создать заказ в CDEK с улучшенной обработкой ошибок.
        
        Args:
            order_data: Данные заказа (type, number, tariff_code, from_location, to_location, packages, recipient, sender)
        
        Returns:
            Созданный заказ с UUID и номером
        
        Raises:
            CdekAPIError: При ошибке создания заказа
        """
        response = await self._request_with_retries("POST", "/v2/orders", json=order_data)
        
        # Улучшенная обработка ошибок CDEK API
        if response.status_code >= 400:
            try:
                error_data = response.json()
                error_messages = []
                
                # Проверяем структуру ошибки CDEK (может быть в разных форматах)
                if isinstance(error_data, dict):
                    # Формат: {"requests": [{"errors": [{"message": "...", "code": "..."}]}]}
                    requests = error_data.get("requests", [])
                    if requests:
                        for req in requests:
                            errors = req.get("errors", [])
                            for err in errors:
                                msg = err.get("message", "Unknown error")
                                code = err.get("code", "")
                                if code:
                                    error_messages.append(f"{msg} (code: {code})")
                                else:
                                    error_messages.append(msg)
                    
                    # Формат: {"message": "...", "error": "..."}
                    if not error_messages:
                        error_messages.append(
                            error_data.get("message") 
                            or error_data.get("error") 
                            or error_data.get("error_description")
                            or "Unknown error"
                        )
                    
                    # Формат: {"errors": [{"message": "..."}]}
                    if not error_messages:
                        errors = error_data.get("errors", [])
                        for err in errors:
                            if isinstance(err, dict):
                                error_messages.append(err.get("message", "Unknown error"))
                            else:
                                error_messages.append(str(err))
                
                if error_messages:
                    error_msg = "; ".join(error_messages)
                    logger.error("CDEK API error creating order: %s", error_msg)
                    raise CdekAPIError(f"CDEK API error: {error_msg}")
                    
            except CdekAPIError:
                raise
            except Exception as e:
                logger.debug("CDEK: Failed to parse error response: %s", e)
            
            # Fallback на стандартную обработку
            response.raise_for_status()
        
        data = response.json()
        
        # CDEK API может возвращать данные в разных форматах
        if isinstance(data, dict):
            # Частый формат ответа: {"entity": {...}, "requests": [...]}
            if isinstance(data.get("entity"), dict):
                return data["entity"]

            # Проверяем наличие ошибок в успешном ответе (200 OK, но есть errors)
            if data.get("requests"):
                request = data["requests"][0]
                if request.get("errors"):
                    errors = request["errors"]
                    error_messages = [
                        err.get("message", "Unknown error") 
                        for err in errors
                    ]
                    raise CdekAPIError(f"CDEK API error: {'; '.join(error_messages)}")
                # Возвращаем entity из успешного запроса
                return request.get("entity", data)
            return data
        
        return data
    
    async def get_order_info(self, order_uuid: Optional[str] = None, cdek_number: Optional[str] = None) -> Dict[str, Any]:
        """
        Получить информацию о заказе.
        
        Args:
            order_uuid: UUID заказа
            cdek_number: Номер заказа CDEK
        
        Returns:
            Информация о заказе
        """
        params = {}
        if order_uuid:
            params["uuid"] = order_uuid
        if cdek_number:
            params["cdek_number"] = cdek_number
        
        response = await self._request_with_retries("GET", "/v2/orders", params=params)
        response.raise_for_status()
        return response.json()
    
    async def get_print_forms(self, order_uuid: Optional[str] = None, cdek_number: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Получить список печатных форм для заказа (накладные, штрих-коды и т.д.).
        
        Args:
            order_uuid: UUID заказа
            cdek_number: Номер заказа CDEK
        
        Returns:
            Список печатных форм с URL для скачивания
        """
        params = {}
        if order_uuid:
            params["order_uuid"] = order_uuid
        if cdek_number:
            params["cdek_number"] = cdek_number
        
        response = await self._request_with_retries("GET", "/v2/print-forms", params=params)
        response.raise_for_status()
        data = response.json()
        
        # CDEK API может возвращать данные в разных форматах
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("print_forms") or data.get("items") or data.get("result", [])
        return []
    
    async def download_print_form(self, print_form_uuid: str) -> bytes:
        """
        Скачать печатную форму по UUID.
        
        Args:
            print_form_uuid: UUID печатной формы
        
        Returns:
            Байты файла печатной формы (обычно PDF)
        """
        response = await self._request_with_retries("GET", f"/v2/print-forms/{print_form_uuid}")
        response.raise_for_status()
        return response.content


# Кэш для городов и ПВЗ (как в OzonClient для продуктов)
_cities_cache: Dict[str, Dict[str, Any]] = {}  # {cache_key: {"data": [...], "expires_at": timestamp}}
_cities_cache_lock = Lock()
_cities_cache_ttl = timedelta(hours=24)  # Кэш городов на 24 часа

_pvz_cache: Dict[str, Dict[str, Any]] = {}  # {cache_key: {"data": [...], "expires_at": timestamp}}
_pvz_cache_lock = Lock()
_pvz_cache_ttl = timedelta(hours=6)  # Кэш ПВЗ на 6 часов


def _prune_cache(cache: Dict[str, Dict[str, Any]], ttl: timedelta) -> None:
    """Очистить устаревшие записи из кэша (как в OzonClient)."""
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - ttl.total_seconds()
    expired_keys = [
        key for key, value in cache.items()
        if isinstance(value, dict) and value.get("expires_at", 0) < cutoff
    ]
    for key in expired_keys:
        cache.pop(key, None)
    if expired_keys:
        logger.debug("CDEK: Pruned %d expired cache entries", len(expired_keys))


def get_cdek_client(config: Optional[CdekConfig] = None) -> CdekClient:
    """
    Фабрика клиентов CDEK с поддержкой глобального экземпляра (как в a-ulianov/OzonAPI).
    
    Args:
        config: Конфигурация CDEK (если не указана, загружается из .env)
        
    Returns:
        Настроенный CdekClient
    """
    global _cdek_client
    
    if config is None:
        config = load_cdek_config()
    
    # Используем глобальный синглтон, если конфигурация совпадает
    if _cdek_client is not None:
        # Проверяем, что конфигурация совпадает
        if (
            _cdek_client.config.client_id == config.client_id
            and _cdek_client.config.base_url == config.base_url
        ):
            return _cdek_client
    
    # Создаем новый клиент
    client = CdekClient(
        config=config,
        total_timeout=config.timeout_s,
        connect_timeout=10.0,
        max_retries=5,
        retry_min_wait=1.0,
        retry_max_wait=10.0,
        retry_multiplier=2.0,
        enable_request_logging=True,
        enable_metrics=True,  # Метрики включены по умолчанию для мониторинга
    )
    
    # Сохраняем как глобальный экземпляр
    with _cdek_client_lock:
        _cdek_client = client
    
    logger.info(
        "CdekClient created: base_url=%s, timeout=%.1fs, max_retries=5",
        config.base_url,
        config.timeout_s,
    )
    
    return client


__all__ = [
    "CdekClient",
    "CdekClientMetrics",
    "CdekAuthError",
    "CdekAPIError",
    "get_cdek_client",
    "load_cdek_config",
]
