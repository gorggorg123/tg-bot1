"""
Unit tests для Pydantic моделей
Тестируем валидацию данных
"""
import pytest
from datetime import datetime
from decimal import Decimal

from pydantic import ValidationError

from botapp.models.domain import (
    ProductInfo,
    ProductImage,
    ProductStock,
    ChatMessage,
    Chat,
    Review,
    Question,
    Transaction,
    AnalyticsData,
    ProductVisibility,
    ChatStatus,
)


class TestProductInfo:
    """Тесты для ProductInfo"""
    
    def test_create_valid_product(self):
        """Тест создания валидного товара"""
        # Arrange
        data = {
            "id": 123,
            "name": "Test Product",
            "offer_id": "SKU-001",
            "sku": 456,
            "price": "1999.99",
        }
        
        # Act
        product = ProductInfo(**data)
        
        # Assert
        assert product.id == 123
        assert product.name == "Test Product"
        assert product.price == "1999.99"
        assert product.price_decimal == Decimal("1999.99")
    
    def test_price_validation(self):
        """Тест валидации цены"""
        # Arrange - используем string вместо float для точности
        data = {
            "id": 123,
            "name": "Product",
            "offer_id": "SKU-001",
            "sku": 456,
            "price": "1999.99",  # string для точности
        }
        
        # Act
        product = ProductInfo(**data)
        
        # Assert
        assert isinstance(product.price, str)
        assert product.price_decimal == Decimal("1999.99")
        
        # Test float input (will have precision issues)
        data2 = {
            "id": 123,
            "name": "Product",
            "offer_id": "SKU-001",
            "sku": 456,
            "price": 1999.99,  # float
        }
        product2 = ProductInfo(**data2)
        assert isinstance(product2.price, str)
        # Float precision - приблизительно равно
        assert abs(product2.price_decimal - Decimal("1999.99")) < Decimal("0.01")
    
    def test_missing_required_fields(self):
        """Тест отсутствия обязательных полей"""
        # Arrange
        data = {
            "id": 123,
            "name": "Product",
            # offer_id отсутствует
            "sku": 456,
        }
        
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            ProductInfo(**data)
        
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("offer_id",) for e in errors)
    
    def test_optional_fields(self):
        """Тест опциональных полей"""
        # Arrange
        data = {
            "id": 123,
            "name": "Product",
            "offer_id": "SKU-001",
            "sku": 456,
            "description": "Test description",
            "barcode": "1234567890",
        }
        
        # Act
        product = ProductInfo(**data)
        
        # Assert
        assert product.description == "Test description"
        assert product.barcode == "1234567890"
        assert product.price is None


class TestReview:
    """Тесты для Review"""
    
    def test_create_valid_review(self):
        """Тест создания валидного отзыва"""
        # Arrange
        data = {
            "id": "review123",
            "product_id": 123,
            "sku": 456,
            "text": "Отличный товар!",
            "rating": 5,
            "author": "Покупатель",
            "created_at": datetime.now(),
        }
        
        # Act
        review = Review(**data)
        
        # Assert
        assert review.id == "review123"
        assert review.rating == 5
        assert review.is_answered is False
    
    def test_rating_constraints(self):
        """Тест ограничений рейтинга"""
        # Valid ratings
        for rating in [1, 2, 3, 4, 5]:
            data = {
                "id": "r1",
                "product_id": 123,
                "sku": 456,
                "text": "Text",
                "rating": rating,
                "author": "Author",
                "created_at": datetime.now(),
            }
            review = Review(**data)
            assert review.rating == rating
        
        # Invalid ratings
        for rating in [0, 6, 10]:
            data["rating"] = rating
            with pytest.raises(ValidationError):
                Review(**data)


class TestTransaction:
    """Тесты для Transaction"""
    
    def test_create_valid_transaction(self):
        """Тест создания валидной транзакции"""
        # Arrange
        data = {
            "operation_id": "op123",
            "operation_type": "sale",
            "operation_date": datetime.now(),
            "amount": "1999.99",
        }
        
        # Act
        transaction = Transaction(**data)
        
        # Assert
        assert transaction.operation_id == "op123"
        assert transaction.amount == Decimal("1999.99")
        assert transaction.currency == "RUB"
    
    def test_amount_validation(self):
        """Тест валидации суммы"""
        # Test with different types
        for amount_value in ["1999.99", 1999.99, 1999]:
            data = {
                "operation_id": "op123",
                "operation_type": "sale",
                "operation_date": datetime.now(),
                "amount": amount_value,
            }
            transaction = Transaction(**data)
            assert isinstance(transaction.amount, Decimal)


class TestAnalyticsData:
    """Тесты для AnalyticsData"""
    
    def test_create_valid_analytics(self):
        """Тест создания валидных данных аналитики"""
        # Arrange
        data = {
            "sku": 123,
            "date": datetime.now(),
            "hits_view": 100,
            "revenue": "5000.00",
            "ordered_units": 10,
        }
        
        # Act
        analytics = AnalyticsData(**data)
        
        # Assert
        assert analytics.sku == 123
        assert analytics.hits_view == 100
        assert analytics.revenue == Decimal("5000.00")
        assert analytics.ordered_units == 10
    
    def test_default_values(self):
        """Тест значений по умолчанию"""
        # Arrange
        data = {}
        
        # Act
        analytics = AnalyticsData(**data)
        
        # Assert
        assert analytics.hits_view == 0
        assert analytics.revenue == Decimal("0")
        assert analytics.ordered_units == 0


class TestChatMessage:
    """Тесты для ChatMessage"""
    
    def test_create_valid_message(self):
        """Тест создания валидного сообщения"""
        # Arrange
        data = {
            "message_id": "msg123",
            "chat_id": "chat123",
            "user_id": "user123",
            "text": "Здравствуйте!",
            "created_at": datetime.now(),
            "is_buyer": True,
        }
        
        # Act
        message = ChatMessage(**data)
        
        # Assert
        assert message.message_id == "msg123"
        assert message.is_buyer is True
        assert message.is_seller is False
        assert message.attachments == []


class TestEnums:
    """Тесты для Enum типов"""
    
    def test_product_visibility_enum(self):
        """Тест ProductVisibility enum"""
        assert ProductVisibility.VISIBLE == "VISIBLE"
        assert ProductVisibility.INVISIBLE == "INVISIBLE"
        assert ProductVisibility.ALL == "ALL"
    
    def test_chat_status_enum(self):
        """Тест ChatStatus enum"""
        assert ChatStatus.OPENED == "opened"
        assert ChatStatus.CLOSED == "closed"
        assert ChatStatus.ALL == "all"
