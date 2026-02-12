"""
Unit tests для CacheRepository
Тестируем многоуровневое кеширование
"""
import pytest
import asyncio
from pathlib import Path
import tempfile
import shutil

from botapp.repositories.cache import CacheRepository


class TestCacheRepository:
    """Тесты для CacheRepository"""
    
    @pytest.fixture
    def temp_dir(self):
        """Временная директория для тестов"""
        temp_path = Path(tempfile.mkdtemp())
        yield temp_path
        # Cleanup
        shutil.rmtree(temp_path, ignore_errors=True)
    
    @pytest.fixture
    def cache(self, temp_dir):
        """Создать экземпляр кеша"""
        return CacheRepository(
            name="test",
            memory_ttl=60,
            memory_maxsize=100,
            disk_path=temp_dir,
            enable_disk_cache=True,
        )
    
    @pytest.fixture
    def memory_only_cache(self):
        """Кеш только в памяти"""
        return CacheRepository(
            name="test_memory",
            memory_ttl=60,
            memory_maxsize=100,
            enable_disk_cache=False,
        )
    
    # ========== TESTS: Basic Operations ==========
    
    @pytest.mark.asyncio
    async def test_set_and_get(self, cache):
        """Тест базовых set/get операций"""
        # Arrange
        key = "test_key"
        value = {"data": "test_value"}
        
        # Act
        await cache.set(key, value)
        result = await cache.get(key)
        
        # Assert
        assert result == value
        assert cache._hits == 1
        assert cache._memory_hits == 1
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_key(self, cache):
        """Тест получения несуществующего ключа"""
        # Act
        result = await cache.get("nonexistent", default="default_value")
        
        # Assert
        assert result == "default_value"
        assert cache._misses == 1
    
    @pytest.mark.asyncio
    async def test_delete(self, cache):
        """Тест удаления из кеша"""
        # Arrange
        key = "test_key"
        value = "test_value"
        await cache.set(key, value)
        
        # Act
        await cache.delete(key)
        result = await cache.get(key, default="not_found")
        
        # Assert
        assert result == "not_found"
    
    @pytest.mark.asyncio
    async def test_clear(self, cache):
        """Тест очистки кеша"""
        # Arrange
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")
        
        # Act
        await cache.clear()
        
        # Assert
        result1 = await cache.get("key1", default="not_found")
        result2 = await cache.get("key2", default="not_found")
        result3 = await cache.get("key3", default="not_found")
        
        assert result1 == "not_found"
        assert result2 == "not_found"
        assert result3 == "not_found"
    
    # ========== TESTS: Disk Cache ==========
    
    @pytest.mark.asyncio
    async def test_disk_cache_persistence(self, temp_dir):
        """Тест персистентности disk cache"""
        # Arrange
        cache1 = CacheRepository(
            name="test1",
            memory_ttl=60,
            disk_path=temp_dir,
            enable_disk_cache=True,
        )
        
        # Act - save to cache
        await cache1.set("persisted_key", "persisted_value")
        
        # Create new cache instance (simulating restart)
        cache2 = CacheRepository(
            name="test2",
            memory_ttl=60,
            disk_path=temp_dir,
            enable_disk_cache=True,
        )
        
        # Assert - should load from disk
        result = await cache2.get("persisted_key")
        assert result == "persisted_value"
        assert cache2._disk_hits == 1
    
    @pytest.mark.asyncio
    async def test_memory_to_disk_promotion(self, cache):
        """Тест продвижения из disk в memory"""
        # Arrange - clear memory cache
        await cache.clear()
        await cache.set("key", "value")
        
        # Clear only L1 (memory)
        cache._memory_cache.clear()
        
        # Act - should load from L2 (disk) and promote to L1
        result = await cache.get("key")
        
        # Assert
        assert result == "value"
        assert cache._disk_hits == 1
        
        # Second get should hit L1
        result2 = await cache.get("key")
        assert result2 == "value"
        assert cache._memory_hits == 1
    
    # ========== TESTS: Fallback ==========
    
    @pytest.mark.asyncio
    async def test_get_with_fallback(self, cache):
        """Тест с fallback функцией"""
        # Arrange
        def fallback_func():
            return "fallback_value"
        
        # Act
        result = await cache.get("new_key", fallback=fallback_func)
        
        # Assert
        assert result == "fallback_value"
        
        # Should now be in cache
        cached_result = await cache.get("new_key")
        assert cached_result == "fallback_value"
        assert cache._hits >= 1
    
    @pytest.mark.asyncio
    async def test_get_with_async_fallback(self, cache):
        """Тест с async fallback"""
        # Arrange
        async def async_fallback():
            await asyncio.sleep(0.01)
            return "async_fallback_value"
        
        # Act
        result = await cache.get("async_key", fallback=async_fallback)
        
        # Assert
        assert result == "async_fallback_value"
    
    # ========== TESTS: Stats ==========
    
    @pytest.mark.asyncio
    async def test_get_stats(self, cache):
        """Тест статистики кеша"""
        # Arrange
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        
        # Hit
        await cache.get("key1")
        
        # Miss
        await cache.get("nonexistent")
        
        # Act
        stats = cache.get_stats()
        
        # Assert
        assert stats["name"] == "test"
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert "hit_rate" in stats
        assert stats["disk_enabled"] is True
    
    @pytest.mark.asyncio
    async def test_hit_rate_calculation(self, cache):
        """Тест расчета hit rate"""
        # Arrange - 7 hits, 3 misses = 70% hit rate
        for i in range(7):
            await cache.set(f"key{i}", f"value{i}")
            await cache.get(f"key{i}")
        
        for i in range(3):
            await cache.get(f"nonexistent{i}")
        
        # Act
        stats = cache.get_stats()
        
        # Assert
        assert stats["hits"] == 7
        assert stats["misses"] == 3
        assert "70.0%" in stats["hit_rate"]
    
    # ========== TESTS: Long Keys ==========
    
    @pytest.mark.asyncio
    async def test_long_key_hashing(self, cache):
        """Тест хеширования длинных ключей"""
        # Arrange
        long_key = "a" * 300  # > 200 chars
        value = "test_value"
        
        # Act
        await cache.set(long_key, value)
        result = await cache.get(long_key)
        
        # Assert
        assert result == value
    
    # ========== TESTS: Memory Only ==========
    
    @pytest.mark.asyncio
    async def test_memory_only_cache(self, memory_only_cache):
        """Тест кеша только в памяти"""
        # Act
        await memory_only_cache.set("key", "value")
        result = await memory_only_cache.get("key")
        
        # Assert
        assert result == "value"
        
        stats = memory_only_cache.get_stats()
        assert stats["disk_enabled"] is False
        assert stats["disk_hits"] == 0
