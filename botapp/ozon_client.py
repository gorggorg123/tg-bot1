# botapp/ozon_client.py
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

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
            return await self._post("/v3/chat/send/message", payload)
        except OzonAPIError as exc:
            if exc.status_code in (404, 400):
                return await self._post("/v1/chat/send/message", payload)
            raise


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
        raise OzonAPIError("Нет write-доступа для отправки ответов (OZON_WRITE_CLIENT_ID/OZON_WRITE_API_KEY).")
    await client.question_answer_create(question_id, text, sku=sku)


__all__ = [
    "OzonAPIError",
    "OzonClient",
    "get_client",
    "get_write_client",
    "has_write_credentials",
    "close_clients",
    "ChatListItem",
    "ChatListResponse",
    "ChatHistoryResponse",
    "Question",
    "QuestionAnswer",
    "chat_list",
    "chat_history",
    "chat_send_message",
    "get_questions_list",
    "get_question_answers",
    "send_question_answer",
]
