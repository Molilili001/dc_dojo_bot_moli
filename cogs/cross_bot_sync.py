# -*- coding: utf-8 -*-

import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import asyncio
import datetime
import aiohttp
from typing import Optional, Dict, List, Set, Tuple
from collections import defaultdict
import re

from .base_cog import BaseCog
from core.database import DatabaseManager
from core.constants import BEIJING_TZ, CONFIG_PATH
from utils.logger import get_logger
from utils.permissions import is_gym_master

logger = get_logger(__name__)


class PunishmentSyncData:
    """处罚同步数据结构"""
    def __init__(self, user_id: str, reason: str, source_bot_id: str, 
                 punishment_type: str = "blacklist", additional_data: Dict = None):
        self.user_id = user_id
        self.reason = reason
        self.source_bot_id = source_bot_id
        self.punishment_type = punishment_type
        self.additional_data = additional_data or {}
        self.timestamp = datetime.datetime.now(BEIJING_TZ)


class CrossBotSyncCog(BaseCog):
    """
    跨bot联动同步模块
    支持多个bot之间的处罚同步、身份组管理等功能
    """
    
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.db = DatabaseManager()
        self.sync_config = self.load_sync_config()
        
        # 用户级别的锁，防止并发处理
        self.user_locks = defaultdict(asyncio.Lock)
        
        # 批量处理队列
        self.punishment_queue: List[PunishmentSyncData] = []
        self.role_removal_queue: Dict[str, Set[str]] = defaultdict(set)  # user_id -> role_ids
        
        # 启动定时任务
        self.batch_processor.start()
        self.sync_status_reporter.start()
        
        # 跟踪处理状态
        self.processed_messages: Set[int] = set()  # 已处理的消息ID，防止重复处理
        self.sync_statistics = {
            "total_synced": 0,
            "failed_syncs": 0,
            "last_sync_time": None
        }
    
    def load_sync_config(self) -> Dict:
        """加载联动配置"""
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                    # 兼容旧配置格式
                    monitor_config = config.get("AUTO_BLACKLIST_MONITOR", {})
                    
                    # 构建新的多bot配置
                    if monitor_config.get("enabled"):
                        # 支持多个目标bot
                        target_bot_ids = monitor_config.get("target_bot_ids", [])
                        if not target_bot_ids and monitor_config.get("target_bot_id"):
                            target_bot_ids = [monitor_config.get("target_bot_id")]
                        
                        return {
                            "enabled": True,
                            "target_bot_ids": target_bot_ids,
                            "monitor_channel_id": monitor_config.get("monitor_channel_id"),
                            "sync_modes": monitor_config.get("sync_modes", ["punishment", "role_removal"]),
                            "auto_role_removal": monitor_config.get("auto_role_removal", True),
                            "batch_size": monitor_config.get("batch_size", 10),
                            "batch_interval": monitor_config.get("batch_interval", 5)  # 秒
                        }
        except Exception as e:
            logger.error(f"Failed to load sync config: {e}")
        
        return {
            "enabled": False,
            "target_bot_ids": [],
            "monitor_channel_id": None,
            "sync_modes": ["punishment", "role_removal"],
            "auto_role_removal": True,
            "batch_size": 10,
            "batch_interval": 5
        }
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """监听消息事件，处理来自其他bot的同步消息"""
        # 忽略已处理的消息
        if message.id in self.processed_messages:
            return
        
        # 忽略机器人自己的消息和私聊消息
        if message.author == self.bot.user or not message.guild:
            return
        
        # 检查配置
        if not self.sync_config.get("enabled", False):
            return
        
        target_bot_ids = self.sync_config.get("target_bot_ids", [])
        monitor_channel_id = self.sync_config.get("monitor_channel_id")
        
        if not target_bot_ids or not monitor_channel_id:
            return
        
        # 检查是否是目标机器人在目标频道的消息
        if str(message.author.id) not in [str(bid) for bid in target_bot_ids]:
            return
        
        if int(message.channel.id) != int(monitor_channel_id):
            return
        
        # 标记为已处理
        self.processed_messages.add(message.id)
        
        # 限制缓存大小
        if len(self.processed_messages) > 1000:
            self.processed_messages = set(list(self.processed_messages)[-500:])
        
        # 处理同步消息
        await self.process_sync_message(message)
    
    async def process_sync_message(self, message: discord.Message):
        """处理同步消息"""
        logger.info(f"CROSS_BOT_SYNC: Processing message from bot {message.author.id}")
        
        try:
            # 尝试解析JSON消息
            data = self.parse_message_content(message.content)
            if not data:
                return
            
            # 处理不同类型的同步指令
            if "punish" in data:
                await self.queue_punishment(data, str(message.author.id))
            
            if "去除身份组" in data or "remove_roles" in data:
                await self.queue_role_removal(data, str(message.author.id))
            
            if "sync_request" in data:
                await self.handle_sync_request(data, message)
                
        except Exception as e:
            logger.error(f"CROSS_BOT_SYNC: Error processing message: {e}", exc_info=True)
    
    def parse_message_content(self, content: str) -> Optional[Dict]:
        """解析消息内容，支持JSON和代码块格式"""
        try:
            # 尝试直接解析JSON
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试从代码块中提取JSON
            json_pattern = r'```(?:json)?\n?(.*?)\n?```'
            match = re.search(json_pattern, content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
        return None
    
    async def queue_punishment(self, data: Dict, source_bot_id: str):
        """将处罚加入队列"""
        user_id = str(data.get("punish", data.get("user_id", "")))
        if not user_id or not user_id.isdigit():
            return
        
        reason = data.get("reason", "跨bot同步处罚")
        punishment_type = data.get("type", "blacklist")
        
        sync_data = PunishmentSyncData(
            user_id=user_id,
            reason=reason,
            source_bot_id=source_bot_id,
            punishment_type=punishment_type,
            additional_data=data
        )
        
        self.punishment_queue.append(sync_data)
        logger.info(f"CROSS_BOT_SYNC: Queued punishment for user {user_id}")
    
    async def queue_role_removal(self, data: Dict, source_bot_id: str):
        """将身份组移除加入队列"""
        user_id = str(data.get("用户id", data.get("user_id", "")))
        if not user_id or not user_id.isdigit():
            return
        
        # 解析身份组ID
        role_ids_str = data.get("去除身份组", data.get("remove_roles", ""))
        if isinstance(role_ids_str, str):
            role_ids = [rid.strip() for rid in role_ids_str.split(",") if rid.strip()]
        elif isinstance(role_ids_str, list):
            role_ids = [str(rid) for rid in role_ids_str]
        else:
            return
        
        self.role_removal_queue[user_id].update(role_ids)
        logger.info(f"CROSS_BOT_SYNC: Queued role removal for user {user_id}: {role_ids}")
    
    @tasks.loop(seconds=5)
    async def batch_processor(self):
        """批量处理队列中的任务"""
        try:
            # 处理处罚队列
            if self.punishment_queue:
                batch_size = self.sync_config.get("batch_size", 10)
                batch = self.punishment_queue[:batch_size]
                self.punishment_queue = self.punishment_queue[batch_size:]
                
                for sync_data in batch:
                    await self.process_punishment_sync(sync_data)
            
            # 处理身份组移除队列
            if self.role_removal_queue:
                for user_id, role_ids in list(self.role_removal_queue.items()):
                    await self.process_role_removal(user_id, role_ids)
                    del self.role_removal_queue[user_id]
                    
        except Exception as e:
            logger.error(f"CROSS_BOT_SYNC: Batch processor error: {e}", exc_info=True)
    
    @tasks.loop(minutes=30)
    async def sync_status_reporter(self):
        """定期报告同步状态"""
        if self.sync_statistics["total_synced"] > 0:
            logger.info(
                f"CROSS_BOT_SYNC Status: Total synced: {self.sync_statistics['total_synced']}, "
                f"Failed: {self.sync_statistics['failed_syncs']}, "
                f"Last sync: {self.sync_statistics['last_sync_time']}"
            )
    
    async def process_punishment_sync(self, sync_data: PunishmentSyncData):
        """处理单个处罚同步"""
        user_id = sync_data.user_id
        
        # 获取所有服务器进行同步
        for guild in self.bot.guilds:
            guild_id = str(guild.id)
            
            # 使用用户级别的锁
            async with self.user_locks[user_id]:
                try:
                    member = guild.get_member(int(user_id))
                    if not member:
                        continue
                    
                    # 添加到黑名单
                    if sync_data.punishment_type in ["blacklist", "ban"]:
                        await self.add_to_sync_blacklist(
                            guild_id, user_id, 
                            sync_data.reason, 
                            f"同步自Bot({sync_data.source_bot_id})"
                        )
                    
                    # 自动移除身份组
                    if self.sync_config.get("auto_role_removal", True):
                        await self.auto_remove_roles(member, guild_id, sync_data)
                    
                    # 重置用户进度
                    await self.reset_user_progress(user_id, guild_id)
                    
                    self.sync_statistics["total_synced"] += 1
                    self.sync_statistics["last_sync_time"] = datetime.datetime.now(BEIJING_TZ).isoformat()
                    
                    logger.info(f"CROSS_BOT_SYNC: Successfully synced punishment for user {user_id} in guild {guild_id}")
                    
                except Exception as e:
                    self.sync_statistics["failed_syncs"] += 1
                    logger.error(f"CROSS_BOT_SYNC: Failed to sync punishment for user {user_id}: {e}")
    
    async def process_role_removal(self, user_id: str, role_ids: Set[str]):
        """处理身份组移除"""
        for guild in self.bot.guilds:
            try:
                member = guild.get_member(int(user_id))
                if not member:
                    continue
                
                roles_to_remove = []
                for role_id in role_ids:
                    role = guild.get_role(int(role_id))
                    if role and role in member.roles:
                        roles_to_remove.append(role)
                
                if roles_to_remove:
                    await member.remove_roles(
                        *roles_to_remove, 
                        reason="跨bot同步 - 自动移除身份组"
                    )
                    logger.info(f"CROSS_BOT_SYNC: Removed {len(roles_to_remove)} roles from user {user_id} in guild {guild.id}")
                    
            except Exception as e:
                logger.error(f"CROSS_BOT_SYNC: Failed to remove roles for user {user_id}: {e}")
    
    async def add_to_sync_blacklist(self, guild_id: str, user_id: str, reason: str, added_by: str):
        """添加用户到同步黑名单"""
        timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
        async with self.db.get_connection() as conn:
            # 检查是否已存在
            existing = await conn.execute(
                "SELECT * FROM cheating_blacklist WHERE guild_id = ? AND target_id = ?",
                (guild_id, user_id)
            )
            if await existing.fetchone():
                # 更新现有记录
                await conn.execute(
                    """UPDATE cheating_blacklist 
                       SET reason = ?, added_by = ?, timestamp = ?
                       WHERE guild_id = ? AND target_id = ?""",
                    (reason, added_by, timestamp, guild_id, user_id)
                )
            else:
                # 插入新记录
                await conn.execute(
                    """INSERT INTO cheating_blacklist 
                       (guild_id, target_id, target_type, reason, added_by, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (guild_id, user_id, 'user', reason, added_by, timestamp)
                )
            await conn.commit()
    
    async def auto_remove_roles(self, member: discord.Member, guild_id: str, sync_data: PunishmentSyncData):
        """自动移除用户的特定身份组"""
        try:
            # 获取配置的自动移除身份组规则
            removed_roles = []
            removed_role_ids = []
            
            # 移除毕业奖励身份组
            graduation_roles = await self.get_graduation_roles(guild_id)
            for role_id in graduation_roles:
                role = member.guild.get_role(int(role_id))
                if role and role in member.roles:
                    await member.remove_roles(role, reason=f"跨bot同步处罚 - {sync_data.reason}")
                    removed_roles.append(role)
                    removed_role_ids.append(str(role_id))
            
            # 移除特权身份组
            privilege_roles = await self.get_privilege_roles(guild_id)
            for role_id in privilege_roles:
                role = member.guild.get_role(int(role_id))
                if role and role in member.roles:
                    await member.remove_roles(role, reason=f"跨bot同步处罚 - {sync_data.reason}")
                    removed_roles.append(role)
                    removed_role_ids.append(str(role_id))
            
            if removed_roles:
                logger.info(f"CROSS_BOT_SYNC: Auto-removed {len(removed_roles)} roles from user {member.id}")
                
                # 发送身份组移除记录到监控频道
                await self.send_role_removal_record(member, removed_role_ids)
                
        except Exception as e:
            logger.error(f"CROSS_BOT_SYNC: Failed to auto-remove roles: {e}")
    
    async def send_role_removal_record(self, member: discord.Member, removed_role_ids: List[str]):
        """发送身份组移除记录到监控频道"""
        try:
            monitor_channel_id = self.sync_config.get("monitor_channel_id")
            if not monitor_channel_id:
                logger.warning("CROSS_BOT_SYNC: No monitor_channel_id configured for role removal record")
                return
            
            channel = self.bot.get_channel(int(monitor_channel_id))
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(int(monitor_channel_id))
                except (discord.NotFound, discord.Forbidden):
                    logger.error(f"CROSS_BOT_SYNC: Cannot access monitoring channel {monitor_channel_id}")
                    return
            
            # 创建JSON记录
            record = {
                "去除身份组": ",".join(removed_role_ids),
                "用户id": str(member.id)
            }
            
            # 发送JSON消息
            json_message = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
            await channel.send(f"```json\n{json_message}\n```")
            
            logger.info(f"CROSS_BOT_SYNC: Sent role removal record for user {member.id}: {removed_role_ids}")
            
        except Exception as e:
            logger.error(f"CROSS_BOT_SYNC: Failed to send role removal record: {e}")
    
    async def get_graduation_roles(self, guild_id: str) -> List[str]:
        """获取毕业奖励身份组ID列表"""
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                """SELECT role_to_add_ids FROM challenge_panels
                   WHERE guild_id = ?
                   AND role_to_add_ids IS NOT NULL
                   AND (associated_gyms IS NULL OR associated_gyms = '' OR associated_gyms = '[]')
                   AND (completion_threshold IS NULL OR completion_threshold = 0)
                   AND (is_ultimate_gym IS NULL OR is_ultimate_gym = FALSE)""",
                (guild_id,)
            ) as cursor:
                panels = await cursor.fetchall()
        
        role_ids = []
        for panel in panels:
            if panel['role_to_add_ids']:
                role_ids.extend(json.loads(panel['role_to_add_ids']))
        
        return role_ids
    
    async def get_privilege_roles(self, guild_id: str) -> List[str]:
        """获取特权身份组ID列表（可以从配置文件或数据库中读取）"""
        # 这里可以扩展为从配置或数据库读取
        # 目前返回空列表，可根据需要添加逻辑
        return []
    
    async def reset_user_progress(self, user_id: str, guild_id: str):
        """重置用户的所有进度"""
        async with self.db.get_connection() as conn:
            # 重置道馆进度
            await conn.execute(
                "DELETE FROM user_progress WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )
            
            # 重置失败记录
            await conn.execute(
                "DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )
            
            # 重置已领取奖励
            await conn.execute(
                "DELETE FROM claimed_role_rewards WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )
            
            # 重置排行榜
            await conn.execute(
                "DELETE FROM ultimate_gym_leaderboard WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )
            
            await conn.commit()
            logger.info(f"CROSS_BOT_SYNC: Reset all progress for user {user_id} in guild {guild_id}")
    
    async def send_role_removal_notification(self, member: discord.Member, removed_roles: List[discord.Role], 
                                            sync_data: PunishmentSyncData):
        """发送身份组移除通知"""
        try:
            monitor_channel_id = self.sync_config.get("monitor_channel_id")
            if not monitor_channel_id:
                return
            
            channel = self.bot.get_channel(int(monitor_channel_id))
            if not channel:
                return
            
            # 创建通知消息
            notification = {
                "type": "role_removal_sync",
                "user_id": str(member.id),
                "removed_roles": [str(role.id) for role in removed_roles],
                "reason": sync_data.reason,
                "source_bot": sync_data.source_bot_id,
                "timestamp": sync_data.timestamp.isoformat()
            }
            
            # 发送JSON通知
            json_message = json.dumps(notification, ensure_ascii=False, indent=2)
            await channel.send(f"```json\n{json_message}\n```")
            
        except Exception as e:
            logger.error(f"CROSS_BOT_SYNC: Failed to send notification: {e}")
    
    async def handle_sync_request(self, data: Dict, message: discord.Message):
        """处理同步请求"""
        request_type = data.get("sync_request")
        
        if request_type == "status":
            # 返回同步状态
            await self.send_sync_status(message.channel)
        elif request_type == "force_sync":
            # 强制同步特定用户
            user_id = data.get("user_id")
            if user_id:
                await self.force_sync_user(user_id, message.channel)
    
    async def send_sync_status(self, channel: discord.TextChannel):
        """发送同步状态"""
        status = {
            "bot_id": str(self.bot.user.id),
            "sync_enabled": self.sync_config.get("enabled"),
            "statistics": self.sync_statistics,
            "queue_size": {
                "punishment": len(self.punishment_queue),
                "role_removal": len(self.role_removal_queue)
            }
        }
        
        json_message = json.dumps(status, ensure_ascii=False, indent=2)
        await channel.send(f"```json\n{json_message}\n```")
    
    async def force_sync_user(self, user_id: str, channel: discord.TextChannel):
        """强制同步特定用户"""
        try:
            # 在所有服务器中查找并同步用户
            sync_count = 0
            for guild in self.bot.guilds:
                member = guild.get_member(int(user_id))
                if member:
                    # 检查黑名单状态
                    blacklist_entry = await self.check_user_blacklist(str(guild.id), user_id)
                    if blacklist_entry:
                        # 执行同步操作
                        sync_data = PunishmentSyncData(
                            user_id=user_id,
                            reason="强制同步",
                            source_bot_id=str(self.bot.user.id),
                            punishment_type="blacklist"
                        )
                        await self.process_punishment_sync(sync_data)
                        sync_count += 1
            
            result = {
                "type": "force_sync_result",
                "user_id": user_id,
                "synced_guilds": sync_count,
                "timestamp": datetime.datetime.now(BEIJING_TZ).isoformat()
            }
            
            json_message = json.dumps(result, ensure_ascii=False, indent=2)
            await channel.send(f"```json\n{json_message}\n```")
            
        except Exception as e:
            logger.error(f"CROSS_BOT_SYNC: Force sync failed: {e}")
    
    async def check_user_blacklist(self, guild_id: str, user_id: str) -> Optional[Dict]:
        """检查用户是否在黑名单中"""
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                "SELECT * FROM cheating_blacklist WHERE guild_id = ? AND target_id = ?",
                (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    
    # ========== 斜杠命令 ==========
    
    @app_commands.command(name="联动同步", description="管理跨bot联动同步功能")
    @app_commands.describe(
        action="要执行的操作",
        target="目标用户",
        reason="操作原因"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="查看状态", value="status"),
        app_commands.Choice(name="强制同步用户", value="force_sync"),
        app_commands.Choice(name="清理队列", value="clear_queue"),
        app_commands.Choice(name="重载配置", value="reload_config")
    ])
    async def sync_management(
        self,
        interaction: discord.Interaction,
        action: str,
        target: Optional[discord.Member] = None,
        reason: Optional[str] = None
    ):
        """管理跨bot联动同步功能"""
        # 权限检查
        if not await is_gym_master(interaction, "联动同步"):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        if action == "status":
            await interaction.response.defer(ephemeral=True)
            
            embed = discord.Embed(
                title="🔄 跨Bot联动同步状态",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now(BEIJING_TZ)
            )
            
            embed.add_field(
                name="启用状态",
                value="✅ 已启用" if self.sync_config.get("enabled") else "❌ 已禁用",
                inline=True
            )
            
            embed.add_field(
                name="监控Bot数量",
                value=len(self.sync_config.get("target_bot_ids", [])),
                inline=True
            )
            
            embed.add_field(
                name="同步统计",
                value=f"总计: {self.sync_statistics['total_synced']}\n"
                      f"失败: {self.sync_statistics['failed_syncs']}",
                inline=True
            )
            
            embed.add_field(
                name="队列状态",
                value=f"处罚队列: {len(self.punishment_queue)}\n"
                      f"身份组队列: {len(self.role_removal_queue)}",
                inline=True
            )
            
            if self.sync_statistics['last_sync_time']:
                embed.add_field(
                    name="最后同步",
                    value=self.sync_statistics['last_sync_time'],
                    inline=False
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        elif action == "force_sync":
            if not target:
                await interaction.response.send_message(
                    "❌ 请指定要同步的用户。",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=True)
            
            # 创建强制同步数据
            sync_data = PunishmentSyncData(
                user_id=str(target.id),
                reason=reason or "管理员强制同步",
                source_bot_id=str(self.bot.user.id),
                punishment_type="blacklist"
            )
            
            await self.process_punishment_sync(sync_data)
            
            await interaction.followup.send(
                f"✅ 已强制同步用户 {target.mention} 的处罚状态。",
                ephemeral=True
            )
        
        elif action == "clear_queue":
            await interaction.response.defer(ephemeral=True)
            
            punishment_count = len(self.punishment_queue)
            role_count = len(self.role_removal_queue)
            
            self.punishment_queue.clear()
            self.role_removal_queue.clear()
            
            await interaction.followup.send(
                f"✅ 已清理队列：\n"
                f"- 处罚队列: {punishment_count} 条\n"
                f"- 身份组队列: {role_count} 条",
                ephemeral=True
            )
        
        elif action == "reload_config":
            await interaction.response.defer(ephemeral=True)
            
            self.sync_config = self.load_sync_config()
            
            await interaction.followup.send(
                f"✅ 配置已重新加载。\n"
                f"启用状态: {'✅ 已启用' if self.sync_config.get('enabled') else '❌ 已禁用'}",
                ephemeral=True
            )
    
    async def cog_load(self):
        """Cog加载时的初始化"""
        logger.info("CrossBotSyncCog loaded")
        if self.sync_config.get("enabled"):
            bot_ids = self.sync_config.get("target_bot_ids", [])
            logger.info(f"Cross-bot sync enabled for {len(bot_ids)} bots")
    
    async def cog_unload(self):
        """Cog卸载时的清理"""
        # 停止定时任务
        self.batch_processor.cancel()
        self.sync_status_reporter.cancel()
        
        # 清理锁和队列
        self.user_locks.clear()
        self.punishment_queue.clear()
        self.role_removal_queue.clear()
        
        logger.info("CrossBotSyncCog unloaded")


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(CrossBotSyncCog(bot))
    logger.info("CrossBotSyncCog has been added to bot")