"""API shim exposing Ozon client helpers from the legacy location."""

from __future__ import annotations

import logging
from typing import Any

from botapp import ozon_client as _ozon_client
from botapp.config import load_ozon_config
from botapp.ozon_client import *  # noqa: F401,F403

logger = logging.getLogger(__name__)

_product_name_cache = _ozon_client._product_name_cache
_original_close_clients = _ozon_client.close_clients
_write_client: OzonClient | None = None


def _env_read_credentials() -> tuple[str, str]:
    """Read Ozon read credentials with support for seller aliases."""

    cfg = load_ozon_config()
    client_id = cfg.client_id.strip()
    api_key = cfg.api_key.strip()
    if not client_id or not api_key:
        raise RuntimeError(
            "Не заданы креденшалы Ozon "
            "(OZON_CLIENT_ID/OZON_API_KEY или OZON_SELLER_CLIENT_ID/OZON_SELLER_API_KEY)"
        )
    return client_id, api_key


def has_write_credentials() -> bool:
    """Check whether we have write credentials for Ozon mutations."""

    cfg = load_ozon_config()
    return bool(cfg.write_client_id.strip() and cfg.write_api_key.strip())


def get_write_client() -> OzonClient | None:
    """Return a dedicated write client when write creds differ from read creds."""

    global _write_client

    if not has_write_credentials():
        return None

    cfg = load_ozon_config()
    read_client_id, read_api_key = _env_read_credentials()
    write_client_id = cfg.write_client_id.strip()
    write_api_key = cfg.write_api_key.strip()

    if write_client_id == read_client_id and write_api_key == read_api_key:
        return _ozon_client.get_client()

    if (
        _write_client is None
        or _write_client.client_id != write_client_id
        or _write_client.api_key != write_api_key
    ):
        _write_client = OzonClient(client_id=write_client_id, api_key=write_api_key)

    return _write_client


def _extract_ozon_error_details(data: Any) -> str:
    if isinstance(data, dict):
        candidates = [
            data.get("message"),
            data.get("error"),
            data.get("error_message"),
            data.get("description"),
            data.get("detail"),
        ]
        message = next((str(v).strip() for v in candidates if str(v or "").strip()), "")
        code = str(data.get("code") or data.get("error_code") or "").strip()
        if code and message:
            return f"{code}: {message}"
        if message:
            return message
        if code:
            return code
        keys = ",".join(sorted(str(k) for k in data.keys()))
        return f"payload_keys={keys or '-'}"
    if data is None:
        return "empty payload"
    return str(data).strip() or "unknown error"


async def send_question_answer(question_id: str, text: str, *, sku: int | None = None) -> bool:
    client = get_write_client()
    if client is None:
        raise OzonAPIError("Нет прав на отправку ответов в Ozon")

    question_id_clean = str(question_id or "").strip()
    if not question_id_clean:
        raise OzonAPIError("Не найден question_id для отправки ответа")

    text_clean = (text or "").strip()
    if len(text_clean) < 2:
        raise OzonAPIError("Ответ пустой или слишком короткий, сначала отредактируйте текст")

    sku_clean = _ozon_client._clean_sku(sku)
    if sku_clean is None:
        logger.warning(
            "question_answer_create blocked qid=%s invalid_sku=%r",
            question_id_clean,
            sku,
        )
        return False

    endpoint = "/v1/question/answer/create"
    body = {"question_id": question_id_clean, "text": text_clean, "sku": sku_clean}
    logger.info(
        "question_answer_create request endpoint=%s qid=%s sku=%s text_len=%s",
        endpoint,
        question_id_clean,
        sku_clean,
        len(text_clean),
    )
    status_code, data = await client._post_with_status(endpoint, body)
    if status_code >= 400:
        details = _extract_ozon_error_details(data)
        logger.warning(
            "question_answer_create rejected endpoint=%s status=%s qid=%s sku=%s details=%s",
            endpoint,
            status_code,
            question_id_clean,
            sku_clean,
            details,
        )
        raise OzonAPIError(f"Ошибка Ozon API: HTTP {status_code} {details}")
    if isinstance(data, dict) and data.get("result") is False:
        details = _extract_ozon_error_details(data)
        logger.warning(
            "question_answer_create rejected endpoint=%s status=%s qid=%s sku=%s details=%s",
            endpoint,
            status_code,
            question_id_clean,
            sku_clean,
            details,
        )
        raise OzonAPIError(f"Ozon отклонил отправку ответа: {details}")
    if data is None:
        raise OzonAPIError(
            "Ошибка Ozon API: пустой ответ при отправке ответа на вопрос"
        )
    logger.info(
        "question_answer_create ok endpoint=%s qid=%s sku=%s",
        endpoint,
        question_id_clean,
        sku_clean,
    )
    return True


async def close_clients() -> None:
    global _write_client

    if _write_client is not None:
        try:
            await _write_client.aclose()
        except Exception:  # pragma: no cover - best effort
            logger.warning("Failed to close Ozon write client HTTP session", exc_info=True)
        _write_client = None

    await _original_close_clients()


_ozon_client._env_read_credentials = _env_read_credentials
_ozon_client.has_write_credentials = has_write_credentials
_ozon_client.get_write_client = get_write_client
_ozon_client.send_question_answer = send_question_answer
_ozon_client.close_clients = close_clients

__all__ = [name for name in globals().keys() if not name.startswith("__")]
