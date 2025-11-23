# botapp/ozon_client.py
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, date, timezone
from typing import Any, Dict, List, Tuple

import httpx
from dotenv import load_dotenv

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
        r = await self._http_client.post(url, json=json)
        status = r.status_code
        try:
            data = r.json()
        except Exception:
            try:
                raw = await r.aread()
            except Exception:
                raw = b""
            logger.error("Ozon %s -> JSON decode failed: %s", url, raw[:500])
            return status, None

        if status >= 400:
            logger.warning("Ozon %s -> HTTP %s: %s", url, status, data)

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
        if status >= 400:
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
        for path in paths:
            try:
                data = await self.post(path, payload)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.info("Product %s returned 404 at %s, trying next", product_id, path)
                    continue
                logger.warning(
                    "Product info HTTP %s for %s at %s",
                    exc.response.status_code,
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
            logger.info("Product name missing for %s at %s, trying next", product_id, path)

        if product_id not in _product_not_found_warned:
            logger.warning("Product %s not found in Ozon catalog (cached miss)", product_id)
            _product_not_found_warned.add(product_id)
        _product_name_cache[product_id] = None
        return None

    # back-compat
    get_account_info = get_seller_info


_client_read: OzonClient | None = None
_client_write: OzonClient | None = None


def has_write_credentials() -> bool:
    """Проверить наличие write-ключа в окружении."""

    return bool((os.getenv("OZON_WRITE_API_KEY") or "").strip())


def get_client() -> OzonClient:
    """Ленивая инициализация клиента для чтения."""

    global _client_read
    if _client_read is None:
        client_id, api_key = _env_read_credentials()
        _client_read = OzonClient(client_id=client_id, api_key=api_key)
    return _client_read


def get_write_client() -> OzonClient | None:
    """Получить write-клиент, если ключ в окружении задан."""

    global _client_write
    api_key = (os.getenv("OZON_WRITE_API_KEY") or "").strip()
    if not api_key:
        return None

    client_id = (
        os.getenv("OZON_WRITE_CLIENT_ID")
        or os.getenv("OZON_CLIENT_ID")
        or ""
    ).strip()
    if not client_id:
        logger.warning("OZON_WRITE_API_KEY задан, но OZON_WRITE_CLIENT_ID пуст")
        return None

    if _client_write is None:
        _client_write = OzonClient(client_id=client_id, api_key=api_key)
    return _client_write

