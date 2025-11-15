import os
import datetime as dt
from typing import Any, Dict

import httpx

OZON_BASE_URL = "https://api-seller.ozon.ru"

MSK_SHIFT_HOURS = 3
ONE_DAY = dt.timedelta(days=1)


def get_ozon_credentials() -> tuple[str, str]:
    client_id = os.getenv("OZON_CLIENT_ID")
    api_key = os.getenv("OZON_API_KEY")

    if not client_id or not api_key:
        raise RuntimeError(
            "Не заданы переменные окружения OZON_CLIENT_ID / OZON_API_KEY"
        )

    return client_id.strip(), api_key.strip()


def build_ozon_headers() -> Dict[str, str]:
    client_id, api_key = get_ozon_credentials()
    return {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _to_iso_no_ms(d: dt.datetime) -> str:
    """ISO без миллисекунд, с суффиксом Z."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    else:
        d = d.astimezone(dt.timezone.utc)
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def msk_day_range(date_utc: dt.datetime | None = None) -> Dict[str, Any]:
    """
    Диапазон текущего дня по МСК, но в UTC.
    Возвращает:
      - since / to — строки ISO (для Ozon)
      - from_dt / to_dt — datetime UTC
      - pretty — строка "дд.мм.гггг 00:00 — 23:59 (МСК)"
    """
    if date_utc is None:
        date_utc = dt.datetime.now(dt.timezone.utc)
    else:
        if date_utc.tzinfo is None:
            date_utc = date_utc.replace(tzinfo=dt.timezone.utc)
        else:
            date_utc = date_utc.astimezone(dt.timezone.utc)

    # Текущий день по UTC
    y, m, d = date_utc.year, date_utc.month, date_utc.day
    midnight_utc = dt.datetime(y, m, d, 0, 0, 0, tzinfo=dt.timezone.utc)

    # 00:00 МСК = 21:00 предыдущего дня по UTC
    start_utc = midnight_utc - dt.timedelta(hours=MSK_SHIFT_HOURS)
    end_utc = start_utc + ONE_DAY - dt.timedelta(microseconds=1)

    # Для красивой подписи берём локальную дату по МСК
    msk_start = start_utc + dt.timedelta(hours=MSK_SHIFT_HOURS)
    dd = f"{msk_start.day:02d}.{msk_start.month:02d}.{msk_start.year}"
    pretty = f"{dd} 00:00 — {dd} 23:59 (МСК)"

    return {
        "since": _to_iso_no_ms(start_utc),
        "to": _to_iso_no_ms(end_utc),
        "from_dt": start_utc,
        "to_dt": end_utc,
        "pretty": pretty,
    }


async def ozon_post(path: str, payload: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    """
    Универсальный POST в Ozon API.
    path — например: "/v3/finance/transaction/totals".
    """
    url = OZON_BASE_URL + path
    headers = build_ozon_headers()

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    if resp.status_code >= 400:
        raise RuntimeError(f"Ozon {path} -> HTTP {resp.status_code}: {data}")

    if isinstance(data, dict):
        return data
    return {"result": data}
