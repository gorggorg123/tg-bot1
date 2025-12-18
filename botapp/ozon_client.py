# botapp/ozon_client.py
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv

# Pydantic v2 is the primary target, but provide a minimal fallback for v1 to
# avoid NameError on platforms that ship only pydantic.v1 by default.
try:  # pragma: no cover - import-time guard
    from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
except Exception:  # pragma: no cover - compatibility with pydantic.v1
    from pydantic.v1 import BaseModel, Field, ValidationError, validator

    class ConfigDict(dict):
        """Lightweight shim so model_config assignments do not crash under v1."""

    def field_validator(*fields, **kwargs):
        def wrapper(func):
            return validator(*fields, **kwargs)(func)

        return wrapper

from botapp.config import load_ozon_config

logger = logging.getLogger(__name__)


class OzonAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(slots=True)
class ChatListItem:
    chat_id: str
    title: str | None = None
    unread_count: int = 0
    last_message_id: int = 0


@dataclass(slots=True)
class ChatListResponse:
    chats: list[ChatListItem]
    total: int | None = None


@dataclass(slots=True)
class ChatHistoryResponse:
    messages: list[dict]
    last_message_id: int | None = None


@dataclass(slots=True)
class Question:
    id: str
    created_at: str | None = None
    updated_at: str | None = None
    sku: str | None = None
    product_name: str | None = None
    question_text: str | None = None

    has_answer: bool = False
    answer_id: str | None = None
    answer_text: str | None = None

    raw: dict | None = None


@dataclass(slots=True)
class QuestionAnswer:
    id: str
    question_id: str
    text: str | None = None
    created_at: str | None = None
    raw: dict | None = None


class OzonClient:
    def __init__(self, *, client_id: str, api_key: str, base_url: str, timeout_s: float = 35.0) -> None:
        self.client_id = (client_id or "").strip()
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").strip().rstrip("/")
        self.timeout_s = float(timeout_s)

        if not self.client_id or not self.api_key:
            raise OzonAPIError("Не заданы OZON_CLIENT_ID / OZON_API_KEY")

        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_s),
            headers={
                "Client-Id": self.client_id,
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        try:
            await self._http.aclose()
        except Exception:
            return

    async def _post(self, path: str, payload: dict) -> dict:
        path = "/" + path.lstrip("/")

        retries = 2
        last_exc: Exception | None = None

        for attempt in range(retries + 1):
            try:
                r = await self._http.post(path, json=payload)
                if r.status_code >= 400:
                    try:
                        body = r.json()
                    except Exception:
                        body = r.text
                    raise OzonAPIError(
                        f"Ozon API error {r.status_code} on {path}",
                        status_code=r.status_code,
                        payload=body,
                    )

                try:
                    data = r.json()
                except Exception as exc:
                    raise OzonAPIError(f"Не удалось распарсить JSON от Ozon ({path})", payload=r.text) from exc

                if not isinstance(data, dict):
                    return {"result": data}
                return data

            except OzonAPIError as exc:
                last_exc = exc
                if exc.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(0.6 + attempt * 0.9)
                    continue
                raise

            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(0.6 + attempt * 0.9)
                    continue
                raise OzonAPIError(f"Сетевой сбой при запросе Ozon: {exc}") from exc

        raise OzonAPIError(f"Запрос Ozon не удался: {last_exc}")

    # Reviews

    async def review_list(self, *, date_start: str, date_end: str, limit: int = 100, offset: int = 0) -> dict:
        return await self._post(
            "/v1/review/list",
            {"date_from": date_start, "date_to": date_end, "limit": int(limit), "offset": int(offset)},
        )

    async def review_info(self, review_id: str) -> dict:
        return await self._post("/v1/review/info", {"review_id": str(review_id)})

    async def review_comment_list(self, review_id: str, *, limit: int = 50, offset: int = 0) -> dict:
        return await self._post("/v1/review/comment/list", {"review_id": str(review_id), "limit": int(limit), "offset": int(offset)})

    async def review_comment_create(self, review_id: str, text: str) -> dict:
        return await self._post("/v1/review/comment/create", {"review_id": str(review_id), "text": (text or "").strip()})

    # Questions

    async def question_list(self, *, status: str = "all", limit: int = 200, offset: int = 0) -> dict:
        return await self._post("/v1/question/list", {"status": status, "limit": int(limit), "offset": int(offset)})

    async def question_answer_list(self, question_id: str, *, sku: int | None = None, limit: int = 1, offset: int = 0) -> dict:
        payload: dict[str, Any] = {"question_id": str(question_id), "limit": int(limit), "offset": int(offset)}
        if sku is not None:
            payload["sku"] = int(sku)
        return await self._post("/v1/question/answer/list", payload)

    async def question_answer_create(self, question_id: str, text: str, *, sku: int | None = None) -> dict:
        payload: dict[str, Any] = {"question_id": str(question_id), "text": (text or "").strip()}
        if sku is not None:
            payload["sku"] = int(sku)
        return await self._post("/v1/question/answer/create", payload)

    # Chats

    async def chat_list(self, *, limit: int = 200, offset: int = 0) -> dict:
        return await self._post("/v3/chat/list", {"limit": int(limit), "offset": int(offset)})

    async def chat_history(self, chat_id: str, *, limit: int = 30) -> dict:
        return await self._post("/v3/chat/history", {"chat_id": str(chat_id), "limit": int(limit)})

    async def chat_send_message(self, chat_id: str, text: str) -> dict:
        payload = {"chat_id": str(chat_id), "text": (text or "").strip()}
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


class ChatListResponse(BaseModel):
    chats: list[dict] = Field(default_factory=list)
    items: list[dict] | None = None
    result: list[dict] | dict | None = None

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    def iter_items(self):
        candidates: list[list[dict]] = []
        if self.chats:
            candidates.append(self.chats)
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
                yield item


class ChatMessage(BaseModel):
    message_id: str | int | None = None
    id: str | int | None = None
    text: str | None = None
    message: str | None = None
    content: str | None = None
    author: dict | str | None = None
    sender: str | None = Field(default=None, alias="from")
    created_at: str | None = None
    send_time: str | None = None

    model_config = ConfigDict(
        extra="allow",
        protected_namespaces=(),
        populate_by_name=True,
    )

    def to_dict(self) -> dict:
        data = self.model_dump(exclude_none=True, by_alias=False)
        mid = data.get("message_id")
        if mid is not None:
            data["message_id"] = str(mid)
        elif self.id is not None:
            data["message_id"] = str(self.id)
        if not data.get("text"):
            for key in ("message", "content"):
                value = getattr(self, key, None)
                if isinstance(value, str) and value:
                    data["text"] = value
                    break
        if self.author is not None:
            data["author"] = self.author
        if self.sender and "from" not in data:
            data["from"] = self.sender
        return data


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


async def chat_list(
    *, limit: int = 100, offset: int = 0, include_service: bool = False
) -> list[dict]:
    """Получить список чатов продавца.

    Увеличиваем лимит до 100 и при необходимости подгружаем несколько страниц,
    чтобы показать максимум живых диалогов за 1–2 запроса к API.
    Служебные чаты (support/system/crm/notification) отфильтровываются мягко:
    если тип пустой или неизвестен, чат всё равно остаётся в выдаче.
    """

    client = get_client()
    max_limit = max(1, min(limit or 100, 100))
    offset_cur = max(0, offset)
    remaining = max_limit
    items_raw: list[dict] = []

    async def _fetch_page(page_limit: int, offset_value: int) -> tuple[int, dict | None]:
        filter_base: dict[str, object] = {
            "chat_status": "all",
            "chat_type": "all" if include_service else "buyer",
        }
        payload_variants = [
            {"limit": page_limit, "offset": offset_value, "filter": filter_base},
            {"limit": page_limit, "offset": offset_value, "filter": {"chat_status": "all"}},
            {"limit": page_limit, "offset": offset_value, "filter": {}},
        ]
        last_status: int | None = None
        last_data: dict | None = None

        for body in payload_variants:
            status_code, data = await client._post_with_status("/v3/chat/list", body)
            last_status = status_code
            last_data = data if isinstance(data, dict) else None
            if status_code < 400 and isinstance(data, dict):
                return status_code, data

        return last_status or 500, last_data

    while remaining > 0:
        page_limit = min(50, remaining)
        status_code, data = await _fetch_page(page_limit, offset_cur)
        if status_code >= 400 or not isinstance(data, dict):
            message = None
            if isinstance(data, dict):
                message = data.get("message") or data.get("error")
                if data.get("code") == 9:
                    logger.warning("Ozon chat list method is obsolete; check API version update")
            raise OzonAPIError(
                f"Ошибка Ozon API при получении списка чатов: HTTP {status_code} {message or data}"
            )

        payload = data.get("result") if isinstance(data.get("result"), dict) else data
        data_keys = list(data.keys()) if isinstance(data, dict) else []
        payload_keys = list(payload.keys()) if isinstance(payload, dict) else []
        logger.debug(
            "chat_list payload received: status=%s data_keys=%s payload_type=%s payload_keys=%s",
            status_code,
            data_keys,
            type(payload).__name__,
            payload_keys,
        )

        def _log_validation_error(exc: ValidationError) -> None:
            logger.warning("Failed to parse chat list payload: %s", exc)
            try:
                preview = str(payload)
                if len(preview) > 500:
                    preview = preview[:500] + "..."
                logger.warning("Chat list payload preview: %s", preview)
            except Exception:
                logger.warning("Unable to render chat list payload preview")

        page_raw: list[dict] = []
        try:
            parsed = ChatListResponse.model_validate(payload)
            page_raw = list(parsed.iter_items())
        except ValidationError as exc:
            _log_validation_error(exc)

        if not page_raw:
            if isinstance(payload, dict):
                for key in ("chats", "chat_list", "items"):
                    maybe = payload.get(key)
                    if isinstance(maybe, list):
                        page_raw = maybe
                        break
                if not page_raw and isinstance(payload.get("result"), list):
                    page_raw = payload.get("result")
            elif isinstance(payload, list):
                page_raw = payload

        if not isinstance(page_raw, list):
            logger.warning("Unexpected chat list payload structure: %s", type(page_raw).__name__)
            break

        items_raw.extend(page_raw)

        has_next = False
        if isinstance(payload, dict):
            has_next = bool(
                payload.get("has_next")
                or payload.get("hasNext")
                or payload.get("has_more")
                or payload.get("hasMore")
            )
            total = payload.get("total") or payload.get("total_count")
            if isinstance(total, int) and total > 0:
                has_next = has_next or offset_cur + len(page_raw) < total

        remaining -= len(page_raw)
        offset_cur += len(page_raw) or page_limit
        if not has_next or len(page_raw) < page_limit or remaining <= 0:
            break

    items: list[dict] = []
    for raw in items_raw:
        if not isinstance(raw, dict):
            continue
        merged = _merge_nested_block(raw, "chat")
        chat_type = (
            merged.get("chat_type")
            or merged.get("chatType")
            or merged.get("type")
        )
        chat_type_str = str(chat_type or "").lower()
        if not include_service:
            if any(bad in chat_type_str for bad in ("support", "system", "notification", "crm")):
                logger.debug("Skip service chat (%s): %s", chat_type_str or "empty", merged)
                continue
        try:
            merged["_raw"] = merged.copy()
            items.append(ChatSummary.model_validate(merged).to_dict())
        except ValidationError as exc:
            logger.warning("Failed to normalize chat summary: %s", exc)
            items.append(merged)
    return items


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

    payload = data.get("result") if isinstance(data.get("result"), dict) else data
    raw_items: list[dict] = []
    try:
        parsed = ChatHistoryResponse.model_validate(payload)
        raw_items = list(parsed.iter_items())
    except ValidationError as exc:
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


_cfg = load_ozon_config()
_READ_CLIENT: OzonClient | None = None
_WRITE_CLIENT: OzonClient | None = None


def get_client() -> OzonClient:
    global _READ_CLIENT
    if _READ_CLIENT is None:
        _READ_CLIENT = OzonClient(
            client_id=_cfg.client_id,
            api_key=_cfg.api_key,
            base_url=_cfg.base_url,
            timeout_s=_cfg.timeout_s,
        )
    return _READ_CLIENT


def has_write_credentials() -> bool:
    return bool((_cfg.write_client_id or "").strip() and (_cfg.write_api_key or "").strip())


def get_write_client() -> OzonClient | None:
    global _WRITE_CLIENT
    if not has_write_credentials():
        return None
    if _WRITE_CLIENT is None:
        _WRITE_CLIENT = OzonClient(
            client_id=_cfg.write_client_id,
            api_key=_cfg.write_api_key,
            base_url=_cfg.base_url,
            timeout_s=_cfg.timeout_s,
        )
    return _WRITE_CLIENT


async def close_clients() -> None:
    global _READ_CLIENT, _WRITE_CLIENT
    if _READ_CLIENT is not None:
        await _READ_CLIENT.aclose()
        _READ_CLIENT = None
    if _WRITE_CLIENT is not None:
        await _WRITE_CLIENT.aclose()
        _WRITE_CLIENT = None


def _get_result_block(data: dict) -> dict:
    r = data.get("result")
    return r if isinstance(r, dict) else data


async def chat_list(*, limit: int = 200, offset: int = 0, refresh: bool = False) -> ChatListResponse:
    data = await get_client().chat_list(limit=limit, offset=offset)
    res = _get_result_block(data)

    raw_list = res.get("chats") or res.get("items") or res.get("list") or []
    chats: list[ChatListItem] = []
    if isinstance(raw_list, list):
        for c in raw_list:
            if not isinstance(c, dict):
                continue
            cid = c.get("chat_id") or c.get("id")
            if cid in (None, ""):
                continue
            chats.append(
                ChatListItem(
                    chat_id=str(cid),
                    title=(c.get("title") or c.get("subject") or c.get("name")),
                    unread_count=int(c.get("unread_count") or c.get("unread") or 0),
                    last_message_id=int(c.get("last_message_id") or c.get("last_id") or 0),
                )
            )

    total = res.get("total") if isinstance(res.get("total"), int) else None
    return ChatListResponse(chats=chats, total=total)


async def chat_history(chat_id: str, *, limit: int = 30) -> ChatHistoryResponse:
    data = await get_client().chat_history(chat_id, limit=limit)
    res = _get_result_block(data)

    msgs = res.get("messages") or res.get("items") or res.get("list") or []
    if not isinstance(msgs, list):
        msgs = []

    last_mid = res.get("last_message_id") or res.get("last_id")
    try:
        last_mid_i = int(last_mid) if last_mid not in (None, "") else None
    except Exception:
        last_mid_i = None

    return ChatHistoryResponse(messages=[m for m in msgs if isinstance(m, dict)], last_message_id=last_mid_i)


async def chat_send_message(chat_id: str, text: str) -> None:
    client = get_write_client()
    if client is None:
        raise OzonAPIError("Нет write-доступа для отправки сообщений в чат (OZON_WRITE_CLIENT_ID/OZON_WRITE_API_KEY).")
    await client.chat_send_message(chat_id, text)


def _parse_question_item(item: dict) -> Question | None:
    qid = item.get("id") or item.get("question_id")
    if qid in (None, ""):
        return None

    qtext = item.get("text") or item.get("question") or item.get("question_text")
    created_at = item.get("created_at") or item.get("created") or item.get("date")
    updated_at = item.get("updated_at") or item.get("updated")

    prod = item.get("product_name") or item.get("item_name") or item.get("product_title")
    sku = item.get("sku") or item.get("offer_id") or item.get("product_id")

    has_answer = bool(item.get("has_answer")) or bool(item.get("is_answered"))
    answer_text = item.get("answer_text") or item.get("answer")
    answer_id = item.get("answer_id")

    if isinstance(answer_text, str) and answer_text.strip():
        has_answer = True

    return Question(
        id=str(qid),
        created_at=str(created_at).strip() if created_at not in (None, "") else None,
        updated_at=str(updated_at).strip() if updated_at not in (None, "") else None,
        sku=str(sku).strip() if sku not in (None, "") else None,
        product_name=str(prod).strip() if prod not in (None, "") else None,
        question_text=str(qtext).strip() if isinstance(qtext, str) else None,
        has_answer=bool(has_answer),
        answer_id=str(answer_id).strip() if answer_id not in (None, "") else None,
        answer_text=str(answer_text).strip() if isinstance(answer_text, str) and answer_text.strip() else None,
        raw=item,
    )


async def get_questions_list(*, status: str = "all", limit: int = 200, offset: int = 0) -> list[Question]:
    data = await get_client().question_list(status=status, limit=limit, offset=offset)
    res = _get_result_block(data)

    raw = res.get("items") or res.get("questions") or res.get("list") or []
    out: list[Question] = []
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict):
                q = _parse_question_item(it)
                if q:
                    out.append(q)
    return out


async def get_question_answers(question_id: str, *, sku: int | None = None, limit: int = 1) -> list[QuestionAnswer]:
    data = await get_client().question_answer_list(question_id, sku=sku, limit=limit, offset=0)
    res = _get_result_block(data)

    raw = res.get("items") or res.get("answers") or res.get("list") or []
    out: list[QuestionAnswer] = []
    if isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            aid = it.get("id") or it.get("answer_id")
            if aid in (None, ""):
                continue
            out.append(
                QuestionAnswer(
                    id=str(aid),
                    question_id=str(question_id),
                    text=str(it.get("text")).strip() if isinstance(it.get("text"), str) else None,
                    created_at=str(it.get("created_at")).strip() if it.get("created_at") not in (None, "") else None,
                    raw=it,
                )
            )
    return out


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
    question_id: str, *, limit: int = 20
) -> list[QuestionAnswer]:
    """Совместимый алиас для получения ответов на вопрос."""

    return await list_question_answers(question_id, limit=limit)


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
