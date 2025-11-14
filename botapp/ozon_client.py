import json
import os
from typing import Any, Dict, List, Tuple

import httpx
import datetime as dt

# Сдвиг Москвы относительно UTC
MSK_SHIFT_H = 3
OZON_API_URL = "https://api-seller.ozon.ru"

OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID")
OZON_API_KEY = os.getenv("OZON_API_KEY")

if not OZON_CLIENT_ID or not OZON_API_KEY:
    print("⚠️ OZON_CLIENT_ID или OZON_API_KEY не заданы. Ozon-запросы работать не будут.")


def _to_iso_no_ms(d: dt.datetime) -> str:
    """ISO без миллисекунд, с Z на конце."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    s = d.astimezone(dt.timezone.utc).isoformat()
    # 2025-11-15T00:00:00+00:00 -> 2025-11-15T00:00:00Z
    return s.replace("+00:00", "Z").split(".")[0] + "Z"


def today_range_utc() -> Tuple[str, str]:
    """
    Диапазон «сегодня по МСК» в UTC ISO.

    from_utc — это 00:00:00 сегодняшнего дня по МСК, переведённое в UTC.
    to_utc   — текущий момент в UTC.
    """
    now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    now_msk = now_utc + dt.timedelta(hours=MSK_SHIFT_H)
    start_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_msk - dt.timedelta(hours=MSK_SHIFT_H)

    return _to_iso_no_ms(start_utc), _to_iso_no_ms(now_utc)


async def ozon_call(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Универсальный вызов Ozon API (POST).
    Бросает RuntimeError, если:
      - нет ключей
      - ответ не JSON
      - HTTP-код 4xx/5xx
    """
    if not OZON_CLIENT_ID or not OZON_API_KEY:
        raise RuntimeError("OZON_CLIENT_ID/OZON_API_KEY не заданы.")

    headers = {
        "Client-Id": OZON_CLIENT_ID,
        "Api-Key": OZON_API_KEY,
        "Content-Type": "application/json",
    }

    url = OZON_API_URL + path
    async with httpx.AsyncClient(timeout=40) as client:
        resp = await client.post(url, headers=headers, json=payload)

    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise RuntimeError(f"Ozon {path}: не JSON, статус {resp.status_code}")

    if resp.status_code >= 400:
        raise RuntimeError(f"Ozon {path}: HTTP {resp.status_code}: {data}")

    return data


def _fmt_rub(amount: float) -> str:
    return f"{amount:,.0f} ₽".replace(",", " ")


# ---------------- Финансы за сегодня ---------------- #


async def build_fin_today_message() -> str:
    """
    Строит текстовую сводку по финансам за сегодня.
    Использует /v3/finance/transaction/totals.
    """
    date_from, date_to = today_range_utc()

    payload = {
        "filter": {
            "date": {
                "from": date_from,
                "to": date_to,
            }
        }
    }

    data = await ozon_call("/v3/finance/transaction/totals", payload)
    res = data.get("result") or {}

    def grab(path: List[str], default: float = 0.0) -> float:
        cur: Any = res
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        try:
            return float(cur)
        except Exception:
            return default

    # Попытка вытащить знакомые поля
    revenue = grab(["accruals_for_sale", "sale", "total"])
    commission = grab(["accruals_for_sale", "sale_commission", "total"])
    logistics = grab(["accruals_for_sale", "delivery", "total"])
    ads = grab(["accruals_for_services", "advertising", "total"])

    profit_before_cogs = revenue - commission - logistics - ads

    lines = [
        "📊 Финансы за сегодня",
        "",
        f"Выручка: {_fmt_rub(revenue)}",
        f"Комиссии: {_fmt_rub(commission)}",
        f"Логистика: {_fmt_rub(logistics)}",
        f"Реклама: {_fmt_rub(ads)}",
        "",
        f"Прибыль до себестоимости: {_fmt_rub(profit_before_cogs)}",
    ]

    # Если всё по нулям — покажем сырой JSON для дебага
    if revenue == commission == logistics == ads == 0:
        lines.append("")
        lines.append("⚠️ Ozon вернул неожиданные данные, сырой ответ:")
        lines.append(json.dumps(res, ensure_ascii=False, indent=2))

    return "\n".join(lines)


# ---------------- Заказы за сегодня ---------------- #


async def build_orders_today_message() -> str:
    """
    Строит текстовую сводку по заказам за сегодня.
    Пытается получить:
      - FBO (через /v2/posting/fbo/list)
      - FBS (через /v3/posting/fbs/list)
    Ошибки по каждому направлению не роняют бот, а попадают вниз сообщения.
    """
    date_from, date_to = today_range_utc()

    payload = {
        "dir": "ASC",
        "filter": {
            "since": date_from,
            "to": date_to,
            "status": "all",
        },
        "limit": 1000,
        "offset": 0,
        "with": {
            "analytics_data": True,
            "financial_data": True,
        },
    }

    total_orders = 0
    revenue = 0.0
    by_status: Dict[str, int] = {}
    errors: List[str] = []

    # --- FBO ---
    try:
        data_fbo = await ozon_call("/v2/posting/fbo/list", payload)
        result_fbo = data_fbo.get("result") or {}
        postings_fbo = result_fbo.get("postings") or []

        for p in postings_fbo:
            total_orders += 1
            st = p.get("status") or "unknown"
            by_status[st] = by_status.get(st, 0) + 1

            fin = p.get("financial_data") or {}
            products = fin.get("products") or []
            for prod in products:
                price = prod.get("price") or 0
                try:
                    revenue += float(price)
                except Exception:
                    pass
    except Exception as e:
        errors.append(f"FBO: {e!s}")

    # --- FBS ---
    try:
        data_fbs = await ozon_call("/v3/posting/fbs/list", payload)
        result_fbs = data_fbs.get("result") or {}
        postings_fbs = result_fbs.get("postings") or []

        for p in postings_fbs:
            total_orders += 1
            st = p.get("status") or "unknown"
            by_status[st] = by_status.get(st, 0) + 1

            fin = p.get("financial_data") or {}
            products = fin.get("products") or []
            for prod in products:
                price = prod.get("price") or 0
                try:
                    revenue += float(price)
                except Exception:
                    pass
    except Exception as e:
        errors.append(f"FBS: {e!s}")

    # Если вообще ничего не достали – пробрасываем вверх
    if total_orders == 0 and errors:
        raise RuntimeError("; ".join(errors))

    lines = [
        "📦 Заказы за сегодня",
        "",
        f"Всего заказов: {total_orders}",
        f"Оборот по товарам: {_fmt_rub(revenue)}",
    ]

    if by_status:
        lines.append("")
        lines.append("По статусам:")
        for st, cnt in sorted(by_status.items(), key=lambda x: x[0]):
            lines.append(f"• {st}: {cnt}")

    if errors:
        lines.append("")
        lines.append("⚠️ Часть данных недоступна:")
        for err in errors:
            lines.append(f"– {err}")

    return "\n".join(lines)


# ---------------- Информация об аккаунте продавца ---------------- #


async def build_seller_info_message() -> str:
    """
    Простая информация об аккаунте продавца.
    Использует /v1/seller/info (если структура другая – покажем сырой JSON).
    """
    data = await ozon_call("/v1/seller/info", {})
    res = data.get("result") or data

    name = res.get("name") or "—"
    legal_address = res.get("legal_address") or res.get("juridical_address") or "—"
    rating = res.get("customer_rating") or res.get("rating") or "—"

    lines = [
        "🧾 Аккаунт Ozon",
        "",
        f"Название: {name}",
        f"Юр. адрес: {legal_address}",
        f"Рейтинг: {rating}",
    ]

    # Если ключей нет — выводим сырой JSON
    if name == "—" and legal_address == "—" and rating == "—":
        lines.append("")
        lines.append("Сырой ответ Ozon:")
        lines.append(json.dumps(res, ensure_ascii=False, indent=2))

    return "\n".join(lines)
