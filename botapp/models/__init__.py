"""
Pydantic models для валидации данных.
Используем Pydantic V2 для строгой типизации и валидации.
"""

from botapp.models.ozon import (
    QuestionItem,
    GetQuestionListResponse,
    StockItem,
    ProductStockInfo,
    ProductListItem,
    ProductListPage,
    ProductInfoItem,
    ChatSummary,
    ChatListItem,
    ChatListResponse,
    ChatHistoryResponse,
    ChatMessage,
    QuestionListFilter,
    GetQuestionListRequest,
    QuestionListItem,
    GetQuestionListResult,
)

__all__ = [
    # Ozon API models
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
