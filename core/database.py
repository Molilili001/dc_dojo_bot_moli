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
                randomize_options BOOLEAN DEFAULT TRUE,
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
        
        # --- 论坛发帖监控配置表 ---
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS forum_post_monitor_configs (
                guild_id TEXT NOT NULL,
                forum_channel_id TEXT NOT NULL,
                auto_role_enabled BOOLEAN DEFAULT FALSE,
                auto_role_id TEXT,
                notify_enabled BOOLEAN DEFAULT TRUE,
                notify_message TEXT,
                mention_role_enabled BOOLEAN DEFAULT FALSE,
                mention_role_id TEXT,
                mention_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, forum_channel_id)
            )
        ''')
        # 索引：按公会与频道快速检索
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_forum_monitor_guild_channel ON forum_post_monitor_configs (guild_id, forum_channel_id)")
        # 索引：按开关快速过滤
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_forum_monitor_guild_enabled ON forum_post_monitor_configs (guild_id, auto_role_enabled, mention_role_enabled, notify_enabled)")
        
        # --- 论坛发帖处理记录表（用于遗漏扫描去重） ---
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS forum_posts_processed (
                thread_id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                forum_channel_id TEXT NOT NULL,
                thread_created_at TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                processed_by TEXT NOT NULL, -- 'event' 或 'scan'
                actions_taken TEXT
            )
        ''')
        # 索引：便于按公会/频道/时间范围查询
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_forum_posts_guild_channel_created ON forum_posts_processed (guild_id, forum_channel_id, thread_created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_forum_posts_processed_at ON forum_posts_processed (processed_at)")
        
        # --- ToDo 列表相关表 ---
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS todo_items (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                list_type TEXT NOT NULL, -- 'person' 或 'channel'
                user_id TEXT,
                channel_id TEXT,
                content TEXT NOT NULL,
                message_link TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_by TEXT NOT NULL,
                created_by_name TEXT,
                created_at TEXT NOT NULL,
                last_modified_by TEXT,
                last_modified_by_name TEXT,
                last_modified_at TEXT,
                deleted BOOLEAN NOT NULL DEFAULT FALSE,
                sort_order INTEGER -- 列表内排序序号（从1开始，新增项目追加到末尾）
            )
        ''')
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_todo_items_person ON todo_items (guild_id, list_type, user_id, sort_order, deleted)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_todo_items_channel ON todo_items (guild_id, list_type, channel_id, sort_order, deleted)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_todo_items_created_at ON todo_items (created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_todo_items_last_modified_at ON todo_items (last_modified_at)")
        
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS todo_reminders (
                reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                reminder_type TEXT NOT NULL, -- 'countdown' 或 'daily'
                countdown_seconds INTEGER,
                daily_time TEXT,
                next_run TEXT,
                created_at TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            )
        ''')
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_todo_reminders_active ON todo_reminders (guild_id, user_id, channel_id, is_active)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_todo_reminders_next_run ON todo_reminders (next_run, is_active)")
        
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS todo_monitor_channels (
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            )
        ''')
        
        # 事件指令权限表（允许的用户或身份组）
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS todo_permissions (
                guild_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                target_type TEXT NOT NULL, -- 'user' 或 'role'
                added_by TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, target_id, target_type)
            )
        ''')
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_todo_permissions_guild_type ON todo_permissions (guild_id, target_type)")

        # --- ToDo 排序字段迁移（为现有数据补充 sort_order，并确保新建追加到末尾） ---
        try:
            # 若旧库无此列，添加之
            await conn.execute("ALTER TABLE todo_items ADD COLUMN sort_order INTEGER")
            logger.info("数据库迁移: 已为 todo_items 添加列 sort_order")
        except Exception:
            # 已存在则忽略
            pass

        # 为缺失 sort_order 的记录补齐（按创建时间升序，组内依次编号）
        try:
            conn.row_factory = aiosqlite.Row

            # 个人列表分组补齐
            personal_rows = []
            async with conn.execute("""
                SELECT item_id, guild_id, user_id, created_at
                FROM todo_items
                WHERE list_type = 'person' AND deleted = 0 AND sort_order IS NULL
                ORDER BY guild_id ASC, user_id ASC, created_at ASC
            """) as cursor:
                personal_rows = await cursor.fetchall()

            if personal_rows:
                groups = {}
                for r in personal_rows:
                    key = (r['guild_id'], r['user_id'])
                    groups.setdefault(key, []).append(r)
                for key, rows in groups.items():
                    updates = [(i, rows[i-1]['item_id']) for i in range(1, len(rows)+1)]
                    await conn.executemany(
                        "UPDATE todo_items SET sort_order = ? WHERE item_id = ?",
                        updates
                    )

            # 频道列表分组补齐
            channel_rows = []
            async with conn.execute("""
                SELECT item_id, guild_id, channel_id, created_at
                FROM todo_items
                WHERE list_type = 'channel' AND deleted = 0 AND sort_order IS NULL
                ORDER BY guild_id ASC, channel_id ASC, created_at ASC
            """) as cursor:
                channel_rows = await cursor.fetchall()

            if channel_rows:
                groups2 = {}
                for r in channel_rows:
                    key = (r['guild_id'], r['channel_id'])
                    groups2.setdefault(key, []).append(r)
                for key, rows in groups2.items():
                    updates = [(i, rows[i-1]['item_id']) for i in range(1, len(rows)+1)]
                    await conn.executemany(
                        "UPDATE todo_items SET sort_order = ? WHERE item_id = ?",
                        updates
                    )
        except Exception as e:
            logger.error(f"排序字段迁移失败: {e}", exc_info=True)
        
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
        
        # --- 反馈系统相关表 ---
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS feedback_submissions (
                submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                type TEXT NOT NULL, -- 'anonymous' 或 'named'
                content TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                message_id TEXT, -- 发送到目标频道的消息ID
                created_at TEXT NOT NULL
            )
        ''')
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_submissions_guild_user_date ON feedback_submissions (guild_id, user_id, created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_submissions_guild_date ON feedback_submissions (guild_id, created_at)")

        # （可选）反馈配置表：用于运行时命令覆盖配置并跨重启保留
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS feedback_configs (
                guild_id TEXT PRIMARY KEY,
                target_channel_id TEXT,
                allowed_role_ids TEXT, -- JSON数组
                panel_texts TEXT,      -- JSON（标题、说明、按钮、提示文案）
                limits TEXT,           -- JSON（min_total_messages、window_seconds等）
                runtime_counters TEXT, -- JSON（是否持久化快照、间隔）
                updated_at TEXT
            )
        ''')

        # （可选）消息计数快照表：若开启持久化快照则使用
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS message_counter_snapshots (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                total_messages INTEGER DEFAULT 0,
                window_bucket TEXT, -- 例如按小时桶 YYYY-MM-DDTHH
                window_messages INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, window_bucket)
            )
        ''')
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_snapshots_guild_user ON message_counter_snapshots (guild_id, user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_snapshots_updated ON message_counter_snapshots (updated_at)")
        
        # --- 帖子自定义命令系统相关表 ---
        # 命令规则表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS thread_command_rules (
                rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                thread_id TEXT,
                channel_id TEXT,
                category_id TEXT,
                forum_channel_id TEXT,
                action_type TEXT NOT NULL,
                reply_content TEXT,
                reply_embed_json TEXT,
                delete_trigger_delay INTEGER,
                delete_reply_delay INTEGER,
                add_reaction TEXT,
                user_reply_cooldown INTEGER,
                user_delete_cooldown INTEGER,
                thread_reply_cooldown INTEGER,
                thread_delete_cooldown INTEGER,
                channel_reply_cooldown INTEGER,
                channel_delete_cooldown INTEGER,
                is_enabled BOOLEAN DEFAULT TRUE,
                priority INTEGER DEFAULT 0,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        
        # --- 数据迁移：为 thread_command_rules 添加 channel_id 和 category_id 列（支持频道和分类规则） ---
        # 注意：这必须在创建索引之前运行，以确保旧数据库能正确迁移
        try:
            await conn.execute("ALTER TABLE thread_command_rules ADD COLUMN channel_id TEXT")
            logger.info("数据库迁移: 已为 thread_command_rules 添加列 channel_id")
        except Exception:
            # 已存在则忽略
            pass
        
        try:
            await conn.execute("ALTER TABLE thread_command_rules ADD COLUMN category_id TEXT")
            logger.info("数据库迁移: 已为 thread_command_rules 添加列 category_id")
        except Exception:
            # 已存在则忽略
            pass
        
        # 创建索引（必须在列存在后创建）
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcr_guild_scope ON thread_command_rules (guild_id, scope)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcr_thread ON thread_command_rules (thread_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcr_channel ON thread_command_rules (channel_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcr_category ON thread_command_rules (category_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcr_guild_enabled ON thread_command_rules (guild_id, is_enabled)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcr_lookup ON thread_command_rules (guild_id, scope, is_enabled, priority DESC)")
        
        # 触发器表（支持多触发器）
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS thread_command_triggers (
                trigger_id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                trigger_text TEXT NOT NULL,
                trigger_mode TEXT DEFAULT 'exact',
                is_enabled BOOLEAN DEFAULT TRUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY (rule_id) REFERENCES thread_command_rules(rule_id) ON DELETE CASCADE
            )
        ''')
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tct_rule ON thread_command_triggers (rule_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tct_text_mode ON thread_command_triggers (trigger_text, trigger_mode)")
        
        # 权限配置表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS thread_command_permissions (
                guild_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                permission_level TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, target_id, target_type, permission_level)
            )
        ''')
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcp_guild_type ON thread_command_permissions (guild_id, target_type)")
        
        # 使用统计表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS thread_command_stats (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                rule_id INTEGER,
                trigger_text TEXT,
                usage_count INTEGER DEFAULT 0,
                last_used_at TEXT,
                PRIMARY KEY (guild_id, user_id, rule_id)
            )
        ''')
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcs_guild_user ON thread_command_stats (guild_id, user_id)")
        
        # 全服配置表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS thread_command_server_config (
                guild_id TEXT PRIMARY KEY,
                is_enabled BOOLEAN DEFAULT TRUE,
                allow_thread_owner_config BOOLEAN DEFAULT TRUE,
                allowed_forum_channels TEXT,
                default_delete_trigger_delay INTEGER,
                default_delete_reply_delay INTEGER,
                default_user_reply_cooldown INTEGER DEFAULT 60,
                default_user_delete_cooldown INTEGER DEFAULT 0,
                default_thread_reply_cooldown INTEGER DEFAULT 30,
                default_thread_delete_cooldown INTEGER DEFAULT 0,
                default_channel_reply_cooldown INTEGER DEFAULT 10,
                default_channel_delete_cooldown INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        
        # --- 数据迁移：为 thread_command_server_config 添加 allowed_forum_channels 列 ---
        try:
            await conn.execute("ALTER TABLE thread_command_server_config ADD COLUMN allowed_forum_channels TEXT")
            logger.info("数据库迁移: 已为 thread_command_server_config 添加列 allowed_forum_channels")
        except Exception:
            # 已存在则忽略
            pass
        
        # 消息处理日志表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS thread_command_message_log (
                message_id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                thread_id TEXT,
                user_id TEXT NOT NULL,
                rule_id INTEGER,
                status TEXT NOT NULL,
                action_taken TEXT,
                reply_message_id TEXT,
                scheduled_delete_at TEXT,
                deleted_at TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcml_status_time ON thread_command_message_log (status, created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcml_scheduled_delete ON thread_command_message_log (scheduled_delete_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcml_guild_channel ON thread_command_message_log (guild_id, channel_id, created_at)")
        
        # 限流状态表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS thread_command_rate_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                rule_id INTEGER NOT NULL,
                limit_type TEXT NOT NULL,
                limit_target TEXT NOT NULL,
                action_type TEXT NOT NULL,
                last_triggered_at TEXT NOT NULL,
                trigger_count INTEGER DEFAULT 1,
                UNIQUE(guild_id, rule_id, limit_type, limit_target, action_type)
            )
        ''')
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcrl_lookup ON thread_command_rate_limits (guild_id, rule_id, limit_type, limit_target, action_type)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tcrl_time ON thread_command_rate_limits (last_triggered_at)")
        
        # --- 数据修复：统一 randomize_options 为 TRUE（仅影响历史为 NULL 或 0 的记录） ---
        try:
            await conn.execute("""
                UPDATE gyms
                SET randomize_options = 1
                WHERE randomize_options IS NULL OR randomize_options = 0
            """)
            logger.info("数据迁移: 已将 gyms.randomize_options 从 NULL/0 标准化为 TRUE")
        except Exception as e:
            logger.error(f"数据迁移(randomize_options)失败: {e}", exc_info=True)

        logger.info("数据库表结构设置完成，数据迁移检查完成")

# ===== Legacy DB config (module-level) =====
def get_legacy_db_path() -> Optional[Path]:
    """
    从配置文件读取可选的“旧库”路径，用于数据互通。
    配置文件: [core/constants.py.CONFIG_PATH](core/constants.py:30)
    约定键:
      - enableLegacySync: bool，开启旧库同步
      - legacyDatabasePath: str，旧库绝对路径（例如 'F:\\dcbot\\progress.db' 或 VPS 上的绝对路径）
    返回:
      - Path 或 None（未启用或未配置时）
    """
    try:
        from core.constants import CONFIG_PATH
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open('r', encoding='utf-8') as f:
                conf = json.load(f)
            enable = conf.get('enableLegacySync', False)
            path_str = conf.get('legacyDatabasePath')
            if enable and path_str:
                p = Path(path_str)
                return p
    except Exception as e:
        logger.warning(f"读取旧库路径失败（忽略并继续）: {e}")
    return None


# 全局数据库管理器实例
db_manager = DatabaseManager()

# 明确导出，避免在某些环境下的部分初始化导致的导入混淆
__all__ = ['DatabaseManager', 'db_manager', 'get_legacy_db_path']