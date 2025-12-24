# botapp/products_service.py
import logging
from typing import List, Optional
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from botapp.db import async_session, ProductModel
from botapp.api.client import BotOzonClient

logger = logging.getLogger(__name__)

class ProductsService:
    def __init__(self, api_client: BotOzonClient):
        self.api = api_client

    async def get_all_products(self, limit: int = 100) -> List[ProductModel]:
        """Получить товары из локальной БД"""
        async with async_session() as session:
            result = await session.execute(select(ProductModel).limit(limit))
            return list(result.scalars().all())

    async def get_by_offer_id(self, offer_id: str) -> Optional[ProductModel]:
        async with async_session() as session:
            result = await session.execute(select(ProductModel).where(ProductModel.offer_id == offer_id))
            return result.scalar_one_or_none()

    async def search_products(self, query: str) -> List[ProductModel]:
        """Поиск по названию или артикулу"""
        q = f"%{query}%"
        async with async_session() as session:
            stmt = select(ProductModel).where(
                (ProductModel.name.ilike(q)) | 
                (ProductModel.offer_id.ilike(q)) |
                (ProductModel.sku.ilike(q))
            ).limit(20)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def sync_catalog(self):
        """Полная синхронизация каталога с Ozon (вызывать периодически)"""
        logger.info("Starting catalog sync...")
        
        # 1. Получаем список товаров v2 (без деталей)
        payload = {"filter": {"visibility": "ALL"}, "limit": 1000}
        try:
            # Используем встроенный метод библиотеки для списка товаров
            # Если в либе нет - используем raw request:
            resp = await self.api.request("POST", "v2/product/list", payload)
            items = resp.get("result", {}).get("items", [])
            
            if not items:
                return

            offer_ids = [i["offer_id"] for i in items]
            
            # 2. Получаем детали (цены, названия) через v2/product/info/list
            # Ozon позволяет до 1000 id за раз в info/list
            info_resp = await self.api.request(
                "POST", 
                "v2/product/info/list", 
                {"offer_id": offer_ids}
            )
            details = info_resp.get("result", {}).get("items", [])

            async with async_session() as session:
                for item in details:
                    # Преобразуем ответ Ozon в нашу модель
                    p = ProductModel(
                        sku=str(item.get("fbo_sku") or item.get("sku") or item.get("id")),
                        product_id=item.get("id"),
                        offer_id=item.get("offer_id"),
                        name=item.get("name"),
                        price=float(item.get("price", "0").split()[0].replace(",", ".")), # грубый парсинг, лучше взять из price_info
                        quant_size=1, # Тут надо парсить доп поля если есть
                        raw_data=item
                    )
                    await session.merge(p) # Upsert (вставить или обновить)
                
                await session.commit()
            logger.info(f"Synced {len(details)} products")
            
        except Exception as e:
            logger.error(f"Catalog sync failed: {e}", exc_info=True)

# Глобальный инстанс будет создан в main.py или DI контейнере