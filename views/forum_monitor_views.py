# -*- coding: utf-8 -*-

import discord
from discord import ui
from typing import Optional, List, Dict, Any

from utils.logger import get_logger
from utils.validators import validate_discord_id
from core.constants import MESSAGE_CONTENT_LIMIT
from utils.permissions import is_admin_or_owner

logger = get_logger(__name__)


async def safe_defer(interaction: discord.Interaction):
    """
    绝对安全的“占坑”函数。
    检查交互是否已响应，若未响应，立即以仅自己可见的方式延迟响应。
    """
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)


def _parse_role_id_from_input(guild: discord.Guild, raw: Optional[str]) -> Optional[str]:
    """
    支持输入为身份组ID或提及<@&id>，返回字符串ID；若无输入或非法则返回None。
    """
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("<@&") and raw.endswith(">"):
        role_id = raw[3:-1]
    else:
        role_id = raw

    if not validate_discord_id(role_id):
        return None
    role = guild.get_role(int(role_id))
    if role is None:
        return None
    return role_id


def _parse_role_ids_csv(guild: discord.Guild, raw: Optional[str]) -> List[str]:
    """
    解析逗号分隔的角色输入为角色ID字符串列表。
    支持格式: "123,456" 或 "<@&123>, <@&456>"；会过滤非法或不存在的角色。
    """
    if not raw or not isinstance(raw, str):
        return []
    parts = [p.strip() for p in raw.split(",") if p and p.strip()]
    ids: List[str] = []
    for p in parts:
        s = p
        if s.startswith("<@&") and s.endswith(">"):
            s = s[3:-1]
        if not validate_discord_id(s):
            continue
        role = guild.get_role(int(s))
        if role is None:
            continue
        if s not in ids:
            ids.append(s)
    return ids


def _format_role_ids_for_display(guild: discord.Guild, raw: Optional[str]) -> str:
    """
    将存储的CSV ID字符串渲染为 @角色 提及串，若无有效项返回 '无'
    """
    if not raw or not isinstance(raw, str) or not raw.strip():
        return "无"
    parts = [p.strip() for p in raw.split(",") if p and p.strip()]
    mentions: List[str] = []
    for p in parts:
        if p.isdigit() and guild.get_role(int(p)):
            mentions.append(f"<@&{p}>")
    return " ".join(mentions) if mentions else "无"


def _truncate_message(text: Optional[str]) -> Optional[str]:
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    return text[:MESSAGE_CONTENT_LIMIT]


def _parse_flags(raw: Optional[str]) -> Dict[str, bool]:
    """
    解析flags文本（可选），格式示例：
    auto=yes, notify=yes, mention=no
    支持: yes/no/true/false/1/0/y/n/t/f
    未提供时默认: notify=True，其它False
    """
    default = {"auto": False, "notify": True, "mention": False}
    if not raw or not isinstance(raw, str):
        return default

    def to_bool(v: str) -> bool:
        s = v.strip().lower()
        return s in ("yes", "true", "1", "y", "t")

    try:
        parts = [p.strip() for p in raw.split(",")]
        flags = {}
        for p in parts:
            if not p or "=" not in p:
                continue
            k, v = p.split("=", 1)
            k = k.strip().lower()
            v = v.strip()
            if k in ("auto", "notify", "mention"):
                flags[k] = to_bool(v)
        return {**default, **flags}
    except Exception:
        return default


class ForumChannelSelect(ui.ChannelSelect):
    """论坛频道选择器（根本解决25项限制，使用系统ChannelSelect）"""
    def __init__(self, guild: discord.Guild, preselect_channel_id: Optional[str] = None):
        super().__init__(
            channel_types=[discord.ChannelType.forum],
            placeholder="选择一个论坛频道",
            min_values=1,
            max_values=1,
            custom_id="forum_monitor_channel_select"
        )
        # 注：ChannelSelect由Discord客户端提供完整频道列表，不受25项静态options限制。

    async def callback(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        parent_view: ForumMonitorPanelView = self.view  # type: ignore
        # ChannelSelect返回的是频道对象列表
        selected = self.values[0]
        parent_view.selected_channel_id = str(selected.id)
        await parent_view.show_current_config(interaction)


class ForumMonitorConfigModal(ui.Modal, title="配置论坛频道监控"):
    """
    配置Modal：
    - auto_role_id: 要自动添加的身份组（ID或@提及）
    - notify_message: 在线程内对帖主的通知文本（可为空，默认“欢迎加入讨论！”）
    - mention_role_id: 要@的身份组（ID或@提及）
    - mention_message: 在线程内@身份组时附加的文本
    - flags: 可选开关 "auto=yes, notify=yes, mention=no"
    """
    auto_role_id = ui.TextInput(label="自动身份组ID或@提及（可选，多个用逗号分隔）", required=False, placeholder="示例: 123,456 或 <@&123>,<@&456>")
    notify_message = ui.TextInput(label="通知消息（可选）", style=discord.TextStyle.paragraph, required=False, placeholder="默认：欢迎加入讨论！")
    mention_role_id = ui.TextInput(label="@身份组ID或@提及（可选，多个用逗号分隔）", required=False, placeholder="示例: 123,456 或 <@&123>,<@&456>")
    mention_message = ui.TextInput(label="@身份组附加消息（可选）", style=discord.TextStyle.paragraph, required=False)
    flags = ui.TextInput(label="开关flags（可选）", required=False, placeholder="auto=yes, notify=yes, mention=no")

    def __init__(self, guild: discord.Guild, channel_id: str):
        super().__init__(timeout=180)
        self.guild = guild
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await safe_defer(interaction)

        # 获取 Cog
        cog = interaction.client.get_cog("ForumPostMonitorCog")
        if not cog:
            await interaction.followup.send("❌ 系统模块未加载，无法保存配置。", ephemeral=True)
            return

        # 解析字段（支持多角色，逗号分隔）
        auto_role_ids = _parse_role_ids_csv(self.guild, str(self.auto_role_id)) if str(self.auto_role_id).strip() else []
        notify_msg = _truncate_message(str(self.notify_message)) if str(self.notify_message).strip() else None
        mention_role_ids = _parse_role_ids_csv(self.guild, str(self.mention_role_id)) if str(self.mention_role_id).strip() else []
        mention_msg = _truncate_message(str(self.mention_message)) if str(self.mention_message).strip() else None
        flags_dict = _parse_flags(str(self.flags)) if str(self.flags).strip() else _parse_flags(None)

        # 确定开关
        auto_enabled = flags_dict.get("auto", False) and len(auto_role_ids) > 0
        notify_enabled = flags_dict.get("notify", True)  # 允许notify开启但消息为空时使用默认文案
        mention_enabled = flags_dict.get("mention", False) and len(mention_role_ids) > 0

        # 默认通知文案
        if notify_enabled and not notify_msg:
            notify_msg = "欢迎加入讨论！"

        guild_id = str(self.guild.id)
        forum_channel_id = self.channel_id

        try:
            # 使用Cog中的持久化方法
            await cog._upsert_config(
                guild_id=guild_id,
                forum_channel_id=forum_channel_id,
                auto_role_enabled=auto_enabled,
                auto_role_id=",".join(auto_role_ids) if auto_role_ids else None,
                notify_enabled=notify_enabled,
                notify_message=notify_msg,
                mention_role_enabled=mention_enabled,
                mention_role_id=",".join(mention_role_ids) if mention_role_ids else None,
                mention_message=mention_msg
            )

            await interaction.followup.send("✅ 配置已保存。", ephemeral=True)

            # 回填最新配置到面板
            parent_view: ForumMonitorPanelView = getattr(self, "parent_view", None)  # type: ignore
            if parent_view:
                await parent_view.show_current_config(interaction)
        except Exception as e:
            logger.error(f"ForumMonitor: save config error: {e}", exc_info=True)
            await interaction.followup.send("❌ 保存配置时发生错误。", ephemeral=True)


class ForumMonitorPanelView(ui.View):
    """帖子监控面板视图"""
    def __init__(self, guild: Optional[discord.Guild] = None, preselect_channel_id: Optional[str] = None):
        super().__init__(timeout=None)  # 面板为公共使用，设置为无超时
        self.guild = guild
        self.selected_channel_id: Optional[str] = preselect_channel_id

        # 初始时尝试构建选择器（如果guild已知）
        if self.guild:
            try:
                self.channel_select = ForumChannelSelect(self.guild, preselect_channel_id=self.selected_channel_id)
                self.add_item(self.channel_select)
            except Exception as e:
                logger.error(f"ForumMonitor: build channel select error: {e}", exc_info=True)

    async def show_current_config(self, interaction: discord.Interaction):
        """
        根据当前选中的频道显示配置摘要
        """
        await safe_defer(interaction)
        if not self.selected_channel_id:
            try:
                await interaction.followup.send("ℹ️ 请先在下拉框中选择一个论坛频道。", ephemeral=True)
            except Exception:
                pass
            return

        cog = interaction.client.get_cog("ForumPostMonitorCog")
        if not cog:
            await interaction.followup.send("❌ 系统模块未加载。", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        config = await cog._get_config(guild_id, self.selected_channel_id)

        # 构建摘要嵌入
        embed = discord.Embed(
            title="论坛频道监控配置",
            color=discord.Color.green()
        )
        channel = interaction.guild.get_channel(int(self.selected_channel_id))
        embed.add_field(name="频道", value=f"{channel.mention if channel else f'ID: {self.selected_channel_id}'}", inline=False)

        def b2e(v: Any) -> str:
            s = str(v).strip().lower()
            if isinstance(v, bool):
                return "启用" if v else "禁用"
            if s in ("1", "true", "t", "yes", "y"):
                return "启用"
            return "禁用"

        if config:
            auto_targets = _format_role_ids_for_display(interaction.guild, config.get('auto_role_id'))
            mention_targets = _format_role_ids_for_display(interaction.guild, config.get('mention_role_id'))
            embed.add_field(name="自动加身份组", value=f"{b2e(config.get('auto_role_enabled'))} | 目标: {auto_targets}", inline=False)
            embed.add_field(name="通知贴主", value=f"{b2e(config.get('notify_enabled'))} | 文案: {config.get('notify_message') or '欢迎加入讨论！'}", inline=False)
            embed.add_field(name="@身份组消息", value=f"{b2e(config.get('mention_role_enabled'))} | 目标: {mention_targets} | 文案: {config.get('mention_message') or '无'}", inline=False)
        else:
            embed.description = "此频道尚未配置监控策略。"

        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except discord.NotFound:
            # 若原始消息不可编辑，退回followup
            await interaction.followup.send(embed=embed, ephemeral=True)

    @ui.button(label="新增/更新配置", style=discord.ButtonStyle.primary)
    async def configure_button(self, interaction: discord.Interaction, button: ui.Button):
        """
        弹出配置Modal。注意：send_modal 不可与 defer 同时使用。
        所有人可查看面板，但仅管理员/开发者可修改配置。
        """
        # 权限检查（不使用defer，避免与send_modal冲突）
        if not await is_admin_or_owner(interaction):
            await interaction.response.send_message("❌ 你没有权限修改配置。", ephemeral=True)
            return

        if not self.selected_channel_id:
            await interaction.response.send_message("ℹ️ 请先选择一个论坛频道。", ephemeral=True)
            return
        try:
            modal = ForumMonitorConfigModal(interaction.guild, self.selected_channel_id)
            # 让Modal可以回调 panel 的刷新方法
            setattr(modal, "parent_view", self)
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"ForumMonitor: open modal error: {e}", exc_info=True)
            await safe_defer(interaction)
            await interaction.followup.send("❌ 无法打开配置表单。", ephemeral=True)

    @ui.button(label="删除该频道配置", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: ui.Button):
        await safe_defer(interaction)
        # 权限限制：仅管理员/开发者可删除配置
        if not await is_admin_or_owner(interaction):
            await interaction.followup.send("❌ 你没有权限删除配置。", ephemeral=True)
            return

        if not self.selected_channel_id:
            await interaction.followup.send("ℹ️ 请先选择一个论坛频道。", ephemeral=True)
            return

        cog = interaction.client.get_cog("ForumPostMonitorCog")
        if not cog:
            await interaction.followup.send("❌ 系统模块未加载。", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        try:
            affected = await cog._delete_config(guild_id, self.selected_channel_id)
            if affected > 0:
                await interaction.followup.send("✅ 已删除该频道的监控配置。", ephemeral=True)
            else:
                await interaction.followup.send("ℹ️ 未找到配置，无需删除。", ephemeral=True)
            await self.show_current_config(interaction)
        except Exception as e:
            logger.error(f"ForumMonitor: delete config error: {e}", exc_info=True)
            await interaction.followup.send("❌ 删除配置时发生错误。", ephemeral=True)

    @ui.button(label="刷新摘要", style=discord.ButtonStyle.secondary)
    async def refresh_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.show_current_config(interaction)


__all__ = [
    "ForumMonitorPanelView",
    "ForumMonitorConfigModal",
    "ForumChannelSelect",
]