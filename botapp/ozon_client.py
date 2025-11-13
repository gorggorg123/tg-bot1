import os
import requests
from datetime import datetime, timedelta, timezone

OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID", "").strip()
OZON_API_KEY = os.getenv("OZON_API_KEY", "").strip()

API_URL = "https://api-seller.ozon.ru"

MSK_SHIFT_H = 3
ONE_DAY = timedelta(days=1)


def _check_env():
    if not OZON_CLIENT_ID or not OZON_API_KEY:
        raise RuntimeError("Не заданы OZON_CLIENT_ID / OZON_API_KEY в переменных окружения")


def ozon_post(path: str, body: dict) -> dict:
    """
    Базовый POST к Ozon Seller API.
    Похоже на ozonPost из JS-версии: ошибки превращаем в понятные исключения.
    """
    _check_env()

    url = API_URL + path
    headers = {
        "Client-Id": OZON_CLIENT_ID,
        "Api-Key": OZON_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    resp = requests.post(url, json=body or {}, headers=headers, timeout=15)
    text = resp.text

    if not resp.ok:
        raise RuntimeError(f"Ozon {path} -> HTTP {resp.status_code}: {text}")

    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"Ozon {path} -> не удалось разобрать JSON: {text[:200]}")

    return data


def msk_today_range_iso():
    """
    Возвращает диапазон дат для 'сегодня' в МСК:
    {
        "from": "2025-11-13T00:00:00Z",
        "to":   "2025-11-13T23:59:59Z",
        "pretty": "13.11.2025 00:00 — 13.11.2025 23:59 (МСК)"
    }
    """
    now_utc = datetime.now(timezone.utc)

    # День в МСК
    msk_now = now_utc + timedelta(hours=MSK_SHIFT_H)
    msk_start = msk_now.replace(hour=0, minute=0, second=0, microsecond=0)
    msk_end = msk_start + ONE_DAY - timedelta(milliseconds=1)

    # Переводим границы в UTC
    utc_start = msk_start - timedelta(hours=MSK_SHIFT_H)
    utc_end = msk_end - timedelta(hours=MSK_SHIFT_H)

    def to_iso_no_ms(dt: datetime) -> str:
        return dt.isoformat(timespec="seconds").replace("+00:00", "Z")

    pretty = f"{msk_start:%d.%m.%Y} 00:00 — {msk_start:%d.%m.%Y} 23:59 (МСК)"

    return {
        "from": to_iso_no_ms(utc_start),
        "to": to_iso_no_ms(utc_end),
        "pretty": pretty,
    }


def parse_num(x) -> float:
    """
    Аккуратный парсер чисел (аналог sNum из JS).
    """
    if x is None:
        return 0.0
    try:
        return float(str(x).replace(" ", "").replace(",", "."))
    except Exception:
        return 0.0


def rub0(x: float) -> str:
    """
    Форматирование рублей без копеек: 10 000 ₽
    """
    try:
        n = int(round(float(x)))
    except Exception:
        n = 0
    s = f"{n:,}".replace(",", " ")
    return f"{s} ₽"

