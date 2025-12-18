# botapp/config.py
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(*names: str, default: str = "") -> str:
    """
    Берём первое непустое значение из списка переменных окружения.
    """
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return (default or "").strip()


@dataclass(frozen=True, slots=True)
class OzonConfig:
    base_url: str
    client_id: str
    api_key: str
    write_client_id: str
    write_api_key: str
    timeout_s: float


def load_ozon_config() -> OzonConfig:
    base_url = _env("OZON_API_BASE_URL", default="https://api-seller.ozon.ru").rstrip("/")

    # Совместимость: старые имена переменных (OZON_SELLER_*) + новые (OZON_*)
    client_id = _env("OZON_CLIENT_ID", "OZON_SELLER_CLIENT_ID")
    api_key = _env("OZON_API_KEY", "OZON_SELLER_API_KEY")

    write_client_id = _env(
        "OZON_WRITE_CLIENT_ID",
        "OZON_SELLER_WRITE_CLIENT_ID",
        default=client_id,
    )
    write_api_key = _env(
        "OZON_WRITE_API_KEY",
        "OZON_SELLER_WRITE_API_KEY",
        default=api_key,
    )

    try:
        timeout_s = float(_env("OZON_HTTP_TIMEOUT_S", default="35"))
    except Exception:
        timeout_s = 35.0

    return OzonConfig(
        base_url=base_url,
        client_id=client_id,
        api_key=api_key,
        write_client_id=write_client_id,
        write_api_key=write_api_key,
        timeout_s=timeout_s,
    )
