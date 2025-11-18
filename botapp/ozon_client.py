# botapp/ozon_client.py
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Tuple

import httpx
from dotenv import load_dotenv
from ozonapi import SellerAPI, SellerAPIConfig
from ozonapi.seller.schemas.entities.postings.filter import PostingFilter
from ozonapi.seller.schemas.entities.postings.filter_with import PostingFilterWith
from ozonapi.seller.schemas.fbo.v2__posting_fbo_list import PostingFBOListRequest

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
        self._api = SellerAPI(
            client_id=self.client_id,
            api_key=self.api_key,
            config=SellerAPIConfig(client_id=self.client_id, api_key=self.api_key),
        )
        self._http_client = httpx.AsyncClient(
            base_url=BASE_URL,
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
        await self._api.close()

    async def post(self, path: str, json: Dict[str, Any]) -> Dict[str, Any]:
        url = path if path.startswith("/") else f"/{path}"
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

    # ---------- Финансы ----------

    async def get_finance_totals(
        self, date_from_iso: str, date_to_iso: str
    ) -> Dict[str, Any]:
        body = {
            "date": {"from": date_from_iso, "to": date_to_iso},
            "transaction_type": "all",
        }
        data = await self.post("/v3/finance/transaction/totals", body)
        return data.get("result") or {}

    # ---------- FBO заказы ----------

    async def get_fbo_postings(
        self, date_from_iso: str, date_to_iso: str
    ) -> List[Dict[str, Any]]:
        """Полная выборка FBO-заказов за период через SellerAPI с пагинацией."""
        postings: List[Dict[str, Any]] = []
        limit = 1000
        offset = 0

        for _ in range(60):
            request = PostingFBOListRequest(
                dir="DESC",
                limit=limit,
                offset=offset,
                filter=PostingFilter(since=date_from_iso, to_=date_to_iso),
                with_=PostingFilterWith(
                    analytics_data=True, financial_data=False, legal_info=False
                ),
            )
            page = await self._api.posting_fbo_list(request)
            items = page.result.postings if getattr(page, "result", None) else []
            if not items:
                break
            postings.extend([p.model_dump() for p in items])
            if len(items) < limit:
                break
            offset += limit

        return postings

    # ---------- Аккаунт ----------

    async def get_company_info(self) -> Dict[str, Any]:
        data = await self.post("/v1/company/info", {})
        return data.get("result") or data

    # ---------- Отзывы ----------

    async def get_reviews(
        self, date_from_iso: str, date_to_iso: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        /v1/review/list — одна страница (до 100 отзывов) за период.
        Используем прямой запрос, т.к. метод пока отсутствует в ozonapi-async.
        """
        body = {
            "page": 1,
            "limit": limit,
            "filter": {
                "date": {
                    "from": date_from_iso[:10],
                    "to": date_to_iso[:10],
                }
            },
        }
        data = await self.post("/v1/review/list", body)
        res = data.get("result") or data
        arr = (
            res.get("reviews")
            or res.get("feedbacks")
            or res.get("items")
            or []
        )
        return arr if isinstance(arr, list) else []


_client: OzonClient | None = None


def get_client() -> OzonClient:
    """Ленивая инициализация клиента с учётом .env."""
    global _client
    if _client is None:
        client_id, api_key = _env_credentials()
        _client = OzonClient(client_id=client_id, api_key=api_key)
    return _client

