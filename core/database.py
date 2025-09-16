import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from core.constants import DATABASE_PATH, DATABASE_TIMEOUT, BEIJING_TZ
from utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """数据库管理器，提供连接池和基础查询功能"""
    
    def __init__(self, db_path: Optional[Path] = None):
        """
        初始化数据库管理器
        
        Args:
            db_path: 数据库文件路径，默认使用constants中的配置
        """
        self.db_path = db_path or DATABASE_PATH
        self._initialized = False
    
    @staticmethod
    def dict_factory(cursor: aiosqlite.Cursor, row: aiosqlite.Row) -> Dict[str, Any]:
        """
        将SQLite Row对象转换为字典
        
        Args:
            cursor: 游标对象
            row: 行对象
            
        Returns:
            字典格式的行数据
        """
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
    
    @staticmethod
    def dict_row(cursor: aiosqlite.Cursor, row: aiosqlite.Row) -> Dict[str, Any]:
        """dict_factory的别名，用于兼容"""
        return DatabaseManager.dict_factory(cursor, row)
        
    async def initialize(self) -> None:
        """初始化数据库，创建必要的表结构"""
        if self._initialized:
            return
            
        # 确保数据目录存在
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with self.get_connection() as conn:
            await self._setup_database(conn)
            await conn.commit()
        
        self._initialized = True
        logger.info(f"数据库初始化完成: {self.db_path}")
    
    @asynccontextmanager
    async def get_connection(self):
        """
        获取数据库连接的上下文管理器
        
        Yields:
            数据库连接对象
        """
        conn = await aiosqlite.connect(
            self.db_path,
            timeout=DATABASE_TIMEOUT
        )
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()
    
    async def execute(self, query: str, params: Optional[Tuple] = None) -> int:
        """
        执行SQL语句（INSERT, UPDATE, DELETE）
        
        Args:
            query: SQL查询语句
            params: 查询参数
            
        Returns:
            受影响的行数
        """
        async with self.get_connection() as conn:
            cursor = await conn.execute(query, params or ())
            await conn.commit()
            return cursor.rowcount
    
    async def executemany(self, query: str, params_list: List[Tuple]) -> int:
        """
        批量执行SQL语句
        
        Args:
            query: SQL查询语句
            params_list: 参数列表
            
        Returns:
            受影响的总行数
        """
        async with self.get_connection() as conn:
            cursor = await conn.executemany(query, params_list)
            await conn.commit()
            return cursor.rowcount
    
    async def fetchone(self, query: str, params: Optional[Tuple] = None) -> Optional[Dict[str, Any]]:
        """
        获取单条记录
        
        Args:
            query: SQL查询语句
            params: 查询参数
            
        Returns:
            记录字典或None
        """
        async with self.get_connection() as conn:
            async with conn.execute(query, params or ()) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    
    async def fetchall(self, query: str, params: Optional[Tuple] = None) -> List[Dict[str, Any]]:
        """
        获取所有记录
        
        Args:
            query: SQL查询语句
            params: 查询参数
            
        Returns:
            记录字典列表
        """
        async with self.get_connection() as conn:
            async with conn.execute(query, params or ()) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def _setup_database(self, conn: aiosqlite.Connection) -> None:
        """
        设置数据库表结构（从原bot.py迁移）
        
        Args:
            conn: 数据库连接
        """
        # 用户进度表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_progress (
                user_id TEXT,
                guild_id TEXT,
                gym_id TEXT,
                completed BOOLEAN DEFAULT TRUE,
                PRIMARY KEY (user_id, guild_id, gym_id)
            )
        ''')
        
        # 挑战面板表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS challenge_panels (
                message_id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                role_to_add_id TEXT,
                role_to_remove_id TEXT,
                role_to_add_ids TEXT,
                role_to_remove_ids TEXT,
                associated_gyms TEXT,
                blacklist_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                completion_threshold INTEGER,
                prerequisite_gyms TEXT,
                is_ultimate_gym BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # 道馆馆主权限表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS gym_masters (
                guild_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                permission TEXT NOT NULL,
                PRIMARY KEY (guild_id, target_id, permission)
            )
        ''')
        
        # 道馆表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS gyms (
                gym_id TEXT,
                guild_id TEXT,
                name TEXT,
                description TEXT,
                tutorial TEXT,
                questions TEXT,
                questions_to_ask INTEGER,
                allowed_mistakes INTEGER,
                badge_image_url TEXT,
                badge_description TEXT,
                is_enabled BOOLEAN DEFAULT TRUE,
                randomize_options BOOLEAN DEFAULT FALSE,
                PRIMARY KEY (guild_id, gym_id)
            )
        ''')
        
        # 挑战失败记录表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS challenge_failures (
                user_id TEXT,
                guild_id TEXT,
                gym_id TEXT,
                failure_count INTEGER DEFAULT 0,
                banned_until TEXT,
                PRIMARY KEY (user_id, guild_id, gym_id)
            )
        ''')
        
        # 道馆审计日志表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS gym_audit_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                gym_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
        
        # 作弊黑名单表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS cheating_blacklist (
                guild_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                reason TEXT,
                added_by TEXT,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (guild_id, target_id)
            )
        ''')
        
        # 挑战封禁名单表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS challenge_ban_list (
                guild_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                reason TEXT,
                added_by TEXT,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (guild_id, target_id)
            )
        ''')
        
        # 究极道馆排行榜表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS ultimate_gym_leaderboard (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                completion_time_seconds REAL NOT NULL,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        
        # 排行榜面板表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS leaderboard_panels (
                message_id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                title TEXT,
                description TEXT
            )
        ''')
        
        # 已领取奖励记录表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS claimed_role_rewards (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, role_id)
            )
        ''')
        
        # 创建索引以提高查询性能
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_progress_user_guild ON user_progress (user_id, guild_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_challenge_failures_user_guild ON challenge_failures (user_id, guild_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_gyms_guild ON gyms (guild_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_gym_masters_guild_target ON gym_masters (guild_id, target_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_cheating_blacklist_guild_target ON cheating_blacklist (guild_id, target_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_challenge_ban_list_guild_target ON challenge_ban_list (guild_id, target_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_claimed_rewards_guild_user_role ON claimed_role_rewards (guild_id, user_id, role_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ultimate_leaderboard_guild_time ON ultimate_gym_leaderboard (guild_id, completion_time_seconds)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_leaderboard_panels_guild ON leaderboard_panels (guild_id)")
        
        # 跨bot同步记录表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS cross_bot_sync_log (
                sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_bot_id TEXT NOT NULL,
                target_guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                sync_type TEXT NOT NULL,
                sync_data TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                timestamp TEXT NOT NULL
            )
        ''')
        
        # 自动身份组管理规则表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS auto_role_rules (
                rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                rule_name TEXT NOT NULL,
                rule_type TEXT NOT NULL,
                target_roles TEXT NOT NULL,
                conditions TEXT,
                action TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                is_enabled BOOLEAN DEFAULT TRUE,
                created_by TEXT,
                created_at TEXT NOT NULL
            )
        ''')
        
        # bot联动配置表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_sync_config (
                config_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                bot_id TEXT NOT NULL,
                sync_enabled BOOLEAN DEFAULT TRUE,
                sync_modes TEXT,
                custom_settings TEXT,
                last_sync TEXT,
                UNIQUE(guild_id, bot_id)
            )
        ''')
        
        # 处罚同步队列表（用于失败重试）
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS punishment_sync_queue (
                queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                guild_id TEXT NOT NULL,
                punishment_type TEXT NOT NULL,
                reason TEXT,
                source_bot_id TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                processed_at TEXT
            )
        ''')
        
        # 创建联动相关索引
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_log_user ON cross_bot_sync_log (user_id, timestamp)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_log_guild ON cross_bot_sync_log (target_guild_id, timestamp)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_role_guild ON auto_role_rules (guild_id, is_enabled)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bot_sync_config ON bot_sync_config (guild_id, sync_enabled)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_punishment_queue_status ON punishment_sync_queue (status, created_at)")
        
        # --- 数据迁移：兼容旧版本面板数据 ---
        # 确保blacklist_enabled字段有默认值
        await conn.execute("UPDATE challenge_panels SET blacklist_enabled = 1 WHERE blacklist_enabled IS NULL")
        
        # 迁移单个role_id到多个role_ids的JSON格式
        conn.row_factory = aiosqlite.Row
        panels_to_migrate = []
        async with conn.execute("SELECT message_id, role_to_add_id, role_to_remove_id, role_to_add_ids, role_to_remove_ids FROM challenge_panels") as cursor:
            panels_to_migrate = await cursor.fetchall()
        
        if panels_to_migrate:
            for panel in panels_to_migrate:
                # 迁移role_to_add_id到role_to_add_ids
                if panel['role_to_add_id'] and not panel['role_to_add_ids']:
                    new_json = json.dumps([panel['role_to_add_id']])
                    await conn.execute("UPDATE challenge_panels SET role_to_add_ids = ? WHERE message_id = ?",
                                     (new_json, panel['message_id']))
                    logger.info(f"数据迁移: 已迁移面板 {panel['message_id']} 的role_to_add_id")
                
                # 迁移role_to_remove_id到role_to_remove_ids
                if panel['role_to_remove_id'] and not panel['role_to_remove_ids']:
                    new_json = json.dumps([panel['role_to_remove_id']])
                    await conn.execute("UPDATE challenge_panels SET role_to_remove_ids = ? WHERE message_id = ?",
                                     (new_json, panel['message_id']))
                    logger.info(f"数据迁移: 已迁移面板 {panel['message_id']} 的role_to_remove_id")
        
        # 重置row_factory
        conn.row_factory = None
        
        logger.info("数据库表结构设置完成，数据迁移检查完成")


# 全局数据库管理器实例
db_manager = DatabaseManager()