# botapp/ozon_client.py
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Tuple

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

BASE_URL = "https://api-seller.ozon.ru"
MSK_SHIFT = timedelta(hours=3)


def _iso_z(dt: datetime) -> str:
    """Вернуть ISO-строку в UTC с Z без миллисекунд."""
    return dt.replace(microsecond=0).isoformat() + "Z"


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


def fmt_int(n: float | int) -> str:
    return f"{int(round(n)):,.0f}".replace(",", " ")


def fmt_rub0(n: float | int) -> str:
    return f"{int(round(n)):,.0f} ₽".replace(",", " ")


def s_num(x: Any) -> float:
    try:
        return float(str(x).replace(" ", "").replace(",", ".")) if x is not None else 0.0
    except Exception:
        return 0.0


def _env_credentials() -> tuple[str, str]:
    client_id = (os.getenv("OZON_CLIENT_ID") or "").strip()
    api_key = (os.getenv("OZON_API_KEY") or "").strip()
    if not client_id or not api_key:
        raise RuntimeError("Не заданы OZON_CLIENT_ID / OZON_API_KEY")
    return client_id, api_key


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

    async def aclose(self) -> None:
        await self._http_client.aclose()

    async def post(self, path: str, json: Dict[str, Any]) -> Dict[str, Any]:
        # Формируем абсолютный URL вручную, чтобы в логах всегда была явная точка входа
        # (на Render фиксировали 404 на https://api-seller.ozon.ru/ без пути).
        suffix = path if path.startswith("/") else f"/{path}"
        url = f"{BASE_URL}{suffix}"
        r = await self._http_client.post(url, json=json)
        try:
            data = r.json()
        except Exception:
            text = await r.aread()
            logger.error("Ozon %s -> HTTP %s: %r", url, r.status_code, text[:500])
            r.raise_for_status()
            return {}
        if r.status_code >= 400:
            logger.error("Ozon %s -> HTTP %s: %r", url, r.status_code, data)
            r.raise_for_status()
        return data

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

    async def get_company_info(self) -> Dict[str, Any]:
        paths = ["/v1/company/info", "/v2/company/info"]
        errors: list[int] = []

        for path in paths:
            for method in (self.post, self.get):
                try:
                    data = await method(path, {} if method is self.post else None)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in (404, 405):
                        errors.append(status)
                        continue
                    raise

                if isinstance(data, dict):
                    res = data.get("result") or data
                    if res:
                        return res
                logger.error("Unexpected company info response (%s): %r", path, data)

        if errors:
            logger.warning("Company info endpoint returned %s", errors)
        return {}

    # ---------- Отзывы ----------

    async def get_reviews(
        self, date_from_iso: str, date_to_iso: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        /v1/review/list с пагинацией.

        Метод поддерживает фильтр по дате только на уровне дней, поэтому
        используем YYYY-MM-DD и листаем страницы, пока не закончится выдача.
        """

        date_filter = {
            "from": date_from_iso[:10],
            "to": date_to_iso[:10],
        }

        page = 1
        reviews: List[Dict[str, Any]] = []

        while page <= 50:  # защитимся от бесконечной пагинации
            body = {
                "page": page,
                "limit": limit,
                "filter": {"date": date_filter},
            }
            data = await self.post("/v1/review/list", body)
            if not isinstance(data, dict):
                logger.error("Unexpected reviews response: %r", data)
                break

            res = data.get("result") or data
            arr: List[Dict[str, Any]] = []
            if isinstance(res, dict):
                arr = (
                    res.get("reviews")
                    or res.get("feedbacks")
                    or res.get("items")
                    or []
                )
            elif isinstance(res, list):
                arr = res

            page_items = [r for r in arr if isinstance(r, dict)]
            reviews.extend(page_items)

            # прекращаем, если меньше лимита или пусто
            if len(page_items) < limit:
                break
            page += 1

        return reviews


_client: OzonClient | None = None


def get_client() -> OzonClient:
    """Ленивая инициализация клиента с учётом .env."""
    global _client
    if _client is None:
        client_id, api_key = _env_credentials()
        _client = OzonClient(client_id=client_id, api_key=api_key)
    return _client

