import os
import json
import datetime as dt
from typing import Any, Dict

import httpx


OZON_API_URL = "https://api-seller.ozon.ru"


def _get_ozon_headers() -> Dict[str, str]:
    client_id = os.getenv("OZON_CLIENT_ID")
    api_key = os.getenv("OZON_API_KEY")

    if not client_id or not api_key:
        raise RuntimeError(
            "Не заданы переменные окружения OZON_CLIENT_ID / OZON_API_KEY"
        )

    return {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }


def _today_msk_range_utc() -> tuple[str, str]:
    """
    Возвращает (from_iso, to_iso) для сегодняшних суток по МСК, но в UTC ISO 8601.
    """
    msk_tz = dt.timezone(dt.timedelta(hours=3))
    now_msk = dt.datetime.now(msk_tz)

    start_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    end_msk = start_msk + dt.timedelta(days=1)

    start_utc = start_msk.astimezone(dt.timezone.utc)
    end_utc = end_msk.astimezone(dt.timezone.utc)

    def iso_z(d: dt.datetime) -> str:
        return d.replace(tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")

    return iso_z(start_utc), iso_z(end_utc)


async def fetch_finance_today_raw() -> Dict[str, Any]:
    """
    Сырой запрос в Ozon /v3/finance/transaction/totals за сегодня (по МСК).
    Возвращает dict (json).
    """
    headers = _get_ozon_headers()
    date_from, date_to = _today_msk_range_utc()

    payload = {
        "filter": {
            "date": {
                "from": date_from,
                "to": date_to,
            },
            "transaction_type": ["all"],
            "posting_type": ["all"],
        }
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{OZON_API_URL}/v3/finance/transaction/totals",
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Ozon /v3/finance/transaction/totals -> HTTP {resp.status_code}: "
            f"{resp.text[:400]}"
        )

    return resp.json()


async def get_finance_today_text() -> str:
    """
    Готовый текст для отправки в Telegram (HTML, <pre>…</pre>).
    Пока без хитрой агрегации: аккуратно выводим result как JSON.
    """
    try:
        data = await fetch_finance_today_raw()
    except Exception as e:
        return (
            "⚠️ Не удалось получить финансы за сегодня.\n"
            f"{e}"
        )

    result = data.get("result")
    if not result:
        return "⚠️ Ozon вернул пустой результат по финансам за сегодня."

    pretty = json.dumps(result, ensure_ascii=False, indent=2)

    # Ограничиваем длину, чтобы влезло в лимит Telegram (4096 символов)
    if len(pretty) > 3500:
        pretty = pretty[:3500] + "\n… (обрезано)"

    return f"<pre>{pretty}</pre>"
