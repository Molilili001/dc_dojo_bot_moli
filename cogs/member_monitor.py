# -*- coding: utf-8 -*-
"""
成员监控系统 - 监听 Discord 官方成员加入事件，统计每日新成员数量并发送告警/日报
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.base_cog import BaseCog
from utils.permissions import is_admin_or_owner
from utils.time_utils import BEIJING_TZ, format_beijing_iso

# 常量定义
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_SETTLEMENT_HOUR = 0
DEFAULT_SETTLEMENT_MINUTE = 0
DEFAULT_JOIN_THRESHOLD = 10
CLEANUP_DAYS = 90  # 保留90天的历史数据


def now_beijing() -> datetime:
    """获取当前北京时间"""
    return datetime.now(BEIJING_TZ)


def get_date_key(dt: Optional[datetime] = None) -> str:
    """获取日期键 (YYYY-MM-DD)，默认为当前北京时间"""
    if dt is None:
        dt = now_beijing()
    return dt.strftime("%Y-%m-%d")


def parse_json_list(raw: Optional[str]) -> List[str]:
    """解析 JSON 数组字符串，返回字符串列表"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data if x]
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def format_json_list(items: List[str]) -> str:
    """将字符串列表格式化为 JSON 数组字符串"""
    return json.dumps(items) if items else "[]"


class MemberMonitorCog(BaseCog):
    """成员监控系统 - 监听成员加入事件，统计每日新成员数量"""

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self._ready = asyncio.Event()
        self._stop_event = asyncio.Event()

    async def cog_load(self):
        """Cog 加载时启动定时任务"""
        self.logger.info("MemberMonitorCog loaded")
        if not self.bot.intents.members:
            self.logger.warning(
                "MemberMonitor: 未启用 intents.members，on_member_join 事件将不会触发。"
                "请在代码与 Discord Developer Portal 中同时开启 Members Intent。"
            )
        else:
            self.logger.info("MemberMonitor: intents.members 已启用，将通过 on_member_join 统计新增成员")

        # 等待 bot ready 后启动定时任务
        self.bot.loop.create_task(self._start_tasks_after_ready())

    async def _start_tasks_after_ready(self):
        """等待 bot ready 后启动定时任务"""
        await self.bot.wait_until_ready()
        self._ready.set()

        # 启动定时任务
        if not self.daily_settlement_task.is_running():
            self.daily_settlement_task.start()
        if not self.cleanup_task.is_running():
            self.cleanup_task.start()

        self.logger.info("MemberMonitor: 定时任务已启动")

    async def cog_unload(self):
        """Cog 卸载时停止定时任务"""
        self._stop_event.set()

        if self.daily_settlement_task.is_running():
            self.daily_settlement_task.cancel()
        if self.cleanup_task.is_running():
            self.cleanup_task.cancel()

        self.logger.info("MemberMonitorCog unloaded")

    # ==================== 配置管理 ====================

    async def _get_config(self, guild_id: str) -> Optional[Dict[str, Any]]:
        """获取服务器的监控配置"""
        return await self.db.fetchone(
            "SELECT * FROM member_monitor_configs WHERE guild_id = ?",
            (guild_id,)
        )

    async def _upsert_config(
        self,
        guild_id: str,
        welcome_channel_id: Optional[str] = None,
        alert_channel_id: Optional[str] = None,
        join_threshold: Optional[int] = None,
        notify_user_ids: Optional[List[str]] = None,
        notify_role_ids: Optional[List[str]] = None,
        timezone: Optional[str] = None,
        settlement_hour: Optional[int] = None,
        settlement_minute: Optional[int] = None,
        is_enabled: Optional[bool] = None
    ) -> None:
        """创建或更新服务器的监控配置"""
        now_iso = format_beijing_iso()
        existing = await self._get_config(guild_id)

        if existing:
            # 更新现有配置
            updates = []
            params = []

            if welcome_channel_id is not None:
                updates.append("welcome_channel_id = ?")
                params.append(welcome_channel_id)
            if alert_channel_id is not None:
                updates.append("alert_channel_id = ?")
                params.append(alert_channel_id)
            if join_threshold is not None:
                updates.append("join_threshold = ?")
                params.append(join_threshold)
            if notify_user_ids is not None:
                updates.append("notify_user_ids = ?")
                params.append(format_json_list(notify_user_ids))
            if notify_role_ids is not None:
                updates.append("notify_role_ids = ?")
                params.append(format_json_list(notify_role_ids))
            if timezone is not None:
                updates.append("timezone = ?")
                params.append(timezone)
            if settlement_hour is not None:
                updates.append("settlement_hour = ?")
                params.append(settlement_hour)
            if settlement_minute is not None:
                updates.append("settlement_minute = ?")
                params.append(settlement_minute)
            if is_enabled is not None:
                updates.append("is_enabled = ?")
                params.append(is_enabled)

            if updates:
                updates.append("updated_at = ?")
                params.append(now_iso)
                params.append(guild_id)

                query = f"UPDATE member_monitor_configs SET {', '.join(updates)} WHERE guild_id = ?"
                await self.db.execute(query, tuple(params))
        else:
            # 创建新配置
            await self.db.execute(
                """
                INSERT INTO member_monitor_configs (
                    guild_id, welcome_channel_id, alert_channel_id, join_threshold,
                    notify_user_ids, notify_role_ids, timezone,
                    settlement_hour, settlement_minute, is_enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    welcome_channel_id or "",
                    alert_channel_id or "",
                    join_threshold or DEFAULT_JOIN_THRESHOLD,
                    format_json_list(notify_user_ids or []),
                    format_json_list(notify_role_ids or []),
                    timezone or DEFAULT_TIMEZONE,
                    settlement_hour if settlement_hour is not None else DEFAULT_SETTLEMENT_HOUR,
                    settlement_minute if settlement_minute is not None else DEFAULT_SETTLEMENT_MINUTE,
                    is_enabled if is_enabled is not None else False,
                    now_iso,
                    now_iso
                )
            )

    async def _delete_config(self, guild_id: str) -> int:
        """删除服务器的监控配置"""
        return await self.db.execute(
            "DELETE FROM member_monitor_configs WHERE guild_id = ?",
            (guild_id,)
        )

    async def _list_enabled_configs(self) -> List[Dict[str, Any]]:
        """获取所有已启用的监控配置"""
        return await self.db.fetchall(
            "SELECT * FROM member_monitor_configs WHERE is_enabled = 1"
        )

    # ==================== 每日统计管理 ====================

    async def _get_daily_stats(self, guild_id: str, date_key: str) -> Optional[Dict[str, Any]]:
        """获取指定日期的统计数据"""
        return await self.db.fetchone(
            "SELECT * FROM member_stats_daily WHERE guild_id = ? AND date_key = ?",
            (guild_id, date_key)
        )

    async def _get_or_create_daily_stats(self, guild_id: str, date_key: Optional[str] = None) -> Dict[str, Any]:
        """获取或创建当日统计数据"""
        if date_key is None:
            date_key = get_date_key()

        stats = await self._get_daily_stats(guild_id, date_key)
        if stats:
            return stats

        # 创建新的当日统计
        now_iso = format_beijing_iso()
        await self.db.execute(
            """
            INSERT OR IGNORE INTO member_stats_daily (
                guild_id, date_key, join_count, threshold_alert_sent,
                daily_report_sent, created_at, updated_at
            ) VALUES (?, ?, 0, 0, 0, ?, ?)
            """,
            (guild_id, date_key, now_iso, now_iso)
        )

        return await self._get_daily_stats(guild_id, date_key) or {
            "guild_id": guild_id,
            "date_key": date_key,
            "join_count": 0,
            "threshold_alert_sent": False,
            "daily_report_sent": False
        }

    async def _increment_join_count(self, guild_id: str, date_key: Optional[str] = None) -> int:
        """增加当日加入计数，返回更新后的计数"""
        if date_key is None:
            date_key = get_date_key()

        # 确保记录存在
        await self._get_or_create_daily_stats(guild_id, date_key)

        now_iso = format_beijing_iso()
        await self.db.execute(
            """
            UPDATE member_stats_daily
            SET join_count = join_count + 1, updated_at = ?
            WHERE guild_id = ? AND date_key = ?
            """,
            (now_iso, guild_id, date_key)
        )

        stats = await self._get_daily_stats(guild_id, date_key)
        return stats["join_count"] if stats else 1

    async def _mark_threshold_alert_sent(self, guild_id: str, date_key: Optional[str] = None) -> None:
        """标记当日阈值告警已发送"""
        if date_key is None:
            date_key = get_date_key()

        now_iso = format_beijing_iso()
        await self.db.execute(
            """
            UPDATE member_stats_daily
            SET threshold_alert_sent = 1, updated_at = ?
            WHERE guild_id = ? AND date_key = ?
            """,
            (now_iso, guild_id, date_key)
        )

    async def _mark_daily_report_sent(self, guild_id: str, date_key: Optional[str] = None) -> None:
        """标记当日日报已发送"""
        if date_key is None:
            date_key = get_date_key()

        now_iso = format_beijing_iso()
        await self.db.execute(
            """
            UPDATE member_stats_daily
            SET daily_report_sent = 1, updated_at = ?
            WHERE guild_id = ? AND date_key = ?
            """,
            (now_iso, guild_id, date_key)
        )

    # ==================== 成员事件监听 ====================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """监听成员加入事件，统计每日新成员数量"""
        guild_id = str(member.guild.id)
        member_id = str(member.id)
        date_key = get_date_key()

        try:
            # 获取配置
            config = await self._get_config(guild_id)
            if not config:
                return

            # 检查是否已启用
            if not config.get("is_enabled"):
                return

            # 增加计数
            current_count = await self._increment_join_count(guild_id, date_key)
            self.logger.debug(
                f"MemberMonitor: 检测到成员加入事件 guild={guild_id} "
                f"member={member_id} date={date_key} count={current_count}"
            )

            # 检查是否需要发送阈值告警
            await self._check_and_send_threshold_alert(guild_id, config, current_count)

        except Exception as e:
            self.logger.error(f"MemberMonitor: on_member_join 处理异常: {e}", exc_info=True)

    async def _check_and_send_threshold_alert(
        self,
        guild_id: str,
        config: Dict[str, Any],
        current_count: int
    ) -> None:
        """检查并发送阈值告警（当日仅首次）"""
        threshold = config.get("join_threshold", DEFAULT_JOIN_THRESHOLD)

        # 未达到阈值
        if current_count < threshold:
            return

        date_key = get_date_key()
        stats = await self._get_daily_stats(guild_id, date_key)

        # 当日已发送过告警
        if stats and stats.get("threshold_alert_sent"):
            return

        # 发送告警
        await self._send_alert(guild_id, config, current_count, is_threshold_alert=True)
        await self._mark_threshold_alert_sent(guild_id, date_key)

    async def _send_alert(
        self,
        guild_id: str,
        config: Dict[str, Any],
        join_count: int,
        is_threshold_alert: bool = False
    ) -> bool:
        """发送告警或日报到指定频道"""
        alert_channel_id = config.get("alert_channel_id")
        if not alert_channel_id:
            self.logger.warning(f"MemberMonitor: guild={guild_id} 未配置告警频道")
            return False

        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            self.logger.warning(f"MemberMonitor: 无法获取 guild={guild_id}")
            return False

        channel = guild.get_channel(int(alert_channel_id))
        if not channel or not isinstance(channel, discord.TextChannel):
            self.logger.warning(f"MemberMonitor: 无法获取告警频道 {alert_channel_id}")
            return False

        # 构建提及字符串
        mentions = self._build_mentions(config)

        # 构建消息内容
        threshold = config.get("join_threshold", DEFAULT_JOIN_THRESHOLD)
        date_key = get_date_key()

        if is_threshold_alert:
            title = "⚠️ 新成员告警"
            description = (
                f"**日期**: {date_key}\n"
                f"**当日新成员数**: {join_count}\n"
                f"**告警阈值**: {threshold}\n\n"
                f"已达到或超过设定的告警阈值！"
            )
            color = discord.Color.orange()
        else:
            title = "📊 每日新成员报告"
            is_over_threshold = join_count >= threshold
            status = "⚠️ 超过阈值" if is_over_threshold else "✅ 正常"
            description = (
                f"**日期**: {date_key}\n"
                f"**当日新成员数**: {join_count}\n"
                f"**告警阈值**: {threshold}\n"
                f"**状态**: {status}"
            )
            color = discord.Color.orange() if is_over_threshold else discord.Color.green()

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=now_beijing()
        )
        embed.set_footer(text=f"服务器: {guild.name}")

        try:
            content = mentions if mentions else None
            await channel.send(content=content, embed=embed)
            self.logger.info(
                f"MemberMonitor: 已发送{'阈值告警' if is_threshold_alert else '日报'} "
                f"guild={guild_id} count={join_count}"
            )
            return True
        except discord.Forbidden:
            self.logger.error(f"MemberMonitor: 无权限发送消息到频道 {alert_channel_id}")
            return False
        except Exception as e:
            self.logger.error(f"MemberMonitor: 发送消息失败: {e}", exc_info=True)
            return False

    def _build_mentions(self, config: Dict[str, Any]) -> str:
        """构建提及字符串"""
        mentions = []

        # 用户提及
        user_ids = parse_json_list(config.get("notify_user_ids"))
        for uid in user_ids:
            mentions.append(f"<@{uid}>")

        # 角色提及
        role_ids = parse_json_list(config.get("notify_role_ids"))
        for rid in role_ids:
            mentions.append(f"<@&{rid}>")

        return " ".join(mentions)

    # ==================== 定时任务 ====================

    @tasks.loop(minutes=1)
    async def daily_settlement_task(self):
        """每分钟检查一次，到达结算时间时发送日报"""
        if not self._ready.is_set():
            return

        try:
            now = now_beijing()
            current_hour = now.hour
            current_minute = now.minute

            # 获取所有已启用的配置
            configs = await self._list_enabled_configs()

            for config in configs:
                try:
                    guild_id = config["guild_id"]
                    settlement_hour = config.get("settlement_hour", DEFAULT_SETTLEMENT_HOUR)
                    settlement_minute = config.get("settlement_minute", DEFAULT_SETTLEMENT_MINUTE)

                    # 检查是否到达结算时间
                    if current_hour != settlement_hour or current_minute != settlement_minute:
                        continue

                    # 获取"昨天"的统计（结算时间是新一天开始时，统计的是前一天的数据）
                    yesterday = now - timedelta(days=1)
                    yesterday_key = get_date_key(yesterday)

                    stats = await self._get_daily_stats(guild_id, yesterday_key)
                    if not stats:
                        # 没有昨日数据，可能是首次运行，跳过
                        continue

                    # 检查是否已发送日报
                    if stats.get("daily_report_sent"):
                        continue

                    # 发送日报
                    join_count = stats.get("join_count", 0)
                    success = await self._send_alert(
                        guild_id, config, join_count, is_threshold_alert=False
                    )

                    if success:
                        await self._mark_daily_report_sent(guild_id, yesterday_key)

                except Exception as e:
                    self.logger.error(
                        f"MemberMonitor: 结算任务处理 guild={config.get('guild_id')} 异常: {e}",
                        exc_info=True
                    )

        except Exception as e:
            self.logger.error(f"MemberMonitor: daily_settlement_task 异常: {e}", exc_info=True)

    @daily_settlement_task.before_loop
    async def before_daily_settlement(self):
        """等待 bot ready"""
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def cleanup_task(self):
        """每24小时清理一次过期的统计数据"""
        if not self._ready.is_set():
            return

        try:
            cutoff_date = (now_beijing() - timedelta(days=CLEANUP_DAYS)).strftime("%Y-%m-%d")

            result = await self.db.execute(
                "DELETE FROM member_stats_daily WHERE date_key < ?",
                (cutoff_date,)
            )

            if result > 0:
                self.logger.info(f"MemberMonitor: 已清理 {result} 条过期统计数据")

        except Exception as e:
            self.logger.error(f"MemberMonitor: cleanup_task 异常: {e}", exc_info=True)

    @cleanup_task.before_loop
    async def before_cleanup(self):
        """等待 bot ready"""
        await self.bot.wait_until_ready()
        # 延迟一小时后开始第一次清理
        await asyncio.sleep(3600)

    # ==================== 斜杠命令 ====================

    member_monitor_group = app_commands.Group(
        name="成员监控",
        description="成员监控系统管理命令"
    )

    @member_monitor_group.command(name="面板", description="打开成员监控配置面板")
    async def open_panel(self, interaction: discord.Interaction):
        """打开成员监控配置面板"""
        if not await is_admin_or_owner(interaction):
            await interaction.response.send_message(
                "❌ 仅管理员或开发者可使用此命令", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            self.logger.error(f"MemberMonitor: defer 失败: {e}")
            return

        try:
            from views.member_monitor_views import MemberMonitorPanelView

            guild_id = str(interaction.guild_id)
            config = await self._get_config(guild_id)

            view = MemberMonitorPanelView(self, interaction.guild, config)
            embed = await view.build_status_embed()

            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        except ImportError as e:
            self.logger.error(f"MemberMonitor: 无法导入视图: {e}")
            await interaction.followup.send(
                "❌ 视图模块加载失败，请检查 views/member_monitor_views.py",
                ephemeral=True
            )
        except Exception as e:
            self.logger.error(f"MemberMonitor: open_panel 异常: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 发生错误: {str(e)[:100]}", ephemeral=True
            )

    @member_monitor_group.command(name="状态", description="查看当前监控状态和今日统计")
    async def view_status(self, interaction: discord.Interaction):
        """查看当前监控状态"""
        if not await is_admin_or_owner(interaction):
            await interaction.response.send_message(
                "❌ 仅管理员或开发者可使用此命令", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            self.logger.error(f"MemberMonitor: defer 失败: {e}")
            return

        try:
            guild_id = str(interaction.guild_id)
            config = await self._get_config(guild_id)

            if not config:
                await interaction.followup.send(
                    "ℹ️ 尚未配置成员监控，请使用 `/成员监控 面板` 进行配置",
                    ephemeral=True
                )
                return

            # 获取今日统计
            date_key = get_date_key()
            stats = await self._get_or_create_daily_stats(guild_id, date_key)

            # 构建状态 Embed
            is_enabled = config.get("is_enabled", False)
            alert_channel_id = config.get("alert_channel_id")
            threshold = config.get("join_threshold", DEFAULT_JOIN_THRESHOLD)
            settlement_hour = config.get("settlement_hour", DEFAULT_SETTLEMENT_HOUR)
            settlement_minute = config.get("settlement_minute", DEFAULT_SETTLEMENT_MINUTE)

            join_count = stats.get("join_count", 0)
            alert_sent = stats.get("threshold_alert_sent", False)

            # 格式化频道显示
            alert_display = f"<#{alert_channel_id}>" if alert_channel_id else "未设置"
            source_display = "👥 官方成员加入事件 (on_member_join)"

            # 格式化通知对象
            user_ids = parse_json_list(config.get("notify_user_ids"))
            role_ids = parse_json_list(config.get("notify_role_ids"))
            notify_display = []
            for uid in user_ids:
                notify_display.append(f"<@{uid}>")
            for rid in role_ids:
                notify_display.append(f"<@&{rid}>")
            notify_str = ", ".join(notify_display) if notify_display else "无"

            embed = discord.Embed(
                title="📊 成员监控状态",
                color=discord.Color.green() if is_enabled else discord.Color.greyple(),
                timestamp=now_beijing()
            )

            embed.add_field(
                name="🔧 基本配置",
                value=(
                    f"**状态**: {'✅ 已启用' if is_enabled else '❌ 已禁用'}\n"
                    f"**统计来源**: {source_display}\n"
                    f"**告警频道**: {alert_display}\n"
                    f"**告警阈值**: {threshold}\n"
                    f"**结算时间**: {settlement_hour:02d}:{settlement_minute:02d}"
                ),
                inline=False
            )

            embed.add_field(
                name="📢 通知对象",
                value=notify_str,
                inline=False
            )

            # 状态颜色
            status_color = "🟢" if join_count < threshold else "🟠"
            alert_status = "✅ 已发送" if alert_sent else "⏳ 未触发"

            embed.add_field(
                name=f"📈 今日统计 ({date_key})",
                value=(
                    f"**新成员数**: {status_color} {join_count}\n"
                    f"**阈值告警**: {alert_status}"
                ),
                inline=False
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            self.logger.error(f"MemberMonitor: view_status 异常: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 发生错误: {str(e)[:100]}", ephemeral=True
            )

    @member_monitor_group.command(name="开关", description="快速切换监控开关")
    async def toggle_monitor(self, interaction: discord.Interaction):
        """快速切换监控开关"""
        if not await is_admin_or_owner(interaction):
            await interaction.response.send_message(
                "❌ 仅管理员或开发者可使用此命令", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            self.logger.error(f"MemberMonitor: defer 失败: {e}")
            return

        try:
            guild_id = str(interaction.guild_id)
            config = await self._get_config(guild_id)

            if not config:
                await interaction.followup.send(
                    "❌ 尚未配置成员监控，请先使用 `/成员监控 面板` 进行配置",
                    ephemeral=True
                )
                return

            # 检查必要配置
            if not config.get("alert_channel_id"):
                await interaction.followup.send(
                    "❌ 请先设置告警频道",
                    ephemeral=True
                )
                return

            # 切换状态
            new_state = not config.get("is_enabled", False)
            await self._upsert_config(guild_id, is_enabled=new_state)

            status = "✅ 已启用" if new_state else "❌ 已禁用"
            await interaction.followup.send(
                f"成员监控已切换为: {status}",
                ephemeral=True
            )

        except Exception as e:
            self.logger.error(f"MemberMonitor: toggle_monitor 异常: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 发生错误: {str(e)[:100]}", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(MemberMonitorCog(bot))
