# -*- coding: utf-8 -*-
"""
模块名称: feedback_views.py
功能描述: 反馈面板视图与模态框（匿名/实名）
作者: Kilo Code
创建日期: 2025-09-29
最后修改: 2025-09-29
"""

import discord
from discord import ui
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class FeedbackModal(ui.Modal):
    """反馈提交模态框（在按钮点击后弹出）"""

    def __init__(
        self,
        cog: "FeedbackCog",
        title: str,
        input_label: str,
        is_anonymous: bool
    ):
        super().__init__(title=title, timeout=None)
        self.cog = cog
        self.is_anonymous = is_anonymous

        # 单个多行文本输入
        self.content_input = ui.TextInput(
            label=input_label,
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
            placeholder="请在此输入你的反馈内容（最多2000字符）"
        )
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction):
        """
        模态框提交回调：
        - 黄金法则：模态提交后第一时间 defer，占坑，然后走 followup
        """
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

            content = str(self.content_input.value or "").strip()
            await self.cog.process_feedback(
                interaction=interaction,
                content=content,
                is_anonymous=self.is_anonymous
            )
        except Exception as e:
            logger.error(f"FeedbackModal.on_submit error: {e}", exc_info=True)
            # 若尚未响应，给出错误提示
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 提交失败：发生未知错误。", ephemeral=True)
            else:
                try:
                    await interaction.followup.send("❌ 提交失败：发生未知错误。", ephemeral=True)
                except Exception:
                    pass


class FeedbackPanelView(ui.View):
    """
    反馈面板视图（持久化）：
    - 包含两个按钮：匿名反馈 / 实名反馈
    - 按钮点击直接使用 send_modal（模态是 defer 例外）
    """

    def __init__(
        self,
        anonymous_button_label: str = "匿名反馈",
        named_button_label: str = "实名反馈",
        anonymous_modal_title: str = "匿名反馈",
        named_modal_title: str = "实名反馈",
        modal_input_label: str = "请输入你的反馈（支持多行）"
    ):
        super().__init__(timeout=None)
        # 保存文案，避免在按钮回调中做耗时IO（遵循“模态是 defer 例外”）
        self._anonymous_button_label = anonymous_button_label
        self._named_button_label = named_button_label
        self._anonymous_modal_title = anonymous_modal_title
        self._named_modal_title = named_modal_title
        self._modal_input_label = modal_input_label

        # 初始化后更新按钮标签
        # 注意：children顺序与下方按钮声明顺序一致
        if len(self.children) >= 2:
            self.children[0].label = self._anonymous_button_label
            self.children[1].label = self._named_button_label

    @ui.button(
        label="匿名反馈",
        style=discord.ButtonStyle.primary,
        custom_id="feedback_anonymous"  # 持久化ID
    )
    async def anonymous_button(self, interaction: discord.Interaction, button: ui.Button):
        """匿名反馈按钮：直接弹出模态框（例外路径，不先 defer）"""
        cog = interaction.client.get_cog("FeedbackCog")
        if not cog:
            return await interaction.response.send_message(
                "❌ 反馈系统暂时不可用。", ephemeral=True
            )

        modal = FeedbackModal(
            cog=cog,
            title=self._anonymous_modal_title,
            input_label=self._modal_input_label,
            is_anonymous=True
        )
        await interaction.response.send_modal(modal)

    @ui.button(
        label="实名反馈",
        style=discord.ButtonStyle.secondary,
        custom_id="feedback_named"  # 持久化ID
    )
    async def named_button(self, interaction: discord.Interaction, button: ui.Button):
        """实名反馈按钮：直接弹出模态框（例外路径，不先 defer）"""
        cog = interaction.client.get_cog("FeedbackCog")
        if not cog:
            return await interaction.response.send_message(
                "❌ 反馈系统暂时不可用。", ephemeral=True
            )

        modal = FeedbackModal(
            cog=cog,
            title=self._named_modal_title,
            input_label=self._modal_input_label,
            is_anonymous=False
        )
        await interaction.response.send_modal(modal)


__all__ = [
    "FeedbackPanelView",
    "FeedbackModal",
]