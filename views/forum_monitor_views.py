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
    auto=yes, notify=yes, mention=no, cross=yes, append_link=yes
    支持: yes/no/true/false/1/0/y/n/t/f
    未提供时默认: notify=True, append_link=True，其它False
    """
    default = {"auto": False, "notify": True, "mention": False, "cross": False, "append_link": True}
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
            if k in ("auto", "notify", "mention", "cross", "append_link"):
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
    - cross_post_config: 跨频道提醒综合配置（多行）
      channel=频道ID或<#频道>
      roles=角色ID列表或<@&角色>列表（逗号分隔）
      template=模板（支持 {thread_url} {thread_title} {forum_name} {author_mention}）
      flags=cross=yes,append_link=yes（也支持 auto/notify/mention）
    """
    auto_role_id = ui.TextInput(label="自动身份组ID或@提及（可选，多个用逗号分隔）", required=False, placeholder="示例: 123,456 或 <@&123>,<@&456>")
    notify_message = ui.TextInput(label="通知消息（可选）", style=discord.TextStyle.paragraph, required=False, placeholder="默认：欢迎加入讨论！")
    mention_role_id = ui.TextInput(label="@身份组ID或@提及（可选，多个用逗号分隔）", required=False, placeholder="示例: 123,456 或 <@&123>,<@&456>")
    mention_message = ui.TextInput(label="@身份组附加消息（可选）", style=discord.TextStyle.paragraph, required=False)
    cross_post_config = ui.TextInput(
        label="跨频道提醒配置（可选，多行key=value）",
        style=discord.TextStyle.paragraph,
        required=False,
        placeholder="channel=<#频道ID>；roles=<@&角色ID>；flags=cross=yes,append_link=yes"
    )

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
        raw_cross_cfg = str(self.cross_post_config).strip()
        cross_map: Dict[str, str] = {}
        if raw_cross_cfg:
            for line in raw_cross_cfg.splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k and v:
                    cross_map[k] = v

        flags_raw = cross_map.get("flags")
        flags_dict = _parse_flags(flags_raw) if flags_raw else _parse_flags(None)

        cross_channel_raw = (cross_map.get("channel") or "").strip()
        cross_channel_id = None
        if cross_channel_raw:
            if cross_channel_raw.startswith("<#") and cross_channel_raw.endswith(">"):
                cross_channel_raw = cross_channel_raw[2:-1]
            if cross_channel_raw.isdigit():
                ch = self.guild.get_channel(int(cross_channel_raw))
                if ch and ch.type in (
                    discord.ChannelType.text,
                    discord.ChannelType.news,
                    discord.ChannelType.public_thread,
                    discord.ChannelType.private_thread,
                ):
                    cross_channel_id = cross_channel_raw

        cross_roles_raw = cross_map.get("roles")
        cross_role_ids = _parse_role_ids_csv(self.guild, cross_roles_raw) if cross_roles_raw else []
        cross_template_raw = cross_map.get("template")
        cross_template = _truncate_message(cross_template_raw) if cross_template_raw else None

        # 确定开关
        auto_enabled = flags_dict.get("auto", False) and len(auto_role_ids) > 0
        notify_enabled = flags_dict.get("notify", True)  # 允许notify开启但消息为空时使用默认文案
        mention_enabled = flags_dict.get("mention", False) and len(mention_role_ids) > 0
        cross_enabled = flags_dict.get("cross", False) and bool(cross_channel_id)
        append_link_enabled = flags_dict.get("append_link", True)

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
                mention_message=mention_msg,
                cross_post_enabled=cross_enabled,
                cross_post_channel_id=cross_channel_id,
                cross_post_role_ids=",".join(cross_role_ids) if cross_role_ids else None,
                cross_post_template=cross_template,
                cross_post_append_link=append_link_enabled,
            )

            await interaction.followup.send("✅ 配置已保存。", ephemeral=True)

            # 回填最新配置到面板
            parent_view: ForumMonitorPanelView = getattr(self, "parent_view", None)  # type: ignore
            if parent_view:
                await parent_view.show_current_config(interaction)
        except Exception as e:
            logger.error(f"ForumMonitor: save config error: {e}", exc_info=True)
            await interaction.followup.send("❌ 保存配置时发生错误。", ephemeral=True)


class ForumMonitorPermissionModal(ui.Modal, title="帖子监控权限管理"):
    action = ui.TextInput(
        label="操作类型",
        required=True,
        placeholder="add / remove / list / clear"
    )
    role_ids = ui.TextInput(
        label="身份组（可选，多个用逗号分隔）",
        required=False,
        style=discord.TextStyle.paragraph,
        placeholder="示例: 123,456 或 <@&123>,<@&456>"
    )

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        await safe_defer(interaction)

        if not await is_admin_or_owner(interaction):
            await interaction.followup.send("❌ 仅管理员/开发者可管理帖子监控权限。", ephemeral=True)
            return

        cog = interaction.client.get_cog("ForumPostMonitorCog")
        if not cog:
            await interaction.followup.send("❌ 系统模块未加载。", ephemeral=True)
            return

        action = str(self.action).strip().lower()
        guild_id = str(self.guild.id)

        try:
            if action == "list":
                role_ids = await cog._list_panel_permission_role_ids(guild_id)
                if not role_ids:
                    await interaction.followup.send("ℹ️ 当前未配置额外可操作帖子监控的身份组。", ephemeral=True)
                    return
                mentions = []
                for rid in role_ids:
                    role = self.guild.get_role(int(rid)) if str(rid).isdigit() else None
                    mentions.append(role.mention if role else f"<@&{rid}>")
                await interaction.followup.send("✅ 当前可操作身份组：\n" + " ".join(mentions), ephemeral=True)
                return

            if action == "clear":
                affected = await cog._clear_panel_permission_roles(guild_id)
                await interaction.followup.send(f"✅ 已清空帖子监控额外权限身份组（{affected} 条）。", ephemeral=True)
                return

            role_ids = _parse_role_ids_csv(self.guild, str(self.role_ids)) if str(self.role_ids).strip() else []
            if not role_ids:
                await interaction.followup.send("❌ 请提供至少一个有效身份组。", ephemeral=True)
                return

            if action == "add":
                for rid in role_ids:
                    await cog._add_panel_permission_role(guild_id, rid, str(interaction.user.id))
                await interaction.followup.send(f"✅ 已添加 {len(role_ids)} 个身份组到帖子监控可操作名单。", ephemeral=True)
                return

            if action == "remove":
                removed = 0
                for rid in role_ids:
                    removed += await cog._remove_panel_permission_role(guild_id, rid)
                await interaction.followup.send(f"✅ 已移除 {removed} 条帖子监控身份组权限。", ephemeral=True)
                return

            await interaction.followup.send("❌ 操作类型无效，请使用 add/remove/list/clear。", ephemeral=True)
        except Exception as e:
            logger.error(f"ForumMonitor: manage permission roles error: {e}", exc_info=True)
            await interaction.followup.send("❌ 处理权限管理时发生错误。", ephemeral=True)


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

            cross_target = "无"
            cross_id = str(config.get("cross_post_channel_id") or "").strip()
            if cross_id.isdigit():
                cross_ch = interaction.guild.get_channel(int(cross_id))
                cross_target = cross_ch.mention if cross_ch else f"ID: {cross_id}"
            cross_roles = _format_role_ids_for_display(interaction.guild, config.get('cross_post_role_ids'))
            cross_tpl = (config.get('cross_post_template') or '默认模板').strip()
            append_link_text = b2e(config.get('cross_post_append_link'))
            embed.add_field(
                name="跨频道提醒",
                value=(
                    f"{b2e(config.get('cross_post_enabled'))} | 目标频道: {cross_target}\n"
                    f"目标身份组: {cross_roles} | 自动附链: {append_link_text}\n"
                    f"模板: {cross_tpl}"
                ),
                inline=False,
            )
        role_ids = await cog._list_panel_permission_role_ids(guild_id)
        if role_ids:
            mentions = []
            for rid in role_ids:
                role = interaction.guild.get_role(int(rid)) if str(rid).isdigit() else None
                mentions.append(role.mention if role else f"<@&{rid}>")
            roles_text = " ".join(mentions)
        else:
            roles_text = "无（仅管理员/开发者可操作）"

        embed.add_field(name="面板可操作身份组", value=roles_text, inline=False)

        if not config:
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
        cog = interaction.client.get_cog("ForumPostMonitorCog")
        if not cog:
            await interaction.response.send_message("❌ 系统模块未加载。", ephemeral=True)
            return

        if not await cog._is_forum_monitor_operator(interaction):
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
        cog = interaction.client.get_cog("ForumPostMonitorCog")
        if not cog:
            await interaction.followup.send("❌ 系统模块未加载。", ephemeral=True)
            return

        # 权限限制：管理员/开发者 或 已授权身份组
        if not await cog._is_forum_monitor_operator(interaction):
            await interaction.followup.send("❌ 你没有权限删除配置。", ephemeral=True)
            return

        if not self.selected_channel_id:
            await interaction.followup.send("ℹ️ 请先选择一个论坛频道。", ephemeral=True)
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

    @ui.button(label="使用说明/预设", style=discord.ButtonStyle.secondary)
    async def help_button(self, interaction: discord.Interaction, button: ui.Button):
        """显示面板使用说明与可复制预设。"""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        guide_embed = discord.Embed(
            title="帖子监控面板使用说明",
            description=(
                "你可以先在上方选择论坛频道，再点“新增/更新配置”。\n"
                "跨频道提醒使用单个多行字段 `key=value` 配置。\n"
                "模板中可写变量，最终会自动替换。"
            ),
            color=discord.Color.blurple(),
        )
        guide_embed.add_field(
            name="开关说明（写在 flags=...）",
            value=(
                "- auto: 自动上身份组\n"
                "- notify: 在线程内通知贴主\n"
                "- mention: 在线程内@身份组\n"
                "- cross: 跨频道提醒\n"
                "- append_link: 自动附加帖子链接"
            ),
            inline=False,
        )
        guide_embed.add_field(
            name="预设A（只保留原有三项）",
            value=(
                "```\n"
                "flags=auto=yes,notify=yes,mention=yes\n"
                "```"
            ),
            inline=False,
        )
        guide_embed.add_field(
            name="预设B（开启跨频道提醒）",
            value=(
                "```\n"
                "channel=<#目标频道ID>\n"
                "roles=<@&角色1>,<@&角色2>\n"
                "template={author_mention} 在 {forum_name} 发布了新帖子：[{thread_title}]({thread_url})\n"
                "flags=auto=yes,notify=yes,mention=yes,cross=yes,append_link=yes\n"
                "```"
            ),
            inline=False,
        )
        guide_embed.add_field(
            name="可用变量（template里可用）",
            value=(
                "- `{thread_url}`: 帖子跳转链接\n"
                "- `{thread_title}`: 帖子标题\n"
                "- `{forum_name}`: 所在论坛频道名\n"
                "- `{author_mention}`: 发帖人@提及"
            ),
            inline=False,
        )
        guide_embed.add_field(
            name="格式与注意事项",
            value=(
                "- 蓝色可跳转标题格式：`[{thread_title}]({thread_url})`\n"
                "- `append_link=yes` 且模板里没有 `{thread_url}` 时，系统会在末尾自动补一条链接\n"
                "- 未识别变量会保留原文，不会报错\n"
                "- `roles=` 可留空，留空则只发文本不@身份组"
            ),
            inline=False,
        )

        await interaction.followup.send(embed=guide_embed, ephemeral=True)

    @ui.button(label="权限管理", style=discord.ButtonStyle.secondary)
    async def dev_permission_button(self, interaction: discord.Interaction, button: ui.Button):
        """管理可操作帖子监控配置的身份组。"""
        if not await is_admin_or_owner(interaction):
            await interaction.response.send_message("❌ 仅管理员/开发者可管理权限。", ephemeral=True)
            return

        try:
            await interaction.response.send_modal(ForumMonitorPermissionModal(interaction.guild))
        except Exception as e:
            logger.error(f"ForumMonitor: open permission modal error: {e}", exc_info=True)
            await safe_defer(interaction)
            await interaction.followup.send("❌ 无法打开权限管理表单。", ephemeral=True)


__all__ = [
    "ForumMonitorPanelView",
    "ForumMonitorConfigModal",
    "ForumMonitorPermissionModal",
    "ForumChannelSelect",
]