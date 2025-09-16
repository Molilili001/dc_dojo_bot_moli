"""
模块名称: badge_views.py
功能描述: 徽章墙视图组件，展示用户获得的道馆徽章
作者: @Kilo Code
创建日期: 2024-09-15
最后修改: 2024-09-15
"""

import discord
from discord import ui
from typing import List, Dict, Any
import math

from utils.logger import get_logger

logger = get_logger(__name__)


class BadgeView(ui.View):
    """徽章墙视图 - 一页显示一个徽章，支持翻页"""
    
    def __init__(self, user: discord.User, completed_gyms: List[Dict[str, Any]]):
        """
        初始化徽章墙视图
        
        Args:
            user: 用户对象
            completed_gyms: 已完成的道馆列表
        """
        super().__init__(timeout=300)  # 5分钟超时
        self.user = user
        self.completed_gyms = completed_gyms
        self.current_page = 0
        self.total_pages = max(1, len(completed_gyms))  # 总页数等于徽章数
        
        # 更新按钮状态
        self.update_buttons()
    
    def update_buttons(self):
        """更新翻页按钮状态"""
        # 清除所有按钮
        self.clear_items()
        
        # 如果没有徽章或只有一个徽章，不添加按钮
        if not self.completed_gyms or len(self.completed_gyms) <= 1:
            return
        
        # 上一页按钮
        prev_button = ui.Button(
            label="◀ 上一个",
            style=discord.ButtonStyle.secondary,
            disabled=(self.current_page == 0)
        )
        prev_button.callback = self.previous_page
        self.add_item(prev_button)
        
        # 页码显示按钮（不可点击）
        page_button = ui.Button(
            label=f"{self.current_page + 1}/{self.total_pages}",
            style=discord.ButtonStyle.primary,
            disabled=True
        )
        self.add_item(page_button)
        
        # 下一页按钮
        next_button = ui.Button(
            label="下一个 ▶",
            style=discord.ButtonStyle.secondary,
            disabled=(self.current_page >= self.total_pages - 1)
        )
        next_button.callback = self.next_page
        self.add_item(next_button)
    
    async def create_embed(self) -> discord.Embed:
        """
        创建徽章墙Embed - 直接显示徽章图片
        
        Returns:
            Discord Embed对象
        """
        if not self.completed_gyms:
            embed = discord.Embed(
                title=f"🏆 {self.user.display_name} 的徽章墙",
                description="还没有获得任何道馆徽章，继续努力！",
                color=discord.Color.gold()
            )
            return embed
        
        # 获取当前页的道馆（每页1个）
        gym = self.completed_gyms[self.current_page]
        
        # 创建Embed
        embed = discord.Embed(
            title=f"🏆 {self.user.display_name} 的徽章墙",
            color=discord.Color.gold()
        )
        
        # 设置道馆名称作为主要描述
        embed.description = f"### 🎖️ **{gym['name']}** 道馆徽章"
        
        # 如果有徽章描述，添加为字段
        if gym.get('badge_description'):
            embed.add_field(
                name="📝 徽章说明",
                value=gym['badge_description'],
                inline=False
            )
        
        # 设置徽章图片（如果有）- 这是最重要的部分
        if gym.get('badge_image_url'):
            embed.set_image(url=gym['badge_image_url'])
        else:
            # 如果没有图片，添加文字提示
            embed.add_field(
                name="⚠️ 提示",
                value="此道馆尚未设置徽章图片",
                inline=False
            )
        
        # 添加页脚显示进度
        embed.set_footer(text=f"徽章 {self.current_page + 1}/{self.total_pages} | 共获得 {len(self.completed_gyms)} 个徽章")
        
        return embed
    
    async def previous_page(self, interaction: discord.Interaction):
        """翻到上一个徽章"""
        # 检查权限
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "你只能查看自己的徽章墙哦！",
                ephemeral=True
            )
            return
        
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            embed = await self.create_embed()
            await interaction.response.edit_message(embed=embed, view=self)
    
    async def next_page(self, interaction: discord.Interaction):
        """翻到下一个徽章"""
        # 检查权限
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "你只能查看自己的徽章墙哦！",
                ephemeral=True
            )
            return
        
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.update_buttons()
            embed = await self.create_embed()
            await interaction.response.edit_message(embed=embed, view=self)
    
    async def on_timeout(self):
        """视图超时处理"""
        # 禁用所有按钮
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True
        
        logger.debug(f"BadgeView timeout for user {self.user.id}")


class BadgeDetailModal(ui.Modal):
    """徽章详情模态框"""
    
    def __init__(self, gym_info: Dict[str, Any]):
        """
        初始化徽章详情模态框
        
        Args:
            gym_info: 道馆信息
        """
        super().__init__(title=f"{gym_info['name']} - 徽章详情")
        self.gym_info = gym_info
        
        # 添加详情文本
        self.detail_input = ui.TextInput(
            label="徽章描述",
            default=gym_info.get('badge_description', '无描述'),
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000
        )
        self.add_item(self.detail_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        """提交处理（只读模态框，直接关闭）"""
        await interaction.response.defer()