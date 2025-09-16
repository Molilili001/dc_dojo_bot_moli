"""
数据库优化器模块
提供连接池优化、查询缓存、批量操作优化等功能
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Any, Tuple
from collections import OrderedDict
from datetime import datetime, timedelta
import aiosqlite
from pathlib import Path

from core.constants import DATABASE_PATH, BEIJING_TZ
from utils.logger import get_logger

logger = get_logger(__name__)


class QueryCache:
    """查询缓存实现"""
    
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300):
        """
        初始化查询缓存
        
        Args:
            max_size: 最大缓存条目数
            ttl_seconds: 缓存过期时间（秒）
        """
        self.cache: OrderedDict = OrderedDict()
        self.max_size = max_size
        self.ttl = timedelta(seconds=ttl_seconds)
        self.hits = 0
        self.misses = 0
        
    def _generate_key(self, query: str, params: Optional[Tuple] = None) -> str:
        """生成缓存键"""
        if params:
            return f"{query}:{hash(params)}"
        return query
        
    def get(self, query: str, params: Optional[Tuple] = None) -> Optional[Any]:
        """获取缓存数据"""
        key = self._generate_key(query, params)
        
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < self.ttl:
                # 移到最后（LRU）
                self.cache.move_to_end(key)
                self.hits += 1
                return data
            else:
                # 过期，删除
                del self.cache[key]
        
        self.misses += 1
        return None
        
    def set(self, query: str, params: Optional[Tuple], data: Any):
        """设置缓存数据"""
        key = self._generate_key(query, params)
        
        # 如果缓存满了，删除最老的
        if len(self.cache) >= self.max_size:
            self.cache.popitem(last=False)
        
        self.cache[key] = (data, datetime.now())
        
    def invalidate(self, pattern: Optional[str] = None):
        """失效缓存"""
        if pattern:
            # 失效匹配pattern的缓存
            keys_to_delete = [k for k in self.cache.keys() if pattern in k]
            for key in keys_to_delete:
                del self.cache[key]
        else:
            # 清空所有缓存
            self.cache.clear()
            
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{hit_rate:.2f}%",
            "ttl_seconds": self.ttl.total_seconds()
        }


class ConnectionPool:
    """数据库连接池"""
    
    def __init__(self, db_path: Path, pool_size: int = 10):
        """
        初始化连接池
        
        Args:
            db_path: 数据库路径
            pool_size: 连接池大小
        """
        self.db_path = db_path
        self.pool_size = pool_size
        self.connections: List[aiosqlite.Connection] = []
        self.available: asyncio.Queue = asyncio.Queue()
        self.lock = asyncio.Lock()
        self.initialized = False
        
    async def initialize(self):
        """初始化连接池"""
        if self.initialized:
            return
            
        async with self.lock:
            if self.initialized:
                return
                
            for _ in range(self.pool_size):
                conn = await aiosqlite.connect(self.db_path)
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.execute("PRAGMA synchronous=NORMAL")
                self.connections.append(conn)
                await self.available.put(conn)
                
            self.initialized = True
            logger.info(f"数据库连接池初始化完成，池大小: {self.pool_size}")
            
    async def acquire(self) -> aiosqlite.Connection:
        """获取连接"""
        if not self.initialized:
            await self.initialize()
        return await self.available.get()
        
    async def release(self, conn: aiosqlite.Connection):
        """释放连接"""
        await self.available.put(conn)
        
    async def close_all(self):
        """关闭所有连接"""
        for conn in self.connections:
            await conn.close()
        self.connections.clear()
        self.initialized = False
        logger.info("数据库连接池已关闭")


class DatabaseOptimizer:
    """数据库优化器"""
    
    def __init__(self):
        self.pool = ConnectionPool(DATABASE_PATH, pool_size=10)
        self.query_cache = QueryCache(max_size=1000, ttl_seconds=300)
        self.slow_query_threshold = 1.0  # 慢查询阈值（秒）
        self.slow_queries: List[Dict[str, Any]] = []
        
    async def initialize(self):
        """初始化优化器"""
        await self.pool.initialize()
        logger.info("数据库优化器初始化完成")
        
    async def execute_cached(self, query: str, params: Optional[Tuple] = None) -> Any:
        """执行缓存查询"""
        # SELECT查询才使用缓存
        if not query.strip().upper().startswith("SELECT"):
            return await self.execute(query, params)
            
        # 尝试从缓存获取
        cached = self.query_cache.get(query, params)
        if cached is not None:
            return cached
            
        # 执行查询并缓存结果
        result = await self.execute(query, params)
        self.query_cache.set(query, params, result)
        return result
        
    async def execute(self, query: str, params: Optional[Tuple] = None) -> Any:
        """执行查询"""
        conn = await self.pool.acquire()
        start_time = time.time()
        
        try:
            if params:
                cursor = await conn.execute(query, params)
            else:
                cursor = await conn.execute(query)
                
            # 根据查询类型返回不同结果
            query_upper = query.strip().upper()
            if query_upper.startswith("SELECT"):
                result = await cursor.fetchall()
            else:
                result = cursor.rowcount
                await conn.commit()
                # 写操作失效相关缓存
                self._invalidate_related_cache(query)
                
            return result
            
        except Exception as e:
            await conn.rollback()
            logger.error(f"数据库查询错误: {e}\nQuery: {query}\nParams: {params}")
            raise
            
        finally:
            elapsed = time.time() - start_time
            if elapsed > self.slow_query_threshold:
                self._record_slow_query(query, params, elapsed)
            await self.pool.release(conn)
            
    async def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """批量执行查询"""
        conn = await self.pool.acquire()
        
        try:
            await conn.executemany(query, params_list)
            await conn.commit()
            self._invalidate_related_cache(query)
            return len(params_list)
            
        except Exception as e:
            await conn.rollback()
            logger.error(f"批量查询错误: {e}")
            raise
            
        finally:
            await self.pool.release(conn)
            
    async def execute_batch(self, operations: List[Tuple[str, Optional[Tuple]]]) -> List[Any]:
        """批量执行多个操作（事务）"""
        conn = await self.pool.acquire()
        results = []
        
        try:
            await conn.execute("BEGIN")
            
            for query, params in operations:
                if params:
                    cursor = await conn.execute(query, params)
                else:
                    cursor = await conn.execute(query)
                    
                query_upper = query.strip().upper()
                if query_upper.startswith("SELECT"):
                    results.append(await cursor.fetchall())
                else:
                    results.append(cursor.rowcount)
                    
            await conn.commit()
            
            # 失效缓存
            for query, _ in operations:
                if not query.strip().upper().startswith("SELECT"):
                    self._invalidate_related_cache(query)
                    
            return results
            
        except Exception as e:
            await conn.rollback()
            logger.error(f"批量操作错误: {e}")
            raise
            
        finally:
            await self.pool.release(conn)
            
    def _invalidate_related_cache(self, query: str):
        """失效相关缓存"""
        query_upper = query.strip().upper()
        
        # 根据不同的操作类型失效不同的缓存
        if "user_progress" in query:
            self.query_cache.invalidate("user_progress")
        elif "gyms" in query:
            self.query_cache.invalidate("gyms")
        elif "challenge_panels" in query:
            self.query_cache.invalidate("challenge_panels")
        elif "leaderboard" in query:
            self.query_cache.invalidate("leaderboard")
            
    def _record_slow_query(self, query: str, params: Optional[Tuple], elapsed: float):
        """记录慢查询"""
        self.slow_queries.append({
            "query": query,
            "params": params,
            "elapsed": elapsed,
            "timestamp": datetime.now(BEIJING_TZ)
        })
        
        # 只保留最近100条
        if len(self.slow_queries) > 100:
            self.slow_queries = self.slow_queries[-100:]
            
        logger.warning(f"慢查询检测 ({elapsed:.2f}s): {query[:100]}...")
        
    async def analyze_performance(self) -> Dict[str, Any]:
        """分析数据库性能"""
        conn = await self.pool.acquire()
        
        try:
            # 获取数据库统计信息
            cursor = await conn.execute("SELECT name, value FROM pragma_database_list")
            db_info = await cursor.fetchall()
            
            # 获取表统计
            cursor = await conn.execute("""
                SELECT name, 
                       (SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND tbl_name=m.name) as index_count
                FROM sqlite_master m 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """)
            table_stats = await cursor.fetchall()
            
            # 获取缓存统计
            cache_stats = self.query_cache.get_stats()
            
            return {
                "database_info": [dict(row) for row in db_info],
                "table_stats": [dict(row) for row in table_stats],
                "cache_stats": cache_stats,
                "slow_queries_count": len(self.slow_queries),
                "connection_pool_size": self.pool.pool_size
            }
            
        finally:
            await self.pool.release(conn)
            
    async def optimize_indexes(self) -> List[str]:
        """优化索引建议"""
        suggestions = []
        conn = await self.pool.acquire()
        
        try:
            # 分析慢查询，提取常用的WHERE条件
            where_patterns = {}
            for sq in self.slow_queries:
                query = sq["query"]
                if "WHERE" in query.upper():
                    # 简单提取WHERE后的字段
                    parts = query.upper().split("WHERE")[1].split()
                    for part in parts:
                        if "." not in part and "=" not in part and part.isalpha():
                            where_patterns[part] = where_patterns.get(part, 0) + 1
                            
            # 根据频率建议索引
            for field, count in where_patterns.items():
                if count > 5:
                    suggestions.append(f"建议为字段 {field} 创建索引（在{count}个慢查询中使用）")
                    
            # 检查没有索引的外键
            cursor = await conn.execute("""
                SELECT sql FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """)
            tables = await cursor.fetchall()
            
            for table in tables:
                sql = table[0]
                if "REFERENCES" in sql and "FOREIGN KEY" in sql:
                    # 简单检查是否有对应索引
                    suggestions.append(f"检查表的外键是否有对应索引: {sql[:50]}...")
                    
            return suggestions
            
        finally:
            await self.pool.release(conn)
            
    async def cleanup(self):
        """清理资源"""
        await self.pool.close_all()
        logger.info("数据库优化器已清理")


# 全局实例
db_optimizer = DatabaseOptimizer()