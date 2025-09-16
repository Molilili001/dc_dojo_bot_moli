"""
模块名称: moderation_views.py
功能描述: 管理功能相关的视图组件，包括黑名单、封禁列表等
作者: @Kilo Code
创建日期: 2024-12-15
最后修改: 2024-12-15
"""

import discord
from discord import ui
from typing import List, Dict, Any, Optional
import math
from datetime import datetime

from core.constants import BEIJING_TZ
from utils.logger import get_logger

logger = get_logger(__name__)


class ConfirmationView(ui.View):
    """通用确认视图"""
    
    def __init__(self, timeout: int = 60):
        """
        初始化确认视图
        
        Args:
            timeout: 超时时间（秒）
        """
        super().__init__(timeout=timeout)
        self.confirmed = False
        self.interaction_response = None
    
    @ui.button(label="确认", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
        """确认按钮回调"""
        self.confirmed = True
        self.interaction_response = interaction
        self.stop()
    
    @ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        """取消按钮回调"""
        self.confirmed = False
        self.interaction_response = interaction
        self.stop()
    
    async def on_timeout(self):
        """超时处理"""
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True
        logger.debug("ConfirmationView timeout")


class ConfirmClearView(ui.View):
    """清空确认视图（黑名单专用）"""
    
    def __init__(self, guild_id: str, original_interaction: discord.Interaction):
        """
        初始化清空确认视图
        
        Args:
            guild_id: 服务器ID
            original_interaction: 原始交互对象
        """
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.original_interaction = original_interaction
        self.confirmed = False
    
    @ui.button(label="确认清空", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
        """确认清空按钮"""
        for item in self.children:
            item.disabled = True
        
        await self.original_interaction.edit_original_response(view=self)
        self.confirmed = True
        
        # 在具体的Cog中处理清空操作
        await interaction.response.defer(ephemeral=True, thinking=True)
        self.stop()
    
    @ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        """取消按钮"""
        for item in self.children:
            item.disabled = True
        
        await self.original_interaction.edit_original_response(
            content="操作已取消。",
            view=self
        )
        await interaction.response.defer()
        self.stop()
    
    async def on_timeout(self):
        """超时处理"""
        for item in self.children:
            item.disabled = True
        
        try:
            await self.original_interaction.edit_original_response(
                content="操作已超时，请重新发起指令。",
                view=self
            )
        except discord.NotFound:
            pass


class BlacklistPaginatorView(ui.View):
    """黑名单分页视图"""
    
    def __init__(self, interaction: discord.Interaction, entries: List[Dict[str, Any]], entries_per_page: int = 5):
        """
        初始化黑名单分页视图
        
        Args:
            interaction: 交互对象
            entries: 黑名单条目列表
            entries_per_page: 每页显示条目数
        """
        super().__init__(timeout=180)
        self.interaction = interaction
        self.entries = entries
        self.entries_per_page = entries_per_page
        self.current_page = 0
        self.total_pages = max(1, math.ceil(len(entries) / entries_per_page))
        self.update_buttons()
    
    def update_buttons(self):
        """更新按钮状态"""
        if len(self.children) >= 2:
            self.children[0].disabled = self.current_page == 0
            self.children[1].disabled = self.current_page >= self.total_pages - 1
    
    async def create_embed(self) -> discord.Embed:
        """
        创建黑名单Embed
        
        Returns:
            Discord Embed对象
        """
        start_index = self.current_page * self.entries_per_page
        end_index = min(start_index + self.entries_per_page, len(self.entries))
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
                
                # 解析目标显示
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
                    timestamp_dt = datetime.fromisoformat(entry['timestamp']).astimezone(BEIJING_TZ)
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
    
    async def show_page(self, interaction: discord.Interaction):
        """显示当前页"""
        self.update_buttons()
        embed = await self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    @ui.button(label="◀️ 上一页", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: ui.Button):
        """上一页按钮"""
        if self.current_page > 0:
            self.current_page -= 1
            await self.show_page(interaction)
    
    @ui.button(label="下一页 ▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        """下一页按钮"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.show_page(interaction)
    
    async def on_timeout(self):
        """超时处理"""
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
            pass


class BanListPaginatorView(ui.View):
    """封禁列表分页视图"""
    
    def __init__(self, interaction: discord.Interaction, entries: List[Dict[str, Any]], entries_per_page: int = 5):
        """
        初始化封禁列表分页视图
        
        Args:
            interaction: 交互对象
            entries: 封禁条目列表
            entries_per_page: 每页显示条目数
        """
        super().__init__(timeout=180)
        self.interaction = interaction
        self.entries = entries
        self.entries_per_page = entries_per_page
        self.current_page = 0
        self.total_pages = max(1, math.ceil(len(entries) / entries_per_page))
        self.update_buttons()
    
    def update_buttons(self):
        """更新按钮状态"""
        if len(self.children) >= 2:
            self.children[0].disabled = self.current_page == 0
            self.children[1].disabled = self.current_page >= self.total_pages - 1
    
    async def create_embed(self) -> discord.Embed:
        """
        创建封禁列表Embed
        
        Returns:
            Discord Embed对象
        """
        start_index = self.current_page * self.entries_per_page
        end_index = min(start_index + self.entries_per_page, len(self.entries))
        page_entries = self.entries[start_index:end_index]
        
        embed = discord.Embed(
            title=f"「{self.interaction.guild.name}」挑战封禁列表 (共 {len(self.entries)} 条)",
            color=discord.Color.from_rgb(139, 0, 0)  # Dark Red
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
                    timestamp_dt = datetime.fromisoformat(entry['timestamp']).astimezone(BEIJING_TZ)
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
    
    async def show_page(self, interaction: discord.Interaction):
        """显示当前页"""
        self.update_buttons()
        embed = await self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    @ui.button(label="◀️ 上一页", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: ui.Button):
        """上一页按钮"""
        if self.current_page > 0:
            self.current_page -= 1
            await self.show_page(interaction)
    
    @ui.button(label="下一页 ▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        """下一页按钮"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.show_page(interaction)
    
    async def on_timeout(self):
        """超时处理"""
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
            pass


class PermissionSelectView(ui.View):
    """权限选择视图"""
    
    def __init__(self, permissions: List[str], timeout: int = 60):
        """
        初始化权限选择视图
        
        Args:
            permissions: 可选权限列表
            timeout: 超时时间
        """
        super().__init__(timeout=timeout)
        self.selected_permissions = []
        
        # 创建权限选择菜单
        select = ui.Select(
            placeholder="选择要授予的权限...",
            min_values=1,
            max_values=len(permissions),
            options=[
                discord.SelectOption(label=perm, value=perm, description=f"授予 {perm} 权限")
                for perm in permissions
            ]
        )
        select.callback = self.select_callback
        self.add_item(select)
    
    async def select_callback(self, interaction: discord.Interaction):
        """选择回调"""
        self.selected_permissions = interaction.data['values']
        await interaction.response.defer()
        self.stop()
    
    async def on_timeout(self):
        """超时处理"""
        for item in self.children:
            item.disabled = True
        logger.debug("PermissionSelectView timeout")


class LeaderboardView(ui.View):
    """排行榜视图（用于究极道馆排行榜）"""
    
    def __init__(self):
        """初始化排行榜视图"""
        super().__init__(timeout=None)
    
    @ui.button(
        label="刷新排行榜",
        style=discord.ButtonStyle.primary,
        custom_id="refresh_leaderboard"
    )
    async def refresh_button(self, interaction: discord.Interaction, button: ui.Button):
        """刷新排行榜按钮"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # 获取排行榜Cog
        leaderboard_cog = interaction.client.get_cog('LeaderboardCog')
        if not leaderboard_cog:
            await interaction.followup.send(
                "❌ 排行榜系统暂时不可用。",
                ephemeral=True
            )
            return
        
        # 调用排行榜Cog的方法来刷新排行榜
        await leaderboard_cog.refresh_leaderboard(interaction)