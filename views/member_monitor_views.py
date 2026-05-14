# -*- coding: utf-8 -*-
"""
成员监控系统 - 管理面板视图
"""

import json
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import discord
from discord import ui

if TYPE_CHECKING:
    from cogs.member_monitor import MemberMonitorCog

# 常量
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_SETTLEMENT_HOUR = 0
DEFAULT_SETTLEMENT_MINUTE = 0
DEFAULT_JOIN_THRESHOLD = 10


async def safe_defer(interaction: discord.Interaction):
    """安全地延迟响应"""
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass


def parse_json_list(raw: Optional[str]) -> List[str]:
    """解析 JSON 数组字符串"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data if x]
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def format_channel(guild: discord.Guild, channel_id: Optional[str]) -> str:
    """格式化频道显示"""
    if not channel_id:
        return "未设置"
    try:
        channel = guild.get_channel(int(channel_id))
        if channel:
            return f"#{channel.name}"
        return f"<#{channel_id}>"
    except (ValueError, TypeError):
        return "未设置"


def format_notify_targets(guild: discord.Guild, config: Optional[Dict[str, Any]]) -> str:
    """格式化通知对象显示"""
    if not config:
        return "无"

    displays = []

    # 用户
    user_ids = parse_json_list(config.get("notify_user_ids"))
    for uid in user_ids:
        try:
            member = guild.get_member(int(uid))
            if member:
                displays.append(f"@{member.display_name}")
            else:
                displays.append(f"<@{uid}>")
        except (ValueError, TypeError):
            continue

    # 角色
    role_ids = parse_json_list(config.get("notify_role_ids"))
    for rid in role_ids:
        try:
            role = guild.get_role(int(rid))
            if role:
                displays.append(f"@{role.name}")
            else:
                displays.append(f"<@&{rid}>")
        except (ValueError, TypeError):
            continue

    return ", ".join(displays) if displays else "无"


class MemberMonitorPanelView(ui.View):
    """成员监控配置面板视图"""

    def __init__(
        self,
        cog: "MemberMonitorCog",
        guild: discord.Guild,
        config: Optional[Dict[str, Any]] = None
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.config = config or {}

    async def build_status_embed(self) -> discord.Embed:
        """构建状态嵌入消息"""
        is_enabled = self.config.get("is_enabled", False)

        embed = discord.Embed(
            title="⚙️ 成员监控配置面板",
            description="监听官方成员加入事件，统计每日新成员数量并发送告警/日报",
            color=discord.Color.green() if is_enabled else discord.Color.greyple()
        )

        # 基本配置
        alert_ch = format_channel(self.guild, self.config.get("alert_channel_id"))
        threshold = self.config.get("join_threshold", DEFAULT_JOIN_THRESHOLD)
        settlement_h = self.config.get("settlement_hour", DEFAULT_SETTLEMENT_HOUR)
        settlement_m = self.config.get("settlement_minute", DEFAULT_SETTLEMENT_MINUTE)
        source_text = "👥 官方成员加入事件 (on_member_join)"

        embed.add_field(
            name="📋 基本配置",
            value=(
                f"**状态**: {'✅ 已启用' if is_enabled else '❌ 已禁用'}\n"
                f"**统计来源**: {source_text}\n"
                f"**告警频道**: {alert_ch}\n"
                f"**告警阈值**: {threshold}\n"
                f"**结算时间**: {settlement_h:02d}:{settlement_m:02d} (北京时间)"
            ),
            inline=False
        )

        # 通知对象
        notify_str = format_notify_targets(self.guild, self.config)
        embed.add_field(
            name="📢 通知对象",
            value=notify_str,
            inline=False
        )

        embed.set_footer(text="点击下方按钮进行配置")

        return embed

    async def refresh_config(self):
        """刷新配置"""
        guild_id = str(self.guild.id)
        self.config = await self.cog._get_config(guild_id) or {}

    @ui.button(label="设置告警频道", style=discord.ButtonStyle.primary, row=0)
    async def set_alert_channel(self, interaction: discord.Interaction, button: ui.Button):
        """设置告警频道"""
        await safe_defer(interaction)

        view = ChannelSelectView(self)
        await interaction.followup.send(
            "请选择**告警/日报发送频道**:",
            view=view,
            ephemeral=True
        )

    @ui.button(label="设置阈值/时间", style=discord.ButtonStyle.secondary, row=0)
    async def set_threshold(self, interaction: discord.Interaction, button: ui.Button):
        """设置阈值和结算时间"""
        modal = ThresholdSettingsModal(self)
        await interaction.response.send_modal(modal)

    @ui.button(label="设置通知对象", style=discord.ButtonStyle.secondary, row=1)
    async def set_notify_targets(self, interaction: discord.Interaction, button: ui.Button):
        """设置通知对象"""
        modal = NotifyTargetsModal(self)
        await interaction.response.send_modal(modal)

    @ui.button(label="切换开关", style=discord.ButtonStyle.success, row=1)
    async def toggle_enabled(self, interaction: discord.Interaction, button: ui.Button):
        """切换启用状态"""
        await safe_defer(interaction)

        guild_id = str(self.guild.id)

        # 检查必要配置
        if not self.config.get("alert_channel_id"):
            await interaction.followup.send("❌ 请先设置告警频道", ephemeral=True)
            return

        # 切换状态
        new_state = not self.config.get("is_enabled", False)
        await self.cog._upsert_config(guild_id, is_enabled=new_state)

        # 刷新并更新面板
        await self.refresh_config()
        embed = await self.build_status_embed()

        status = "✅ 已启用" if new_state else "❌ 已禁用"
        await interaction.followup.send(f"成员监控已切换为: {status}", ephemeral=True)

        try:
            await interaction.message.edit(embed=embed, view=self)
        except Exception:
            pass

    @ui.button(label="删除配置", style=discord.ButtonStyle.danger, row=1)
    async def delete_config(self, interaction: discord.Interaction, button: ui.Button):
        """删除配置"""
        view = ConfirmDeleteView(self)
        await interaction.response.send_message(
            "⚠️ 确定要删除该服务器的成员监控配置吗？\n此操作不可撤销。",
            view=view,
            ephemeral=True
        )

    async def on_timeout(self):
        """超时处理"""
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True


class ChannelSelectView(ui.View):
    """频道选择视图"""

    def __init__(self, parent: MemberMonitorPanelView):
        super().__init__(timeout=60)
        self.parent = parent

        # 添加频道选择器
        self.add_item(ChannelSelect())

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class ChannelSelect(ui.ChannelSelect):
    """频道选择器"""

    def __init__(self):
        super().__init__(
            placeholder="选择一个文字频道",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        await safe_defer(interaction)

        parent_view: ChannelSelectView = self.view
        panel = parent_view.parent

        if not self.values:
            await interaction.followup.send("❌ 未选择频道", ephemeral=True)
            return

        channel = self.values[0]
        channel_id = str(channel.id)
        guild_id = str(panel.guild.id)

        await panel.cog._upsert_config(guild_id, alert_channel_id=channel_id)
        msg = f"✅ 告警频道已设置为: #{channel.name}"

        # 刷新面板
        await panel.refresh_config()
        embed = await panel.build_status_embed()

        await interaction.followup.send(msg, ephemeral=True)

        try:
            # 尝试更新原始面板消息
            original_msg = interaction.message
            if original_msg and original_msg.reference:
                ref_msg = await interaction.channel.fetch_message(original_msg.reference.message_id)
                await ref_msg.edit(embed=embed, view=panel)
        except Exception:
            pass


class ThresholdSettingsModal(ui.Modal, title="设置阈值和结算时间"):
    """阈值和结算时间设置 Modal"""

    threshold_input = ui.TextInput(
        label="告警阈值",
        placeholder="输入触发告警的新成员数量（默认10）",
        default="10",
        required=True,
        max_length=10
    )

    settlement_time_input = ui.TextInput(
        label="结算时间 (HH:MM，北京时间)",
        placeholder="例如: 00:00 或 08:30",
        default="00:00",
        required=True,
        max_length=5
    )

    def __init__(self, parent: MemberMonitorPanelView):
        super().__init__()
        self.parent = parent

        # 设置默认值
        config = parent.config
        if config:
            threshold = config.get("join_threshold", DEFAULT_JOIN_THRESHOLD)
            self.threshold_input.default = str(threshold)

            h = config.get("settlement_hour", DEFAULT_SETTLEMENT_HOUR)
            m = config.get("settlement_minute", DEFAULT_SETTLEMENT_MINUTE)
            self.settlement_time_input.default = f"{h:02d}:{m:02d}"

    async def on_submit(self, interaction: discord.Interaction):
        await safe_defer(interaction)

        # 解析阈值
        try:
            threshold = int(self.threshold_input.value.strip())
            if threshold < 1:
                raise ValueError("阈值必须大于0")
        except ValueError:
            await interaction.followup.send("❌ 阈值必须是大于0的整数", ephemeral=True)
            return

        # 解析结算时间
        time_str = self.settlement_time_input.value.strip()
        try:
            parts = time_str.split(":")
            if len(parts) != 2:
                raise ValueError()
            hour = int(parts[0])
            minute = int(parts[1])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError()
        except (ValueError, IndexError):
            await interaction.followup.send(
                "❌ 时间格式错误，请使用 HH:MM 格式（如 00:00 或 08:30）",
                ephemeral=True
            )
            return

        # 更新配置
        guild_id = str(self.parent.guild.id)
        await self.parent.cog._upsert_config(
            guild_id,
            join_threshold=threshold,
            settlement_hour=hour,
            settlement_minute=minute
        )

        # 刷新面板
        await self.parent.refresh_config()
        embed = await self.parent.build_status_embed()

        await interaction.followup.send(
            f"✅ 设置已更新:\n- 告警阈值: {threshold}\n- 结算时间: {hour:02d}:{minute:02d}",
            ephemeral=True
        )

        try:
            await interaction.message.edit(embed=embed, view=self.parent)
        except Exception:
            pass


class NotifyTargetsModal(ui.Modal, title="设置通知对象"):
    """通知对象设置 Modal"""

    user_ids_input = ui.TextInput(
        label="通知用户 ID（多个用逗号分隔）",
        placeholder="例如: 123456789,987654321",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph
    )

    role_ids_input = ui.TextInput(
        label="通知身份组 ID（多个用逗号分隔）",
        placeholder="例如: 111222333,444555666",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph
    )

    def __init__(self, parent: MemberMonitorPanelView):
        super().__init__()
        self.parent = parent

        # 设置默认值
        config = parent.config
        if config:
            user_ids = parse_json_list(config.get("notify_user_ids"))
            if user_ids:
                self.user_ids_input.default = ",".join(user_ids)

            role_ids = parse_json_list(config.get("notify_role_ids"))
            if role_ids:
                self.role_ids_input.default = ",".join(role_ids)

    async def on_submit(self, interaction: discord.Interaction):
        await safe_defer(interaction)

        # 解析用户 ID
        user_ids = []
        user_input = self.user_ids_input.value.strip()
        if user_input:
            for part in user_input.replace(" ", "").split(","):
                part = part.strip()
                if part.isdigit():
                    user_ids.append(part)

        # 解析角色 ID
        role_ids = []
        role_input = self.role_ids_input.value.strip()
        if role_input:
            for part in role_input.replace(" ", "").split(","):
                part = part.strip()
                if part.isdigit():
                    role_ids.append(part)

        # 更新配置
        guild_id = str(self.parent.guild.id)
        await self.parent.cog._upsert_config(
            guild_id,
            notify_user_ids=user_ids,
            notify_role_ids=role_ids
        )

        # 刷新面板
        await self.parent.refresh_config()
        embed = await self.parent.build_status_embed()

        # 构建回复
        result_lines = []
        if user_ids:
            result_lines.append(f"用户: {len(user_ids)} 个")
        if role_ids:
            result_lines.append(f"身份组: {len(role_ids)} 个")

        if result_lines:
            await interaction.followup.send(
                f"✅ 通知对象已更新:\n" + "\n".join(result_lines),
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "✅ 已清除所有通知对象",
                ephemeral=True
            )

        try:
            await interaction.message.edit(embed=embed, view=self.parent)
        except Exception:
            pass


class ConfirmDeleteView(ui.View):
    """删除确认视图"""

    def __init__(self, parent: MemberMonitorPanelView):
        super().__init__(timeout=30)
        self.parent = parent

    @ui.button(label="确认删除", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        await safe_defer(interaction)

        guild_id = str(self.parent.guild.id)
        await self.parent.cog._delete_config(guild_id)

        await interaction.followup.send("✅ 成员监控配置已删除", ephemeral=True)

        # 禁用按钮
        for item in self.children:
            item.disabled = True

        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await safe_defer(interaction)
        await interaction.followup.send("已取消", ephemeral=True)

        # 禁用按钮
        for item in self.children:
            item.disabled = True

        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
