"""
Domain models - модели предметной области
Используют Pydantic для валидации и сериализации
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, ConfigDict


# ========== ENUMS ==========

class ProductVisibility(str, Enum):
    """Видимость товара"""
    ALL = "ALL"
    VISIBLE = "VISIBLE"
    INVISIBLE = "INVISIBLE"
    EMPTY_STOCK = "EMPTY_STOCK"
    NOT_MODERATED = "NOT_MODERATED"
    MODERATED = "MODERATED"
    DISABLED = "DISABLED"
    STATE_FAILED = "STATE_FAILED"
    READY_TO_SUPPLY = "READY_TO_SUPPLY"
    VALIDATION_STATE_PENDING = "VALIDATION_STATE_PENDING"
    VALIDATION_STATE_FAIL = "VALIDATION_STATE_FAIL"
    VALIDATION_STATE_SUCCESS = "VALIDATION_STATE_SUCCESS"


class ChatStatus(str, Enum):
    """Статус чата"""
    OPENED = "opened"
    CLOSED = "closed"
    ALL = "all"


class TransactionType(str, Enum):
    """Тип транзакции"""
    ALL = "all"
    ORDERS = "orders"
    RETURNS = "returns"
    SERVICES = "services"
    COMPENSATIONS = "compensations"
    TRANSFERS = "transfers"
    OTHERS = "others"


# ========== PRODUCT MODELS ==========

class ProductImage(BaseModel):
    """Изображение товара"""
    model_config = ConfigDict(protected_namespaces=())
    
    file_name: str
    default: bool = False
    url: Optional[str] = None


class ProductStock(BaseModel):
    """Остатки товара"""
    model_config = ConfigDict(protected_namespaces=())
    
    coming: int = 0
    present: int = 0
    reserved: int = 0


class ProductInfo(BaseModel):
    """
    Информация о товаре
    Валидированная модель на основе Ozon API response
    """
    model_config = ConfigDict(protected_namespaces=())
    
    id: int = Field(..., description="ID товара")
    name: str = Field(..., description="Название товара")
    offer_id: str = Field(..., description="Артикул продавца")
    sku: int = Field(..., description="SKU")
    
    # Optional fields
    barcode: Optional[str] = None
    category_id: Optional[int] = None
    description: Optional[str] = None
    height: Optional[int] = None
    width: Optional[int] = None
    depth: Optional[int] = None
    dimension_unit: Optional[str] = None
    weight: Optional[int] = None
    weight_unit: Optional[str] = None
    
    images: List[ProductImage] = Field(default_factory=list)
    primary_image: Optional[str] = None
    
    old_price: Optional[str] = None
    price: Optional[str] = None
    marketing_price: Optional[str] = None
    premium_price: Optional[str] = None
    
    vat: Optional[str] = None
    min_price: Optional[str] = None
    
    status: Optional[Dict[str, Any]] = None
    stocks: Optional[ProductStock] = None
    
    visible: bool = False
    visibility_details: Optional[Dict[str, Any]] = None
    
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @field_validator("price", "old_price", "marketing_price", "premium_price", mode="before")
    @classmethod
    def validate_price(cls, v):
        """Валидация цены"""
        if v is None:
            return None
        try:
            return str(Decimal(v))
        except (ValueError, TypeError):
            return str(v)
    
    @property
    def price_decimal(self) -> Optional[Decimal]:
        """Цена как Decimal"""
        if self.price:
            try:
                return Decimal(self.price)
            except:
                return None
        return None


# ========== CHAT MODELS ==========

class ChatMessage(BaseModel):
    """Сообщение в чате"""
    model_config = ConfigDict(protected_namespaces=())
    
    message_id: str
    chat_id: str
    user_id: str
    text: str
    created_at: datetime
    
    is_seller: bool = False
    is_buyer: bool = False
    
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class Chat(BaseModel):
    """Чат с покупателем"""
    model_config = ConfigDict(protected_namespaces=())
    
    chat_id: str
    chat_type: str
    created_at: datetime
    
    status: Optional[ChatStatus] = None
    unread_count: int = 0
    
    last_message_timestamp: Optional[datetime] = None
    last_message_text: Optional[str] = None


# ========== REVIEW & QUESTION MODELS ==========

class Review(BaseModel):
    """Отзыв покупателя"""
    model_config = ConfigDict(protected_namespaces=())
    
    id: str
    product_id: int
    sku: int
    
    text: str
    rating: int = Field(..., ge=1, le=5)
    
    author: str
    created_at: datetime
    
    photos: List[str] = Field(default_factory=list)
    is_answered: bool = False
    answer_text: Optional[str] = None


class Question(BaseModel):
    """Вопрос покупателя"""
    model_config = ConfigDict(protected_namespaces=())
    
    id: str
    product_id: int
    
    text: str
    author: str
    created_at: datetime
    
    is_answered: bool = False
    answer_text: Optional[str] = None


# ========== FINANCE MODELS ==========

class Transaction(BaseModel):
    """Финансовая транзакция"""
    model_config = ConfigDict(protected_namespaces=())
    
    operation_id: str
    operation_type: str
    operation_date: datetime
    
    amount: Decimal = Field(..., description="Сумма операции")
    currency: str = "RUB"
    
    posting_number: Optional[str] = None
    sku: Optional[int] = None
    product_name: Optional[str] = None
    
    type_name: Optional[str] = None
    services: List[Dict[str, Any]] = Field(default_factory=list)
    
    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, v):
        """Валидация суммы"""
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        return v


# ========== ANALYTICS MODELS ==========

class AnalyticsData(BaseModel):
    """Аналитические данные"""
    model_config = ConfigDict(protected_namespaces=())
    
    sku: Optional[int] = None
    date: Optional[datetime] = None
    
    hits_view: int = 0
    hits_view_search: int = 0
    hits_view_pdp: int = 0
    hits_tocart: int = 0
    hits_tocart_search: int = 0
    hits_tocart_pdp: int = 0
    
    session_view: int = 0
    session_view_search: int = 0
    session_view_pdp: int = 0
    
    conv_tocart: float = 0.0
    revenue: Decimal = Field(default=Decimal("0"))
    
    ordered_units: int = 0
    delivered_units: int = 0
    returns: int = 0
    
    @field_validator("revenue", mode="before")
    @classmethod
    def validate_revenue(cls, v):
        """Валидация дохода"""
        if v is None:
            return Decimal("0")
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        return v
