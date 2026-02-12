# botapp/models/ozon.py
"""
Pydantic модели для Ozon API.
Вынесены из ozon_client.py для лучшей структурированности.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class QuestionItem(BaseModel):
    """Модель вопроса покупателя."""
    
    question_id: str | None = Field(default=None)
    product_id: str | None = None
    offer_id: str | None = None
    sku: str | None = None
    product_title: str | None = None
    product_name: str | None = None
    text: str | None = None
    question: str | None = None
    message: str | None = None
    status: str | None = None
    answer: str | None = None
    last_answer: str | None = None
    answer_id: str | None = None
    created_at: Any = None
    updated_at: Any = None

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    @property
    def id(self) -> str:
        return self.question_id or ""

    @property
    def question_text(self) -> str:
        return self.text or self.question or self.message or ""

    @property
    def has_answer(self) -> bool:
        return bool(self.answer or self.last_answer or self.answer_id)

    @property
    def answer_text(self) -> str | None:
        return self.answer or self.last_answer or None


class GetQuestionListResponse(BaseModel):
    """Ответ API на запрос списка вопросов."""
    
    questions: list[QuestionItem] = Field(default_factory=list)
    result: list[QuestionItem] | None = None
    last_id: str | None = None
    total: int | None = None

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    @property
    def items(self) -> list[QuestionItem]:
        if self.questions:
            return self.questions
        if isinstance(self.result, list):
            return self.result
        return []


class StockItem(BaseModel):
    """Остаток товара на складе."""
    
    type: str | None = None
    present: int = 0
    reserved: int = 0

    model_config = ConfigDict(extra="allow", protected_namespaces=())


class ProductStockInfo(BaseModel):
    """Информация о складских остатках товара."""
    
    product_id: int | None = None
    offer_id: str | None = None
    stocks: list[StockItem] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow", protected_namespaces=())


class ProductListItem(BaseModel):
    """Элемент списка товаров."""
    
    product_id: int | None = None
    offer_id: str | None = None
    is_fbo_visible: bool = True
    is_fbs_visible: bool = True
    archived: bool = False
    is_discounted: bool = False
    model_id: int | None = Field(default=None, alias="model_id")
    model_info: dict | None = Field(default=None, alias="model_info")

    model_config = ConfigDict(extra="allow", protected_namespaces=(), populate_by_name=True)


class ProductListPage(BaseModel):
    """Страница списка товаров."""
    
    result: dict | None = None
    items: list[ProductListItem] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow", protected_namespaces=())


class ProductInfoItem(BaseModel):
    """Детальная информация о товаре."""
    
    id: int | None = None
    product_id: int | None = None
    offer_id: str | None = None
    sku: int | None = None
    name: str | None = None
    barcode: str | None = None
    category_id: int | None = None
    model_id: int | None = Field(default=None, alias="model_id")
    model_info: dict | None = Field(default=None, alias="model_info")
    visible_in_services: dict | None = None
    images: list | None = None
    images360: list | None = None
    color_image: str | None = None
    primary_image: str | None = None
    status: dict | None = None
    old_price: str | None = None
    price: str | None = None
    marketing_price: str | None = None
    min_price: str | None = None
    sources: list | None = None
    stocks: dict | None = None
    commissions: list | None = None
    volume_weight: float | None = None
    is_prepayment: bool | None = None
    is_prepayment_allowed: bool | None = None
    description_category_id: int | None = None
    type_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    model_config = ConfigDict(extra="allow", protected_namespaces=(), populate_by_name=True)


class ChatSummary(BaseModel):
    """Краткая информация о чате."""
    
    id: str | None = None
    chat_id: str | None = None
    chat_type: str | None = None
    chat_status: str | None = None
    created_at: Any = None
    first_unread_message_id: str | None = None
    last_message_id: str | None = None
    unread_count: int = 0

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    @property
    def effective_id(self) -> str:
        return self.chat_id or self.id or ""


class ChatListItem(BaseModel):
    """Элемент списка чатов."""
    
    id: str | None = None
    chat_id: str | None = None
    chat_type: str | None = None
    chat_status: str | None = None
    created_at: Any = None
    first_unread_message_id: str | None = None
    last_message_id: str | None = None
    unread_count: int = 0
    last_message_text: str | None = None
    chat: ChatSummary | None = None

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    @property
    def effective_id(self) -> str:
        if self.chat_id:
            return self.chat_id
        if self.id:
            return self.id
        if self.chat and self.chat.effective_id:
            return self.chat.effective_id
        return ""


class ChatListResponse(BaseModel):
    """Ответ API на запрос списка чатов."""
    
    chats: list[ChatListItem] = Field(default_factory=list)
    has_next: bool = False
    total_chats_count: int | None = None
    total_unread_count: int | None = None
    next_page_token: str | None = None

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    @model_validator(mode="before")
    @classmethod
    def _flatten_result(cls, data):
        if isinstance(data, dict):
            if "result" in data and isinstance(data["result"], dict):
                for key in ("chats", "has_next", "total_chats_count", "total_unread_count"):
                    if key in data["result"] and key not in data:
                        data[key] = data["result"][key]
        return data


class ChatHistoryResponse(BaseModel):
    """Ответ API на запрос истории чата."""
    
    messages: list[dict] = Field(default_factory=list)
    has_next: bool = False

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    @model_validator(mode="before")
    @classmethod
    def _flatten_result(cls, data):
        if isinstance(data, dict):
            result = data.get("result")
            if isinstance(result, dict):
                for key in ("messages", "has_next"):
                    if key in result and key not in data:
                        data[key] = result[key]
        return data


class ChatMessage(BaseModel):
    """Сообщение в чате."""
    
    id: str | None = None
    message_id: str | None = None
    chat_id: str | None = None
    created_at: Any = None
    is_read: bool = False
    sender: str | None = None
    user: str | None = None
    text: str | None = None
    data: dict | None = None
    message_type: str | None = None

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    @property
    def effective_id(self) -> str:
        return self.message_id or self.id or ""

    @property
    def role(self) -> str:
        """Определяет роль отправителя: buyer, seller или system."""
        s = (self.sender or self.user or "").lower()
        if "customer" in s or "buyer" in s:
            return "buyer"
        if "seller" in s or "merchant" in s:
            return "seller"
        return "system"


class QuestionListFilter(BaseModel):
    """Фильтр для запроса списка вопросов."""
    
    status: str = "NEW"

    model_config = ConfigDict(extra="allow", protected_namespaces=())


class GetQuestionListRequest(BaseModel):
    """Запрос списка вопросов."""
    
    filter: QuestionListFilter = Field(default_factory=QuestionListFilter)
    last_id: str = ""
    limit: int = 100

    model_config = ConfigDict(extra="allow", protected_namespaces=())


class QuestionListItem(BaseModel):
    """Альтернативная модель элемента списка вопросов."""
    
    id: str | None = None
    product_id: int | None = None
    question_id: str | None = None
    sku: int | None = None
    text: str | None = None
    question: str | None = None
    answer: str | None = None
    last_answer: str | None = None
    answer_id: str | None = None
    created_at: Any = None
    status: str | None = None

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    @property
    def effective_id(self) -> str:
        return self.id or self.question_id or ""

    @property
    def question_text(self) -> str:
        return self.text or self.question or ""

    @property
    def has_answer(self) -> bool:
        return bool(self.answer or self.last_answer or self.answer_id)


class GetQuestionListResult(BaseModel):
    """Результат запроса списка вопросов."""
    
    questions: list[QuestionListItem] = Field(default_factory=list)
    last_id: str = ""
    total: int = 0

    model_config = ConfigDict(extra="allow", protected_namespaces=())


# Обратная совместимость - экспортируем все модели
__all__ = [
    "QuestionItem",
    "GetQuestionListResponse",
    "StockItem",
    "ProductStockInfo",
    "ProductListItem",
    "ProductListPage",
    "ProductInfoItem",
    "ChatSummary",
    "ChatListItem",
    "ChatListResponse",
    "ChatHistoryResponse",
    "ChatMessage",
    "QuestionListFilter",
    "GetQuestionListRequest",
    "QuestionListItem",
    "GetQuestionListResult",
]
