# botapp/api/client.py
import logging
from typing import Optional, List, Dict, Any
from ozonapi import SellerAPI

logger = logging.getLogger(__name__)

class BotOzonClient(SellerAPI):
    """
    Расширенный клиент Ozon.
    Наследуется от ozonapi, добавляет методы для Чатов V3 и безопасного обновления стоков.
    """

    def __init__(self, client_id: str, api_key: str):
        # Инициализируем родительский класс (он сам настроит ретраи и лимиты)
        super().__init__(client_id=client_id, api_key=api_key)

    async def chat_list_v3(self, filter_data: Dict[str, Any], limit: int = 30) -> Dict:
        """Метод списка чатов V3 (которого может не быть в либе)"""
        return await self.request(
            method="POST",
            url="v3/chat/list",
            payload={
                "filter": filter_data,
                "limit": limit
            }
        )

    async def chat_history_v3(self, chat_id: str, limit: int = 100, direction: str = "forward") -> Dict:
        """История чата V3"""
        return await self.request(
            method="POST",
            url="v3/chat/history",
            payload={
                "chat_id": chat_id,
                "limit": limit,
                "direction": direction
            }
        )

    async def send_message_v3(self, chat_id: str, text: str) -> Dict:
        """Отправка сообщения"""
        return await self.request(
            method="POST",
            url="v3/chat/send/message",
            payload={
                "chat_id": chat_id,
                "text": text
            }
        )

    async def update_stocks_safe(self, stocks: List[Dict[str, Any]]) -> Dict:
        """
        Безопасное обновление остатков.
        ВАЖНО: Стандартная библиотека может резать поле 'quant_size', 
        которое обязательно для 'Эконом' товаров. Мы шлем сырой запрос.
        """
        # Пример payload: [{"offer_id": "...", "stock": 10, "warehouse_id": 123, "quant_size": 1}]
        return await self.request(
            method="POST",
            url="v2/products/stocks",
            payload={"stocks": stocks}
        )

    async def get_product_info_v2(self, offer_id: str) -> Dict:
        """Получение инфо о товаре по offer_id"""
        try:
            # Используем библиотечный метод, если он работает корректно, 
            # или raw request для надежности
            res = await self.request(
                "POST", 
                "v2/product/info", 
                {"offer_id": offer_id}
            )
            return res.get("result", {})
        except Exception as e:
            logger.error(f"Error getting product info {offer_id}: {e}")
            return {}