# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
import json
import asyncio
import datetime
import logging
from typing import Optional, Dict, List
from collections import defaultdict

from .base_cog import BaseCog
from core.database import DatabaseManager
from core.constants import BEIJING_TZ, CONFIG_PATH
from utils.logger import get_logger

logger = get_logger(__name__)


class AutoMonitorCog(BaseCog):
    """
    自动监控模块
    监控特定频道的JSON消息并自动执行相关处罚
    """
    
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.db = DatabaseManager()
        self.monitor_config = self.load_monitor_config()
        # 用户级别的锁，防止并发处理同一用户
        self.user_punishment_locks = defaultdict(asyncio.Lock)
        
    def load_monitor_config(self) -> Dict:
        """加载监控配置"""
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    return config.get("AUTO_BLACKLIST_MONITOR", {})
        except Exception as e:
            logger.error(f"Failed to load monitor config: {e}")
        return {}
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """监听消息事件"""
        # 忽略机器人自己的消息和私聊消息
        if message.author == self.bot.user or not message.guild:
            return
        
        # 检查监控配置
        if not self.monitor_config.get("enabled", False):
            return
        
        target_bot_id = self.monitor_config.get("target_bot_id")
        monitor_channel_id = self.monitor_config.get("monitor_channel_id")
        
        if not target_bot_id or not monitor_channel_id:
            return
        
        # 检查是否是目标机器人在目标频道的消息
        if int(message.author.id) != int(target_bot_id) or int(message.channel.id) != int(monitor_channel_id):
            return
        
        # 处理JSON消息
        await self.process_json_message(message)
    
    async def process_json_message(self, message: discord.Message):
        """处理JSON格式的消息"""
        logger.info(f"AUTO_MONITOR: Processing message from bot {message.author.id} in channel {message.channel.id}")
        
        try:
            data = json.loads(message.content)
            
            # 检查是否有处罚指令
            punished_user_id = data.get("punish")
            if isinstance(punished_user_id, (str, int)) and str(punished_user_id).isdigit():
                await self.handle_punishment(message.guild, str(punished_user_id))
                
        except json.JSONDecodeError as e:
            logger.warning(f"AUTO_MONITOR: Invalid JSON from bot {message.author.id}: {e}")
        except Exception as e:
            logger.error(f"AUTO_MONITOR: Error processing message: {e}", exc_info=True)
    
    async def handle_punishment(self, guild: discord.Guild, user_id: str):
        """处理用户处罚"""
        guild_id = str(guild.id)
        
        # 获取成员对象
        member = guild.get_member(int(user_id))
        if not member:
            try:
                member = await guild.fetch_member(int(user_id))
            except discord.NotFound:
                logger.warning(f"AUTO_PUNISHMENT: User {user_id} not found in guild {guild_id}")
                return
            except (discord.HTTPException, discord.Forbidden) as e:
                logger.error(f"AUTO_PUNISHMENT: Failed to fetch user {user_id}: {e}")
                return
        
        # 使用用户级别的锁防止并发处理
        async with self.user_punishment_locks[user_id]:
            try:
                reason = "因答题处罚被自动同步"
                added_by = f"自动同步自 ({self.monitor_config.get('target_bot_id', 'Unknown')})"
                
                # 添加到黑名单
                await self.add_to_blacklist(guild_id, user_id, reason, added_by)
                
                # 移除毕业奖励身份组
                await self.remove_graduation_roles(member, guild_id)
                
                # 重置用户进度
                await self.reset_user_progress(user_id, guild_id)
                
                logger.info(f"AUTO_PUNISHMENT: Successfully processed punishment for user {user_id} in guild {guild_id}")
                
            except Exception as e:
                logger.error(f"AUTO_PUNISHMENT: Failed to process punishment for user {user_id}: {e}", exc_info=True)
    
    async def add_to_blacklist(self, guild_id: str, user_id: str, reason: str, added_by: str):
        """添加用户到黑名单"""
        timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
        async with self.db.get_connection() as conn:
            await conn.execute(
                """INSERT OR REPLACE INTO cheating_blacklist 
                   (guild_id, target_id, target_type, reason, added_by, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guild_id, user_id, 'user', reason, added_by, timestamp)
            )
            await conn.commit()
    
    async def remove_graduation_roles(self, member: discord.Member, guild_id: str):
        """移除用户的毕业奖励身份组"""
        try:
            logger.info(f"AUTO_PUNISHMENT: Starting graduation role removal for user {member.id}")
            
            # 获取所有毕业面板配置
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
            
            logger.info(f"AUTO_PUNISHMENT: Found {len(panels)} graduation panels")
            
            removed_roles = []
            removed_role_details = []
            failed_removals = []
            
            for panel in panels:
                role_to_add_ids_json = panel['role_to_add_ids']
                if role_to_add_ids_json:
                    role_ids = json.loads(role_to_add_ids_json)
                    for role_id in role_ids:
                        role = member.guild.get_role(int(role_id))
                        if role and role in member.roles:
                            try:
                                await member.remove_roles(role, reason="自动同步处罚 - 移除全通关奖励身份组")
                                removed_roles.append(role.name)
                                removed_role_details.append({
                                    "role_id": role_id,
                                    "role_name": role.name
                                })
                                logger.info(f"AUTO_PUNISHMENT: Removed role {role.name} ({role_id}) from user {member.id}")
                            except discord.Forbidden:
                                failed_removals.append({"role_id": role_id, "role_name": role.name, "reason": "权限不足"})
                                logger.error(f"AUTO_PUNISHMENT: Lacks permission to remove role {role_id}")
                            except Exception as e:
                                failed_removals.append({"role_id": role_id, "role_name": role.name, "reason": str(e)})
                                logger.error(f"AUTO_PUNISHMENT: Failed to remove role {role_id}: {e}")
            
            # 发送身份组移除记录
            if removed_roles:
                logger.info(f"AUTO_PUNISHMENT: Successfully removed {len(removed_roles)} roles from user {member.id}")
                await self.send_role_removal_record(member, guild_id, removed_role_details)
            else:
                logger.info(f"AUTO_PUNISHMENT: No graduation roles found to remove for user {member.id}")
            
            if failed_removals:
                logger.warning(f"AUTO_PUNISHMENT: Failed to remove {len(failed_removals)} roles: {failed_removals}")
                
        except Exception as e:
            logger.error(f"AUTO_PUNISHMENT: Failed to remove graduation roles for user {member.id}: {e}", exc_info=True)
    
    async def send_role_removal_record(self, member: discord.Member, guild_id: str, removed_role_details: List[Dict]):
        """发送身份组移除记录到监控频道（只在移除身份组后发送）"""
        # 只有真正移除了身份组才发送记录
        if not removed_role_details:
            logger.info(f"AUTO_PUNISHMENT: No roles were removed for user {member.id}, skipping record")
            return
            
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                monitor_channel_id = self.monitor_config.get("monitor_channel_id")
                
                if not monitor_channel_id:
                    logger.warning("AUTO_PUNISHMENT: No monitor_channel_id configured")
                    return
                
                channel = self.bot.get_channel(int(monitor_channel_id))
                if not channel:
                    try:
                        channel = await self.bot.fetch_channel(int(monitor_channel_id))
                    except (discord.NotFound, discord.Forbidden):
                        logger.error(f"AUTO_PUNISHMENT: Cannot access monitoring channel {monitor_channel_id}")
                        return
                
                # 创建精简的JSON记录 - 只包含去除的身份组ID和用户ID
                role_ids = [detail["role_id"] for detail in removed_role_details]
                record = {
                    "去除身份组": ",".join(role_ids),
                    "用户id": str(member.id)
                }
                
                # 发送JSON记录
                json_message = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
                await channel.send(f"```json\n{json_message}\n```")
                
                logger.info(f"AUTO_PUNISHMENT: Sent role removal record for user {member.id}: removed {len(role_ids)} roles")
                return  # 成功发送，退出重试循环
                
            except discord.HTTPException as e:
                if attempt < max_retries - 1:
                    logger.warning(f"AUTO_PUNISHMENT: Failed to send record (attempt {attempt + 1}/{max_retries}): {e}")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                else:
                    logger.error(f"AUTO_PUNISHMENT: Failed to send record after {max_retries} attempts: {e}")
            except Exception as e:
                logger.error(f"AUTO_PUNISHMENT: Error sending role removal record: {e}", exc_info=True)
                break
    
    async def reset_user_progress(self, user_id: str, guild_id: str):
        """重置用户的所有进度"""
        async with self.db.get_connection() as conn:
            # 重置道馆进度
            p_cursor = await conn.execute(
                "DELETE FROM user_progress WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )
            p_count = p_cursor.rowcount
            
            # 重置失败记录
            f_cursor = await conn.execute(
                "DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )
            f_count = f_cursor.rowcount
            
            # 重置已领取奖励
            r_cursor = await conn.execute(
                "DELETE FROM claimed_role_rewards WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )
            r_count = r_cursor.rowcount
            
            # 重置究极排行榜
            u_cursor = await conn.execute(
                "DELETE FROM ultimate_gym_leaderboard WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )
            u_count = u_cursor.rowcount
            
            await conn.commit()
            
            logger.info(
                f"PROGRESS_RESET: Fully reset user {user_id} in guild {guild_id}. "
                f"Removed {p_count} progress, {f_count} failures, {r_count} rewards, {u_count} leaderboard scores"
            )
    
    async def cog_load(self):
        """Cog加载时的初始化"""
        logger.info("AutoMonitorCog loaded")
        # 重新加载配置，以防配置文件在运行时被更新
        self.monitor_config = self.load_monitor_config()
        if self.monitor_config.get("enabled"):
            logger.info(f"Auto monitoring enabled for bot {self.monitor_config.get('target_bot_id')} in channel {self.monitor_config.get('monitor_channel_id')}")
        else:
            logger.info("Auto monitoring is disabled")
    
    async def cog_unload(self):
        """Cog卸载时的清理"""
        logger.info("AutoMonitorCog unloaded")
        # 清理锁
        self.user_punishment_locks.clear()


async def process_json_punishment(bot: commands.Bot, message: discord.Message):
    """
    处理JSON格式的处罚消息
    这是一个辅助函数，可以从bot.py中调用
    """
    # 获取AutoMonitorCog实例
    cog = bot.get_cog("AutoMonitorCog")
    if cog and isinstance(cog, AutoMonitorCog):
        await cog.process_json_message(message)
    else:
        logger.warning("AutoMonitorCog not loaded, cannot process punishment message")


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(AutoMonitorCog(bot))
    logger.info("AutoMonitorCog has been added to bot")