"""
Unit tests для OzonService
Тестируем бизнес-логику без реальных API вызовов
"""
import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from botapp.services.ozon_service import OzonService, get_ozon_service
from botapp.core.exceptions import OzonAPIError, ValidationError


class TestOzonService:
    """Тесты для OzonService"""
    
    @pytest.fixture
    def service(self):
        """Создать экземпляр сервиса"""
        return OzonService(profile="default")
    
    @pytest.fixture
    def mock_client(self):
        """Мокнутый Ozon API клиент"""
        client = AsyncMock()
        client.get_products_list = AsyncMock()
        client.get_product_info = AsyncMock()
        client.get_analytics_data = AsyncMock()
        client.get_finance_transactions = AsyncMock()
        client.get_chats_list = AsyncMock()
        client.send_chat_message = AsyncMock()
        return client
    
    # ========== TESTS: get_products_list ==========
    
    @pytest.mark.asyncio
    async def test_get_products_list_success(self, service, mock_client):
        """Тест успешного получения списка товаров"""
        # Arrange
        mock_response = {
            "items": [
                {"id": 123, "name": "Product 1"},
                {"id": 456, "name": "Product 2"},
            ],
            "result": {"last_id": "456"},
        }
        mock_client.get_products_list.return_value = mock_response
        
        with patch.object(service, '_get_client', return_value=mock_client):
            # Act
            result = await service.get_products_list(limit=100)
            
            # Assert
            assert result == mock_response
            assert len(result["items"]) == 2
            mock_client.get_products_list.assert_called_once_with(
                limit=100,
                last_id="",
                visibility=None,
            )
    
    @pytest.mark.asyncio
    async def test_get_products_list_invalid_limit(self, service):
        """Тест валидации лимита"""
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            await service.get_products_list(limit=2000)
        
        assert "Limit должен быть от 1 до 1000" in str(exc_info.value)
        assert exc_info.value.field == "limit"
        assert exc_info.value.value == 2000
    
    @pytest.mark.asyncio
    async def test_get_products_list_api_error(self, service, mock_client):
        """Тест обработки ошибок API"""
        # Arrange
        mock_client.get_products_list.side_effect = Exception("API Error")
        
        with patch.object(service, '_get_client', return_value=mock_client):
            # Act & Assert
            with pytest.raises(OzonAPIError) as exc_info:
                await service.get_products_list(limit=100)
            
            assert "Ошибка получения списка товаров" in str(exc_info.value)
            assert exc_info.value.code == "PRODUCTS_LIST_ERROR"
    
    # ========== TESTS: get_product_info ==========
    
    @pytest.mark.asyncio
    async def test_get_product_info_by_product_id(self, service, mock_client):
        """Тест получения товара по product_id"""
        # Arrange
        mock_response = {
            "result": {
                "id": 123,
                "name": "Test Product",
                "sku": 456,
            }
        }
        mock_client.get_product_info.return_value = mock_response
        
        with patch.object(service, '_get_client', return_value=mock_client):
            # Act
            result = await service.get_product_info(product_id=123)
            
            # Assert
            assert result == mock_response
            mock_client.get_product_info.assert_called_once_with(
                product_id=123,
                sku=None,
                offer_id=None,
            )
    
    @pytest.mark.asyncio
    async def test_get_product_info_no_identifiers(self, service):
        """Тест без идентификаторов"""
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            await service.get_product_info()
        
        assert "Необходимо указать хотя бы один идентификатор" in str(exc_info.value)
    
    # ========== TESTS: get_analytics_data ==========
    
    @pytest.mark.asyncio
    async def test_get_analytics_data_success(self, service, mock_client):
        """Тест получения аналитики"""
        # Arrange
        date_from = datetime.now() - timedelta(days=7)
        date_to = datetime.now()
        
        mock_response = {
            "result": {
                "data": [
                    {"sku": 123, "views": 100, "revenue": "1000.00"},
                    {"sku": 456, "views": 200, "revenue": "2000.00"},
                ]
            }
        }
        mock_client.get_analytics_data.return_value = mock_response
        
        with patch.object(service, '_get_client', return_value=mock_client):
            # Act
            result = await service.get_analytics_data(
                date_from=date_from,
                date_to=date_to,
                metrics=["views", "revenue"],
                dimension=["sku"],
            )
            
            # Assert
            assert len(result["result"]["data"]) == 2
            mock_client.get_analytics_data.assert_called_once()
    
    # ========== TESTS: send_chat_message ==========
    
    @pytest.mark.asyncio
    async def test_send_chat_message_success(self, service, mock_client):
        """Тест отправки сообщения"""
        # Arrange
        mock_response = {"result": {"message_id": "msg123"}}
        mock_client.send_chat_message.return_value = mock_response
        
        with patch.object(service, '_get_client', return_value=mock_client):
            # Act
            result = await service.send_chat_message(
                chat_id="chat123",
                text="Здравствуйте!",
            )
            
            # Assert
            assert result == mock_response
            mock_client.send_chat_message.assert_called_once_with(
                chat_id="chat123",
                text="Здравствуйте!",
            )
    
    @pytest.mark.asyncio
    async def test_send_chat_message_empty_text(self, service):
        """Тест отправки пустого сообщения"""
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            await service.send_chat_message(chat_id="chat123", text="")
        
        assert "Текст сообщения не может быть пустым" in str(exc_info.value)
        assert exc_info.value.field == "text"
    
    # ========== TESTS: get_client_metrics ==========
    
    @pytest.mark.asyncio
    async def test_get_client_metrics(self, service):
        """Тест получения метрик"""
        # Arrange
        mock_manager = MagicMock()
        mock_manager.get_all_metrics.return_value = {
            "default": "Total: 100",
            "write": "Total: 50",
        }
        
        with patch('botapp.services.ozon_service.get_ozon_client_manager', return_value=mock_manager):
            # Act
            metrics = await service.get_client_metrics()
            
            # Assert
            assert "default" in metrics
            assert "write" in metrics


class TestGetOzonService:
    """Тесты для глобального экземпляра"""
    
    def test_get_ozon_service_singleton(self):
        """Тест что возвращается один экземпляр"""
        # Act
        service1 = get_ozon_service()
        service2 = get_ozon_service()
        
        # Assert
        assert service1 is service2
    
    def test_get_ozon_service_different_profile(self):
        """Тест разных профилей"""
        # Act
        service1 = get_ozon_service(profile="default")
        service2 = get_ozon_service(profile="write")
        
        # Assert
        assert service1 is not service2
        assert service1.profile == "default"
        assert service2.profile == "write"
