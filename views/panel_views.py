# -*- coding: utf-8 -*-
"""
模块名称: panel_views.py
功能描述: 各种面板相关的视图组件
作者: Kilo Code
创建日期: 2024-12-15
最后修改: 2024-12-15
"""

import discord
from discord import ui
import logging
from typing import Optional, List, Dict

from utils.logger import get_logger

logger = get_logger(__name__)


class BadgePanelView(ui.View):
    """徽章墙面板视图"""
    
    def __init__(self):
        super().__init__(timeout=None)
    
    @ui.button(
        label="我的徽章墙",
        style=discord.ButtonStyle.primary,
        custom_id="show_my_badges"  # 不需要persistent前缀
    )
    async def show_my_badges_button(self, interaction: discord.Interaction, button: ui.Button):
        """显示用户的徽章墙"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # 获取用户进度Cog
        progress_cog = interaction.client.get_cog('UserProgressCog')
        if not progress_cog:
            await interaction.followup.send(
                "❌ 徽章系统暂时不可用。",
                ephemeral=True
            )
            return
        
        # 调用进度Cog的方法来显示徽章墙
        await progress_cog.show_badge_wall(interaction)


class GraduationPanelView(ui.View):
    """毕业面板视图，用于领取全通关奖励"""
    
    def __init__(self):
        super().__init__(timeout=None)
    
    @ui.button(
        label="领取毕业奖励",
        style=discord.ButtonStyle.success,
        custom_id="claim_graduation_role"  # 不需要persistent前缀
    )
    async def claim_graduation_role_button(self, interaction: discord.Interaction, button: ui.Button):
        """领取毕业奖励按钮"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)
        panel_message_id = str(interaction.message.id)
        
        # 获取面板管理Cog
        panels_cog = interaction.client.get_cog('PanelsCog')
        if not panels_cog:
            await interaction.followup.send(
                "❌ 面板系统暂时不可用。",
                ephemeral=True
            )
            return
        
        # 调用面板Cog的方法来处理毕业奖励
        await panels_cog.handle_graduation_claim(
            interaction,
            guild_id,
            user_id,
            panel_message_id
        )


class BadgeView(ui.View):
    """徽章展示视图，用于浏览多个徽章"""
    
    def __init__(self, user: discord.User, gyms: List[Dict]):
        super().__init__(timeout=180)
        self.user = user
        self.gyms = gyms
        self.current_index = 0
        self.update_buttons()
    
    async def create_embed(self) -> discord.Embed:
        """创建当前徽章的嵌入消息"""
        gym = self.gyms[self.current_index]
        gym_name = gym['name']
        url = gym.get('badge_image_url')
        badge_desc = gym.get('badge_description')
        
        embed = discord.Embed(
            title=f"{self.user.display_name}的徽章墙",
            color=discord.Color.gold()
        )
        
        # 构建描述文本
        description_text = f"### {gym_name}\n\n"
        if badge_desc:
            description_text += f"{badge_desc}\n\n"
        
        embed.description = description_text
        embed.set_footer(text=f"徽章 {self.current_index + 1}/{len(self.gyms)}")
        
        if isinstance(url, str) and url:
            embed.set_image(url=url)
        else:
            # 如果没有图片，添加提示
            embed.description += "🖼️ *此道馆未设置徽章图片。*"
        
        return embed
    
    def update_buttons(self):
        """根据当前索引启用/禁用按钮"""
        if len(self.gyms) <= 1:
            self.children[0].disabled = True
            self.children[1].disabled = True
            return
        
        self.children[0].disabled = self.current_index == 0
        self.children[1].disabled = self.current_index == len(self.gyms) - 1
    
    async def handle_interaction(self, interaction: discord.Interaction):
        """处理按钮交互的中心方法"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "你不能操作别人的徽章墙哦。",
                ephemeral=True
            )
            return
        
        self.update_buttons()
        await interaction.response.edit_message(
            embed=await self.create_embed(),
            view=self
        )
    
    @ui.button(label="◀️ 上一个", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: ui.Button):
        self.current_index -= 1
        await self.handle_interaction(interaction)
    
    @ui.button(label="下一个 ▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        self.current_index += 1
        await self.handle_interaction(interaction)


class PaginatorView(ui.View):
    """通用分页视图基类"""
    
    def __init__(self, interaction: discord.Interaction, entries: List, entries_per_page: int = 5):
        super().__init__(timeout=180)
        self.interaction = interaction
        self.entries = entries
        self.entries_per_page = entries_per_page
        self.current_page = 0
        self.total_pages = (len(self.entries) - 1) // self.entries_per_page + 1 if entries else 1
        self.update_buttons()
    
    def update_buttons(self):
        """更新按钮状态"""
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = self.current_page >= self.total_pages - 1
    
    async def create_embed(self) -> discord.Embed:
        """创建嵌入消息（子类需要重写）"""
        raise NotImplementedError("Subclasses must implement create_embed method")
    
    async def show_page(self, interaction: discord.Interaction):
        """显示当前页"""
        self.update_buttons()
        embed = await self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    @ui.button(label="◀️ 上一页", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await self.show_page(interaction)
    
    @ui.button(label="下一页 ▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
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


class ConfirmationView(ui.View):
    """通用确认视图"""
    
    def __init__(self, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.value = None
        self.interaction = None
    
    @ui.button(label="确认", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
        self.value = True
        self.interaction = interaction
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
    
    @ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        self.value = False
        self.interaction = interaction
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
    
    async def on_timeout(self):
        """超时处理"""
        for item in self.children:
            item.disabled = True
        self.stop()


class GymListView(ui.View):
    """道馆列表视图，支持分页"""
    
    def __init__(self, gyms: List[Dict], page: int = 0, per_page: int = 10):
        super().__init__(timeout=180)
        self.gyms = gyms
        self.page = page
        self.per_page = per_page
        self.total_pages = (len(gyms) - 1) // per_page + 1 if gyms else 1
        self.update_buttons()
    
    def update_buttons(self):
        """更新按钮状态"""
        # 查找上一页和下一页按钮
        for item in self.children:
            if isinstance(item, ui.Button):
                if item.custom_id == "gym_list_prev":
                    item.disabled = self.page == 0
                elif item.custom_id == "gym_list_next":
                    item.disabled = self.page >= self.total_pages - 1
    
    def get_current_page_gyms(self) -> List[Dict]:
        """获取当前页的道馆"""
        start = self.page * self.per_page
        end = start + self.per_page
        return self.gyms[start:end]
    
    async def create_embed(self) -> discord.Embed:
        """创建道馆列表嵌入消息"""
        embed = discord.Embed(
            title="道馆列表",
            color=discord.Color.purple()
        )
        
        page_gyms = self.get_current_page_gyms()
        if not page_gyms:
            embed.description = "这一页没有道馆。"
        else:
            description_lines = []
            for gym in page_gyms:
                status_emoji = "✅" if gym.get('is_enabled', True) else "⏸️"
                status_text = "开启" if gym.get('is_enabled', True) else "关闭"
                badge_text = "🖼️" if gym.get('badge_image_url') else "➖"
                
                line = f"{status_emoji} **{gym['name']}** `(ID: {gym['id']})`\n"
                line += f"  状态: {status_text} | 徽章: {badge_text}"
                description_lines.append(line)
            
            embed.description = "\n".join(description_lines)
        
        embed.set_footer(text=f"第 {self.page + 1}/{self.total_pages} 页 | 共 {len(self.gyms)} 个道馆")
        return embed
    
    @ui.button(label="◀️ 上一页", style=discord.ButtonStyle.secondary, custom_id="gym_list_prev")
    async def previous_page(self, interaction: discord.Interaction, button: ui.Button):
        if self.page > 0:
            self.page -= 1
            self.update_buttons()
            await interaction.response.edit_message(
                embed=await self.create_embed(),
                view=self
            )
    
    @ui.button(label="下一页 ▶️", style=discord.ButtonStyle.secondary, custom_id="gym_list_next")
    async def next_page(self, interaction: discord.Interaction, button: ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
            self.update_buttons()
            await interaction.response.edit_message(
                embed=await self.create_embed(),
                view=self
            )


class MainView(ui.View):
    """主面板视图，用于挑战道馆的主界面"""
    
    def __init__(self):
        super().__init__(timeout=None)
    
    @ui.button(
        label="开始挑战",
        style=discord.ButtonStyle.primary,
        custom_id="start_challenge"
    )
    async def start_challenge_button(self, interaction: discord.Interaction, button: ui.Button):
        """开始挑战按钮"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # 获取挑战管理Cog
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.followup.send(
                "❌ 挑战系统暂时不可用。",
                ephemeral=True
            )
            return
        
        # 调用挑战Cog的方法来处理挑战开始
        await challenge_cog.handle_challenge_start(interaction)
    
    @ui.button(
        label="查看进度",
        style=discord.ButtonStyle.secondary,
        custom_id="view_progress"
    )
    async def view_progress_button(self, interaction: discord.Interaction, button: ui.Button):
        """查看进度按钮"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # 获取用户进度Cog
        progress_cog = interaction.client.get_cog('UserProgressCog')
        if not progress_cog:
            await interaction.followup.send(
                "❌ 进度系统暂时不可用。",
                ephemeral=True
            )
            return
        
        # 调用进度Cog的方法来显示用户进度
        await progress_cog.show_user_progress(interaction)


# 导出所有视图类
__all__ = [
    'BadgePanelView',
    'GraduationPanelView',
    'BadgeView',
    'PaginatorView',
    'ConfirmationView',
    'GymListView',
    'MainView'
]