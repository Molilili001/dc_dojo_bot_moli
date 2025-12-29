"""
缓存系统模块
提供内存缓存和可选的Redis缓存支持
"""

import asyncio
import json
import time
from typing import Any, Optional, Dict, List, Union
from datetime import datetime, timedelta
from collections import OrderedDict
import logging

from utils.logger import get_logger

logger = get_logger(__name__)


class CacheEntry:
    """缓存条目"""
    
    def __init__(self, key: str, value: Any, ttl: Optional[int] = None):
        """
        初始化缓存条目
        
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 生存时间（秒）
        """
        self.key = key
        self.value = value
        self.created_at = time.time()
        self.ttl = ttl
        self.hits = 0
        self.last_accessed = self.created_at
        
    def is_expired(self) -> bool:
        """检查是否过期"""
        if self.ttl is None:
            return False
        return time.time() - self.created_at > self.ttl
        
    def access(self) -> Any:
        """访问缓存条目"""
        self.hits += 1
        self.last_accessed = time.time()
        return self.value


class MemoryCache:
    """内存缓存实现"""
    
    def __init__(self, max_size: int = 10000, default_ttl: int = 300):
        """
        初始化内存缓存
        
        Args:
            max_size: 最大缓存条目数
            default_ttl: 默认TTL（秒）
        """
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.lock = asyncio.Lock()
        self.stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "expirations": 0
        }
        
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        async with self.lock:
            if key not in self.cache:
                self.stats["misses"] += 1
                return None
                
            entry = self.cache[key]
            
            # 检查过期
            if entry.is_expired():
                del self.cache[key]
                self.stats["expirations"] += 1
                self.stats["misses"] += 1
                return None
                
            # LRU: 移到末尾
            self.cache.move_to_end(key)
            self.stats["hits"] += 1
            return entry.access()
            
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """设置缓存值"""
        async with self.lock:
            # 如果已存在，先删除
            if key in self.cache:
                del self.cache[key]
                
            # 检查容量
            while len(self.cache) >= self.max_size:
                # 移除最老的条目
                oldest_key = next(iter(self.cache))
                del self.cache[oldest_key]
                self.stats["evictions"] += 1
                
            # 添加新条目
            entry = CacheEntry(key, value, ttl or self.default_ttl)
            self.cache[key] = entry
            
    async def delete(self, key: str) -> bool:
        """删除缓存条目"""
        async with self.lock:
            if key in self.cache:
                del self.cache[key]
                return True
            return False
            
    async def clear(self) -> None:
        """清空所有缓存"""
        async with self.lock:
            self.cache.clear()
            
    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        async with self.lock:
            if key not in self.cache:
                return False
            entry = self.cache[key]
            if entry.is_expired():
                del self.cache[key]
                self.stats["expirations"] += 1
                return False
            return True
            
    async def get_many(self, keys: List[str]) -> Dict[str, Any]:
        """批量获取缓存值"""
        result = {}
        for key in keys:
            value = await self.get(key)
            if value is not None:
                result[key] = value
        return result
        
    async def set_many(self, items: Dict[str, Any], ttl: Optional[int] = None) -> None:
        """批量设置缓存值"""
        for key, value in items.items():
            await self.set(key, value, ttl)
            
    async def cleanup_expired(self) -> int:
        """清理过期条目"""
        async with self.lock:
            expired_keys = []
            for key, entry in self.cache.items():
                if entry.is_expired():
                    expired_keys.append(key)
                    
            for key in expired_keys:
                del self.cache[key]
                self.stats["expirations"] += 1
                
            return len(expired_keys)
            
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        total_requests = self.stats["hits"] + self.stats["misses"]
        hit_rate = (self.stats["hits"] / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.stats["hits"],
            "misses": self.stats["misses"],
            "hit_rate": f"{hit_rate:.2f}%",
            "evictions": self.stats["evictions"],
            "expirations": self.stats["expirations"],
            "default_ttl": self.default_ttl
        }


class CacheManager:
    """缓存管理器"""
    
    def __init__(self):
        """初始化缓存管理器"""
        # 不同类型数据的缓存实例
        # 注：针对 2GB VPS 优化，原配置总计 22,100 条目，现缩减至 4,750 条目
        # 预估节省约 20 MB 内存（假设每条目约 1.2 KB）
        self.caches = {
            "user": MemoryCache(max_size=1000, default_ttl=300),      # 用户数据缓存（原5000）
            "gym": MemoryCache(max_size=200, default_ttl=600),        # 道馆数据缓存（原1000）
            "progress": MemoryCache(max_size=2000, default_ttl=180),  # 进度数据缓存（原10000）
            "leaderboard": MemoryCache(max_size=50, default_ttl=60),  # 排行榜缓存（原100）
            "session": MemoryCache(max_size=500, default_ttl=1800),   # 会话缓存（原1000）
            "general": MemoryCache(max_size=1000, default_ttl=300)    # 通用缓存（原5000）
        }
        
        # 缓存键前缀
        self.prefixes = {
            "user": "user:",
            "gym": "gym:",
            "progress": "prog:",
            "leaderboard": "lb:",
            "session": "sess:",
            "general": "gen:"
        }
        
        # 启动定期清理任务
        self.cleanup_task = None
        
    async def start(self):
        """启动缓存管理器"""
        self.cleanup_task = asyncio.create_task(self._periodic_cleanup())
        logger.info("缓存管理器已启动")
        
    async def stop(self):
        """停止缓存管理器"""
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("缓存管理器已停止")
        
    async def _periodic_cleanup(self):
        """定期清理过期缓存"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟清理一次
                total_cleaned = 0
                for cache_name, cache in self.caches.items():
                    cleaned = await cache.cleanup_expired()
                    total_cleaned += cleaned
                if total_cleaned > 0:
                    logger.debug(f"清理了 {total_cleaned} 个过期缓存条目")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"缓存清理任务出错: {e}")
                
    def _get_cache(self, cache_type: str) -> MemoryCache:
        """获取对应类型的缓存实例"""
        return self.caches.get(cache_type, self.caches["general"])
        
    def _format_key(self, cache_type: str, key: str) -> str:
        """格式化缓存键"""
        prefix = self.prefixes.get(cache_type, "")
        return f"{prefix}{key}"
        
    async def get(self, key: str, cache_type: str = "general") -> Optional[Any]:
        """获取缓存值"""
        cache = self._get_cache(cache_type)
        formatted_key = self._format_key(cache_type, key)
        return await cache.get(formatted_key)
        
    async def set(self, key: str, value: Any, ttl: Optional[int] = None, cache_type: str = "general") -> None:
        """设置缓存值"""
        cache = self._get_cache(cache_type)
        formatted_key = self._format_key(cache_type, key)
        await cache.set(formatted_key, value, ttl)
        
    async def delete(self, key: str, cache_type: str = "general") -> bool:
        """删除缓存条目"""
        cache = self._get_cache(cache_type)
        formatted_key = self._format_key(cache_type, key)
        return await cache.delete(formatted_key)
        
    async def clear(self, cache_type: Optional[str] = None) -> None:
        """清空缓存"""
        if cache_type:
            cache = self._get_cache(cache_type)
            await cache.clear()
        else:
            # 清空所有缓存
            for cache in self.caches.values():
                await cache.clear()
                
    async def preload(self, data: Dict[str, Any], cache_type: str = "general", ttl: Optional[int] = None) -> None:
        """预加载缓存数据"""
        cache = self._get_cache(cache_type)
        for key, value in data.items():
            formatted_key = self._format_key(cache_type, key)
            await cache.set(formatted_key, value, ttl)
        logger.info(f"预加载了 {len(data)} 个{cache_type}缓存条目")
        
    async def get_user_progress(self, guild_id: str, user_id: str) -> Optional[Dict]:
        """获取用户进度（缓存优化）"""
        key = f"{guild_id}:{user_id}"
        return await self.get(key, "progress")
        
    async def set_user_progress(self, guild_id: str, user_id: str, progress: Dict, ttl: int = 180) -> None:
        """设置用户进度缓存"""
        key = f"{guild_id}:{user_id}"
        await self.set(key, progress, ttl, "progress")
        
    async def get_gym_data(self, guild_id: str, gym_id: str) -> Optional[Dict]:
        """获取道馆数据（缓存优化）"""
        key = f"{guild_id}:{gym_id}"
        return await self.get(key, "gym")
        
    async def set_gym_data(self, guild_id: str, gym_id: str, data: Dict, ttl: int = 600) -> None:
        """设置道馆数据缓存"""
        key = f"{guild_id}:{gym_id}"
        await self.set(key, data, ttl, "gym")
        
    async def invalidate_guild_cache(self, guild_id: str) -> None:
        """失效指定服务器的所有缓存"""
        for cache_type, cache in self.caches.items():
            prefix = self._format_key(cache_type, f"{guild_id}:")
            keys_to_delete = []
            
            async with cache.lock:
                for key in cache.cache.keys():
                    if key.startswith(prefix):
                        keys_to_delete.append(key)
                        
                for key in keys_to_delete:
                    del cache.cache[key]
                    
            if keys_to_delete:
                logger.debug(f"失效了 {len(keys_to_delete)} 个 {cache_type} 缓存条目")
                
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有缓存的统计信息"""
        stats = {}
        for cache_type, cache in self.caches.items():
            stats[cache_type] = cache.get_stats()
        return stats
        
    async def warmup(self, database) -> None:
        """缓存预热"""
        try:
            # 预热常用数据
            logger.info("开始缓存预热...")
            
            # 这里可以添加具体的预热逻辑
            # 例如：加载活跃用户数据、热门道馆数据等
            
            logger.info("缓存预热完成")
            
        except Exception as e:
            logger.error(f"缓存预热失败: {e}")


# 全局缓存管理器实例
cache_manager = CacheManager()


# 缓存装饰器
def cached(cache_type: str = "general", ttl: Optional[int] = None):
    """
    缓存装饰器
    
    Args:
        cache_type: 缓存类型
        ttl: 缓存时间（秒）
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            
            # 尝试从缓存获取
            cached_value = await cache_manager.get(cache_key, cache_type)
            if cached_value is not None:
                return cached_value
                
            # 执行函数
            result = await func(*args, **kwargs)
            
            # 缓存结果
            await cache_manager.set(cache_key, result, ttl, cache_type)
            
            return result
        return wrapper
    return decorator