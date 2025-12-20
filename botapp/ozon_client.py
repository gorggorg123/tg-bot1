# botapp/ozon_client.py
from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, date, timezone
from typing import Any, Dict, Iterable, List, Sequence, Tuple
from time import monotonic
from urllib.parse import urlparse, unquote

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Разрешаем использовать поля с префиксом ``model_`` (ozonapi модели с model_id/model_info).
BaseModel.model_config = ConfigDict(**dict(BaseModel.model_config), protected_namespaces=())

try:  # ozonapi-async 0.19.x содержит seller_info, 0.1.0 — нет
    from ozonapi import SellerAPI
except Exception:  # pragma: no cover - совместимость, если пакет не установлен
    SellerAPI = None  # type: ignore

logger = logging.getLogger(__name__)

load_dotenv()

BASE_URL = "https://api-seller.ozon.ru"
MSK_SHIFT = timedelta(hours=3)
MSK_TZ = timezone(MSK_SHIFT)

_product_name_cache: dict[str, str | None] = {}
_product_not_found_warned: set[str] = set()
_product_info_miss_cache: set[str] = set()
_analytics_forbidden: bool = False
_analytics_forbidden_logged: bool = False
_stock_not_found_warned: set[str] = set()
_product_list_warned: bool = False


class _SimpleRateLimiter:
    def __init__(self, *, rate: int, per_seconds: float):
        self.rate = max(1, rate)
        self.per_seconds = max(per_seconds, 0.001)
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
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


_question_answer_rate_limiter = _SimpleRateLimiter(rate=50, per_seconds=1.0)


class QuestionItem(BaseModel):
    question_id: str | None = Field(default=None)
    product_id: str | None = None
    offer_id: str | None = None
    sku: str | None = None
    product_title: str | None = None
    product_name: str | None = None
    text: str | None = None
    question: str | None = None
    message: str | None = None
    status: str | None = None
    answer: str | None = None
    last_answer: str | None = None
    answer_id: str | None = None
    created_at: Any = None
    updated_at: Any = None

    model_config = ConfigDict(extra="allow", protected_namespaces=())


class GetQuestionListResponse(BaseModel):
    questions: list[QuestionItem] = Field(default_factory=list)
    result: list[QuestionItem] | None = None
    last_id: str | None = None
    total: int | None = None

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    @property
    def items(self) -> list[QuestionItem]:
        if self.questions:
            return self.questions
        if isinstance(self.result, list):
            return self.result
        return []


def _parse_sku_title_map(payload: Dict[str, Any] | None) -> tuple[Dict[str, str], list[Any]]:
    """Построить мапу sku -> title из ответа /v1/analytics/data."""

    if not isinstance(payload, dict):
        return {}, []

    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    data_rows = result.get("data") if isinstance(result, dict) else []
    if not isinstance(data_rows, list):
        return {}, []

    sku_title_map: Dict[str, str] = {}
    for row in data_rows:
        if not isinstance(row, dict):
            continue
        dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), list) else []
        if not dimensions:
            continue

        sku_key: str | None = None
        title_val: Any = None

        legacy_dim = next(
            (dim for dim in dimensions if isinstance(dim, dict) and dim.get("id") == "sku"),
            None,
        )
        if isinstance(legacy_dim, dict):
            sku_raw = legacy_dim.get("value") or legacy_dim.get("id_value") or legacy_dim.get("sku")
            title_val = legacy_dim.get("name") or legacy_dim.get("title") or legacy_dim.get("description")
            sku_key = str(sku_raw).strip() if sku_raw not in (None, "") else None

        if sku_key is None:
            first_dim = next((dim for dim in dimensions if isinstance(dim, dict)), None)
            if isinstance(first_dim, dict):
                sku_raw = first_dim.get("value") or first_dim.get("id") or first_dim.get("sku")
                title_val = first_dim.get("name") or first_dim.get("title") or title_val
                sku_key = str(sku_raw).strip() if sku_raw not in (None, "") else None

        if not sku_key:
            continue

        if title_val not in (None, ""):
            sku_title_map[sku_key] = str(title_val).strip()

    return sku_title_map, data_rows


def _iso_z(dt: datetime) -> str:
    """Вернуть ISO-строку в UTC с Z без миллисекунд."""

    dt_utc = _ensure_utc(dt)
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _clean_sku(value: Any) -> int | None:
    """Вернуть положительный SKU или None, чтобы не отправлять 0 в Ozon."""

    try:
        sku_int = int(value)
    except Exception:
        return None

    if sku_int <= 0:
        return None

    return sku_int


def msk_today_range() -> Tuple[str, str, str]:
    """
    Диапазон на сегодня в МСК, но границы в UTC.
    Возвращает (from_iso, to_iso, pretty_text).
    """
    now_utc = datetime.utcnow()
    now_msk = now_utc + MSK_SHIFT
    d = now_msk.date()

    start_utc = datetime(d.year, d.month, d.day) - MSK_SHIFT
    end_utc = start_utc + timedelta(days=1) - timedelta(seconds=1)

    pretty = (
        f"{d.strftime('%d.%m.%Y')} 00:00 — "
        f"{d.strftime('%d.%m.%Y')} 23:59 (МСК)"
    )
    return _iso_z(start_utc), _iso_z(end_utc), pretty


def msk_current_month_range() -> Tuple[str, str, str]:
    """
    Диапазон с 1-го числа текущего месяца по сегодня (МСК).
    Границы возвращаются в UTC, плюс красивый текст.
    """
    now_utc = datetime.utcnow()
    now_msk = now_utc + MSK_SHIFT
    today = now_msk.date()

    first = date(today.year, today.month, 1)
    if today.month == 12:
        last_calendar = date(today.year, 12, 31)
    else:
        next_first = date(today.year, today.month + 1, 1)
        last_calendar = next_first - timedelta(days=1)

    end_date = today if today <= last_calendar else last_calendar

    start_utc = datetime(first.year, first.month, first.day) - MSK_SHIFT
    end_utc = datetime(
        end_date.year, end_date.month, end_date.day, 23, 59, 59
    ) - MSK_SHIFT

    pretty = (
        f"{first.strftime('%d.%m.%Y')} — "
        f"{end_date.strftime('%d.%m.%Y')} (МСК)"
    )
    return _iso_z(start_utc), _iso_z(end_utc), pretty


def msk_week_range() -> Tuple[str, str, str]:
    """Возвращает диапазон за последние 7 дней с учётом МСК."""
    now_utc = datetime.utcnow()
    now_msk = now_utc + MSK_SHIFT
    today = now_msk.date()
    week_ago = today - timedelta(days=6)

    start_utc = datetime(week_ago.year, week_ago.month, week_ago.day) - MSK_SHIFT
    end_utc = datetime(today.year, today.month, today.day, 23, 59, 59) - MSK_SHIFT

    pretty = (
        f"{week_ago.strftime('%d.%m.%Y')} — "
        f"{today.strftime('%d.%m.%Y')} (МСК)"
    )
    return _iso_z(start_utc), _iso_z(end_utc), pretty


def msk_yesterday_range() -> Tuple[str, str, str]:
    """Диапазон за вчера (МСК)."""

    now_utc = datetime.utcnow()
    now_msk = now_utc + MSK_SHIFT
    yesterday = now_msk.date() - timedelta(days=1)

    start_utc = datetime(yesterday.year, yesterday.month, yesterday.day) - MSK_SHIFT
    end_utc = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59, 59) - MSK_SHIFT

    pretty = (
        f"{yesterday.strftime('%d.%m.%Y')} 00:00 — "
        f"{yesterday.strftime('%d.%m.%Y')} 23:59 (МСК)"
    )
    return _iso_z(start_utc), _iso_z(end_utc), pretty


def fmt_int(n: float | int) -> str:
    return f"{int(round(n)):,.0f}".replace(",", " ")


def fmt_rub0(n: float | int) -> str:
    return f"{int(round(n)):,.0f} ₽".replace(",", " ")


def s_num(x: Any) -> float:
    try:
        return float(str(x).replace(" ", "").replace(",", ".")) if x is not None else 0.0
    except Exception:
        return 0.0


def _env_read_credentials() -> tuple[str, str]:
    """Прочитать пару Client-Id/Api-Key для чтения."""

    client_id = (os.getenv("OZON_CLIENT_ID") or "").strip()
    api_key = (os.getenv("OZON_API_KEY") or "").strip()

    if not client_id or not api_key:
        raise RuntimeError("Не заданы креденшалы OZON_CLIENT_ID / OZON_API_KEY")

    return client_id, api_key


class OzonAPIError(RuntimeError):
    """Ошибка вызова Ozon API."""


class OzonProductNotFound(OzonAPIError):
    """Товар не найден (404)."""


@dataclass
class OzonClient:
    client_id: str
    api_key: str

    def __post_init__(self) -> None:
        # Используем явные абсолютные URL вместо base_url, чтобы исключить
        # ошибки склейки (в логах на проде виден вызов на корень `/`).
        self._http_client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Client-Id": self.client_id,
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self._seller_api: SellerAPI | None = None

    async def aclose(self) -> None:
        await self._http_client.aclose()
        if self._seller_api and hasattr(self._seller_api, "close"):
            try:
                await self._seller_api.close()  # type: ignore[arg-type]
            except Exception:
                pass

    def _get_seller_api(self) -> SellerAPI | None:
        if SellerAPI is None:
            return None
        if self._seller_api is None:
            self._seller_api = SellerAPI(client_id=self.client_id, api_key=self.api_key)
        return self._seller_api


    async def _post_with_status(
        self, path: str, json: Dict[str, Any]
    ) -> tuple[int, Dict[str, Any] | None]:
        """Отправить POST-запрос и вернуть статус + JSON без raise_for_status."""

        suffix = path if path.startswith("/") else f"/{path}"
        url = f"{BASE_URL}{suffix}"
        max_attempts = 4
        backoffs = [0.5, 1.0, 2.0, 4.0]
        r: httpx.Response | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                r = await self._http_client.post(url, json=json)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                if attempt >= max_attempts:
                    raise

                delay = (backoffs[min(attempt - 1, len(backoffs) - 1)] + random.uniform(0, 0.25))
                logger.warning(
                    "Ozon %s retry %s/%s after %.2fs due to %s",
                    suffix,
                    attempt,
                    max_attempts,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                continue

            status = r.status_code
            retryable_status = status == 429 or status in {500, 502, 503, 504}
            if retryable_status and attempt < max_attempts:
                retry_after_header = r.headers.get("Retry-After") if status == 429 else None
                retry_after: float | None = None
                if retry_after_header:
                    try:
                        retry_after = float(retry_after_header)
                    except Exception:
                        retry_after = None

                delay = retry_after if retry_after is not None else backoffs[min(attempt - 1, len(backoffs) - 1)]
                delay += random.uniform(0, 0.25)
                logger.warning(
                    "Ozon %s retry %s/%s on HTTP %s in %.2fs",
                    suffix,
                    attempt,
                    max_attempts,
                    status,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            break

        if r is None:
            raise RuntimeError("HTTP client did not return a response")

        status = r.status_code
        request_id = r.headers.get("X-Request-Id") or r.headers.get("X-Ozon-Request-Id")
        try:
            data = r.json()
        except Exception:
            try:
                raw = await r.aread()
            except Exception:
                raw = b""
            logger.error(
                "Ozon %s -> HTTP %s (request_id=%s) JSON decode failed: %s",
                url,
                status,
                request_id or "-",
                raw[:500],
            )
            return status, {"raw": raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)}

        if status >= 400:
            logger.warning("Ozon %s -> HTTP %s (request_id=%s): %s", url, status, request_id or "-", data)

        return status, data if isinstance(data, dict) else None

    async def post(self, path: str, json: Dict[str, Any]) -> Dict[str, Any]:
        # Формируем абсолютный URL вручную, чтобы в логах всегда была явная точка входа
        # (на Render фиксировали 404 на https://api-seller.ozon.ru/ без пути).
        suffix = path if path.startswith("/") else f"/{path}"
        url = f"{BASE_URL}{suffix}"
        r = await self._http_client.post(url, json=json)

        # Сначала проверяем статус, чтобы не пытаться парсить HTML/текст 404 как JSON
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError:
            logger.warning("Ozon %s -> HTTP %s", url, r.status_code)
            raise

        try:
            return r.json()
        except Exception:
            text = await r.aread()
            logger.error("Ozon %s -> JSON decode failed: %r", url, text[:500])
            raise

    async def get(self, path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        suffix = path if path.startswith("/") else f"/{path}"
        url = f"{BASE_URL}{suffix}"
        r = await self._http_client.get(url, params=params)
        try:
            data = r.json()
        except Exception:
            text = await r.aread()
            logger.error("Ozon GET %s -> HTTP %s: %r", url, r.status_code, text[:500])
            r.raise_for_status()
            return {}
        if r.status_code >= 400:
            logger.error("Ozon GET %s -> HTTP %s: %r", url, r.status_code, data)
            r.raise_for_status()
        return data

    # ---------- Финансы ----------

    async def get_finance_totals(
        self, date_from_iso: str, date_to_iso: str
    ) -> Dict[str, Any]:
        body = {
            "date": {"from": date_from_iso, "to": date_to_iso},
            "transaction_type": "all",
        }
        data = await self.post("/v3/finance/transaction/totals", body)
        res = data.get("result") if isinstance(data, dict) else {}
        return res or {}

    # ---------- FBO заказы ----------

    async def get_fbo_postings(
        self, date_from_iso: str, date_to_iso: str
    ) -> List[Dict[str, Any]]:
        """Полная выборка FBO-заказов за период через прямой REST с пагинацией."""
        postings: List[Dict[str, Any]] = []
        limit = 1000
        offset = 0

        for _ in range(60):
            body = {
                "dir": "DESC",
                "limit": limit,
                "offset": offset,
                "filter": {"since": date_from_iso, "to": date_to_iso},
                "with": {"analytics_data": True, "financial_data": True, "legal_info": False},
            }
            page = await self.post("/v2/posting/fbo/list", body)
            if not isinstance(page, dict):
                logger.error("Unexpected FBO response: %r", page)
                break

            result = page.get("result")
            if isinstance(result, list):
                # Некоторые ответы приходят списком — явно приводим к словарю
                items = result
            elif isinstance(result, dict):
                items = result.get("postings") or result.get("items") or []
            else:
                items = []

            if not items:
                break
            postings.extend(i for i in items if isinstance(i, dict))
            if len(items) < limit:
                break
            offset += limit

        return postings

    # ---------- Аккаунт ----------

    async def get_seller_info(self) -> Dict[str, Any]:
        """Получить информацию о продавце через SellerAPI /v1/seller/info."""

        api = self._get_seller_api()
        if api and hasattr(api, "seller_info"):
            try:
                if hasattr(api, "initialize"):
                    await api.initialize()  # type: ignore[func-returns-value]
                res = await api.seller_info()  # type: ignore[call-arg]
                if hasattr(res, "model_dump"):
                    return res.model_dump()
                if isinstance(res, dict):
                    return res
            except Exception:
                logger.exception("SellerAPI.seller_info failed, fallback to REST")

        data = await self.post("/v1/seller/info", {})
        if not isinstance(data, dict):
            logger.error("Unexpected seller info response: %r", data)
            raise OzonAPIError("Некорректный ответ seller/info")

        # Новые ответы SellerAPI могут не содержать поля result, сразу отдаём payload
        if "result" in data and isinstance(data.get("result"), dict):
            payload = data["result"]
        else:
            payload = data

        if isinstance(payload, dict):
            logger.info(
                "Seller info fetched: company=%s subscription=%s",
                payload.get("company", {}).get("name") if isinstance(payload.get("company"), dict) else payload.get("company"),
                payload.get("subscription"),
            )
            return payload

        raise OzonAPIError("Не удалось получить информацию о продавце")

    # ---------- Отзывы ----------

    async def get_reviews(
        self,
        date_from: datetime,
        date_to: datetime,
        *,
        limit_per_page: int = 80,
        max_count: int | None = 200,
    ) -> List[Dict[str, Any]]:
        """
        Загрузить отзывы через /v1/review/list с корректной пагинацией по last_id.

        ВАЖНО:
        - Ozon ожидает поля `date_from` и `date_to` в корне тела запроса, а не
          `filter.date.{from,to}`.
        - Пагинация делается по `last_id` + `has_next`. Offset используем не будем,
          чтобы не застревать на старых отзывах.
        """

        safe_limit = max(20, min(limit_per_page, 100))
        max_reviews = max_count if max_count is not None else 10_000

        date_from_utc = _ensure_utc(date_from)
        date_to_utc = _ensure_utc(date_to)

        reviews: List[Dict[str, Any]] = []
        last_id: str | None = None
        pages = 0

        while len(reviews) < max_reviews:
            body: Dict[str, Any] = {
                "date_from": _iso_z(date_from_utc),
                "date_to": _iso_z(date_to_utc),
                "limit": safe_limit,
            }

            # Ozon поддерживает пагинацию через last_id + has_next.
            # Если last_id уже получен, продолжаем с него.
            if last_id:
                body["last_id"] = last_id

            data = await self.post("/v1/review/list", body)
            if not isinstance(data, dict):
                logger.error("Unexpected reviews response: %r", data)
                break

            res = data.get("result") or data
            if isinstance(res, dict):
                arr = res.get("reviews") or res.get("feedbacks") or res.get("items") or []
                has_next = bool(res.get("has_next") or res.get("hasNext"))
                next_last_id = res.get("last_id") or res.get("lastId")
            elif isinstance(res, list):
                arr = res
                has_next = False
                next_last_id = None
            else:
                logger.error("Unexpected reviews result payload: %r", res)
                break

            if not isinstance(arr, list):
                logger.error("Unexpected reviews array type: %r", arr)
                break

            page_items = [x for x in arr if isinstance(x, dict)]
            if not page_items:
                # Пустая страница — выходим
                break

            reviews.extend(page_items)
            pages += 1

            if len(reviews) >= max_reviews:
                break

            if has_next and next_last_id:
                # Нормальная пагинация по last_id
                last_id = str(next_last_id)
                continue

            # has_next == False или нет last_id — дальше страниц нет
            break

        logger.info(
            "Reviews fetched: %s items for %s..%s limit=%s pages=%s max=%s",
            len(reviews),
            _iso_z(date_from_utc),
            _iso_z(date_to_utc),
            safe_limit,
            pages,
            max_count,
        )
        return reviews[:max_reviews]

    async def get_reviews_page(
        self, date_start_iso: str, date_end_iso: str, *, limit: int = 100, page: int = 1
    ) -> tuple[int, Dict[str, Any] | None]:
        """
        Получить страницу отзывов через /v1/review/list без исключений.

        Возвращает (HTTP статус, JSON или None).
        """

        body = {
            "page": max(1, page),
            "limit": max(1, min(limit, 100)),
            "date_start": date_start_iso,
            "date_end": date_end_iso,
        }
        return await self._post_with_status("/v1/review/list", body)

    async def review_list(
        self,
        *,
        limit: int = 100,
        last_id: str | None = None,
        sort_dir: str = "DESC",
        status: str = "ALL",
    ) -> dict | None:
        """Обёртка над /v1/review/list для новых бета-методов отзывов."""

        body: Dict[str, Any] = {
            "limit": max(1, min(limit, 100)),
            "sort_dir": sort_dir or "DESC",
            "status": status or "ALL",
        }
        if last_id:
            body["last_id"] = last_id

        data = await self.post("/v1/review/list", body)
        if not isinstance(data, dict):
            logger.warning("Unexpected /v1/review/list response: %r", data)
            return None
        return data.get("result") if isinstance(data.get("result"), dict) else data

    async def review_info(self, review_id: str) -> dict | None:
        """Получить детали конкретного отзыва через /v1/review/info."""

        payload = {"review_id": review_id}
        data = await self.post("/v1/review/info", payload)
        if not isinstance(data, dict):
            logger.warning("Unexpected /v1/review/info response: %r", data)
            return None
        return data.get("result") if isinstance(data.get("result"), dict) else data

    async def review_comment_list(self, review_id: str, *, limit: int = 50) -> dict | None:
        """Получить комментарии/ответы продавца через /v1/review/comment/list."""

        body = {"review_id": review_id, "limit": max(1, min(limit, 50))}
        data = await self.post("/v1/review/comment/list", body)
        if not isinstance(data, dict):
            logger.warning("Unexpected /v1/review/comment/list response: %r", data)
            return None
        return data.get("result") if isinstance(data.get("result"), dict) else data

    async def question_list(self, *, limit: int = 50, page: int | None = None) -> GetQuestionListResponse | dict | None:
        """Обёртка над /v1/question/list с валидацией схемы."""

        body: Dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if page is not None:
            body["page"] = max(1, page)

        raw = await self.post("/v1/question/list", body)
        if not isinstance(raw, dict):
            logger.warning("Unexpected /v1/question/list response: %r", raw)
            return None

        payload = raw.get("result") if isinstance(raw.get("result"), dict) else raw
        try:
            parsed = GetQuestionListResponse.model_validate(payload)
            return parsed
        except ValidationError as exc:
            logger.warning("Failed to parse questions response: %s", exc)
            return payload

    async def question_answer(
        self, question_id: str, text: str, *, sku: int | None = None
    ) -> dict | None:
        """Отправить ответ на вопрос через /v1/question/answer/create."""

        text_clean = (text or "").strip()
        if len(text_clean) < 2:
            raise OzonAPIError("Ответ пустой или слишком короткий для отправки в Ozon")

        logger.debug(
            "Sending Ozon question answer: question_id=%s, len(text)=%d, text_preview=%r",
            question_id,
            len(text_clean),
            text_clean[:80],
        )

        body = {"question_id": question_id, "text": text_clean}

        sku_clean = _clean_sku(sku)
        if sku_clean is not None:
            body["sku"] = sku_clean
        data = await self.post("/v1/question/answer/create", body)
        if not isinstance(data, dict):
            logger.warning("Unexpected /v1/question/answer/create response: %r", data)
            return None
        return data.get("result") if isinstance(data.get("result"), dict) else data

    async def question_answer_list(
        self, question_id: str, *, limit: int = 20, sku: int | None = None
    ) -> list[QuestionAnswer]:
        """Получить ответы продавца на конкретный вопрос."""

        body = {"question_id": question_id, "limit": max(1, min(limit, 50))}

        sku_clean = _clean_sku(sku)
        if sku_clean is not None:
            body["sku"] = sku_clean

        await _question_answer_rate_limiter.wait()
        status_code, payload = await self._post_with_status(
            "/v1/question/answer/list", body
        )
        if status_code >= 400:
            raise OzonAPIError(
                f"Ошибка Ozon API: HTTP {status_code} {payload.get('message') if isinstance(payload, dict) else payload}"
            )
        if not isinstance(payload, dict):
            logger.warning("Unexpected /v1/question/answer/list response: %r", payload)
            return []

        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        raw_answers = result.get("answers") if isinstance(result, dict) else []
        if not isinstance(raw_answers, list):
            raw_answers = result.get("items") if isinstance(result, dict) else []
        answers: list[QuestionAnswer] = []
        for item in raw_answers if isinstance(raw_answers, list) else []:
            if not isinstance(item, dict):
                continue
            answers.append(
                QuestionAnswer(
                    id=str(item.get("id") or item.get("answer_id") or item.get("answerId") or "")
                    or None,
                    text=str(item.get("text") or item.get("answer") or "") or None,
                    created_at=str(item.get("created_at") or item.get("createdAt") or "") or None,
                    updated_at=str(item.get("updated_at") or item.get("updatedAt") or "") or None,
                )
            )
        return answers

    async def question_answer_delete(self, answer_id: str) -> dict | None:
        """Удалить ответ продавца на вопрос через /v1/question/answer/delete."""

        body = {"answer_id": answer_id}
        status_code, payload = await self._post_with_status(
            "/v1/question/answer/delete", body
        )
        if status_code >= 400:
            raise OzonAPIError(
                f"Ошибка Ozon API: HTTP {status_code} {payload.get('message') if isinstance(payload, dict) else payload}"
            )
        if payload is None:
            logger.warning("Empty response for /v1/question/answer/delete %s", answer_id)
        return payload

    async def create_review_comment(
        self,
        review_id: str,
        text: str,
        mark_as_processed: bool = True,
        parent_comment_id: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "review_id": review_id,
            "text": text,
            "mark_review_as_processed": mark_as_processed,
        }
        if parent_comment_id:
            body["parent_comment_id"] = parent_comment_id

        try:
            res = await self.post("/v1/review/comment/create", body)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Failed to create review comment %s HTTP %s: %s",
                review_id,
                exc.response.status_code,
                exc,
            )
            raise
        except Exception as exc:
            logger.warning("Failed to create review comment %s: %s", review_id, exc)
            raise

        logger.info(
            "Review %s comment created via /v1/review/comment/create: %s",
            review_id,
            res,
        )
        return res

    async def get_analytics_by_sku(
        self, date_from: str, date_to: str, *, limit: int = 1000, offset: int = 0
    ) -> tuple[int, Dict[str, Any] | None]:
        """Вызов /v1/analytics/data для получения метаданных по SKU."""

        if _analytics_forbidden:
            return 403, None

        body = {
            "date_from": date_from,
            "date_to": date_to,
            "dimension": ["sku"],
            "metrics": ["revenue"],
            "filters": [],
            "sort": [{"key": "revenue", "order": "DESC"}],
            "limit": max(1, min(limit, 1000)),
            "offset": max(0, offset),
        }
        return await self._post_with_status("/v1/analytics/data", body)

    async def get_sku_title_map(
        self, date_from: str, date_to: str, *, limit: int = 1000, offset: int = 0
    ) -> tuple[int, Dict[str, str], list[Any]]:
        """Получить мапу SKU -> название через /v1/analytics/data."""

        status, payload = await self.get_analytics_by_sku(
            date_from, date_to, limit=limit, offset=offset
        )
        if status == 403 and isinstance(payload, dict):
            code = payload.get("code")
            message = str(payload.get("message") or "").lower()
            if code == 7 or "required role" in message:
                global _analytics_forbidden, _analytics_forbidden_logged
                _analytics_forbidden = True
                if not _analytics_forbidden_logged:
                    logger.warning(
                        "Analytics /v1/analytics/data is forbidden for the current key: %s",
                        payload,
                    )
                    _analytics_forbidden_logged = True
                return status, {}, []
        if status >= 400 and not _analytics_forbidden:
            logger.warning(
                "Analytics /v1/analytics/data HTTP %s for %s..%s",
                status,
                date_from,
                date_to,
            )
        if not isinstance(payload, dict):
            return status, {}, []

        sku_title_map, sample_rows = _parse_sku_title_map(payload)
        return status, sku_title_map, sample_rows

    async def get_product_name(self, product_id: str) -> str | None:
        """Получить название товара по product_id с кэшем и мягкими фолбэками."""

        if not product_id:
            return None

        if product_id in _product_name_cache:
            return _product_name_cache[product_id]
        if product_id in _product_info_miss_cache:
            return None

        normalized_id = str(product_id).strip()
        payload_id: int | str = normalized_id
        if normalized_id.isdigit():
            try:
                payload_id = int(normalized_id)
            except Exception:
                payload_id = normalized_id

        payload: dict[str, int | str] = {"product_id": payload_id}

        def _extract_name(res: dict[str, Any] | None) -> str | None:
            if not isinstance(res, dict):
                return None
            return str(
                next(
                    (
                        v
                        for v in (
                            res.get("name"),
                            res.get("title"),
                            res.get("product_name"),
                            res.get("offer_id"),
                        )
                        if v not in (None, "")
                    ),
                    "",
                )
            ).strip() or None

        api = self._get_seller_api()
        if api and hasattr(api, "product_info"):
            try:
                res = await api.product_info(product_id=payload["product_id"])  # type: ignore[arg-type]
                if hasattr(res, "model_dump"):
                    res = res.model_dump()
                name = _extract_name(res if isinstance(res, dict) else None)
                if name:
                    _product_name_cache[product_id] = name
                    return name
            except Exception as exc:
                logger.warning("SellerAPI product_info failed for %s: %s", product_id, exc)

        paths = ("/v1/product/info", "/v2/product/info")
        last_status: int | None = None
        for path in paths:
            try:
                data = await self.post(path, payload)
                last_status = 200
            except httpx.HTTPStatusError as exc:
                last_status = exc.response.status_code
                if last_status == 404:
                    continue
                logger.warning(
                    "Product info HTTP %s for %s at %s",
                    last_status,
                    product_id,
                    path,
                )
                continue
            except Exception as exc:
                logger.warning("Product info failed for %s on %s: %s", product_id, path, exc)
                continue

            if not isinstance(data, dict):
                logger.warning("Unexpected product info response for %s at %s: %r", product_id, path, data)
                continue

            res = data.get("result") if isinstance(data.get("result"), dict) else data
            name = _extract_name(res if isinstance(res, dict) else None)
            if name:
                _product_name_cache[product_id] = name
                return name

        if last_status == 404 or product_id not in _product_not_found_warned:
            logger.warning("Product %s not found in Ozon catalog (cached miss)", product_id)
            _product_not_found_warned.add(product_id)
        _product_name_cache[product_id] = None
        _product_info_miss_cache.add(product_id)
        return None

    # back-compat
    get_account_info = get_seller_info

    async def get_product_stocks(
        self,
        *,
        offer_id: str | None = None,
        sku: int | None = None,
        product_id: str | None = None,
    ) -> ProductStockInfo | None:
        """Fetch stock info for a single product via /v4/product/info/stocks."""

        body: Dict[str, Any] = {"filter": {}, "limit": 1}
        if offer_id:
            body["offer_id"] = [offer_id]
            body["filter"]["offer_id"] = [offer_id]
        if sku:
            body["sku"] = [sku]
            body["filter"]["sku"] = [sku]
        if product_id:
            body["product_id"] = [product_id]
            body["filter"]["product_id"] = [product_id]

        status_code, data = await self._post_with_status("/v4/product/info/stocks", body)
        if status_code >= 400 or not isinstance(data, dict):
            message = None
            if isinstance(data, dict):
                message = data.get("message") or data.get("error")
            raise OzonAPIError(
                f"Ошибка Ozon API при получении остатков: HTTP {status_code} {message or data}"
            )

        payload = data.get("result") if isinstance(data.get("result"), list) else data.get("result") or data
        items = payload if isinstance(payload, list) else payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            items = []

        parsed: list[ProductStockInfo] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            try:
                parsed.append(ProductStockInfo.model_validate(raw))
            except ValidationError as exc:
                logger.warning("Failed to parse product stock payload: %s", exc)
                continue

        if not parsed:
            lookup_key = offer_id or str(sku or product_id)
            if lookup_key and lookup_key not in _stock_not_found_warned:
                logger.warning("No stock info returned for %s", lookup_key)
                _stock_not_found_warned.add(lookup_key)
            return None

        return parsed[0]

    async def get_product_stocks_by_warehouse_fbs(
        self, *, offer_id: str | None = None, sku: int | None = None
    ) -> list[StockItem]:
        """Fetch FBS stocks by warehouse via /v1/product/info/stocks-by-warehouse/fbs."""

        body: Dict[str, Any] = {"limit": 100, "offset": 0}
        if offer_id:
            body["offer_id"] = [offer_id]
        if sku:
            body["sku"] = [sku]

        status_code, data = await self._post_with_status(
            "/v1/product/info/stocks-by-warehouse/fbs", body
        )
        if status_code >= 400 or not isinstance(data, dict):
            message = None
            if isinstance(data, dict):
                message = data.get("message") or data.get("error")
            raise OzonAPIError(
                f"Ошибка Ozon API при получении остатков по складам: HTTP {status_code} {message or data}"
            )

        payload = data.get("result") if isinstance(data.get("result"), dict) else data
        items = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            items = []

        stocks: list[StockItem] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            try:
                stocks.append(StockItem.model_validate(raw))
            except ValidationError as exc:
                logger.warning("Failed to parse warehouse stock payload: %s", exc)
                continue
        return stocks

    async def list_products(
        self,
        *,
        limit: int = 100,
        last_id: str | None = None,
        visibility: str = "ALL",
        offer_ids: Iterable[str] | None = None,
        product_ids: Iterable[int] | None = None,
    ) -> ProductListPage:
        filter_: dict[str, Any] = {"visibility": visibility}
        if offer_ids:
            filter_["offer_id"] = list(offer_ids)
        if product_ids:
            filter_["product_id"] = list(product_ids)

        body: Dict[str, Any] = {
            "filter": filter_,
            "limit": limit,
            "last_id": last_id or "",
        }

        status_code, data = await self._post_with_status("/v3/product/list", body)
        if status_code >= 400:
            raise OzonAPIError(f"Product list failed with HTTP {status_code}: {data}")

        items_raw = []
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict):
            items_raw = result.get("items") if isinstance(result.get("items"), list) else []
            last_id_val = result.get("last_id") if isinstance(result.get("last_id"), str) else None
        elif isinstance(data, dict):
            items_raw = data.get("items") if isinstance(data.get("items"), list) else []
            last_id_val = data.get("last_id") if isinstance(data.get("last_id"), str) else None
        else:
            last_id_val = None

        items: list[ProductListItem] = []
        for raw in items_raw:
            if not isinstance(raw, dict):
                continue
            try:
                items.append(ProductListItem.model_validate(raw))
            except ValidationError as exc:
                logger.warning("Failed to parse product list item: %s", exc)
                continue

        if not items and not _product_list_warned:
            logger.warning("Product list returned no items")
            globals()["_product_list_warned"] = True

        return ProductListPage(items=items, last_id=last_id_val)

    async def product_info_list(
        self,
        *,
        product_ids: Sequence[int | str] | None = None,
        offer_ids: Sequence[str] | None = None,
        skus: Sequence[int | str] | None = None,
    ) -> list[ProductInfoItem]:
        """Wrapper for /v3/product/info/list.

        Exactly one identifier group must be provided.
        """

        has_product_ids = bool(product_ids)
        has_offer_ids = bool(offer_ids)
        has_skus = bool(skus)

        if (has_product_ids + has_offer_ids + has_skus) != 1:
            raise ValueError("Provide exactly one of product_ids, offer_ids or skus")

        if has_product_ids:
            body: Dict[str, Any] = {
                "product_id": [str(pid) for pid in product_ids]
            }
        elif has_offer_ids:
            body = {"offer_id": list(offer_ids)}
        else:
            body = {"sku": [str(s) for s in skus]}

        logger.debug(
            "Ozon /v3/product/info/list request: %s", body,
        )
        status_code, data = await self._post_with_status("/v3/product/info/list", body)
        logger.info(
            "Ozon /v3/product/info/list HTTP %s, payload keys=%s",
            status_code,
            list(data.keys()) if isinstance(data, dict) else None,
        )
        if status_code >= 400:
            raise OzonAPIError(
                f"Product info list failed with HTTP {status_code}: {data}"
            )

        payload_candidates: list[Any] = []
        if isinstance(data, dict):
            payload_candidates.append(data.get("result"))
            payload_candidates.append(data)

        items_raw: list[Any] = []
        for payload in payload_candidates:
            if isinstance(payload, list):
                items_raw = payload
                break
            if isinstance(payload, dict):
                maybe_items = payload.get("items")
                if isinstance(maybe_items, list):
                    items_raw = maybe_items
                    break

        items: list[ProductInfoItem] = []
        for raw in items_raw:
            if not isinstance(raw, dict):
                continue
            try:
                items.append(ProductInfoItem.model_validate(raw))
            except ValidationError as exc:
                logger.warning("Failed to parse product info list item: %s", exc)
                continue
        return items

    async def get_product_info_list(
        self,
        *,
        product_ids: Sequence[int | str] | None = None,
        offer_ids: Sequence[str] | None = None,
        skus: Sequence[int | str] | None = None,
    ) -> list[ProductInfoItem]:
        return await self.product_info_list(
            product_ids=product_ids, offer_ids=offer_ids, skus=skus
        )

    async def generate_barcodes(self, product_ids: list[int]) -> list[str]:
        if not product_ids:
            raise ValueError("generate_barcodes: product_ids must not be empty")

        body: Dict[str, Any] = {"product_ids": product_ids}
        logger.debug("Ozon /v1/barcode/generate request: %s", body)
        status_code, data = await self._post_with_status("/v1/barcode/generate", body)
        logger.info(
            "Ozon /v1/barcode/generate HTTP %s, payload keys=%s",
            status_code,
            list(data.keys()) if isinstance(data, dict) else None,
        )
        if status_code >= 400:
            raise OzonAPIError(
                f"Barcode generation failed with HTTP {status_code}: {data}"
            )

        result = data.get("result") if isinstance(data, dict) else None

        parsed: list[str] = []

        def _collect(entry: Any) -> None:
            if isinstance(entry, str):
                parsed.append(entry)
            elif isinstance(entry, dict):
                if entry.get("barcode"):
                    parsed.append(str(entry["barcode"]))
                nested = entry.get("barcodes")
                if isinstance(nested, list):
                    for val in nested:
                        if val:
                            parsed.append(str(val))

        if isinstance(result, dict):
            barcodes = result.get("barcodes")
            if isinstance(barcodes, list):
                for entry in barcodes:
                    _collect(entry)

            if not parsed:
                fallback = result.get("barcodes_to_link")
                if isinstance(fallback, list):
                    for entry in fallback:
                        _collect(entry)

        elif isinstance(result, list):
            for item in result:
                _collect(item)

        return parsed

    async def add_barcode(self, offer_id: str, barcode: str) -> bool:
        body: Dict[str, Any] = {"offer_id": offer_id, "barcodes": [barcode]}
        status_code, data = await self._post_with_status("/v1/barcode/add", body)
        if status_code >= 400:
            logger.warning("Failed to attach barcode %s to %s: %s", barcode, offer_id, data)
            return False
        return True


class StockItem(BaseModel):
    """Normalized stock info for a product on a warehouse or by type."""

    type: str | None = None
    warehouse_id: str | None = None
    present: int = 0
    reserved: int = 0

    model_config = ConfigDict(extra="ignore", protected_namespaces=())


class ProductStockInfo(BaseModel):
    product_id: str | None = None
    offer_id: str | None = None
    sku: int | None = None
    stocks: list[StockItem] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore", protected_namespaces=())


class ProductListItem(BaseModel):
    product_id: int | None = None
    offer_id: str | None = None
    name: str | None = None
    sku: int | None = None
    visibility: str | None = None

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    @field_validator("product_id", mode="before")
    @classmethod
    def _coerce_product_id(cls, value: Any) -> Any:
        """Allow product_id to come as string without dropping the item."""
        if value is None:
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return value


class ProductListPage(BaseModel):
    items: list[ProductListItem] = Field(default_factory=list)
    last_id: str | None = None

    model_config = ConfigDict(extra="ignore", protected_namespaces=())


class ProductInfoItem(BaseModel):
    product_id: str | None = Field(default=None, alias="id")
    offer_id: str | None = None
    sku: int | None = None
    name: str | None = None
    barcode: str | None = None
    barcodes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(
        extra="ignore", protected_namespaces=(), populate_by_name=True
    )

    @field_validator("product_id", mode="before")
    @classmethod
    def _coerce_product_id(cls, value: Any) -> Any:
        """Allow integer IDs while keeping the field as string internally."""
        if value is None:
            return value
        try:
            return str(value)
        except Exception:
            return value



_client_read: OzonClient | None = None


def has_write_credentials() -> bool:
    """
    Проверить, можно ли отправлять ответы на отзывы.

    Теперь используется тот же самый ключ OZON_API_KEY, что и для чтения.
    Главное — чтобы в личном кабинете Ozon у этого ключа были права
    на работу с отзывами (просмотр и ответы продавца).
    """
    client_id = (os.getenv("OZON_CLIENT_ID") or "").strip()
    api_key = (os.getenv("OZON_API_KEY") or "").strip()
    return bool(client_id and api_key)


# ---------- Chats ----------


class ChatSummary(BaseModel):
    chat_id: str | None = None
    id: str | None = None
    posting_number: str | None = None
    order_id: str | None = None
    raw: dict = Field(default_factory=dict, alias="_raw")
    buyer_name: str | None = None
    client_name: str | None = None
    customer_name: str | None = None
    last_message: dict | str | None = None
    last_message_text: str | None = None
    last_message_time: str | None = None
    updated_at: str | None = None
    unread_count: int | None = None
    is_unread: bool | None = None
    has_unread: bool | None = None

    model_config = ConfigDict(extra="allow", protected_namespaces=(), populate_by_name=True)

    def to_dict(self) -> dict:
        data = self.model_dump(exclude_none=True, by_alias=True)
        if not data.get("chat_id") and self.id:
            data["chat_id"] = str(self.id)
        if self.last_message is not None:
            data["last_message"] = self.last_message
        return data


class ChatListItem(BaseModel):
    chat_id: str | None = None
    id: str | None = None
    title: str | None = None
    chat_type: str | None = None
    unread_count: int | None = None
    last_message_id: int | None = None

    model_config = ConfigDict(extra="allow", protected_namespaces=(), populate_by_name=True)

    @property
    def safe_chat_id(self) -> str | None:
        return str(self.chat_id or self.id).strip() if (self.chat_id or self.id) else None


class ChatListResponse(BaseModel):
    chats: list[ChatListItem] = Field(default_factory=list)
    items: list[dict] | None = None
    result: list[dict] | dict | None = None

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    def iter_items(self):
        candidates: list[list[dict]] = []
        if self.chats:
            candidates.append([c.model_dump(mode="python") for c in self.chats])
        if isinstance(self.items, list):
            candidates.append(self.items)
        if isinstance(self.result, list):
            candidates.append(self.result)
        elif isinstance(self.result, dict):
            for key in ("chats", "items", "result"):
                maybe_items = self.result.get(key)
                if isinstance(maybe_items, list):
                    candidates.append(maybe_items)

        for bucket in candidates:
            for item in bucket:
                try:
                    yield ChatListItem.model_validate(item)
                except ValidationError:
                    continue


class ChatHistoryResponse(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    items: list[dict] | None = None
    result: list[dict] | dict | None = None

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    def iter_items(self):
        candidates: list[list[dict]] = []
        if self.messages:
            candidates.append(self.messages)
        if isinstance(self.items, list):
            candidates.append(self.items)
        if isinstance(self.result, list):
            candidates.append(self.result)
        elif isinstance(self.result, dict):
            for key in ("messages", "items", "result"):
                maybe_items = self.result.get(key)
                if isinstance(maybe_items, list):
                    candidates.append(maybe_items)

        for bucket in candidates:
            for item in bucket:
                yield item


def _merge_nested_block(item: dict, key: str) -> dict:
    if not isinstance(item, dict):
        return {}
    nested = item.get(key)
    if isinstance(nested, dict):
        merged = {**item, **nested}
        merged.pop(key, None)
        return merged
    return item


def _normalize_chat_history_payload(data: object) -> tuple[dict | None, bool]:
    """Coerce chat history responses into a dict payload and flag anomalies."""

    payload: object = data
    payload_was_weird = False

    if isinstance(data, dict):
        result_block = data.get("result")
        if isinstance(result_block, dict):
            payload = result_block
        elif isinstance(result_block, list):
            payload = {"result": result_block}
            payload_was_weird = True
        else:
            payload = data
    else:
        payload_was_weird = True

    if not isinstance(payload, dict):
        if isinstance(payload, list):
            payload = {"items": payload}
            payload_was_weird = True
        else:
            return None, True

    return payload, payload_was_weird


def _normalize_chat_list_payload(data: object) -> dict | None:
    """Coerce chat list responses into a dict payload or return None."""

    payload: object = data
    if isinstance(data, dict):
        result_block = data.get("result")
        if isinstance(result_block, dict):
            payload = result_block
        elif isinstance(result_block, list):
            payload = {"result": result_block}
        else:
            payload = data

    if isinstance(payload, list):
        payload = {"items": payload}

    if isinstance(payload, dict):
        return payload
    return None


async def chat_list(
    limit: int = 100,
    offset: int = 0,
    chat_status: str = "ALL",
    unread_only: bool = False,
    include_service: bool = False,
    refresh: bool | None = None,  # backward-compatible no-op
) -> ChatListResponse:
    """List chats via the current v3 endpoint.

    Новая версия API не принимает refresh и больше не поддерживает /v2/chat/list.
    Сохраняем мягкий парсинг для разных форматов ответа (chats/items/result).
    """
    client = get_client()

    safe_limit = max(1, min(int(limit or 0), 500))
    offset_cur = max(0, int(offset or 0))

    safe_status = (chat_status or "ALL").upper()
    body_filter: dict[str, object] = {
        "chat_status": safe_status,
        "unread_only": bool(unread_only),
    }

    collected: list[ChatListItem] = []
    max_pages = 50  # защита от бесконечного цикла при странных пагинациях
    for _ in range(max_pages):
        if len(collected) >= safe_limit:
            break

        page_limit = min(100, safe_limit - len(collected))
        body = {
            "limit": int(page_limit),
            "offset": int(offset_cur),
            "filter": body_filter,
        }

        status_code, data = await client._post_with_status("/v3/chat/list", body)
        if status_code >= 400:
            raise OzonAPIError(f"Ozon API error {status_code} on /v3/chat/list: {data}")

        payload = _normalize_chat_list_payload(data)
        if payload is None:
            logger.warning("Chat list payload is not a dict: %r", data)
            return ChatListResponse(chats=[])
        parsed_items: list[ChatListItem] = []
        try:
            parsed = ChatListResponse.model_validate(payload)
            parsed_items = list(parsed.iter_items())
        except ValidationError as exc:
            logger.warning("Failed to parse chat list payload: %s", exc)
            if isinstance(payload, dict):
                maybe_items = payload.get("chats") or payload.get("items")
                if isinstance(maybe_items, list):
                    for raw in maybe_items:
                        if isinstance(raw, dict):
                            if isinstance(raw.get("chat"), dict):
                                merged = _merge_nested_block(raw, "chat")
                            else:
                                merged = raw
                            try:
                                parsed_items.append(ChatListItem.model_validate(merged))
                            except ValidationError:
                                continue

        if not parsed_items:
            break

        for item in parsed_items:
            if not include_service:
                ctype = str(item.chat_type or "").lower()
                if ctype and ctype not in ("buyer_seller", "buyer-seller", "buyer_seller_chat"):
                    continue
            collected.append(item)

        if len(parsed_items) < page_limit:
            break

        offset_cur += len(parsed_items)

    return ChatListResponse(chats=collected)


async def chat_history(chat_id: str, *, limit: int = 30) -> list[dict]:
    client = get_client()
    body = {"chat_id": chat_id, "limit": max(1, min(limit, 50))}
    status_code, data = await client._post_with_status("/v3/chat/history", body)
    if status_code >= 400 or not isinstance(data, dict):
        message = None
        if isinstance(data, dict):
            message = data.get("message") or data.get("error")
            if data.get("code") == 9:
                logger.warning("Ozon chat history method is obsolete; check API version update")
        raise OzonAPIError(
            f"Ошибка Ozon API при получении истории чата: HTTP {status_code} {message or data}"
        )

    payload, payload_was_weird = _normalize_chat_history_payload(data)
    if payload is None:
        logger.warning("Chat history payload is not a dict: %r", data)
        return [
            {"text": "История пуста / не удалось разобрать ответ API", "_raw": data}
        ]

    raw_items: list[dict] = []
    try:
        parsed = ChatHistoryResponse.model_validate(payload)
        raw_items = list(parsed.iter_items())
    except ValidationError as exc:
        payload_was_weird = True
        logger.warning("Failed to parse chat history payload: %s", exc)
        if isinstance(payload, dict):
            for key in ("messages", "items", "result"):
                maybe = payload.get(key)
                if isinstance(maybe, list):
                    raw_items = maybe
                    break
                if isinstance(maybe, dict):
                    for inner_key in ("messages", "items", "result"):
                        inner = maybe.get(inner_key)
                        if isinstance(inner, list):
                            raw_items = inner
                            break
                    if raw_items:
                        break
        if not raw_items:
            logger.warning("Chat history payload is empty or invalid: %r", payload)
            return [
                {
                    "text": "История пуста / не удалось разобрать ответ API",
                    "_raw": payload,
                }
            ]

    if not raw_items:
        if payload_was_weird:
            logger.warning("Chat history payload has no messages: %r", payload)
            return [
                {
                    "text": "История пуста / не удалось разобрать ответ API",
                    "_raw": payload,
                }
            ]
        return []

    messages: list[dict] = []
    for raw in raw_items:
        merged = _merge_nested_block(raw, "message")
        if not isinstance(merged, dict):
            continue
        try:
            normalized = ChatMessage.model_validate(merged).to_dict()
        except ValidationError as exc:
            logger.warning("Failed to normalize chat message: %s; using raw item", exc)
            normalized = dict(merged)

        normalized.setdefault("_raw", merged)
        messages.append(normalized)
    return messages


async def download_with_auth(url: str) -> bytes:
    """Скачать файл с Ozon API с учётом авторизационных заголовков."""

    client = get_client()
    response = await client._http_client.get(url)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:  # pragma: no cover - сеть/HTTP
        logger.warning("Ozon %s -> HTTP %s", url, response.status_code)
        raise OzonAPIError(f"Не удалось скачать вложение: {exc}") from exc

    return await response.aread()


async def download_chat_file(url: str) -> tuple[bytes, str]:
    """Скачать файл чата с учётом авторизации и вернуть содержимое и мета."""

    client = get_client()
    response = await client._http_client.get(url)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:  # pragma: no cover - сеть/HTTP
        logger.warning("Ozon %s -> HTTP %s", url, response.status_code)
        raise OzonAPIError(f"Не удалось скачать вложение: {exc}") from exc

    content = await response.aread()
    meta = response.headers.get("Content-Type") or ""
    if not meta:
        meta = response.headers.get("Content-Disposition") or ""
    return content, meta


async def chat_read(chat_id: str, messages: Sequence[dict] | None = None) -> None:
    """Mark chat messages as read up to the latest known message."""

    if messages:
        last_message_id: str | None = None
        for raw in reversed(messages):
            if not isinstance(raw, dict):
                continue
            message_id = raw.get("message_id") or raw.get("id")
            if message_id:
                last_message_id = str(message_id)
                break
        if not last_message_id:
            logger.warning("Chat %s has messages without message_id, skip chat/read", chat_id)
            return
    else:
        logger.info("No messages in chat %s, skip chat/read", chat_id)
        return

    client = get_client()
    body = {"chat_id": chat_id, "from_message_id": last_message_id}
    status_code, data = await client._post_with_status("/v2/chat/read", body)
    if status_code >= 400:
        logger.warning("Failed to mark chat %s as read: %s", chat_id, data)


async def chat_send_message(chat_id: str, text: str) -> None:
    client = get_write_client()
    if client is None:
        raise OzonAPIError("Нет прав на отправку сообщений в чаты Ozon")

    text_clean = (text or "").strip()
    if len(text_clean) < 2:
        raise OzonAPIError("Текст сообщения пустой или слишком короткий")

    body = {"chat_id": chat_id, "text": text_clean}
    status_code, data = await client._post_with_status("/v1/chat/send/message", body)
    if status_code >= 400:
        message = None
        if isinstance(data, dict):
            message = data.get("message") or data.get("error")
        raise OzonAPIError(
            f"Ошибка Ozon API при отправке сообщения: HTTP {status_code} {message or data}"
        )
    if isinstance(data, dict) and data.get("result") is False:
        raise OzonAPIError("Ozon отклонил отправку сообщения в чат")


async def get_posting_products(posting_number: str) -> list[str]:
    """Fetch product titles for a posting number using Ozon API.

    The helper is resilient to schema changes and returns an empty list on errors.
    """

    posting = (posting_number or "").strip()
    if not posting:
        return []

    client = get_client()
    body = {"posting_number": posting}
    try:
        status_code, data = await client._post_with_status("/v3/posting/fbs/get", body)
    except Exception as exc:  # pragma: no cover - network/safety
        logger.warning("Failed to load posting %s products: %s", posting, exc)
        return []

    if status_code >= 400 or not isinstance(data, (dict, list)):
        logger.warning("Unexpected response for posting %s: %s", posting, data)
        return []

    payload = data.get("result") if isinstance(data, dict) else data

    def _extract_products(container: dict | list | None) -> list[dict]:
        if isinstance(container, list):
            return [item for item in container if isinstance(item, dict)]
        if isinstance(container, dict):
            for key in ("products", "items"):
                maybe = container.get(key)
                if isinstance(maybe, list):
                    return [item for item in maybe if isinstance(item, dict)]
        return []

    buckets: list[list[dict]] = []
    if isinstance(payload, dict):
        buckets.append(_extract_products(payload.get("posting") if isinstance(payload.get("posting"), dict) else payload))
        buckets.append(_extract_products(payload.get("result") if isinstance(payload.get("result"), dict) else None))
        buckets.append(_extract_products(payload))
    elif isinstance(payload, list):
        buckets.append(_extract_products(payload[0] if payload else None))

    names: list[str] = []
    for bucket in buckets:
        for item in bucket:
            name = item.get("name") or item.get("product_name") or item.get("offer_name")
            if name:
                names.append(str(name))

    return names


async def get_posting_details(posting_number: str) -> tuple[dict | None, str | None]:
    """Fetch posting details trying FBS first then FBO.

    Returns a tuple of (payload, schema) where schema is ``"fbs"`` or ``"fbo"``
    depending on which endpoint returned data.
    """

    posting = (posting_number or "").strip()
    if not posting:
        return None, None

    client = get_client()
    for path, schema in (("/v3/posting/fbs/get", "fbs"), ("/v2/posting/fbo/get", "fbo")):
        try:
            status_code, data = await client._post_with_status(path, {"posting_number": posting})
        except Exception as exc:  # pragma: no cover - network/safety
            logger.warning("Failed to load posting %s via %s: %s", posting, path, exc)
            continue

        if status_code >= 400 or not isinstance(data, (dict, list)):
            continue

        payload = data.get("result") if isinstance(data, dict) else data
        if isinstance(payload, dict):
            return payload, schema
        if isinstance(payload, list) and payload:
            maybe = payload[0]
            if isinstance(maybe, dict):
                return maybe, schema

    logger.warning("Posting %s not found via FBS/FBO endpoints", posting)
    return None, None


async def chat_start(posting_number: str) -> dict | None:
    client = get_write_client()
    if client is None:
        raise OzonAPIError("Нет прав на создание чатов в Ozon")

    body = {"posting_number": posting_number}
    status_code, data = await client._post_with_status("/v1/chat/start", body)
    if status_code >= 400:
        message = None
        if isinstance(data, dict):
            message = data.get("message") or data.get("error")
        raise OzonAPIError(
            f"Ошибка Ozon API при создании чата: HTTP {status_code} {message or data}"
        )
    if isinstance(data, dict):
        return data.get("result") or data
    return None


def get_client() -> OzonClient:
    """
    Ленивая инициализация клиента Ozon для всех операций (чтение, аналитика, ответы).
    Используется один ключ OZON_API_KEY.
    """
    global _client_read
    if _client_read is None:
        client_id, api_key = _env_read_credentials()
        _client_read = OzonClient(client_id=client_id, api_key=api_key)
    return _client_read


def get_write_client() -> OzonClient | None:
    """
    Совместимый с прежним кодом «write‑клиент».

    Сейчас используется тот же клиент, что и для чтения.
    Если переменные окружения не заданы, вернёт None.
    """
    if not has_write_credentials():
        return None
    return get_client()


async def close_clients() -> None:
    """Закрыть общий http‑клиент и сбросить кеш."""

    global _client_read
    if _client_read is not None:
        try:
            await _client_read.aclose()
        except Exception:  # pragma: no cover - best effort
            logger.warning("Failed to close Ozon client HTTP session", exc_info=True)
        _client_read = None


# ---------- Questions helpers ----------


QUESTION_STATUS_MAP: Dict[str, str] = {
    "all": "ALL",
    "unanswered": "UNPROCESSED",
    "answered": "PROCESSED",
}


class QuestionListFilter(BaseModel):
    status: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="allow", protected_namespaces=())


class GetQuestionListRequest(BaseModel):
    limit: int = Field(..., ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    filter: QuestionListFilter | None = None

    model_config = ConfigDict(populate_by_name=True, extra="allow", protected_namespaces=())


class QuestionListItem(BaseModel):
    """Гибкая модель элемента из /v1/question/list.

    Ozon иногда меняет схему, поэтому:
    * все поля опциональные,
    * extra-поля разрешены,
    * id можно получить и из ``id``, и из ``question_id``.
    """

    id: str | None = None
    question_id: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None
    sku: Any | None = None
    product_id: Any | None = None
    product_name: str | None = None
    product_title: str | None = None
    item_name: str | None = None
    title: str | None = None
    product_url: str | None = None
    published_at: Any | None = None
    text: str | None = None
    question_text: str | None = None
    question: str | None = None
    answer_text: str | None = None
    answer: str | None = None
    message: str | None = None
    status: str | None = None

    model_config = ConfigDict(extra="allow", protected_namespaces=())


class GetQuestionListResult(BaseModel):
    questions: list[QuestionListItem] = Field(default_factory=list)
    items: list[QuestionListItem] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    def collect(self) -> list[QuestionListItem]:
        if self.questions:
            return self.questions
        if self.items:
            return self.items
        return []


class GetQuestionListResponse(BaseModel):
    """Нормализованный ответ /v1/question/list.

    Возможные варианты структуры:
      * {"result": {"questions": [...]}}
      * {"result": {"items": [...]}}
      * {"questions": [...]}
      * {"items": [...]}
    """

    result: GetQuestionListResult | None = None
    questions: list[QuestionListItem] = Field(default_factory=list)
    items: list[QuestionListItem] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    def collect(self) -> list[QuestionListItem]:
        if self.result:
            collected = self.result.collect()
            if collected:
                return collected
        if self.questions:
            return self.questions
        if self.items:
            return self.items
        return []


@dataclass
class QuestionAnswer:
    id: str | None
    text: str | None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class Question:
    id: str
    question_text: str
    created_at: str | None = None
    sku: int | None = None
    product_id: str | None = None
    product_name: str | None = None
    answer_text: str | None = None
    status: str | None = None
    answer_created_at: str | None = None
    has_answer: bool = False
    answer_id: str | None = None
    answers_count: int | None = None


def _name_from_product_url(url: str) -> str | None:
    try:
        path = urlparse(url).path
    except Exception:
        return None

    parts = [p for p in path.split("/") if p]
    if "product" not in parts:
        return None

    try:
        slug = parts[parts.index("product") + 1]
    except Exception:
        return None

    slug = unquote(slug)
    tokens = slug.split("-")
    if tokens and tokens[-1].isdigit():
        tokens = tokens[:-1]
    name = " ".join(tokens).strip()
    return name or None


def _parse_question_item(item: Dict[str, Any]) -> Question | None:
    """Приводим "сырой" элемент ответа к dataclass Question.

    Принимает либо dict, либо QuestionListItem.
    """

    try:
        answers_count = 0
        if isinstance(item, QuestionListItem):
            question_id_raw = item.id or item.question_id
            created = item.published_at or item.created_at
            extras = getattr(item, "model_extra", {}) or {}
            product_name = (
                item.product_name
                or item.product_title
                or item.item_name
                or item.title
                or extras.get("product_name")
                or extras.get("product_title")
                or extras.get("item_name")
                or extras.get("title")
            )
            question_text = (
                item.question_text
                or item.text
                or item.question
                or extras.get("question_text")
                or extras.get("question")
                or extras.get("text")
            )
            answer_text = (
                item.answer_text
                or item.answer
                or extras.get("answer_text")
                or extras.get("answer")
                or extras.get("message")
            )
            answer_id = getattr(item, "answer_id", None) or extras.get("answer_id")
            sku_val = item.sku or item.product_id
            status = item.status or extras.get("status")
            product_url = item.product_url or extras.get("product_url")
            answers_count = (
                getattr(item, "answers_count", None)
                or extras.get("answers_count")
                or 0
            )
        else:
            question_id_raw = item.get("question_id") or item.get("id")
            created = (
                item.get("created_at")
                or item.get("createdAt")
                or item.get("date")
                or item.get("published_at")
                or item.get("publishedAt")
            )
            product_name = (
                item.get("product_name")
                or item.get("item_name")
                or item.get("product_title")
                or item.get("title")
            )
            question_text = (
                item.get("question_text")
                or item.get("question")
                or item.get("text")
                or item.get("message")
            )
            answer_text = (
                item.get("answer_text")
                or item.get("answer")
                or item.get("message")
            )
            answer_id = item.get("answer_id")
            sku_val = (
                item.get("sku")
                or item.get("product_id")
                or item.get("productId")
            )
            status = item.get("status")
            product_url = item.get("product_url")
            answers_count = item.get("answers_count") or item.get("answersCount") or 0

        question_id = str(question_id_raw or "").strip()
        if not question_id:
            return None

        try:
            sku_int = int(sku_val) if sku_val is not None else None
        except Exception:
            sku_int = None

        if (product_name in (None, "")) and product_url:
            product_name = _name_from_product_url(str(product_url)) or product_name

        answer_text_clean = str(answer_text) if answer_text not in (None, "") else None
        try:
            answers_count_int = int(answers_count) if answers_count is not None else 0
        except Exception:
            answers_count_int = 0
        has_answer = (
            bool(answer_text_clean)
            or answers_count_int > 0
            or str(status or "").upper() == "PROCESSED"
        )
        return Question(
            id=question_id,
            created_at=str(created) if created is not None else None,
            sku=sku_int,
            product_id=str(sku_val) if sku_val is not None else None,
            product_name=str(product_name) if product_name not in (None, "") else None,
            question_text=str(question_text) if question_text not in (None, "") else "",
            answer_text=answer_text_clean,
            status=str(status or "").strip() or None,
            has_answer=has_answer,
            answer_id=str(answer_id) if answer_id not in (None, "") else None,
            answers_count=answers_count_int,
        )
    except Exception as exc:  # pragma: no cover - защита от неожиданных данных
        logger.warning("Failed to parse question item %s: %s", item, exc)
        return None


def _map_question_status(category: str | None) -> str:
    if not category:
        return ""
    mapped = QUESTION_STATUS_MAP.get(category)
    if not mapped:
        logger.warning("Unknown question category %s, fallback to ALL", category)
        return ""
    return mapped


# ---------- Questions ----------


async def get_questions_list(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Question]:
    """Получить список вопросов покупателей через Seller API.

    Устойчиво к неожиданным форматам ответа:
    при ошибке валидации возвращает список (может быть пустой),
    а не падает с исключением.
    """

    client = get_client()
    ozon_status = _map_question_status(status)

    request = GetQuestionListRequest(
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
        filter=QuestionListFilter(status=ozon_status) if ozon_status else None,
    )

    body = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    status_code, data = await client._post_with_status("/v1/question/list", body)
    if status_code >= 400 or not isinstance(data, dict):
        message = None
        if isinstance(data, dict):
            message = data.get("message") or data.get("error")
        logger.warning("Failed to fetch questions: HTTP %s %s", status_code, data)
        raise OzonAPIError(f"Ошибка Ozon API: HTTP {status_code} {message or data}")

    # Аккуратно парсим ответ
    try:
        resp = GetQuestionListResponse.model_validate(
            data.get("result") if isinstance(data.get("result"), dict) else data
        )
        raw_items: list[QuestionListItem | Dict[str, Any]] = resp.collect()
    except ValidationError as exc:
        # Если схема изменилась — логируем и пробуем работать как с "сырым" dict
        logger.warning("Failed to parse questions response: %s", exc)
        payload = data.get("result") if isinstance(data.get("result"), dict) else data
        arr = payload.get("questions") if isinstance(payload, dict) else []
        if not isinstance(arr, list):
            arr = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(arr, list):
            logger.warning("Unexpected questions payload: %r", data)
            return []
        raw_items = arr  # type: ignore[assignment]

    result: list[Question] = []
    for raw in raw_items:
        parsed = _parse_question_item(raw)
        if parsed:
            result.append(parsed)
    return result


async def send_question_answer(question_id: str, text: str, *, sku: int | None = None) -> None:
    client = get_write_client()
    if client is None:
        raise OzonAPIError("Нет прав на отправку ответов в Ozon")

    text_clean = (text or "").strip()
    if len(text_clean) < 2:
        raise OzonAPIError("Ответ пустой или слишком короткий, сначала отредактируйте текст")

    logger.debug(
        "Sending Ozon question answer: question_id=%s, len(text)=%d, text_preview=%r",
        question_id,
        len(text_clean),
        text_clean[:80],
    )

    body = {"question_id": question_id, "text": text_clean}
    sku_clean = _clean_sku(sku)
    if sku_clean is not None:
        body["sku"] = sku_clean
    status_code, data = await client._post_with_status("/v1/question/answer/create", body)
    if status_code >= 400:
        raise OzonAPIError(
            f"Ошибка Ozon API: HTTP {status_code} {data.get('message') if isinstance(data, dict) else data}"
        )
    if data is None:
        raise OzonAPIError(
            "Ошибка Ozon API: пустой ответ при отправке ответа на вопрос"
        )


async def list_question_answers(
    question_id: str, *, limit: int = 20, sku: int | None = None
) -> list[QuestionAnswer]:
    sku_clean = _clean_sku(sku)
    if sku is not None and sku_clean is None:
        logger.warning(
            "Skip question_answer_list for %s: invalid sku=%r", question_id, sku
        )
        return []

    if sku_clean is None:
        logger.warning(
            "Skip question_answer_list for %s: missing SKU to avoid 400 from Ozon",
            question_id,
        )
        return []

    client = get_client()
    answers = await client.question_answer_list(
        question_id, limit=limit, sku=sku_clean
    )
    return answers


async def get_question_answers(
    question_id: str, *, limit: int = 20, sku: int | None = None
) -> list[QuestionAnswer]:
    """Совместимый алиас для получения ответов на вопрос."""

    return await list_question_answers(question_id, limit=limit, sku=sku)


async def delete_question_answer(
    question_id: str, *, answer_id: str | None = None, sku: int | None = None
) -> None:
    client = get_write_client()
    if client is None:
        raise OzonAPIError("Нет прав на удаление ответов в Ozon")

    target_answer_id = answer_id
    if not target_answer_id:
        try:
            existing = await client.question_answer_list(
                question_id, limit=1, sku=sku
            )
        except Exception as exc:
            raise OzonAPIError(f"Не удалось получить список ответов: {exc}")
        target_answer_id = existing[0].id if existing else None

    if not target_answer_id:
        raise OzonAPIError("Ответ не найден, удалять нечего")

    await client.question_answer_delete(target_answer_id)


async def get_question_by_id(question_id: str) -> Question | None:
    questions = await get_questions_list(status=None, limit=200, offset=0)
    for q in questions:
        if q.id == question_id:
            return q
    return None
