# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
from discord import app_commands
import typing
import datetime
import json
import asyncio
import logging

from .base_cog import BaseCog
from core.database import DatabaseManager
from core.models import BlacklistEntry, BanEntry
from core.constants import BEIJING_TZ
from utils.permissions import is_gym_master
from utils.formatters import FormatUtils
from utils.logger import get_logger
from views.panel_views import ConfirmationView, PaginatorView

logger = get_logger(__name__)


class BlacklistPaginatorView(PaginatorView):
    """黑名单列表分页视图"""
    
    async def create_embed(self) -> discord.Embed:
        """创建黑名单列表嵌入消息"""
        start_index = self.current_page * self.entries_per_page
        end_index = start_index + self.entries_per_page
        page_entries = self.entries[start_index:end_index]
        
        embed = discord.Embed(
            title=f"「{self.interaction.guild.name}」黑名单列表 (共 {len(self.entries)} 人)",
            color=discord.Color.dark_red()
        )
        
        if not page_entries:
            embed.description = "这一页没有内容。"
        else:
            description_lines = []
            guild = self.interaction.guild
            
            for entry in page_entries:
                target_id_str = entry['target_id']
                target_type = entry['target_type']
                target_id = int(target_id_str)
                
                # 尝试解析用户/身份组名称
                target_display = ""
                if target_type == 'user':
                    member = guild.get_member(target_id)
                    if member:
                        target_display = f"{member.display_name} (<@{target_id}>)"
                    else:
                        target_display = f"[已离开的用户] (`{target_id_str}`)"
                elif target_type == 'role':
                    role = guild.get_role(target_id)
                    if role:
                        target_display = f"{role.name} (<@&{target_id}>)"
                    else:
                        target_display = f"[已删除的身份组] (`{target_id_str}`)"
                
                reason = entry.get('reason', '无')
                added_by_id = entry.get('added_by', '未知')
                
                # 格式化操作人
                operator_str = f"<@{added_by_id}>" if added_by_id.isdigit() else added_by_id
                
                # 解析时间戳
                try:
                    timestamp_dt = datetime.datetime.fromisoformat(entry['timestamp']).astimezone(BEIJING_TZ)
                    timestamp_str = timestamp_dt.strftime('%Y-%m-%d %H:%M')
                except (ValueError, TypeError):
                    timestamp_str = "未知时间"
                
                line = (
                    f"**对象**: {target_display}\n"
                    f"**原因**: {reason}\n"
                    f"**操作人**: {operator_str}\n"
                    f"**时间**: {timestamp_str}\n"
                    "---"
                )
                description_lines.append(line)
            
            embed.description = "\n".join(description_lines)
        
        embed.set_footer(text=f"第 {self.current_page + 1}/{self.total_pages} 页")
        return embed


class BanListPaginatorView(PaginatorView):
    """封禁列表分页视图"""
    
    async def create_embed(self) -> discord.Embed:
        """创建封禁列表嵌入消息"""
        start_index = self.current_page * self.entries_per_page
        end_index = start_index + self.entries_per_page
        page_entries = self.entries[start_index:end_index]
        
        embed = discord.Embed(
            title=f"「{self.interaction.guild.name}」挑战封禁列表 (共 {len(self.entries)} 条)",
            color=discord.Color.from_rgb(139, 0, 0)
        )
        
        if not page_entries:
            embed.description = "这一页没有内容。"
        else:
            description_lines = []
            guild = self.interaction.guild
            
            for entry in page_entries:
                target_id_str = entry['target_id']
                target_type = entry['target_type']
                target_id = int(target_id_str)
                
                target_display = ""
                if target_type == 'user':
                    member = guild.get_member(target_id)
                    if member:
                        target_display = f"{member.display_name} (<@{target_id}>)"
                    else:
                        target_display = f"[已离开的用户] (`{target_id_str}`)"
                elif target_type == 'role':
                    role = guild.get_role(target_id)
                    if role:
                        target_display = f"{role.name} (<@&{target_id}>)"
                    else:
                        target_display = f"[已删除的身份组] (`{target_id_str}`)"
                
                reason = entry.get('reason', '无')
                added_by_id = entry.get('added_by', '未知')
                
                operator_str = f"<@{added_by_id}>" if added_by_id.isdigit() else added_by_id
                
                try:
                    timestamp_dt = datetime.datetime.fromisoformat(entry['timestamp']).astimezone(BEIJING_TZ)
                    timestamp_str = timestamp_dt.strftime('%Y-%m-%d %H:%M')
                except (ValueError, TypeError):
                    timestamp_str = "未知时间"
                
                line = (
                    f"**对象**: {target_display}\n"
                    f"**原因**: {reason}\n"
                    f"**操作人**: {operator_str}\n"
                    f"**时间**: {timestamp_str}\n"
                    "---"
                )
                description_lines.append(line)
            
            embed.description = "\n".join(description_lines)
        
        embed.set_footer(text=f"第 {self.current_page + 1}/{self.total_pages} 页")
        return embed


class ModerationCog(BaseCog):
    """
    管理功能模块
    负责黑名单和封禁列表的管理
    """
    
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.db = DatabaseManager()
    
    # ========== 黑名单管理 ==========
    
    async def add_to_blacklist(
        self, 
        guild_id: str, 
        target_id: str, 
        target_type: str, 
        reason: str, 
        added_by: str
    ):
        """添加用户或身份组到黑名单"""
        timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
        async with self.db.get_connection() as conn:
            await conn.execute(
                """INSERT OR REPLACE INTO cheating_blacklist 
                   (guild_id, target_id, target_type, reason, added_by, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guild_id, target_id, target_type, reason, added_by, timestamp)
            )
            await conn.commit()
    
    async def add_to_blacklist_bulk(
        self, 
        guild_id: str, 
        members: list[discord.Member], 
        reason: str, 
        added_by: str
    ) -> int:
        """批量添加成员到黑名单"""
        timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
        records = [
            (guild_id, str(member.id), 'user', reason, added_by, timestamp)
            for member in members
        ]
        
        if not records:
            return 0
        
        async with self.db.get_connection() as conn:
            await conn.executemany(
                """INSERT OR REPLACE INTO cheating_blacklist 
                   (guild_id, target_id, target_type, reason, added_by, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                records
            )
            await conn.commit()
        return len(records)
    
    async def remove_from_blacklist(self, guild_id: str, target_id: str) -> int:
        """从黑名单移除用户或身份组"""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM cheating_blacklist WHERE guild_id = ? AND target_id = ?",
                (guild_id, target_id)
            )
            await conn.commit()
            return cursor.rowcount
    
    async def clear_blacklist(self, guild_id: str) -> int:
        """清空服务器的黑名单"""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM cheating_blacklist WHERE guild_id = ?",
                (guild_id,)
            )
            await conn.commit()
            return cursor.rowcount
    
    async def get_blacklist(self, guild_id: str) -> list:
        """获取服务器的黑名单列表"""
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                "SELECT * FROM cheating_blacklist WHERE guild_id = ? ORDER BY timestamp DESC",
                (guild_id,)
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    
    async def is_user_blacklisted(self, guild_id: str, user: discord.Member) -> typing.Optional[dict]:
        """检查用户或其身份组是否在黑名单中"""
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            
            # 检查用户ID
            async with conn.execute(
                "SELECT * FROM cheating_blacklist WHERE guild_id = ? AND target_id = ? AND target_type = 'user'",
                (guild_id, str(user.id))
            ) as cursor:
                user_blacklist_entry = await cursor.fetchone()
                if user_blacklist_entry:
                    return dict(user_blacklist_entry)
            
            # 检查用户的所有身份组
            role_ids = [str(role.id) for role in user.roles]
            if not role_ids:
                return None
            
            placeholders = ','.join('?' for _ in role_ids)
            query = f"""
                SELECT * FROM cheating_blacklist
                WHERE guild_id = ? AND target_type = 'role' AND target_id IN ({placeholders})
            """
            params = [guild_id] + role_ids
            async with conn.execute(query, params) as cursor:
                role_blacklist_entry = await cursor.fetchone()
                if role_blacklist_entry:
                    return dict(role_blacklist_entry)
        
        return None
    
    # ========== 封禁管理 ==========
    
    async def add_to_ban_list(
        self, 
        guild_id: str, 
        target_id: str, 
        target_type: str, 
        reason: str, 
        added_by: str
    ):
        """添加用户或身份组到封禁列表"""
        timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
        async with self.db.get_connection() as conn:
            await conn.execute(
                """INSERT OR REPLACE INTO challenge_ban_list 
                   (guild_id, target_id, target_type, reason, added_by, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guild_id, target_id, target_type, reason, added_by, timestamp)
            )
            await conn.commit()
    
    async def remove_from_ban_list(self, guild_id: str, target_id: str) -> int:
        """从封禁列表移除用户或身份组"""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM challenge_ban_list WHERE guild_id = ? AND target_id = ?",
                (guild_id, target_id)
            )
            await conn.commit()
            return cursor.rowcount
    
    async def get_ban_list(self, guild_id: str) -> list:
        """获取服务器的封禁列表"""
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                "SELECT * FROM challenge_ban_list WHERE guild_id = ? ORDER BY timestamp DESC",
                (guild_id,)
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    
    async def is_user_banned(self, guild_id: str, user: discord.Member) -> typing.Optional[dict]:
        """检查用户或其身份组是否在封禁列表中"""
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            
            # 检查用户ID
            async with conn.execute(
                "SELECT * FROM challenge_ban_list WHERE guild_id = ? AND target_id = ? AND target_type = 'user'",
                (guild_id, str(user.id))
            ) as cursor:
                user_ban_entry = await cursor.fetchone()
                if user_ban_entry:
                    return dict(user_ban_entry)
            
            # 检查用户的所有身份组
            role_ids = [str(role.id) for role in user.roles]
            if not role_ids:
                return None
            
            placeholders = ','.join('?' for _ in role_ids)
            query = f"""
                SELECT * FROM challenge_ban_list
                WHERE guild_id = ? AND target_type = 'role' AND target_id IN ({placeholders})
            """
            params = [guild_id] + role_ids
            async with conn.execute(query, params) as cursor:
                role_ban_entry = await cursor.fetchone()
                if role_ban_entry:
                    return dict(role_ban_entry)
        
        return None
    
    # ========== 斜杠命令 ==========
    
    @app_commands.command(name="道馆黑名单", description="管理作弊黑名单")
    @app_commands.describe(
        action="要执行的操作",
        target="[添加/移除] 操作的目标用户或身份组",
        role_target="[记录] 操作的目标身份组",
        reason="[添加/记录] 操作的原因"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="添加 (用户或身份组)", value="add"),
        app_commands.Choice(name="移除 (用户或身份组)", value="remove"),
        app_commands.Choice(name="记录 (身份组成员)", value="record_role"),
        app_commands.Choice(name="查看列表", value="view_list"),
        app_commands.Choice(name="清空 (!!!)", value="clear"),
    ])
    async def gym_blacklist(
        self,
        interaction: discord.Interaction,
        action: str,
        target: typing.Optional[typing.Union[discord.Member, discord.Role]] = None,
        role_target: typing.Optional[discord.Role] = None,
        reason: typing.Optional[str] = "无"
    ):
        """管理作弊黑名单"""
        # 权限检查
        if not await is_gym_master(interaction, "道馆黑名单"):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        guild_id = str(interaction.guild.id)
        added_by = str(interaction.user.id)
        
        if action == "add":
            if not target:
                return await interaction.response.send_message(
                    "❌ `添加` 操作需要一个 `target` (用户或身份组)。",
                    ephemeral=True
                )
            
            await interaction.response.defer(ephemeral=True, thinking=True)
            target_id = str(target.id)
            target_type = 'user' if isinstance(target, (discord.User, discord.Member)) else 'role'
            
            try:
                await self.add_to_blacklist(guild_id, target_id, target_type, reason, added_by)
                logger.info(f"User '{added_by}' added '{target_id}' ({target_type}) to blacklist in guild '{guild_id}'")
                await interaction.followup.send(
                    f"✅ 已成功将 {target.mention} 添加到黑名单。\n**原因:** {reason}",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error in blacklist add: {e}", exc_info=True)
                await interaction.followup.send("❌ 添加到黑名单时发生错误。", ephemeral=True)
        
        elif action == "remove":
            if not target:
                return await interaction.response.send_message(
                    "❌ `移除` 操作需要一个 `target` (用户或身份组)。",
                    ephemeral=True
                )
            
            await interaction.response.defer(ephemeral=True, thinking=True)
            target_id = str(target.id)
            
            try:
                removed_count = await self.remove_from_blacklist(guild_id, target_id)
                if removed_count > 0:
                    logger.info(f"User '{interaction.user.id}' removed '{target_id}' from blacklist")
                    await interaction.followup.send(
                        f"✅ 已成功将 {target.mention} 从黑名单中移除。",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"ℹ️ {target.mention} 不在黑名单中。",
                        ephemeral=True
                    )
            except Exception as e:
                logger.error(f"Error in blacklist remove: {e}", exc_info=True)
                await interaction.followup.send("❌ 从黑名单移除时发生错误。", ephemeral=True)
        
        elif action == "record_role":
            if not role_target:
                return await interaction.response.send_message(
                    "❌ `记录` 操作需要一个 `role_target` (身份组)。",
                    ephemeral=True
                )
            
            members_in_role = role_target.members
            member_count = len(members_in_role)
            
            if not members_in_role:
                await interaction.response.send_message(
                    f"ℹ️ 身份组 {role_target.mention} 中没有任何成员。",
                    ephemeral=True
                )
                return
            
            await interaction.response.send_message(
                f"✅ **任务已开始**\n正在后台记录身份组 {role_target.mention} 的 {member_count} 名成员。完成后将发送通知。",
                ephemeral=True
            )
            
            # 后台任务
            async def background_task():
                chunk_size = 1000
                total_added_count = 0
                try:
                    for i in range(0, member_count, chunk_size):
                        chunk = members_in_role[i:i + chunk_size]
                        if not chunk:
                            continue
                        
                        added_count = await self.add_to_blacklist_bulk(guild_id, chunk, reason, added_by)
                        total_added_count += added_count
                        logger.info(f"Processed blacklist chunk {i//chunk_size + 1}, added {added_count} members")
                        await asyncio.sleep(1)
                    
                    logger.info(f"User '{added_by}' bulk-added {total_added_count} members to blacklist")
                    await interaction.followup.send(
                        f"✅ **后台记录完成**\n- **身份组:** {role_target.mention}\n- **成功添加:** {total_added_count} 名成员",
                        ephemeral=True
                    )
                except Exception as e:
                    logger.error(f"Error in background blacklist task: {e}", exc_info=True)
                    await interaction.followup.send("❌ 批量记录黑名单时发生严重错误。", ephemeral=True)
            
            self.bot.loop.create_task(background_task())
        
        elif action == "clear":
            view = ConfirmationView()
            await interaction.response.send_message(
                "⚠️ **警告:** 此操作将永久删除本服务器的 **所有** 黑名单记录，且无法撤销。\n请确认你的操作。",
                view=view,
                ephemeral=True
            )
            
            await view.wait()
            if view.value:
                try:
                    deleted_count = await self.clear_blacklist(guild_id)
                    logger.info(f"User '{interaction.user.id}' cleared blacklist for guild '{guild_id}'")
                    await view.interaction.followup.send(
                        f"✅ 黑名单已成功清空，共删除了 {deleted_count} 条记录。",
                        ephemeral=True
                    )
                except Exception as e:
                    logger.error(f"Error in blacklist clear: {e}", exc_info=True)
                    await view.interaction.followup.send("❌ 清空黑名单时发生错误。", ephemeral=True)
        
        elif action == "view_list":
            await interaction.response.defer(ephemeral=True, thinking=True)
            blacklist_entries = await self.get_blacklist(guild_id)
            
            if not blacklist_entries:
                await interaction.followup.send("✅ 本服务器的黑名单是空的。", ephemeral=True)
                return
            
            view = BlacklistPaginatorView(interaction, blacklist_entries)
            embed = await view.create_embed()
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    @app_commands.command(name="道馆封禁", description="管理挑战封禁名单")
    @app_commands.describe(
        action="要执行的操作",
        target="[添加/移除] 操作的目标用户或身份组",
        reason="[添加] 操作的原因"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="添加 (用户或身份组)", value="add"),
        app_commands.Choice(name="移除 (用户或身份组)", value="remove"),
        app_commands.Choice(name="查看列表", value="view_list"),
    ])
    async def gym_ban(
        self,
        interaction: discord.Interaction,
        action: str,
        target: typing.Optional[typing.Union[discord.Member, discord.Role]] = None,
        reason: typing.Optional[str] = "无"
    ):
        """管理挑战封禁名单"""
        # 权限检查
        if not await is_gym_master(interaction, "道馆封禁"):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        guild_id = str(interaction.guild.id)
        added_by = str(interaction.user.id)
        
        if action == "add":
            if not target:
                return await interaction.response.send_message(
                    "❌ `添加` 操作需要一个 `target` (用户或身份组)。",
                    ephemeral=True
                )
            
            await interaction.response.defer(ephemeral=True, thinking=True)
            target_id = str(target.id)
            target_type = 'user' if isinstance(target, (discord.User, discord.Member)) else 'role'
            
            try:
                await self.add_to_ban_list(guild_id, target_id, target_type, reason, added_by)
                logger.info(f"User '{added_by}' added '{target_id}' ({target_type}) to ban list")
                await interaction.followup.send(
                    f"✅ 已成功将 {target.mention} 添加到挑战封禁名单。\n**原因:** {reason}",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error in ban list add: {e}", exc_info=True)
                await interaction.followup.send("❌ 添加到封禁名单时发生错误。", ephemeral=True)
        
        elif action == "remove":
            if not target:
                return await interaction.response.send_message(
                    "❌ `移除` 操作需要一个 `target` (用户或身份组)。",
                    ephemeral=True
                )
            
            await interaction.response.defer(ephemeral=True, thinking=True)
            target_id = str(target.id)
            
            try:
                removed_count = await self.remove_from_ban_list(guild_id, target_id)
                if removed_count > 0:
                    logger.info(f"User '{interaction.user.id}' removed '{target_id}' from ban list")
                    await interaction.followup.send(
                        f"✅ 已成功将 {target.mention} 从挑战封禁名单中移除。",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"ℹ️ {target.mention} 不在挑战封禁名单中。",
                        ephemeral=True
                    )
            except Exception as e:
                logger.error(f"Error in ban list remove: {e}", exc_info=True)
                await interaction.followup.send("❌ 从封禁名单移除时发生错误。", ephemeral=True)
        
        elif action == "view_list":
            await interaction.response.defer(ephemeral=True, thinking=True)
            ban_list_entries = await self.get_ban_list(guild_id)
            
            if not ban_list_entries:
                await interaction.followup.send("✅ 本服务器的挑战封禁名单是空的。", ephemeral=True)
                return
            
            view = BanListPaginatorView(interaction, ban_list_entries)
            embed = await view.create_embed()
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(ModerationCog(bot))
    logger.info("ModerationCog has been added to bot")