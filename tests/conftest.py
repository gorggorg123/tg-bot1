"""
Pytest configuration and fixtures
"""
import pytest
import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_product_data():
    """Sample product data for tests"""
    return {
        "id": 123,
        "name": "Test Product",
        "offer_id": "SKU-001",
        "sku": 456,
        "price": "1999.99",
        "old_price": "2499.99",
        "visible": True,
    }


@pytest.fixture
def sample_review_data():
    """Sample review data for tests"""
    from datetime import datetime
    return {
        "id": "review123",
        "product_id": 123,
        "sku": 456,
        "text": "Отличный товар!",
        "rating": 5,
        "author": "Покупатель",
        "created_at": datetime.now(),
        "is_answered": False,
    }


@pytest.fixture
def sample_transaction_data():
    """Sample transaction data for tests"""
    from datetime import datetime
    return {
        "operation_id": "op123",
        "operation_type": "sale",
        "operation_date": datetime.now(),
        "amount": "1999.99",
        "currency": "RUB",
    }
