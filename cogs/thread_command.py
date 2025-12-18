"""
模块名称: thread_command.py
功能描述: 帖子自定义命令系统 - 支持自定义消息检测和处理
作者: Bot重构项目
创建日期: 2024
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.base_cog import BaseCog
from core.models import (
    ThreadCommandTrigger,
    ThreadCommandRule,
    ThreadCommandServerConfig,
    ThreadCommandPermission,
)
from utils.logger import get_logger
from views.thread_command_views import (
    RuleCreateModal,
    RuleEditModal,
    TriggerAddModal,
    ServerConfigView,
    RuleListView,
    RuleDetailView,
    DeleteConfirmView,
    QuickSetupView,
    PermissionManageView,
    PermissionAddModal,
    ACTION_TYPE_DISPLAY,
    ACTION_TYPE_MAP,
    MATCH_MODE_DISPLAY,
    MATCH_MODE_MAP,
)

logger = get_logger(__name__)


# ==================== 配置常量 ====================

CACHE_CONFIG = {
    'server_rules_ttl': 3600,       # 全服规则缓存1小时
    'thread_rules_ttl': 1800,       # 帖子规则缓存30分钟
    'server_config_ttl': 3600,      # 服务器配置缓存1小时
    'max_cached_threads': 200,      # 最多缓存200个帖子的规则
    'max_cached_guilds': 10,        # 最多缓存10个服务器的规则
}

SCAN_CONFIG = {
    'enabled': True,                # 默认开启扫描
    'interval_seconds': 600,        # 每10分钟扫描一次
    'lookback_minutes': 15,         # 回看15分钟
    'max_messages_per_scan': 30,    # 单次扫描最大消息数
    'max_threads_per_scan': 5,      # 单次扫描最大帖子数
}

HISTORICAL_MESSAGE_CONFIG = {
    'threshold_seconds': 300,       # 超过5分钟视为历史消息
    'silent_mode': True,            # 启用静默模式
    'allowed_actions': ['delete', 'react'],
    'skip_actions': ['reply', 'mention'],
}

RESOURCE_LIMITS = {
    'max_server_rules': 50,         # 每服务器最大全服规则数
    'max_thread_rules': 10,         # 每帖子最大规则数
    'max_triggers_per_rule': 10,    # 每规则最大触发器数
    'max_trigger_length': 100,      # 触发文本最大长度
    'max_reply_length': 2000,       # 回复内容最大长度
    'max_pending_deletes': 1000,    # 待删除队列最大长度
}

# 默认回顶规则配置
DEFAULT_GO_TO_TOP_RULE = {
    'scope': 'server',
    'action_type': 'go_to_top',
    'reply_content': None,
    'delete_trigger_delay': 300,
    'delete_reply_delay': 300,
    'add_reaction': '✅',
    'priority': 0,
    'triggers': [
        {'text': '/回顶', 'mode': 'exact'},
        {'text': '／回顶', 'mode': 'exact'},
        {'text': '回顶', 'mode': 'exact'},
    ]
}

# 范围中文映射
SCOPE_DISPLAY = {
    'server': '全服',
    'thread': '帖子',
}


# ==================== 缓存管理器 ====================

class RuleCacheManager:
    """规则缓存管理器 - Write-through Cache 策略"""
    
    def __init__(self, db_manager):
        self.db = db_manager
        self.server_rules_ttl = CACHE_CONFIG['server_rules_ttl']
        self.thread_rules_ttl = CACHE_CONFIG['thread_rules_ttl']
        self.server_config_ttl = CACHE_CONFIG['server_config_ttl']
        self.max_cached_threads = CACHE_CONFIG['max_cached_threads']
        self.max_cached_guilds = CACHE_CONFIG['max_cached_guilds']
        
        # 缓存存储: {key: (data, expire_time)}
        self._server_rules: Dict[str, Tuple[List[ThreadCommandRule], float]] = {}
        self._thread_rules: Dict[str, Tuple[List[ThreadCommandRule], float]] = {}
        self._server_config: Dict[str, Tuple[ThreadCommandServerConfig, float]] = {}
        self._permissions: Dict[str, Tuple[List[ThreadCommandPermission], float]] = {}
    
    # ========== 读取方法 ==========
    
    async def get_server_rules(self, guild_id: str) -> List[ThreadCommandRule]:
        """获取全服规则，优先读缓存"""
        cached = self._server_rules.get(guild_id)
        if cached and time.time() < cached[1]:
            return cached[0]
        
        rules = await self._load_server_rules_from_db(guild_id)
        self._server_rules[guild_id] = (rules, time.time() + self.server_rules_ttl)
        self._enforce_cache_limits()
        return rules
    
    async def get_thread_rules(self, thread_id: str) -> List[ThreadCommandRule]:
        """获取帖子规则，优先读缓存"""
        cached = self._thread_rules.get(thread_id)
        if cached and time.time() < cached[1]:
            return cached[0]
        
        rules = await self._load_thread_rules_from_db(thread_id)
        self._thread_rules[thread_id] = (rules, time.time() + self.thread_rules_ttl)
        self._enforce_cache_limits()
        return rules
    
    async def get_server_config(self, guild_id: str) -> Optional[ThreadCommandServerConfig]:
        """获取服务器配置，优先读缓存"""
        cached = self._server_config.get(guild_id)
        if cached and time.time() < cached[1]:
            return cached[0]
        
        config = await self._load_server_config_from_db(guild_id)
        if config:
            self._server_config[guild_id] = (config, time.time() + self.server_config_ttl)
        return config
    
    async def get_permissions(self, guild_id: str) -> List[ThreadCommandPermission]:
        """获取服务器权限配置"""
        cached = self._permissions.get(guild_id)
        if cached and time.time() < cached[1]:
            return cached[0]
        
        perms = await self._load_permissions_from_db(guild_id)
        self._permissions[guild_id] = (perms, time.time() + self.server_config_ttl)
        return perms
    
    # ========== 数据库加载方法 ==========
    
    async def _load_server_rules_from_db(self, guild_id: str) -> List[ThreadCommandRule]:
        """从数据库加载全服规则"""
        rules_data = await self.db.fetchall(
            """SELECT * FROM thread_command_rules 
               WHERE guild_id = ? AND scope = 'server' AND is_enabled = 1
               ORDER BY priority DESC""",
            (guild_id,)
        )
        
        rules = []
        for row in rules_data:
            triggers_data = await self.db.fetchall(
                "SELECT * FROM thread_command_triggers WHERE rule_id = ? AND is_enabled = 1",
                (row['rule_id'],)
            )
            triggers = [ThreadCommandTrigger.from_row(t) for t in triggers_data]
            rule = ThreadCommandRule.from_row(row, triggers)
            rules.append(rule)
        
        return rules
    
    async def _load_thread_rules_from_db(self, thread_id: str) -> List[ThreadCommandRule]:
        """从数据库加载帖子规则"""
        rules_data = await self.db.fetchall(
            """SELECT * FROM thread_command_rules 
               WHERE thread_id = ? AND scope = 'thread' AND is_enabled = 1
               ORDER BY priority DESC""",
            (thread_id,)
        )
        
        rules = []
        for row in rules_data:
            triggers_data = await self.db.fetchall(
                "SELECT * FROM thread_command_triggers WHERE rule_id = ? AND is_enabled = 1",
                (row['rule_id'],)
            )
            triggers = [ThreadCommandTrigger.from_row(t) for t in triggers_data]
            rule = ThreadCommandRule.from_row(row, triggers)
            rules.append(rule)
        
        return rules
    
    async def _load_server_config_from_db(self, guild_id: str) -> Optional[ThreadCommandServerConfig]:
        """从数据库加载服务器配置"""
        row = await self.db.fetchone(
            "SELECT * FROM thread_command_server_config WHERE guild_id = ?",
            (guild_id,)
        )
        if row:
            return ThreadCommandServerConfig.from_row(row)
        return None
    
    async def _load_permissions_from_db(self, guild_id: str) -> List[ThreadCommandPermission]:
        """从数据库加载权限配置"""
        rows = await self.db.fetchall(
            "SELECT * FROM thread_command_permissions WHERE guild_id = ?",
            (guild_id,)
        )
        return [ThreadCommandPermission.from_row(r) for r in rows]
    
    # ========== 写入方法（刷新缓存） ==========
    
    async def refresh_server_rules(self, guild_id: str):
        """刷新服务器规则缓存"""
        rules = await self._load_server_rules_from_db(guild_id)
        self._server_rules[guild_id] = (rules, time.time() + self.server_rules_ttl)
    
    async def refresh_thread_rules(self, thread_id: str):
        """刷新帖子规则缓存"""
        rules = await self._load_thread_rules_from_db(thread_id)
        self._thread_rules[thread_id] = (rules, time.time() + self.thread_rules_ttl)
    
    async def refresh_server_config(self, guild_id: str):
        """刷新服务器配置缓存"""
        config = await self._load_server_config_from_db(guild_id)
        if config:
            self._server_config[guild_id] = (config, time.time() + self.server_config_ttl)
        elif guild_id in self._server_config:
            del self._server_config[guild_id]
    
    async def refresh_permissions(self, guild_id: str):
        """刷新权限缓存"""
        perms = await self._load_permissions_from_db(guild_id)
        self._permissions[guild_id] = (perms, time.time() + self.server_config_ttl)
    
    def invalidate_thread(self, thread_id: str):
        """使帖子缓存失效"""
        if thread_id in self._thread_rules:
            del self._thread_rules[thread_id]
    
    def invalidate_guild(self, guild_id: str):
        """使服务器相关缓存失效"""
        if guild_id in self._server_rules:
            del self._server_rules[guild_id]
        if guild_id in self._server_config:
            del self._server_config[guild_id]
        if guild_id in self._permissions:
            del self._permissions[guild_id]
    
    # ========== 缓存管理 ==========
    
    def _enforce_cache_limits(self):
        """强制执行缓存容量限制"""
        # LRU淘汰：按过期时间排序，移除最早过期的
        if len(self._thread_rules) > self.max_cached_threads:
            sorted_keys = sorted(
                self._thread_rules.keys(),
                key=lambda k: self._thread_rules[k][1]
            )
            for key in sorted_keys[:len(self._thread_rules) - self.max_cached_threads]:
                del self._thread_rules[key]
        
        if len(self._server_rules) > self.max_cached_guilds:
            sorted_keys = sorted(
                self._server_rules.keys(),
                key=lambda k: self._server_rules[k][1]
            )
            for key in sorted_keys[:len(self._server_rules) - self.max_cached_guilds]:
                del self._server_rules[key]
    
    def clear_expired(self):
        """清理过期缓存"""
        now = time.time()
        self._server_rules = {k: v for k, v in self._server_rules.items() if v[1] > now}
        self._thread_rules = {k: v for k, v in self._thread_rules.items() if v[1] > now}
        self._server_config = {k: v for k, v in self._server_config.items() if v[1] > now}
        self._permissions = {k: v for k, v in self._permissions.items() if v[1] > now}


# ==================== 限流管理器 ====================

class RateLimitManager:
    """限流状态管理器"""
    
    def __init__(self):
        # 内存限流状态: {(guild_id, rule_id, limit_type, target, action): last_triggered_time}
        self._limits: Dict[Tuple[str, int, str, str, str], float] = {}
        self._max_entries = 2000
    
    def check_rate_limit(
        self,
        guild_id: str,
        rule_id: int,
        limit_type: str,  # 'user', 'thread', 'channel'
        target_id: str,
        action_type: str,  # 'reply', 'delete'
        cooldown_seconds: int
    ) -> bool:
        """检查是否在限流期内，返回True表示允许执行"""
        if cooldown_seconds <= 0:
            return True
        
        key = (guild_id, rule_id, limit_type, target_id, action_type)
        last_triggered = self._limits.get(key, 0)
        now = time.time()
        
        if now - last_triggered >= cooldown_seconds:
            return True
        return False
    
    def record_trigger(
        self,
        guild_id: str,
        rule_id: int,
        limit_type: str,
        target_id: str,
        action_type: str
    ):
        """记录触发时间"""
        key = (guild_id, rule_id, limit_type, target_id, action_type)
        self._limits[key] = time.time()
        
        # 容量限制
        if len(self._limits) > self._max_entries:
            self._cleanup_old_entries()
    
    def _cleanup_old_entries(self):
        """清理旧条目"""
        now = time.time()
        # 保留最近1小时的记录
        self._limits = {k: v for k, v in self._limits.items() if now - v < 3600}


# ==================== 统计缓冲区 ====================

class StatsBuffer:
    """统计写入缓冲区"""
    
    def __init__(self, db_manager, flush_interval: int = 30, batch_size: int = 100):
        self.db = db_manager
        self.buffer: List[Tuple[str, str, int, str, str]] = []
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self._last_flush = time.time()
    
    async def increment(self, guild_id: str, user_id: str, rule_id: int, trigger_text: str):
        """添加统计记录到缓冲区"""
        now = datetime.utcnow().isoformat()
        self.buffer.append((guild_id, user_id, rule_id, trigger_text, now))
        
        if len(self.buffer) >= self.batch_size:
            await self.flush()
    
    async def flush(self):
        """批量写入数据库"""
        if not self.buffer:
            return
        
        try:
            for guild_id, user_id, rule_id, trigger_text, now in self.buffer:
                await self.db.execute(
                    """INSERT INTO thread_command_stats 
                       (guild_id, user_id, rule_id, trigger_text, usage_count, last_used_at)
                       VALUES (?, ?, ?, ?, 1, ?)
                       ON CONFLICT(guild_id, user_id, rule_id) 
                       DO UPDATE SET usage_count = usage_count + 1, last_used_at = ?""",
                    (guild_id, user_id, rule_id, trigger_text, now, now)
                )
            self.buffer.clear()
            self._last_flush = time.time()
        except Exception as e:
            logger.error(f"统计写入失败: {e}")
    
    async def maybe_flush(self):
        """检查是否需要刷新"""
        if time.time() - self._last_flush >= self.flush_interval:
            await self.flush()


# ==================== 主 Cog ====================

class ThreadCommandCog(BaseCog):
    """帖子自定义命令系统"""
    
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.cache = RuleCacheManager(self.db)
        self.rate_limiter = RateLimitManager()
        self.stats_buffer = StatsBuffer(self.db)
        
        # 待删除消息队列: [(message_id, channel_id, delete_at)]
        self._pending_deletes: List[Tuple[int, int, float]] = []
    
    async def cog_load(self) -> None:
        """Cog加载时启动后台任务"""
        await super().cog_load()
        self.cleanup_task.start()
        self.stats_flush_task.start()
        self.cache_cleanup_task.start()
        self.init_default_rules_task.start()
        self.logger.info("帖子自定义命令系统已加载")
    
    async def cog_unload(self) -> None:
        """Cog卸载时停止后台任务"""
        self.cleanup_task.cancel()
        self.stats_flush_task.cancel()
        self.cache_cleanup_task.cancel()
        if self.init_default_rules_task.is_running():
            self.init_default_rules_task.cancel()
        await self.stats_buffer.flush()
        await super().cog_unload()
    
    # ==================== 后台任务 ====================
    
    @tasks.loop(seconds=30)
    async def cleanup_task(self):
        """定期清理待删除消息"""
        now = time.time()
        to_delete = [(mid, cid) for mid, cid, delete_at in self._pending_deletes if delete_at <= now]
        self._pending_deletes = [(mid, cid, delete_at) for mid, cid, delete_at in self._pending_deletes if delete_at > now]
        
        for message_id, channel_id in to_delete:
            try:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    message = await channel.fetch_message(message_id)
                    await message.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                pass
            except Exception as e:
                self.logger.debug(f"删除消息失败: {e}")
    
    @tasks.loop(seconds=30)
    async def stats_flush_task(self):
        """定期刷新统计缓冲区"""
        await self.stats_buffer.maybe_flush()
    
    @tasks.loop(minutes=10)
    async def cache_cleanup_task(self):
        """定期清理过期缓存"""
        self.cache.clear_expired()
    
    @tasks.loop(count=1)
    async def init_default_rules_task(self):
        """初始化默认回顶规则（仅运行一次）"""
        await self.bot.wait_until_ready()
        
        self.logger.info("开始为所有服务器初始化默认回顶规则...")
        initialized_count = 0
        
        for guild in self.bot.guilds:
            guild_id = str(guild.id)
            
            # 检查是否已有回顶规则
            existing = await self.db.fetchone(
                "SELECT * FROM thread_command_rules WHERE guild_id = ? AND action_type = 'go_to_top'",
                (guild_id,)
            )
            
            if not existing:
                try:
                    # 创建默认回顶规则
                    await self.create_default_huiding_rule(guild_id, str(self.bot.user.id))
                    initialized_count += 1
                    self.logger.info(f"已为服务器 {guild.name} ({guild_id}) 创建默认回顶规则")
                except Exception as e:
                    self.logger.error(f"为服务器 {guild_id} 创建默认回顶规则失败: {e}")
        
        self.logger.info(f"默认回顶规则初始化完成，共初始化 {initialized_count} 个服务器")
    
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """当Bot加入新服务器时，自动创建默认回顶规则"""
        guild_id = str(guild.id)
        
        # 检查是否已有回顶规则
        existing = await self.db.fetchone(
            "SELECT * FROM thread_command_rules WHERE guild_id = ? AND action_type = 'go_to_top'",
            (guild_id,)
        )
        
        if not existing:
            try:
                await self.create_default_huiding_rule(guild_id, str(self.bot.user.id))
                self.logger.info(f"已为新加入的服务器 {guild.name} ({guild_id}) 创建默认回顶规则")
            except Exception as e:
                self.logger.error(f"为新服务器 {guild_id} 创建默认回顶规则失败: {e}")
    
    # ==================== 消息监听 ====================
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """监听消息事件"""
        # 快速过滤
        if message.author.bot:
            return
        if not message.guild:
            return
        if not message.content:
            return
        
        guild_id = str(message.guild.id)
        
        # 检查全服开关
        config = await self.cache.get_server_config(guild_id)
        if config and not config.is_enabled:
            return
        
        # 检查论坛频道限制（仅对帖子内消息生效）
        if isinstance(message.channel, discord.Thread):
            parent = message.channel.parent
            if parent and isinstance(parent, discord.ForumChannel):
                if config and config.allowed_forum_channels:
                    try:
                        allowed_channels = json.loads(config.allowed_forum_channels)
                        if allowed_channels and str(parent.id) not in allowed_channels:
                            # 当前帖子所在论坛不在允许列表中
                            return
                    except (json.JSONDecodeError, TypeError):
                        pass
        
        # 获取规则并匹配
        await self._process_message(message, config, is_scan=False)
    
    async def _process_message(
        self,
        message: discord.Message,
        config: Optional[ThreadCommandServerConfig],
        is_scan: bool = False
    ):
        """处理消息匹配和动作执行"""
        guild_id = str(message.guild.id)
        content = message.content.strip()
        
        # 优先检查帖子规则
        matched_rule = None
        
        # 检查是否在帖子内
        if isinstance(message.channel, discord.Thread):
            thread_id = str(message.channel.id)
            thread_rules = await self.cache.get_thread_rules(thread_id)
            for rule in thread_rules:
                if rule.match(content):
                    matched_rule = rule
                    break
        
        # 如果没有匹配帖子规则，检查全服规则
        if not matched_rule:
            server_rules = await self.cache.get_server_rules(guild_id)
            for rule in server_rules:
                if rule.match(content):
                    matched_rule = rule
                    break
        
        if not matched_rule:
            return
        
        # 检查是否为历史消息（扫描模式）
        is_historical = False
        if is_scan:
            message_age = (datetime.utcnow() - message.created_at.replace(tzinfo=None)).total_seconds()
            is_historical = message_age > HISTORICAL_MESSAGE_CONFIG['threshold_seconds']
        
        # 执行动作
        await self._execute_action(message, matched_rule, config, is_historical)
    
    async def _execute_action(
        self,
        message: discord.Message,
        rule: ThreadCommandRule,
        config: Optional[ThreadCommandServerConfig],
        is_historical: bool = False
    ):
        """执行规则动作"""
        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        channel_id = str(message.channel.id)
        thread_id = str(message.channel.id) if isinstance(message.channel, discord.Thread) else None
        
        # 获取限流配置（规则优先，否则使用全服默认，0表示不限流）
        user_reply_cd = rule.user_reply_cooldown
        if user_reply_cd is None and config:
            user_reply_cd = config.default_user_reply_cooldown
        if user_reply_cd is None:
            user_reply_cd = 60
        
        thread_reply_cd = rule.thread_reply_cooldown
        if thread_reply_cd is None and config:
            thread_reply_cd = config.default_thread_reply_cooldown
        if thread_reply_cd is None:
            thread_reply_cd = 30
        
        # 检查限流 - 回复
        can_reply = True
        if rule.action_type in ('reply', 'go_to_top', 'reply_and_react'):
            # 用户级限流
            if not self.rate_limiter.check_rate_limit(
                guild_id, rule.rule_id, 'user', user_id, 'reply', user_reply_cd
            ):
                can_reply = False
            # 帖子/频道级限流
            elif thread_id and not self.rate_limiter.check_rate_limit(
                guild_id, rule.rule_id, 'thread', thread_id, 'reply', thread_reply_cd
            ):
                can_reply = False
        
        # 历史消息静默模式：不回复
        if is_historical and HISTORICAL_MESSAGE_CONFIG['silent_mode']:
            can_reply = False
        
        reply_msg = None
        
        # 执行回复
        if can_reply and rule.action_type in ('reply', 'go_to_top', 'reply_and_react'):
            try:
                if rule.action_type == 'go_to_top':
                    reply_msg = await self._send_go_to_top_reply(message)
                else:
                    reply_msg = await self._send_custom_reply(message, rule)
                
                # 记录限流
                self.rate_limiter.record_trigger(guild_id, rule.rule_id, 'user', user_id, 'reply')
                if thread_id:
                    self.rate_limiter.record_trigger(guild_id, rule.rule_id, 'thread', thread_id, 'reply')
                
            except Exception as e:
                self.logger.error(f"发送回复失败: {e}")
        
        # 添加反应
        if rule.action_type in ('react', 'reply_and_react') or rule.add_reaction:
            try:
                reaction = rule.add_reaction or '✅'
                await message.add_reaction(reaction)
            except Exception as e:
                self.logger.debug(f"添加反应失败: {e}")
        
        # 调度删除
        if rule.delete_trigger_delay is not None:
            delete_at = time.time() + rule.delete_trigger_delay
            self._schedule_delete(message.id, message.channel.id, delete_at)
        
        if reply_msg and rule.delete_reply_delay is not None:
            delete_at = time.time() + rule.delete_reply_delay
            self._schedule_delete(reply_msg.id, reply_msg.channel.id, delete_at)
        
        # 更新统计
        matched_trigger = rule.get_matched_trigger(message.content.strip())
        trigger_text = matched_trigger.trigger_text if matched_trigger else ''
        await self.stats_buffer.increment(guild_id, user_id, rule.rule_id, trigger_text)
        
        self.log_action(
            'THREAD_CMD_TRIGGER',
            user_id,
            guild_id,
            {'rule_id': rule.rule_id, 'action': rule.action_type, 'trigger': trigger_text}
        )
    
    async def _send_go_to_top_reply(self, message: discord.Message) -> Optional[discord.Message]:
        """发送回顶回复"""
        channel = message.channel
        
        # 获取首楼消息
        first_message = None
        async for msg in channel.history(limit=1, oldest_first=True):
            first_message = msg
            break
        
        if not first_message:
            return None
        
        # 构建首楼链接
        message_url = f"https://discord.com/channels/{message.guild.id}/{channel.id}/{first_message.id}"
        
        embed = discord.Embed(
            title="🔝 回到顶楼",
            description=f"📍 **频道**: {channel.mention}\n"
                       f"🔗 **首楼链接**: [点击跳转]({message_url})\n"
                       f"📅 **首楼时间**: {first_message.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            color=0x00ff00
        )
        
        if first_message.content:
            preview = first_message.content[:100] + "..." if len(first_message.content) > 100 else first_message.content
            embed.add_field(name="📝 首楼内容预览", value=f"```{preview}```", inline=False)
        
        # 获取用户使用次数
        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        stats = await self.db.fetchone(
            "SELECT usage_count FROM thread_command_stats WHERE guild_id = ? AND user_id = ? AND trigger_text = ?",
            (guild_id, user_id, '回顶')
        )
        usage_count = (stats['usage_count'] if stats else 0) + 1
        
        footer_text = f"首楼作者: {first_message.author.display_name} • 已为你提供了{usage_count}次回顶链接"
        embed.set_footer(text=footer_text, icon_url=first_message.author.display_avatar.url)
        
        return await message.reply(embed=embed)
    
    async def _send_custom_reply(self, message: discord.Message, rule: ThreadCommandRule) -> Optional[discord.Message]:
        """发送自定义回复
        
        支持三种格式:
        1. 纯文本 - 直接发送
        2. JSON embed - 以 { 开头的JSON格式，会解析为embed
        3. reply_embed_json 字段 - 数据库中的embed配置
        """
        if not rule.reply_content and not rule.reply_embed_json:
            return None
        
        content = rule.reply_content or ''
        embed = None
        final_content = None
        
        # 先检查是否为JSON格式的embed（以 { 开头）
        if content.strip().startswith('{'):
            try:
                embed_data = json.loads(content)
                
                # 对embed中的文本字段进行模板变量替换
                embed_data = self._replace_template_vars_in_dict(embed_data, message)
                
                embed = discord.Embed.from_dict(embed_data)
                final_content = None  # 使用embed时不发送文本内容
            except (json.JSONDecodeError, Exception) as e:
                # JSON解析失败，当作普通文本处理
                self.logger.debug(f"Embed JSON解析失败，作为普通文本处理: {e}")
                final_content = self._replace_template_vars(content, message)
        else:
            # 普通文本，进行模板变量替换
            final_content = self._replace_template_vars(content, message)
        
        # 检查数据库中的 reply_embed_json 字段
        if not embed and rule.reply_embed_json:
            try:
                embed_data = json.loads(rule.reply_embed_json)
                embed_data = self._replace_template_vars_in_dict(embed_data, message)
                embed = discord.Embed.from_dict(embed_data)
            except Exception:
                pass
        
        if final_content or embed:
            return await message.reply(content=final_content if final_content else None, embed=embed)
        return None
    
    def _replace_template_vars_in_dict(self, data: Any, message: discord.Message) -> Any:
        """递归替换字典中的模板变量"""
        if isinstance(data, str):
            return self._replace_template_vars(data, message)
        elif isinstance(data, dict):
            return {k: self._replace_template_vars_in_dict(v, message) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._replace_template_vars_in_dict(item, message) for item in data]
        return data
    
    def _replace_template_vars(self, content: str, message: discord.Message) -> str:
        """替换模板变量"""
        replacements = {
            '{user}': message.author.mention,
            '{user_name}': message.author.display_name,
            '{channel}': message.channel.mention,
            '{channel_name}': message.channel.name,
            '{guild_name}': message.guild.name,
        }
        
        for key, value in replacements.items():
            content = content.replace(key, value)
        
        return content
    
    def _schedule_delete(self, message_id: int, channel_id: int, delete_at: float):
        """调度消息删除"""
        if len(self._pending_deletes) >= RESOURCE_LIMITS['max_pending_deletes']:
            # 队列满，移除最早的
            self._pending_deletes.sort(key=lambda x: x[2])
            self._pending_deletes = self._pending_deletes[100:]
        
        self._pending_deletes.append((message_id, channel_id, delete_at))
    
    # ==================== 权限检查 ====================
    
    async def check_server_config_permission(
        self,
        interaction: discord.Interaction
    ) -> bool:
        """检查是否有全服配置权限"""
        # Bot开发者拥有全部权限
        if await self.bot.is_owner(interaction.user):
            return True
        
        # 管理员
        if interaction.user.guild_permissions.administrator:
            return True
        
        # 管理服务器权限
        if interaction.user.guild_permissions.manage_guild:
            return True
        
        # 检查特殊权限
        guild_id = str(interaction.guild.id)
        permissions = await self.cache.get_permissions(guild_id)
        
        user_id = str(interaction.user.id)
        user_roles = [str(r.id) for r in interaction.user.roles]
        
        for perm in permissions:
            if perm.permission_level != 'server_config':
                continue
            if perm.target_type == 'user' and perm.target_id == user_id:
                return True
            if perm.target_type == 'role' and perm.target_id in user_roles:
                return True
        
        return False
    
    async def check_thread_config_permission(
        self,
        interaction: discord.Interaction,
        thread: discord.Thread
    ) -> bool:
        """检查是否有帖子配置权限"""
        # 先检查全服权限
        if await self.check_server_config_permission(interaction):
            return True
        
        # 检查是否为帖主
        if thread.owner_id == interaction.user.id:
            # 检查是否允许贴主配置
            guild_id = str(interaction.guild.id)
            config = await self.cache.get_server_config(guild_id)
            # 如果没有配置记录，默认允许贴主配置；如果有配置，检查 allow_thread_owner_config
            if config is None or config.allow_thread_owner_config:
                return True
        
        return False
    
    # ==================== 斜杠命令 ====================
    
    scan_cmd = app_commands.Group(
        name="扫描监听提醒",
        description="扫描监听提醒功能管理",
        default_permissions=discord.Permissions(send_messages=True)
    )
    
    @scan_cmd.command(name="状态", description="查看功能开关状态")
    async def show_status(self, interaction: discord.Interaction):
        """显示功能状态（临时消息）"""
        guild_id = str(interaction.guild.id)
        
        config = await self.cache.get_server_config(guild_id)
        
        # 查询所有全服规则（包括禁用的），用于显示准确的规则数量
        all_server_rules = await self.db.fetchall(
            "SELECT * FROM thread_command_rules WHERE guild_id = ? AND scope = 'server'",
            (guild_id,)
        )
        
        is_enabled = config.is_enabled if config else True
        allow_owner = config.allow_thread_owner_config if config else True
        
        # 获取允许的论坛频道
        allowed_channels = []
        if config and config.allowed_forum_channels:
            try:
                channel_ids = json.loads(config.allowed_forum_channels)
                for cid in channel_ids[:5]:
                    channel = self.bot.get_channel(int(cid))
                    if channel:
                        allowed_channels.append(channel.mention)
            except:
                pass
        
        embed = discord.Embed(
            title="📊 扫描监听提醒 - 状态",
            color=0x00ff00 if is_enabled else 0xff9900
        )
        
        # 全服开关
        embed.add_field(
            name="🌐 全服功能",
            value="✅ 开启" if is_enabled else "❌ 关闭",
            inline=True
        )
        
        # 贴内功能开关
        embed.add_field(
            name="📝 贴主配置权限",
            value="✅ 允许" if allow_owner else "❌ 禁止",
            inline=True
        )
        
        # 全服规则数（显示总数，包括禁用的）
        enabled_count = sum(1 for r in all_server_rules if r['is_enabled'])
        embed.add_field(
            name="📋 全服规则数",
            value=f"{enabled_count}/{len(all_server_rules)} 启用",
            inline=True
        )
        
        # 允许的论坛频道
        if allowed_channels:
            embed.add_field(
                name="📌 启用的论坛频道",
                value='\n'.join(allowed_channels),
                inline=False
            )
        else:
            embed.add_field(
                name="📌 启用的论坛频道",
                value="所有论坛频道（未限制）",
                inline=False
            )
        
        # 规则预览（从数据库结果中构建预览）
        if all_server_rules:
            rules_info = []
            for idx, rule_row in enumerate(all_server_rules[:3], 1):
                # 获取该规则的触发器
                triggers_data = await self.db.fetchall(
                    "SELECT trigger_text FROM thread_command_triggers WHERE rule_id = ? LIMIT 2",
                    (rule_row['rule_id'],)
                )
                trigger_strs = [t['trigger_text'] for t in triggers_data]
                trigger_str = ', '.join(trigger_strs)
                if len(triggers_data) > 2:
                    trigger_str += '...'
                status = "✅" if rule_row['is_enabled'] else "❌"
                action_display = ACTION_TYPE_DISPLAY.get(rule_row['action_type'], rule_row['action_type'])
                rules_info.append(f"{status} 全服{idx}号: `{trigger_str}` → {action_display}")
            
            embed.add_field(
                name="规则预览",
                value='\n'.join(rules_info),
                inline=False
            )
        
        # 使用提示
        embed.set_footer(text="使用 /扫描监听提醒 配置 管理全服设置 | /扫描监听提醒 帖子配置 管理帖子设置")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @scan_cmd.command(name="配置", description="服务器配置面板（管理员）")
    async def server_config_panel(self, interaction: discord.Interaction):
        """服务器配置面板 - 管理员用"""
        if not await self.check_server_config_permission(interaction):
            await interaction.response.send_message("❌ 权限不足，需要服务器管理权限或特殊权限", ephemeral=True)
            return
        
        guild_id = str(interaction.guild.id)
        config = await self.cache.get_server_config(guild_id)
        
        # 查询所有全服规则（包括禁用的），用于显示准确的规则数量
        all_server_rules = await self.db.fetchall(
            "SELECT * FROM thread_command_rules WHERE guild_id = ? AND scope = 'server'",
            (guild_id,)
        )
        server_rules = await self.cache.get_server_rules(guild_id)
        
        # 构建配置数据
        config_data = {
            'is_enabled': config.is_enabled if config else True,
            'allow_thread_owner_config': config.allow_thread_owner_config if config else True,
            'default_user_reply_cooldown': config.default_user_reply_cooldown if config else 60,
            'default_thread_reply_cooldown': config.default_thread_reply_cooldown if config else 30,
            'allowed_forum_channels': [],
        }
        
        if config and config.allowed_forum_channels:
            try:
                config_data['allowed_forum_channels'] = json.loads(config.allowed_forum_channels)
            except:
                pass
        
        # 构建主面板Embed
        embed = discord.Embed(
            title="⚙️ 扫描监听提醒 - 服务器配置",
            description="通过下方按钮管理全服扫描监听设置",
            color=0x3498db
        )
        
        # 开关状态
        embed.add_field(
            name="🔘 功能开关",
            value="✅ 已开启" if config_data['is_enabled'] else "❌ 已关闭",
            inline=True
        )
        embed.add_field(
            name="👥 贴主配置",
            value="✅ 允许" if config_data['allow_thread_owner_config'] else "❌ 禁止",
            inline=True
        )
        # 规则数量（显示 启用/总数）
        enabled_count = sum(1 for r in all_server_rules if r['is_enabled'])
        embed.add_field(
            name="📋 规则数量",
            value=f"{enabled_count}/{len(all_server_rules)} 启用",
            inline=True
        )
        
        # 论坛频道设置
        channel_info = "未限制（所有论坛）"
        if config_data['allowed_forum_channels']:
            channel_mentions = []
            for cid in config_data['allowed_forum_channels'][:5]:
                channel = self.bot.get_channel(int(cid))
                if channel:
                    channel_mentions.append(channel.mention)
            if channel_mentions:
                channel_info = '\n'.join(channel_mentions)
                if len(config_data['allowed_forum_channels']) > 5:
                    channel_info += f"\n... +{len(config_data['allowed_forum_channels']) - 5} 个"
        
        embed.add_field(
            name="📌 允许的论坛频道",
            value=channel_info,
            inline=False
        )
        
        # 限流设置
        embed.add_field(
            name="⏱️ 默认限流",
            value=f"用户: {config_data['default_user_reply_cooldown']}s | 帖子: {config_data['default_thread_reply_cooldown']}s",
            inline=False
        )
        
        # 创建视图
        view = ServerConfigPanelView(self, guild_id, config_data, server_rules)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @scan_cmd.command(name="帖子配置", description="帖子配置面板（贴主）")
    async def thread_config_panel(self, interaction: discord.Interaction):
        """帖子配置面板 - 贴主用"""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ 此命令只能在帖子内使用", ephemeral=True)
            return
        
        if not await self.check_thread_config_permission(interaction, interaction.channel):
            await interaction.response.send_message("❌ 权限不足，需要是帖主或有配置权限", ephemeral=True)
            return
        
        thread_id = str(interaction.channel.id)
        guild_id = str(interaction.guild.id)
        
        # 查询所有帖子规则（包括禁用的）
        all_thread_rules = await self.db.fetchall(
            "SELECT * FROM thread_command_rules WHERE thread_id = ?",
            (thread_id,)
        )
        # 获取启用的帖子规则（用于缓存）
        thread_rules = await self.cache.get_thread_rules(thread_id)
        
        # 构建面板Embed
        embed = discord.Embed(
            title="📝 扫描监听提醒 - 帖子配置",
            description=f"帖子: {interaction.channel.mention}",
            color=0x2ecc71
        )
        
        # 规则数量（显示 启用/总数）
        enabled_count = sum(1 for r in all_thread_rules if r['is_enabled'])
        embed.add_field(
            name="📋 当前规则数",
            value=f"{enabled_count}/{len(all_thread_rules)} 启用",
            inline=True
        )
        
        # 规则列表（使用全部规则数据，包括禁用的）
        if all_thread_rules:
            rules_info = []
            for idx, rule_row in enumerate(all_thread_rules[:5], 1):
                # 获取该规则的触发器
                triggers_data = await self.db.fetchall(
                    "SELECT trigger_text FROM thread_command_triggers WHERE rule_id = ? LIMIT 2",
                    (rule_row['rule_id'],)
                )
                trigger_strs = [t['trigger_text'] for t in triggers_data]
                trigger_str = ', '.join(trigger_strs)
                if len(triggers_data) > 2:
                    trigger_str += '...'
                status = "✅" if rule_row['is_enabled'] else "❌"
                action_display = ACTION_TYPE_DISPLAY.get(rule_row['action_type'], rule_row['action_type'])
                rules_info.append(f"{status} 帖子{idx}号: `{trigger_str}` → {action_display}")
            
            embed.add_field(
                name="规则列表",
                value='\n'.join(rules_info),
                inline=False
            )
            
            if len(all_thread_rules) > 5:
                embed.set_footer(text=f"显示前5条，共{len(all_thread_rules)}条规则")
        else:
            embed.add_field(
                name="规则列表",
                value="暂无规则，点击下方按钮添加",
                inline=False
            )
        
        # 创建视图
        view = ThreadConfigPanelView(self, guild_id, thread_id, thread_rules)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    # ==================== 辅助方法（供面板调用） ====================
    
    async def create_default_huiding_rule(self, guild_id: str, user_id: str) -> int:
        """创建默认回顶规则"""
        now = datetime.utcnow().isoformat()
        config = DEFAULT_GO_TO_TOP_RULE
        
        await self.db.execute(
            """INSERT INTO thread_command_rules
               (guild_id, scope, action_type, reply_content, delete_trigger_delay,
                delete_reply_delay, add_reaction, is_enabled, priority, created_by,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (
                guild_id,
                config['scope'],
                config['action_type'],
                config['reply_content'],
                config['delete_trigger_delay'],
                config['delete_reply_delay'],
                config['add_reaction'],
                config['priority'],
                user_id,
                now, now
            )
        )
        
        rule_row = await self.db.fetchone(
            "SELECT rule_id FROM thread_command_rules WHERE guild_id = ? ORDER BY rule_id DESC LIMIT 1",
            (guild_id,)
        )
        rule_id = rule_row['rule_id']
        
        for trigger in config['triggers']:
            await self.db.execute(
                """INSERT INTO thread_command_triggers
                   (rule_id, trigger_text, trigger_mode, is_enabled, created_at)
                   VALUES (?, ?, ?, 1, ?)""",
                (rule_id, trigger['text'], trigger['mode'], now)
            )
        
        # 确保服务器配置存在
        existing_config = await self.db.fetchone(
            "SELECT * FROM thread_command_server_config WHERE guild_id = ?",
            (guild_id,)
        )
        if not existing_config:
            await self.db.execute(
                """INSERT INTO thread_command_server_config
                   (guild_id, is_enabled, created_at, updated_at) VALUES (?, 1, ?, ?)""",
                (guild_id, now, now)
            )
        
        await self.cache.refresh_server_rules(guild_id)
        await self.cache.refresh_server_config(guild_id)
        
        return rule_id
    
    async def toggle_feature(self, guild_id: str, enabled: bool):
        """开关全服功能"""
        now = datetime.utcnow().isoformat()
        
        existing = await self.db.fetchone(
            "SELECT * FROM thread_command_server_config WHERE guild_id = ?",
            (guild_id,)
        )
        
        if existing:
            await self.db.execute(
                "UPDATE thread_command_server_config SET is_enabled = ?, updated_at = ? WHERE guild_id = ?",
                (enabled, now, guild_id)
            )
        else:
            await self.db.execute(
                """INSERT INTO thread_command_server_config
                   (guild_id, is_enabled, created_at, updated_at) VALUES (?, ?, ?, ?)""",
                (guild_id, enabled, now, now)
            )
        
        await self.cache.refresh_server_config(guild_id)
    
    async def toggle_thread_owner_config(self, guild_id: str, enabled: bool):
        """开关贴主配置权限"""
        now = datetime.utcnow().isoformat()
        
        existing = await self.db.fetchone(
            "SELECT * FROM thread_command_server_config WHERE guild_id = ?",
            (guild_id,)
        )
        
        if existing:
            await self.db.execute(
                "UPDATE thread_command_server_config SET allow_thread_owner_config = ?, updated_at = ? WHERE guild_id = ?",
                (enabled, now, guild_id)
            )
        else:
            await self.db.execute(
                """INSERT INTO thread_command_server_config
                   (guild_id, allow_thread_owner_config, created_at, updated_at) VALUES (?, ?, ?, ?)""",
                (guild_id, enabled, now, now)
            )
        
        await self.cache.refresh_server_config(guild_id)
    
    async def update_cooldown_settings(self, guild_id: str, user_cd: int, thread_cd: int):
        """更新默认限流设置"""
        now = datetime.utcnow().isoformat()
        
        existing = await self.db.fetchone(
            "SELECT * FROM thread_command_server_config WHERE guild_id = ?",
            (guild_id,)
        )
        
        if existing:
            await self.db.execute(
                """UPDATE thread_command_server_config
                   SET default_user_reply_cooldown = ?, default_thread_reply_cooldown = ?, updated_at = ?
                   WHERE guild_id = ?""",
                (user_cd, thread_cd, now, guild_id)
            )
        else:
            await self.db.execute(
                """INSERT INTO thread_command_server_config
                   (guild_id, default_user_reply_cooldown, default_thread_reply_cooldown, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (guild_id, user_cd, thread_cd, now, now)
            )
        
        await self.cache.refresh_server_config(guild_id)
    
    async def update_allowed_channels(self, guild_id: str, channel_ids: list):
        """更新允许的论坛频道"""
        now = datetime.utcnow().isoformat()
        channels_json = json.dumps(channel_ids) if channel_ids else None
        
        existing = await self.db.fetchone(
            "SELECT * FROM thread_command_server_config WHERE guild_id = ?",
            (guild_id,)
        )
        
        if existing:
            await self.db.execute(
                "UPDATE thread_command_server_config SET allowed_forum_channels = ?, updated_at = ? WHERE guild_id = ?",
                (channels_json, now, guild_id)
            )
        else:
            await self.db.execute(
                """INSERT INTO thread_command_server_config
                   (guild_id, allowed_forum_channels, created_at, updated_at) VALUES (?, ?, ?, ?)""",
                (guild_id, channels_json, now, now)
            )
        
        await self.cache.refresh_server_config(guild_id)
    
    async def add_rule(
        self,
        guild_id: str,
        scope: str,
        trigger_list: list,
        trigger_mode: str,
        action_type: str,
        reply_content: Optional[str],
        delete_delay: Optional[int],
        user_id: str,
        thread_id: Optional[str] = None
    ) -> int:
        """添加规则"""
        now = datetime.utcnow().isoformat()
        
        await self.db.execute(
            """INSERT INTO thread_command_rules
               (guild_id, thread_id, scope, action_type, reply_content,
                delete_trigger_delay, delete_reply_delay, is_enabled, priority,
                created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)""",
            (
                guild_id, thread_id, scope, action_type, reply_content,
                delete_delay, delete_delay, user_id, now, now
            )
        )
        
        rule_row = await self.db.fetchone(
            "SELECT rule_id FROM thread_command_rules WHERE guild_id = ? ORDER BY rule_id DESC LIMIT 1",
            (guild_id,)
        )
        rule_id = rule_row['rule_id']
        
        for t in trigger_list:
            if len(t) > RESOURCE_LIMITS['max_trigger_length']:
                t = t[:RESOURCE_LIMITS['max_trigger_length']]
            await self.db.execute(
                """INSERT INTO thread_command_triggers
                   (rule_id, trigger_text, trigger_mode, is_enabled, created_at)
                   VALUES (?, ?, ?, 1, ?)""",
                (rule_id, t, trigger_mode, now)
            )
        
        if scope == 'server':
            await self.cache.refresh_server_rules(guild_id)
        elif thread_id:
            await self.cache.refresh_thread_rules(thread_id)
        
        return rule_id
    
    async def delete_rule(self, rule_id: int, guild_id: str) -> bool:
        """删除规则"""
        rule = await self.db.fetchone(
            "SELECT * FROM thread_command_rules WHERE rule_id = ? AND guild_id = ?",
            (rule_id, guild_id)
        )
        
        if not rule:
            return False
        
        await self.db.execute(
            "DELETE FROM thread_command_rules WHERE rule_id = ?",
            (rule_id,)
        )
        
        if rule['scope'] == 'server':
            await self.cache.refresh_server_rules(guild_id)
        elif rule['thread_id']:
            await self.cache.refresh_thread_rules(rule['thread_id'])
        
        return True
    
    async def toggle_rule(self, rule_id: int, guild_id: str, enabled: bool) -> bool:
        """开关规则"""
        rule = await self.db.fetchone(
            "SELECT * FROM thread_command_rules WHERE rule_id = ? AND guild_id = ?",
            (rule_id, guild_id)
        )
        
        if not rule:
            return False
        
        await self.db.execute(
            "UPDATE thread_command_rules SET is_enabled = ?, updated_at = ? WHERE rule_id = ?",
            (enabled, datetime.utcnow().isoformat(), rule_id)
        )
        
        if rule['scope'] == 'server':
            await self.cache.refresh_server_rules(guild_id)
        elif rule['thread_id']:
            await self.cache.refresh_thread_rules(rule['thread_id'])
        
        return True


# ==================== 面板视图组件 ====================

class ServerConfigPanelView(discord.ui.View):
    """服务器配置面板视图"""
    
    def __init__(self, cog: ThreadCommandCog, guild_id: str, config_data: dict, rules: list):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.config_data = config_data
        self.rules = rules
    
    @discord.ui.button(label="开关全服功能", style=discord.ButtonStyle.primary, row=0)
    async def toggle_feature(self, interaction: discord.Interaction, button: discord.ui.Button):
        new_state = not self.config_data['is_enabled']
        await self.cog.toggle_feature(self.guild_id, new_state)
        self.config_data['is_enabled'] = new_state
        
        await interaction.response.send_message(
            f"{'✅ 已开启' if new_state else '❌ 已关闭'} 全服扫描监听功能",
            ephemeral=True
        )
    
    @discord.ui.button(label="开关贴主配置", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_owner_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        new_state = not self.config_data['allow_thread_owner_config']
        await self.cog.toggle_thread_owner_config(self.guild_id, new_state)
        self.config_data['allow_thread_owner_config'] = new_state
        
        await interaction.response.send_message(
            f"{'✅ 已允许' if new_state else '❌ 已禁止'} 贴主配置帖子规则",
            ephemeral=True
        )
    
    @discord.ui.button(label="初始化回顶规则", style=discord.ButtonStyle.success, row=0)
    async def init_huiding(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 检查是否已存在
        existing = await self.cog.db.fetchone(
            "SELECT * FROM thread_command_rules WHERE guild_id = ? AND action_type = 'go_to_top'",
            (self.guild_id,)
        )
        
        if existing:
            await interaction.response.send_message("⚠️ 回顶规则已存在", ephemeral=True)
            return
        
        rule_id = await self.cog.create_default_huiding_rule(
            self.guild_id,
            str(interaction.user.id)
        )
        
        await interaction.response.send_message(
            f"✅ 已创建默认回顶规则 #{rule_id}\n"
            "触发词: `/回顶`、`／回顶`、`回顶`\n"
            "动作: 回复首楼链接，5分钟后自动删除",
            ephemeral=True
        )
    
    @discord.ui.button(label="设置限流", style=discord.ButtonStyle.secondary, row=1)
    async def set_cooldown(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ServerCooldownModal(self.cog, self.guild_id, self.config_data)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="设置论坛频道", style=discord.ButtonStyle.secondary, row=1)
    async def set_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ForumChannelModal(self.cog, self.guild_id, self.config_data['allowed_forum_channels'])
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="添加规则", style=discord.ButtonStyle.success, row=2)
    async def add_rule(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddRuleModal(self.cog, self.guild_id, 'server', None)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="查看全部规则", style=discord.ButtonStyle.secondary, row=2)
    async def view_rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        rules_data = await self.cog.db.fetchall(
            "SELECT * FROM thread_command_rules WHERE guild_id = ? AND scope = 'server' ORDER BY priority DESC",
            (self.guild_id,)
        )
        
        if not rules_data:
            await interaction.response.send_message("📋 暂无全服规则", ephemeral=True)
            return
        
        embed = discord.Embed(title="📋 全服规则列表", color=0x3498db)
        
        for idx, rule_row in enumerate(rules_data[:10], 1):
            triggers_data = await self.cog.db.fetchall(
                "SELECT * FROM thread_command_triggers WHERE rule_id = ?",
                (rule_row['rule_id'],)
            )
            
            trigger_strs = [f"`{t['trigger_text']}`" for t in triggers_data[:3]]
            if len(triggers_data) > 3:
                trigger_strs.append(f"...+{len(triggers_data)-3}")
            
            status = "✅" if rule_row['is_enabled'] else "❌"
            
            action_display = ACTION_TYPE_DISPLAY.get(rule_row['action_type'], rule_row['action_type'])
            embed.add_field(
                name=f"{status} 全服{idx}号",
                value=f"触发: {', '.join(trigger_strs)}\n动作: {action_display}",
                inline=False
            )
        
        if len(rules_data) > 10:
            embed.set_footer(text=f"显示前10条，共{len(rules_data)}条规则")
        
        # 添加规则管理视图
        view = RuleManageView(self.cog, self.guild_id, rules_data, scope='server')
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="权限管理", style=discord.ButtonStyle.danger, row=2)
    async def manage_perms(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 需要管理员权限", ephemeral=True)
            return
        
        permissions = await self.cog.cache.get_permissions(self.guild_id)
        
        embed = discord.Embed(
            title="🔐 权限管理",
            description="管理谁可以配置扫描监听功能",
            color=0x9b59b6
        )
        
        if permissions:
            user_perms = [p for p in permissions if p.target_type == 'user']
            role_perms = [p for p in permissions if p.target_type == 'role']
            
            if user_perms:
                embed.add_field(
                    name="👤 用户权限",
                    value='\n'.join([f"<@{p.target_id}>" for p in user_perms[:10]]),
                    inline=False
                )
            if role_perms:
                embed.add_field(
                    name="🏷️ 身份组权限",
                    value='\n'.join([f"<@&{p.target_id}>" for p in role_perms[:10]]),
                    inline=False
                )
        else:
            embed.add_field(name="权限列表", value="暂无特殊权限配置", inline=False)
        
        view = PermissionPanelView(self.cog, self.guild_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ThreadConfigPanelView(discord.ui.View):
    """帖子配置面板视图"""
    
    def __init__(self, cog: ThreadCommandCog, guild_id: str, thread_id: str, rules: list):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.thread_id = thread_id
        self.rules = rules
    
    @discord.ui.button(label="添加规则", style=discord.ButtonStyle.success, row=0)
    async def add_rule(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 检查规则数量限制
        existing_count = await self.cog.db.fetchone(
            "SELECT COUNT(*) as cnt FROM thread_command_rules WHERE thread_id = ?",
            (self.thread_id,)
        )
        if existing_count['cnt'] >= RESOURCE_LIMITS['max_thread_rules']:
            await interaction.response.send_message(
                f"❌ 帖子规则已达上限 ({RESOURCE_LIMITS['max_thread_rules']})",
                ephemeral=True
            )
            return
        
        modal = AddRuleModal(self.cog, self.guild_id, 'thread', self.thread_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="管理规则", style=discord.ButtonStyle.primary, row=0)
    async def manage_rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        rules_data = await self.cog.db.fetchall(
            "SELECT * FROM thread_command_rules WHERE thread_id = ? ORDER BY priority DESC",
            (self.thread_id,)
        )
        
        if not rules_data:
            await interaction.response.send_message("📋 暂无帖子规则", ephemeral=True)
            return
        
        embed = discord.Embed(title="📋 帖子规则列表", color=0x3498db)
        
        for idx, r in enumerate(rules_data[:10], 1):
            triggers = await self.cog.db.fetchall(
                "SELECT * FROM thread_command_triggers WHERE rule_id = ?",
                (r['rule_id'],)
            )
            trigger_strs = [f"`{t['trigger_text']}`" for t in triggers[:3]]
            status = "✅" if r['is_enabled'] else "❌"
            action_display = ACTION_TYPE_DISPLAY.get(r['action_type'], r['action_type'])
            embed.add_field(
                name=f"{status} 帖子{idx}号",
                value=f"触发: {', '.join(trigger_strs)}\n动作: {action_display}",
                inline=False
            )
        
        view = RuleManageView(self.cog, self.guild_id, rules_data, scope='thread')
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="禁用所有规则", style=discord.ButtonStyle.danger, row=0)
    async def disable_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.db.execute(
            "UPDATE thread_command_rules SET is_enabled = 0, updated_at = ? WHERE thread_id = ?",
            (datetime.utcnow().isoformat(), self.thread_id)
        )
        await self.cog.cache.refresh_thread_rules(self.thread_id)
        await interaction.response.send_message("✅ 已禁用所有帖子规则", ephemeral=True)


class RuleManageView(discord.ui.View):
    """规则管理视图"""
    
    def __init__(self, cog: ThreadCommandCog, guild_id: str, rules_data: list, scope: str = 'server'):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.rules_data = rules_data
        self.scope = scope
        self.scope_prefix = "全服" if scope == 'server' else "帖子"
        
        # 添加规则选择器
        if rules_data:
            options = []
            for idx, r in enumerate(rules_data[:25], 1):
                action_display = ACTION_TYPE_DISPLAY.get(r['action_type'], r['action_type'])
                options.append(discord.SelectOption(
                    label=f"{self.scope_prefix}{idx}号",
                    value=str(r['rule_id']),
                    description=f"{action_display} - {'启用' if r['is_enabled'] else '禁用'}"
                ))
            
            self.rule_select = discord.ui.Select(
                placeholder="选择要操作的规则...",
                options=options
            )
            self.rule_select.callback = self.on_rule_select
            self.add_item(self.rule_select)
    
    def _get_rule_display_name(self, rule_id: int) -> str:
        """获取规则的显示名称（如：全服1号）"""
        for idx, r in enumerate(self.rules_data, 1):
            if r['rule_id'] == rule_id:
                return f"{self.scope_prefix}{idx}号"
        return f"规则{rule_id}"
    
    async def on_rule_select(self, interaction: discord.Interaction):
        rule_id = int(self.rule_select.values[0])
        
        # 找到规则信息和索引
        rule = None
        rule_idx = 0
        for idx, r in enumerate(self.rules_data, 1):
            if r['rule_id'] == rule_id:
                rule = r
                rule_idx = idx
                break
        
        if not rule:
            await interaction.response.send_message("❌ 规则不存在", ephemeral=True)
            return
        
        rule_display_name = f"{self.scope_prefix}{rule_idx}号"
        
        # 获取触发器信息
        triggers = await self.cog.db.fetchall(
            "SELECT * FROM thread_command_triggers WHERE rule_id = ?",
            (rule_id,)
        )
        
        # 显示规则详情面板（全服和帖子规则统一处理）
        embed = discord.Embed(
            title=f"📝 {rule_display_name} 详情",
            color=0x3498db
        )
        action_display = ACTION_TYPE_DISPLAY.get(rule['action_type'], rule['action_type'])
        scope_display = SCOPE_DISPLAY.get(rule['scope'], rule['scope'])
        embed.add_field(name="状态", value="✅ 启用" if rule['is_enabled'] else "❌ 禁用", inline=True)
        embed.add_field(name="动作", value=action_display, inline=True)
        embed.add_field(name="范围", value=scope_display, inline=True)
        
        trigger_info = '\n'.join([
            f"• `{t['trigger_text']}` ({MATCH_MODE_DISPLAY.get(t['trigger_mode'], t['trigger_mode'])})"
            for t in triggers
        ])
        embed.add_field(name="触发器", value=trigger_info or "无", inline=False)
        
        if rule['reply_content']:
            embed.add_field(name="回复内容", value=rule['reply_content'][:200], inline=False)
        
        view = RuleActionView(self.cog, self.guild_id, rule_id, rule['is_enabled'], rule_display_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class RuleActionView(discord.ui.View):
    """规则操作视图"""
    
    def __init__(self, cog: ThreadCommandCog, guild_id: str, rule_id: int, is_enabled: bool, rule_display_name: str = None):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild_id = guild_id
        self.rule_id = rule_id
        self.is_enabled = is_enabled
        self.rule_display_name = rule_display_name or f"规则{rule_id}"
    
    @discord.ui.button(label="切换启用状态", style=discord.ButtonStyle.primary)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        new_state = not self.is_enabled
        success = await self.cog.toggle_rule(self.rule_id, self.guild_id, new_state)
        
        if success:
            self.is_enabled = new_state
            await interaction.response.send_message(
                f"{'✅ 已启用' if new_state else '❌ 已禁用'} {self.rule_display_name}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ 操作失败", ephemeral=True)
    
    @discord.ui.button(label="编辑规则", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 获取规则信息
        rule = await self.cog.db.fetchone(
            "SELECT * FROM thread_command_rules WHERE rule_id = ?",
            (self.rule_id,)
        )
        if not rule:
            await interaction.response.send_message("❌ 规则不存在", ephemeral=True)
            return
        
        triggers = await self.cog.db.fetchall(
            "SELECT * FROM thread_command_triggers WHERE rule_id = ?",
            (self.rule_id,)
        )
        
        modal = EditRuleModal(self.cog, self.guild_id, dict(rule), triggers, self.rule_display_name)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="删除规则", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        success = await self.cog.delete_rule(self.rule_id, self.guild_id)
        
        if success:
            await interaction.response.send_message(f"✅ 已删除 {self.rule_display_name}", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 操作失败", ephemeral=True)


class EditRuleModal(discord.ui.Modal, title="编辑规则"):
    """编辑规则的Modal"""
    
    trigger_text = discord.ui.TextInput(
        label="触发词（多个用逗号分隔）",
        placeholder="你好, hello, 回顶",
        max_length=200
    )
    
    trigger_mode = discord.ui.TextInput(
        label="匹配模式（精确/前缀/包含/正则）",
        placeholder="精确=完全一致 | 前缀=以此开头 | 包含=包含此文字 | 正则=正则表达式",
        default="精确",
        max_length=20
    )
    
    reply_content = discord.ui.TextInput(
        label="回复内容（纯文本或JSON格式embed）",
        style=discord.TextStyle.paragraph,
        placeholder='普通文本 或 {"title":"标题","description":"描述","color":65280}',
        required=False,
        max_length=2000
    )
    
    delete_delay = discord.ui.TextInput(
        label="删除延迟秒数（0表示不删除）",
        placeholder="300",
        required=False,
        max_length=10
    )
    
    extra_settings = discord.ui.TextInput(
        label="额外设置（限流:用户秒,帖子秒 反应:emoji）",
        placeholder="限流:60,30 反应:✅ （可省略任意部分）",
        required=False,
        max_length=50
    )
    
    def __init__(self, cog: ThreadCommandCog, guild_id: str, rule: dict, triggers: list, rule_display_name: str):
        super().__init__(title=f"编辑 {rule_display_name}")
        self.cog = cog
        self.guild_id = guild_id
        self.rule = rule
        self.rule_id = rule['rule_id']
        self.rule_display_name = rule_display_name
        self.original_triggers = triggers
        
        # 填充当前值
        trigger_texts = [t['trigger_text'] for t in triggers]
        self.trigger_text.default = ', '.join(trigger_texts)
        
        # 匹配模式
        if triggers:
            current_mode = triggers[0].get('trigger_mode', 'exact')
            self.trigger_mode.default = MATCH_MODE_DISPLAY.get(current_mode, current_mode)
        
        if rule.get('reply_content'):
            self.reply_content.default = rule['reply_content']
        
        if rule.get('delete_trigger_delay'):
            self.delete_delay.default = str(rule['delete_trigger_delay'])
        
        # 额外设置：限流和反应
        extra_parts = []
        user_cd = rule.get('user_reply_cooldown')
        thread_cd = rule.get('thread_reply_cooldown')
        if user_cd is not None or thread_cd is not None:
            user_cd = user_cd if user_cd is not None else 60
            thread_cd = thread_cd if thread_cd is not None else 30
            extra_parts.append(f"限流:{user_cd},{thread_cd}")
        if rule.get('add_reaction'):
            extra_parts.append(f"反应:{rule['add_reaction']}")
        if extra_parts:
            self.extra_settings.default = ' '.join(extra_parts)
    
    async def on_submit(self, interaction: discord.Interaction):
        # 解析触发词
        trigger_list = [t.strip() for t in self.trigger_text.value.split(',') if t.strip()]
        if not trigger_list:
            await interaction.response.send_message("❌ 触发词不能为空", ephemeral=True)
            return
        
        # 解析匹配模式（支持中英文）
        mode_input = self.trigger_mode.value.strip()
        new_mode = MATCH_MODE_MAP.get(mode_input) or MATCH_MODE_MAP.get(mode_input.lower())
        if not new_mode:
            new_mode = 'exact'
        
        # 验证正则表达式
        if new_mode == 'regex':
            import re
            for t in trigger_list:
                try:
                    re.compile(t)
                except re.error as e:
                    await interaction.response.send_message(
                        f"❌ 正则表达式无效: {t}\n错误: {e}",
                        ephemeral=True
                    )
                    return
        
        # 解析删除延迟
        delete_delay = None
        if self.delete_delay.value.strip():
            try:
                delay = int(self.delete_delay.value.strip())
                if delay > 0:
                    delete_delay = delay
            except:
                pass
        
        # 解析额外设置（限流和反应）
        user_cooldown = None
        thread_cooldown = None
        add_reaction = None
        
        extra_value = self.extra_settings.value.strip()
        if extra_value:
            # 解析 限流:60,30
            import re
            cooldown_match = re.search(r'限流[：:](\d+),(\d+)', extra_value)
            if cooldown_match:
                user_cooldown = int(cooldown_match.group(1))
                thread_cooldown = int(cooldown_match.group(2))
            
            # 解析 反应:✅
            reaction_match = re.search(r'反应[：:](\S+)', extra_value)
            if reaction_match:
                add_reaction = reaction_match.group(1)
        
        now = datetime.utcnow().isoformat()
        
        try:
            # 更新规则
            update_fields = ['updated_at = ?']
            update_values = [now]
            
            # 回复内容
            if self.reply_content.value.strip():
                update_fields.append('reply_content = ?')
                update_values.append(self.reply_content.value.strip())
            
            # 删除延迟
            update_fields.append('delete_trigger_delay = ?')
            update_fields.append('delete_reply_delay = ?')
            update_values.extend([delete_delay, delete_delay])
            
            # 限流设置
            update_fields.append('user_reply_cooldown = ?')
            update_fields.append('thread_reply_cooldown = ?')
            update_values.extend([user_cooldown, thread_cooldown])
            
            # 添加反应
            update_fields.append('add_reaction = ?')
            update_values.append(add_reaction)
            
            update_values.append(self.rule_id)
            
            await self.cog.db.execute(
                f"UPDATE thread_command_rules SET {', '.join(update_fields)} WHERE rule_id = ?",
                tuple(update_values)
            )
            
            # 更新触发器：删除旧的，添加新的（使用新的匹配模式）
            await self.cog.db.execute(
                "DELETE FROM thread_command_triggers WHERE rule_id = ?",
                (self.rule_id,)
            )
            
            for trigger in trigger_list:
                await self.cog.db.execute(
                    """INSERT INTO thread_command_triggers
                       (rule_id, trigger_text, trigger_mode, is_enabled, created_at)
                       VALUES (?, ?, ?, 1, ?)""",
                    (self.rule_id, trigger, new_mode, now)
                )
            
            # 刷新缓存
            if self.rule.get('thread_id'):
                await self.cog.cache.refresh_thread_rules(self.rule['thread_id'])
            else:
                await self.cog.cache.refresh_server_rules(self.guild_id)
            
            mode_display = MATCH_MODE_DISPLAY.get(new_mode, new_mode)
            await interaction.response.send_message(
                f"✅ 已更新 {self.rule_display_name}\n"
                f"匹配模式: {mode_display}",
                ephemeral=True
            )
            
        except Exception as e:
            self.cog.logger.error(f"更新规则失败: {e}")
            await interaction.response.send_message(f"❌ 更新失败: {e}", ephemeral=True)


class ServerCooldownModal(discord.ui.Modal, title="设置默认限流"):
    """服务器限流设置Modal"""
    
    user_cooldown = discord.ui.TextInput(
        label="用户限流（秒，0表示不限流）",
        placeholder="同一用户触发同一规则的回复间隔，0=不限流",
        default="60",
        max_length=10
    )
    
    thread_cooldown = discord.ui.TextInput(
        label="帖子限流（秒，0表示不限流）",
        placeholder="同一帖子内触发同一规则的回复间隔，0=不限流",
        default="30",
        max_length=10
    )
    
    def __init__(self, cog, guild_id: str, config_data: dict):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        
        # 填充当前值
        user_cd = config_data.get('default_user_reply_cooldown')
        thread_cd = config_data.get('default_thread_reply_cooldown')
        
        if user_cd is not None:
            self.user_cooldown.default = str(user_cd)
        if thread_cd is not None:
            self.thread_cooldown.default = str(thread_cd)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_cd = int(self.user_cooldown.value.strip())
            thread_cd = int(self.thread_cooldown.value.strip())
            
            if user_cd < 0 or thread_cd < 0:
                await interaction.response.send_message("❌ 限流时间不能为负数", ephemeral=True)
                return
            
            await self.cog.update_cooldown_settings(self.guild_id, user_cd, thread_cd)
            
            # 构建提示信息
            user_info = f"{user_cd}秒" if user_cd > 0 else "不限流"
            thread_info = f"{thread_cd}秒" if thread_cd > 0 else "不限流"
            
            await interaction.response.send_message(
                f"✅ 已更新默认限流设置\n"
                f"• 用户限流: {user_info}\n"
                f"• 帖子限流: {thread_info}",
                ephemeral=True
            )
        except ValueError:
            await interaction.response.send_message("❌ 请输入有效的数字", ephemeral=True)


class ForumChannelModal(discord.ui.Modal, title="设置允许的论坛频道"):
    """论坛频道设置Modal"""
    
    channel_ids = discord.ui.TextInput(
        label="论坛频道ID（每行一个，留空表示所有频道）",
        style=discord.TextStyle.paragraph,
        placeholder="1234567890123456789\n9876543210987654321",
        required=False,
        max_length=500
    )
    
    def __init__(self, cog: ThreadCommandCog, guild_id: str, current_channels: list):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        if current_channels:
            self.channel_ids.default = '\n'.join(current_channels)
    
    async def on_submit(self, interaction: discord.Interaction):
        channel_ids = []
        if self.channel_ids.value.strip():
            for line in self.channel_ids.value.strip().split('\n'):
                cid = line.strip()
                if cid.isdigit():
                    channel_ids.append(cid)
        
        await self.cog.update_allowed_channels(self.guild_id, channel_ids)
        
        if channel_ids:
            await interaction.response.send_message(
                f"✅ 已更新允许的论坛频道（{len(channel_ids)} 个）",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "✅ 已清除论坛频道限制（所有论坛频道都可使用）",
                ephemeral=True
            )


class AddRuleModal(discord.ui.Modal, title="添加规则"):
    """添加规则Modal"""
    
    trigger = discord.ui.TextInput(
        label="触发词（多个用逗号分隔）",
        placeholder="你好, hello, 回顶",
        max_length=200
    )
    
    trigger_mode = discord.ui.TextInput(
        label="匹配模式（精确/前缀/包含/正则）",
        placeholder="精确=完全一致 | 前缀=以此开头 | 包含=包含此文字 | 正则=正则表达式",
        default="精确",
        max_length=20
    )
    
    action_type = discord.ui.TextInput(
        label="动作类型（回复/回顶/反应/回复并反应）",
        placeholder="回复=发送消息 | 回顶=顶帖效果 | 反应=添加表情",
        default="回复",
        max_length=20
    )
    
    reply_content = discord.ui.TextInput(
        label="回复内容（纯文本或JSON格式embed）",
        style=discord.TextStyle.paragraph,
        placeholder='普通文本 或 {"title":"标题","description":"描述","color":65280}',
        required=False,
        max_length=2000
    )
    
    delete_delay = discord.ui.TextInput(
        label="删除延迟秒数（可选，0或留空不删除）",
        placeholder="300",
        required=False,
        max_length=10
    )
    
    def __init__(self, cog: ThreadCommandCog, guild_id: str, scope: str, thread_id: Optional[str]):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.scope = scope
        self.thread_id = thread_id
    
    async def on_submit(self, interaction: discord.Interaction):
        # 解析触发词
        trigger_list = [t.strip() for t in self.trigger.value.split(',') if t.strip()]
        if not trigger_list:
            await interaction.response.send_message("❌ 触发词不能为空", ephemeral=True)
            return
        
        # 验证匹配模式（支持中英文）
        mode_input = self.trigger_mode.value.strip()
        mode = MATCH_MODE_MAP.get(mode_input) or MATCH_MODE_MAP.get(mode_input.lower())
        if not mode:
            mode = 'exact'
        
        # 验证动作类型（支持中英文）
        action_input = self.action_type.value.strip()
        action = ACTION_TYPE_MAP.get(action_input) or ACTION_TYPE_MAP.get(action_input.lower())
        if not action:
            action = 'reply'
        
        # 解析删除延迟
        delete_delay = None
        if self.delete_delay.value.strip():
            try:
                delay = int(self.delete_delay.value.strip())
                if delay > 0:
                    delete_delay = delay
            except:
                pass
        
        # 验证正则表达式
        if mode == 'regex':
            import re
            for t in trigger_list:
                try:
                    re.compile(t)
                except re.error as e:
                    await interaction.response.send_message(
                        f"❌ 正则表达式无效: {t}\n错误: {e}",
                        ephemeral=True
                    )
                    return
        
        # 创建规则
        reply_content = self.reply_content.value.strip() if self.reply_content.value else None
        
        rule_id = await self.cog.add_rule(
            self.guild_id,
            self.scope,
            trigger_list,
            mode,
            action,
            reply_content,
            delete_delay,
            str(interaction.user.id),
            self.thread_id
        )
        
        # 获取规则显示编号
        scope_prefix = "全服" if self.scope == 'server' else "帖子"
        if self.scope == 'server':
            # 查询该规则在全服规则中的序号
            all_rules = await self.cog.db.fetchall(
                "SELECT rule_id FROM thread_command_rules WHERE guild_id = ? AND scope = 'server' ORDER BY rule_id",
                (self.guild_id,)
            )
        else:
            # 查询该规则在帖子规则中的序号
            all_rules = await self.cog.db.fetchall(
                "SELECT rule_id FROM thread_command_rules WHERE thread_id = ? ORDER BY rule_id",
                (self.thread_id,)
            )
        
        rule_idx = 1
        for idx, r in enumerate(all_rules, 1):
            if r['rule_id'] == rule_id:
                rule_idx = idx
                break
        
        rule_display = f"{scope_prefix}{rule_idx}号"
        mode_display = MATCH_MODE_DISPLAY.get(mode, mode)
        action_display = ACTION_TYPE_DISPLAY.get(action, action)
        
        await interaction.response.send_message(
            f"✅ 已创建 {rule_display}\n"
            f"触发词: {', '.join(trigger_list)}\n"
            f"模式: {mode_display}\n"
            f"动作: {action_display}",
            ephemeral=True
        )


class PermissionPanelView(discord.ui.View):
    """权限管理面板视图"""
    
    def __init__(self, cog: ThreadCommandCog, guild_id: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self._setup_permission_select()
    
    def _setup_permission_select(self):
        """动态设置权限选择器"""
        # 需要在显示时动态加载权限列表
        pass
    
    @discord.ui.button(label="添加用户权限", style=discord.ButtonStyle.success, row=0)
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddPermissionModal(self.cog, self.guild_id, 'user')
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="添加身份组权限", style=discord.ButtonStyle.success, row=0)
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddPermissionModal(self.cog, self.guild_id, 'role')
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="删除权限", style=discord.ButtonStyle.danger, row=0)
    async def remove_perm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """显示权限列表供删除"""
        permissions = await self.cog.cache.get_permissions(self.guild_id)
        
        if not permissions:
            await interaction.response.send_message("📋 暂无权限配置", ephemeral=True)
            return
        
        # 构建权限列表嵌入
        embed = discord.Embed(
            title="🗑️ 删除权限",
            description="选择要删除的权限",
            color=0xe74c3c
        )
        
        perm_list = []
        for idx, perm in enumerate(permissions, 1):
            type_emoji = "👤" if perm.target_type == 'user' else "🏷️"
            if perm.target_type == 'user':
                perm_list.append(f"{idx}. {type_emoji} <@{perm.target_id}>")
            else:
                perm_list.append(f"{idx}. {type_emoji} <@&{perm.target_id}>")
        
        embed.add_field(
            name="当前权限列表",
            value='\n'.join(perm_list) if perm_list else "无",
            inline=False
        )
        
        view = PermissionDeleteView(self.cog, self.guild_id, permissions)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class AddPermissionModal(discord.ui.Modal, title="添加权限"):
    """添加权限Modal"""
    
    target_id = discord.ui.TextInput(
        label="用户或身份组ID",
        placeholder="1234567890123456789",
        max_length=30
    )
    
    def __init__(self, cog: ThreadCommandCog, guild_id: str, target_type: str):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.target_type = target_type
        self.target_id.label = "用户ID" if target_type == 'user' else "身份组ID"
    
    async def on_submit(self, interaction: discord.Interaction):
        target_id = self.target_id.value.strip()
        
        if not target_id.isdigit():
            await interaction.response.send_message("❌ 无效的ID", ephemeral=True)
            return
        
        now = datetime.utcnow().isoformat()
        
        existing = await self.cog.db.fetchone(
            "SELECT * FROM thread_command_permissions WHERE guild_id = ? AND target_type = ? AND target_id = ?",
            (self.guild_id, self.target_type, target_id)
        )
        
        if existing:
            await interaction.response.send_message("⚠️ 该权限已存在", ephemeral=True)
            return
        
        await self.cog.db.execute(
            """INSERT INTO thread_command_permissions
               (guild_id, target_type, target_id, permission_level, created_by, created_at)
               VALUES (?, ?, ?, 'server_config', ?, ?)""",
            (self.guild_id, self.target_type, target_id, str(interaction.user.id), now)
        )
        
        await self.cog.cache.refresh_permissions(self.guild_id)
        
        type_name = "用户" if self.target_type == 'user' else "身份组"
        await interaction.response.send_message(
            f"✅ 已添加{type_name}权限: {target_id}",
            ephemeral=True
        )


class PermissionDeleteView(discord.ui.View):
    """权限删除选择视图"""
    
    def __init__(self, cog: 'ThreadCommandCog', guild_id: str, permissions: list):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild_id = guild_id
        self.permissions = permissions
        
        # 构建选择器选项
        if permissions:
            options = []
            for idx, perm in enumerate(permissions[:25], 1):
                type_label = "用户" if perm.target_type == 'user' else "身份组"
                options.append(discord.SelectOption(
                    label=f"{idx}. {type_label}: {perm.target_id}",
                    value=f"{perm.target_type}:{perm.target_id}",
                    description=f"权限级别: {perm.permission_level}"
                ))
            
            self.perm_select = discord.ui.Select(
                placeholder="选择要删除的权限...",
                options=options
            )
            self.perm_select.callback = self.on_select
            self.add_item(self.perm_select)
    
    async def on_select(self, interaction: discord.Interaction):
        """处理权限删除选择"""
        value = self.perm_select.values[0]
        target_type, target_id = value.split(':', 1)
        
        # 删除权限
        await self.cog.db.execute(
            "DELETE FROM thread_command_permissions WHERE guild_id = ? AND target_type = ? AND target_id = ?",
            (self.guild_id, target_type, target_id)
        )
        
        await self.cog.cache.refresh_permissions(self.guild_id)
        
        type_name = "用户" if target_type == 'user' else "身份组"
        await interaction.response.send_message(
            f"✅ 已删除{type_name}权限: {target_id}",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    """Cog设置函数"""
    await bot.add_cog(ThreadCommandCog(bot))