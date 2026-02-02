# -*- coding: utf-8 -*-
"""
模块名称: feedback.py
功能描述: 反馈系统 Cog（包含反馈面板召唤、匿名/实名反馈处理、限流与白名单校验）
作者: Kilo Code
创建日期: 2025-09-29
最后修改: 2025-09-29
"""

import json
import datetime
from typing import Optional, Union

import discord
from discord.ext import commands
from discord import app_commands

from .base_cog import BaseCog
from utils.logger import get_logger
from utils.validators import validate_user_input
# from utils.permissions import admin_or_owner  # 不再直接使用装饰器，避免交互前阻塞
from core.database import db_manager
from views.feedback_views import FeedbackPanelView

logger = get_logger(__name__)


# 黄金法则：统一的“占坑”函数（除模态框send_modal例外）
async def safe_defer(interaction: discord.Interaction):
    """
    一个绝对安全的“占坑”函数。
    它会检查交互是否已被响应，如果没有，就立即以“仅自己可见”的方式延迟响应，
    避免3秒超时与重复响应。
    """
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)


class FeedbackCog(BaseCog):
    """
    反馈系统：
    - 单一面板包含两个按钮：匿名反馈 / 实名反馈
    - 两种反馈投递到同一个目标频道（或子区）
    - 限制条件：全服总发言数阈值 + 时间窗口内发言数阈值 + 每日最大反馈次数
    - 白名单：拥有任一配置的身份组才允许提交（若列表为空，默认不限制）
    - 文案可配置：面板标题/描述、按钮文本、模态标题与输入标签、回执文本
    """

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        # 注：已移除 _msg_counters 消息计数器功能以节省内存
        # 限流改为仅使用白名单 + 每日反馈次数限制（从数据库查询）

    async def cog_load(self):
        """Cog加载时注册持久视图"""
        # 从配置读取文案，实例化持久化视图
        # 注意：持久视图需要在启动时注册，以保证自定义ID长期有效
        default_conf = self._get_static_config()
        view = FeedbackPanelView(
            anonymous_button_label=default_conf.get("anonymous_button_label", "匿名反馈"),
            named_button_label=default_conf.get("named_button_label", "实名反馈"),
            anonymous_modal_title=default_conf.get("anonymous_modal_title", "匿名反馈"),
            named_modal_title=default_conf.get("named_modal_title", "实名反馈"),
            modal_input_label=default_conf.get("modal_input_label", "请输入你的反馈（支持多行）"),
        )
        # 防止在热重载后重复注册持久化视图（与 bot 级注册配合）
        if not getattr(self.bot, "_feedback_view_registered", False):
            self.bot.add_view(view)
            setattr(self.bot, "_feedback_view_registered", True)
            self.logger.info("FeedbackCog registered persistent FeedbackPanelView")
        else:
            self.logger.info("FeedbackPanelView already registered; skip duplicate")

    async def cog_unload(self):
        """Cog卸载时的清理"""
        pass  # 已移除消息计数器相关清理

    # ----------------------------
    # 配置读取与覆盖
    # ----------------------------
    def _get_static_config(self) -> dict:
        """
        读取全局静态配置中的 FEEDBACK 段。
        注意：这是默认配置。如需按服务器覆盖，可从DB读取 feedback_configs。
        """
        try:
            conf = self.bot.config.get("FEEDBACK", {}) if hasattr(self.bot, "config") else {}
            return conf or {}
        except Exception as e:
            self.logger.error(f"读取静态FEEDBACK配置失败: {e}", exc_info=True)
            return {}

    async def _get_guild_config(self, guild_id: str) -> dict:
        """
        按服务器读取最终生效的配置：
        - 以静态 config.json 的 FEEDBACK 为默认
        - 若 DB 存在 feedback_configs 覆盖，则合并（DB优先）
        """
        conf = self._get_static_config()
        try:
            row = await db_manager.fetchone(
                "SELECT target_channel_id, allowed_role_ids, panel_texts, limits, runtime_counters FROM feedback_configs WHERE guild_id = ?",
                (guild_id,),
            )
            if row:
                # 合并覆盖
                if row.get("target_channel_id"):
                    conf["target_channel_id"] = row["target_channel_id"]
                if row.get("allowed_role_ids"):
                    try:
                        conf["allowed_role_ids"] = json.loads(row["allowed_role_ids"])
                    except Exception:
                        pass
                if row.get("panel_texts"):
                    try:
                        panel_texts = json.loads(row["panel_texts"])
                        conf.update(panel_texts or {})
                    except Exception:
                        pass
                if row.get("limits"):
                    try:
                        conf["limits"] = json.loads(row["limits"])
                    except Exception:
                        pass
                if row.get("runtime_counters"):
                    try:
                        conf["runtime_counters"] = json.loads(row["runtime_counters"])
                    except Exception:
                        pass
        except Exception as e:
            self.logger.error(f"读取feedback_configs失败: {e}", exc_info=True)
        return conf or {}

    # ----------------------------
    # 权限：轻量级管理员/开发者校验（避免在检查阶段调用 application_info 导致超时）
    # ----------------------------
    def _get_developer_ids(self) -> set:
        try:
            cfg = getattr(self.bot, "config", {}) or {}
            ids = cfg.get("DEVELOPER_IDS", [])
            return {str(i) for i in ids if i is not None}
        except Exception:
            return set()

    def _is_admin_or_developer(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        try:
            if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
                return True
        except Exception:
            pass
        dev_ids = self._get_developer_ids()
        return str(interaction.user.id) in dev_ids

    # ----------------------------
    # 核心处理：提交反馈
    # ----------------------------
    async def process_feedback(
        self,
        interaction: discord.Interaction,
        content: str,
        is_anonymous: bool,
    ):
        """
        处理模态提交：
        1) 文本校验
        2) 身份组白名单校验
        3) 限流校验（总发言 + 时间窗口 + 每日次数）
        4) 组装并投递Embed到目标频道（匿名蓝色/实名黄色）
        5) 写入反馈记录并回执
        """
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else interaction.user
        if not guild:
            return await interaction.followup.send("❌ 此功能仅在服务器内可用。", ephemeral=True)

        guild_id = str(guild.id)
        user_id = str(interaction.user.id)

        # 1) 文本校验
        ok, err = validate_user_input(content, max_length=2000)
        if not ok:
            return await interaction.followup.send(f"❌ 输入无效：{err}", ephemeral=True)

        # 读取最终配置
        conf = await self._get_guild_config(guild_id)
        if not conf.get("enabled", True):
            return await interaction.followup.send("❌ 反馈系统未启用。", ephemeral=True)

        target_channel_id = str(conf.get("target_channel_id") or "")
        if not target_channel_id.isdigit():
            return await interaction.followup.send("❌ 未配置有效的目标频道。", ephemeral=True)

        # 2) 身份组白名单校验（若列表为空，默认不限制）
        allow_roles = conf.get("allowed_role_ids", [])
        if isinstance(allow_roles, list) and len(allow_roles) > 0:
            member_role_ids = {str(r.id) for r in getattr(member, "roles", [])}
            if not (set(allow_roles) & member_role_ids):
                denied_msg = conf.get(
                    "role_denied_message_ephemeral", "🚫 你尚未具备允许提交反馈的身份组。"
                )
                return await interaction.followup.send(denied_msg, ephemeral=True)

        # 3) 每日反馈次数限制（从数据库查询，不使用内存计数器）
        limits = conf.get("limits", {}) or {}
        max_per_day = int(limits.get("max_feedbacks_per_day", 0))

        if max_per_day > 0:
            recent_24h = await self._count_recent_feedbacks(guild_id, user_id, hours=24)
            if recent_24h >= max_per_day:
                rate_msg = conf.get(
                    "rate_limited_message_ephemeral",
                    "⏰ 你的反馈次数已达上限，暂时无法提交反馈。",
                )
                return await interaction.followup.send(
                    f"{rate_msg}\n原因：今日反馈次数已达上限（{recent_24h}/{max_per_day}）",
                    ephemeral=True
                )

        # 4) 组装并投递Embed
        # 匿名蓝色 / 实名黄色
        if is_anonymous:
            embed_title = conf.get("anonymous_modal_title", "匿名反馈")
            embed_color = discord.Color.blue()
            embed = discord.Embed(title=embed_title, description=content, color=embed_color)
        else:
            embed_title = conf.get("named_modal_title", "实名反馈")
            embed_color = discord.Color.gold()
            embed = discord.Embed(title=embed_title, description=content, color=embed_color)
            # 实名：展示提交者信息 + 记录用户ID
            try:
                embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            except Exception:
                embed.set_author(name=interaction.user.display_name)
            # 明确记录用户ID，便于审计与后续跟踪
            try:
                embed.add_field(name="反馈者ID", value=str(interaction.user.id), inline=False)
            except Exception:
                pass
            # 额外在footer保留时间戳
            try:
                embed.timestamp = datetime.datetime.utcnow()
                embed.set_footer(text="实名反馈")
            except Exception:
                pass

        # 获取目标频道（支持 TextChannel / Thread；ForumChannel 将创建主题）
        target_channel = guild.get_channel(int(target_channel_id)) or self.bot.get_channel(int(target_channel_id))
        if not target_channel:
            # 可能是线程或未缓存对象，尝试 HTTP fetch
            try:
                target_channel = await self.bot.fetch_channel(int(target_channel_id))
            except Exception:
                target_channel = None

        if not target_channel:
            return await interaction.followup.send("❌ 目标频道不存在或不可见。", ephemeral=True)

        sent_msg: Optional[discord.Message] = None
        try:
            if isinstance(target_channel, discord.ForumChannel):
                # 论坛子区：创建主题（首帖尽量携带Embed）
                try:
                    thread = await target_channel.create_thread(
                        name=f"用户反馈（{'匿名' if is_anonymous else interaction.user.display_name}｜ID:{interaction.user.id if not is_anonymous else 'N/A'}）",
                        embed=embed
                    )
                    sent_msg = None  # 部分版本无法直接拿到首帖对象
                except TypeError:
                    # 回退：创建空主题并在主题内发送Embed
                    thread = await target_channel.create_thread(
                        name=f"用户反馈（{'匿名' if is_anonymous else interaction.user.display_name}｜ID:{interaction.user.id if not is_anonymous else 'N/A'}）"
                    )
                    sent_msg = await thread.send(embed=embed)
            elif isinstance(target_channel, discord.Thread):
                # 主题：直接在主题内发送
                sent_msg = await target_channel.send(embed=embed)
            else:
                # 文本频道：直接发送
                sent_msg = await target_channel.send(embed=embed)
        except discord.Forbidden:
            return await interaction.followup.send("❌ 我没有权限在目标频道发送消息。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"发送反馈消息失败: {e}", exc_info=True)
            return await interaction.followup.send("❌ 发送反馈失败：发生未知错误。", ephemeral=True)

        # 5) 写入反馈记录
        try:
            created_at = datetime.datetime.utcnow().isoformat()
            await db_manager.execute(
                '''
                INSERT INTO feedback_submissions (guild_id, user_id, type, content, channel_id, message_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    guild_id,
                    user_id,
                    "anonymous" if is_anonymous else "named",
                    content,
                    str(target_channel.id),
                    str(sent_msg.id) if sent_msg else None,
                    created_at,
                ),
            )
        except Exception as e:
            self.logger.error(f"写入反馈记录失败: {e}", exc_info=True)

        # 回执
        success_msg = conf.get("success_message_ephemeral", "✅ 已收到你的反馈！")
        await interaction.followup.send(success_msg, ephemeral=True)

    async def _count_recent_feedbacks(self, guild_id: str, user_id: str, hours: int = 24) -> int:
        try:
            cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=hours)).isoformat()
            rows = await db_manager.fetchall(
                "SELECT COUNT(*) AS cnt FROM feedback_submissions WHERE guild_id = ? AND user_id = ? AND created_at >= ?",
                (guild_id, user_id, cutoff),
            )
            if rows and isinstance(rows[0].get("cnt"), (int, float)):
                return int(rows[0]["cnt"])
            return 0
        except Exception as e:
            self.logger.error(f"统计最近反馈次数失败: {e}", exc_info=True)
            return 0

    # ----------------------------
    # 斜杠命令：召唤反馈面板
    # ----------------------------
    @app_commands.command(name="召唤反馈面板", description="在该频道召唤反馈面板（匿名/实名）")
    @app_commands.guild_only()
    @app_commands.describe(
        panel_title="面板标题（可选）",
        panel_description="面板说明（可选，支持 \\n 换行）",
        anonymous_button_label="匿名按钮文本（可选）",
        named_button_label="实名按钮文本（可选）",
        modal_input_label="模态输入框标签（可选）",
        target_channel="目标频道（可选，不填则为当前频道）"
    )
    async def summon_feedback_panel(
        self,
        interaction,
        panel_title: Optional[str] = None,
        panel_description: Optional[str] = None,
        anonymous_button_label: Optional[str] = None,
        named_button_label: Optional[str] = None,
        modal_input_label: Optional[str] = None,
        target_channel: Optional[discord.TextChannel] = None
    ):
        """
        召唤反馈面板
        - 支持覆盖默认文案与目标频道
        """
        # 交互入口日志（用于诊断“未响应”）
        try:
            self.logger.info(f"[召唤反馈面板] 收到交互: user={interaction.user.id}, guild={getattr(interaction.guild, 'id', None)}, channel={getattr(interaction.channel, 'id', None)}")
        except Exception:
            pass

        # A1: 立即占坑（避免任何前置阻塞导致未响应）
        try:
            await safe_defer(interaction)
            self.logger.debug("[召唤反馈面板] 已完成defer")
        except Exception as e:
            # 若占坑失败，直接返回
            self.logger.error(f"[召唤反馈面板] defer失败: {e}", exc_info=True)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ 交互占坑失败。", ephemeral=True)
            except Exception:
                pass
            return

        # A2: 权限校验（快速、本地）
        if not self._is_admin_or_developer(interaction):
            return await interaction.followup.send("❌ 你没有执行此命令的权限。", ephemeral=True)

        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("❌ 此命令需在服务器中使用。", ephemeral=True)
        guild_id = str(guild.id)

        # A3: 读取配置
        try:
            conf = await self._get_guild_config(guild_id)
            self.logger.debug(f"[召唤反馈面板] 已读取配置，target_channel_id={conf.get('target_channel_id')}")
        except Exception as e:
            self.logger.error(f"[召唤反馈面板] 读取配置失败: {e}", exc_info=True)
            return await interaction.followup.send("❌ 读取配置失败。", ephemeral=True)

        # A4: 目标频道
        channel = target_channel or interaction.channel
        if not channel:
            return await interaction.followup.send("❌ 未找到有效的目标频道。", ephemeral=True)

        # A5: 文案
        title = panel_title or conf.get("panel_title", "反馈中心")
        description = (panel_description or conf.get("panel_description", "请选择匿名或实名提交反馈，所有反馈将转发到指定频道。")).replace("\\n", "\n")
        anon_label = anonymous_button_label or conf.get("anonymous_button_label", "匿名反馈")
        named_label = named_button_label or conf.get("named_button_label", "实名反馈")
        input_label = modal_input_label or conf.get("modal_input_label", "请输入你的反馈（支持多行）")

        embed = discord.Embed(title=title, description=description, color=discord.Color.purple())
        view = FeedbackPanelView(
            anonymous_button_label=anon_label,
            named_button_label=named_label,
            anonymous_modal_title=conf.get("anonymous_modal_title", "匿名反馈"),
            named_modal_title=conf.get("named_modal_title", "实名反馈"),
            modal_input_label=input_label,
        )

        # A6: 下发消息
        try:
            self.logger.debug(f"[召唤反馈面板] 准备发送面板到 channel={getattr(channel, 'id', None)} type={type(channel)}")
            await channel.send(embed=embed, view=view)
            self.logger.debug("[召唤反馈面板] 面板消息已发送")
            await interaction.followup.send(f"✅ 反馈面板已创建于 {channel.mention}。", ephemeral=True)
        except discord.Forbidden:
            self.logger.error("[召唤反馈面板] Forbidden: 无法在该频道发送消息")
            await interaction.followup.send("❌ 我没有权限在该频道发送消息。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"[召唤反馈面板] 发送面板异常: {e}", exc_info=True)
            await interaction.followup.send("❌ 召唤反馈面板失败：发生未知错误。", ephemeral=True)

 

    # ----------------------------
    # 配置维护：DB 覆盖静态配置
    # ----------------------------
    async def _upsert_guild_config(self, guild_id: str, patch: dict) -> None:
        """
        将传入的 patch 合并到 feedback_configs（DB），支持：
        - target_channel_id: str
        - allowed_role_ids: list[str]
        - panel_texts: dict 文案
        - limits: dict
        - runtime_counters: dict
        """
        try:
            existing = await db_manager.fetchone(
                "SELECT target_channel_id, allowed_role_ids, panel_texts, limits, runtime_counters FROM feedback_configs WHERE guild_id = ?",
                (guild_id,)
            )
            target_channel_id = (existing or {}).get("target_channel_id")
            allowed_role_ids_json = (existing or {}).get("allowed_role_ids")
            panel_texts_json = (existing or {}).get("panel_texts")
            limits_json = (existing or {}).get("limits")
            runtime_counters_json = (existing or {}).get("runtime_counters")

            # 解析为对象
            def _loads(x):
                try:
                    return json.loads(x) if x else None
                except Exception:
                    return None

            allowed_role_ids = _loads(allowed_role_ids_json) or []
            panel_texts = _loads(panel_texts_json) or {}
            limits = _loads(limits_json) or {}
            runtime_counters = _loads(runtime_counters_json) or {}

            # 合并 patch
            if "target_channel_id" in patch and patch["target_channel_id"]:
                target_channel_id = str(patch["target_channel_id"])
            if "allowed_role_ids" in patch and isinstance(patch["allowed_role_ids"], list):
                allowed_role_ids = list({str(x) for x in patch["allowed_role_ids"]})
            if "panel_texts" in patch and isinstance(patch["panel_texts"], dict):
                panel_texts.update(patch["panel_texts"])
            if "limits" in patch and isinstance(patch["limits"], dict):
                limits.update({k: int(v) for k, v in patch["limits"].items() if v is not None})
            if "runtime_counters" in patch and isinstance(patch["runtime_counters"], dict):
                runtime_counters.update(patch["runtime_counters"])

            updated_at = datetime.datetime.utcnow().isoformat()
            await db_manager.execute(
                '''
                INSERT OR REPLACE INTO feedback_configs (guild_id, target_channel_id, allowed_role_ids, panel_texts, limits, runtime_counters, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    guild_id,
                    target_channel_id,
                    json.dumps(allowed_role_ids, ensure_ascii=False),
                    json.dumps(panel_texts, ensure_ascii=False),
                    json.dumps(limits, ensure_ascii=False),
                    json.dumps(runtime_counters, ensure_ascii=False),
                    updated_at
                )
            )
        except Exception as e:
            self.logger.error(f"更新反馈配置失败: {e}", exc_info=True)

    # ----------------------------
    # ----------------------------
    # 斜杠命令：设置目标频道（反馈推送命令）
    # ----------------------------
    @app_commands.command(name="反馈设置频道", description="设置反馈投递目标频道（文本频道或论坛子区）")
    @app_commands.guild_only()
    @app_commands.describe(
        target_channel="目标频道（推荐选择文本频道或论坛子区）",
        channel_id="可选：直接填频道/子区/主题ID（用于无法从下拉选择到子区或线程时）"
    )
    async def set_feedback_channel(
        self,
        interaction: discord.Interaction,
        target_channel: Union[discord.TextChannel, discord.ForumChannel, None] = None,
        channel_id: Optional[str] = None
    ):
        await safe_defer(interaction)
        # 权限校验
        if not self._is_admin_or_developer(interaction):
            return await interaction.followup.send("❌ 你没有执行此命令的权限。", ephemeral=True)

        if not interaction.guild:
            return await interaction.followup.send("❌ 此命令需在服务器中使用。", ephemeral=True)

        try:
            guild = interaction.guild
            resolved = None

            if target_channel is not None:
                resolved = target_channel
            elif channel_id and channel_id.isdigit():
                cid = int(channel_id)
                # 先尝试从缓存获取
                resolved = guild.get_channel(cid) or self.bot.get_channel(cid)
                # 如果仍未找到，尝试 HTTP fetch（可能是线程或跨缓存对象）
                if resolved is None:
                    try:
                        resolved = await self.bot.fetch_channel(cid)
                    except Exception:
                        resolved = None

            if resolved is None:
                return await interaction.followup.send("❌ 未找到指定的频道/子区/主题，请检查参数。", ephemeral=True)

            # 合法类型：文本频道、论坛子区、主题（Thread）
            if not isinstance(resolved, (discord.TextChannel, discord.ForumChannel, discord.Thread)):
                return await interaction.followup.send("❌ 仅支持文本频道、论坛子区或主题（Thread）。", ephemeral=True)

            await self._upsert_guild_config(str(guild.id), {"target_channel_id": str(resolved.id)})

            kind = "主题" if isinstance(resolved, discord.Thread) else ("论坛子区" if isinstance(resolved, discord.ForumChannel) else "文本频道")
            await interaction.followup.send(f"✅ 已设置反馈目标为 {kind}：{getattr(resolved, 'mention', resolved.id)}", ephemeral=True)
        except Exception as e:
            self.logger.error(f"设置反馈频道失败: {e}", exc_info=True)
            await interaction.followup.send("❌ 设置失败：发生未知错误。", ephemeral=True)

    # ----------------------------
    # 斜杠命令：反馈白名单一体化（添加/移除/列出/清空）
    # ----------------------------
    @app_commands.command(name="反馈白名单", description="管理反馈白名单（添加/移除/列出/清空）")
    @app_commands.guild_only()
    @app_commands.choices(action=[
        app_commands.Choice(name="添加", value="add"),
        app_commands.Choice(name="移除", value="remove"),
        app_commands.Choice(name="列出", value="list"),
        app_commands.Choice(name="清空", value="clear"),
    ])
    @app_commands.describe(
        action="选择要执行的操作（添加/移除/列出/清空）",
        role="当选择添加或移除时需指定的身份组（可选）"
    )
    async def manage_feedback_whitelist(
        self,
        interaction: discord.Interaction,
        action: str,
        role: Optional[discord.Role] = None
    ):
        """
        管理反馈白名单（添加/移除/列出/清空）
        """
        await safe_defer(interaction)
        # 权限校验
        if not self._is_admin_or_developer(interaction):
            return await interaction.followup.send("❌ 你没有执行此命令的权限。", ephemeral=True)

        if not interaction.guild:
            return await interaction.followup.send("❌ 此命令需在服务器中使用。", ephemeral=True)
        guild_id = str(interaction.guild.id)

        try:
            conf = await self._get_guild_config(guild_id)
            roles_set = set(conf.get("allowed_role_ids", []))

            if action == "list":
                if not roles_set:
                    return await interaction.followup.send("ℹ️ 当前白名单为空。", ephemeral=True)
                lines = []
                for rid in roles_set:
                    r = interaction.guild.get_role(int(rid))
                    lines.append(r.mention if r else f"(不存在的角色ID: {rid})")
                embed = discord.Embed(
                    title="反馈白名单",
                    description="\n".join(lines),
                    color=discord.Color.purple()
                )
                return await interaction.followup.send(embed=embed, ephemeral=True)

            if action == "clear":
                await self._upsert_guild_config(guild_id, {"allowed_role_ids": []})
                return await interaction.followup.send("✅ 已清空反馈白名单。", ephemeral=True)

            # add/remove 需要 role
            if role is None and action in ("add", "remove"):
                return await interaction.followup.send("❌ 请指定身份组（当选择添加或移除时）。", ephemeral=True)

            if action == "add" and role is not None:
                roles_set.add(str(role.id))
                await self._upsert_guild_config(guild_id, {"allowed_role_ids": list(roles_set)})
                return await interaction.followup.send(f"✅ 已添加白名单身份组：{role.mention}", ephemeral=True)

            if action == "remove" and role is not None:
                roles_set = {rid for rid in roles_set if rid != str(role.id)}
                await self._upsert_guild_config(guild_id, {"allowed_role_ids": list(roles_set)})
                return await interaction.followup.send(f"✅ 已移除白名单身份组：{role.mention}", ephemeral=True)

            # 未知动作
            await interaction.followup.send("❌ 无效的操作。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"管理白名单失败: {e}", exc_info=True)
            await interaction.followup.send("❌ 设置失败：发生未知错误。", ephemeral=True)
    # 斜杠命令：设置目标频道
    # ----------------------------
    # ----------------------------
    # 斜杠命令：反馈白名单一体化（添加/移除/列出/清空）
    # ----------------------------




async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(FeedbackCog(bot))
    logger.info("FeedbackCog has been added to bot")