# -*- coding: utf-8 -*-
import asyncio
import datetime
import json
from typing import Optional, Dict, Any, List

import discord
from discord.ext import commands, tasks
from discord import app_commands

from .base_cog import BaseCog
from core.database import db_manager
from core.constants import BEIJING_TZ, MESSAGE_CONTENT_LIMIT, FORUM_SCAN_INTERVAL, FORUM_SCAN_WINDOW_SECONDS, FORUM_RECORD_RETENTION_DAYS
from utils.logger import get_logger
from utils.validators import validate_discord_id
from utils.permissions import admin_or_owner

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now(BEIJING_TZ).isoformat()


async def _fetch_member_safe(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        logger.warning(f"ForumMonitor: user {user_id} not found in guild {guild.id}")
    except (discord.HTTPException, discord.Forbidden) as e:
        logger.error(f"ForumMonitor: failed to fetch user {user_id}: {e}")
    return None


def _parse_role_id_from_input(guild: discord.Guild, raw: Optional[str]) -> Optional[str]:
    """
    支持输入为身份组ID或提及<@&id>，返回字符串ID；若无输入或非法则返回None。
    """
    if not raw:
        return None
    raw = raw.strip()
    role_id = None
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

def _parse_role_ids_csv(raw: Optional[str]) -> List[int]:
    """
    将逗号分隔的角色ID或@提及文本解析为整数ID列表。
    支持输入如: "123,456" 或 "<@&123>, <@&456>"，会去重并忽略非法项。
    """
    if not raw:
        return []
    parts = [p.strip() for p in str(raw).split(",") if p and p.strip()]
    ids: List[int] = []
    for p in parts:
        val = p
        if p.startswith("<@&") and p.endswith(">"):
            val = p[3:-1]
        if val.isdigit():
            try:
                vid = int(val)
                if vid not in ids:
                    ids.append(vid)
            except Exception:
                continue
    return ids


class ForumPostMonitorCog(BaseCog):
    """
    帖子监控模块：
    - 监听论坛频道的新贴（新线程）
    - 为贴主自动上身份组、在帖子内通知并@贴主、在帖子内@指定身份组发送消息
    - 多频道独立配置，持久化于SQLite
    """

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

    async def cog_load(self):
        self.logger.info("ForumPostMonitorCog loaded")
        # 启动遗漏扫描任务
        try:
            if not self.scan_missed_posts.is_running():
                self.scan_missed_posts.start()
        except Exception as e:
            self.logger.error(f"ForumMonitor: failed to start scan task: {e}", exc_info=True)

    async def cog_unload(self):
        # 停止遗漏扫描任务
        try:
            if self.scan_missed_posts.is_running():
                self.scan_missed_posts.cancel()
        except Exception:
            pass
        try:
            await super().cog_unload()
        except Exception:
            pass

    # ==== 数据访问 ====

    async def _get_config(self, guild_id: str, forum_channel_id: str) -> Optional[Dict[str, Any]]:
        query = """
            SELECT guild_id, forum_channel_id,
                   auto_role_enabled, auto_role_id,
                   notify_enabled, notify_message,
                   mention_role_enabled, mention_role_id, mention_message,
                   cross_post_enabled, cross_post_channel_id, cross_post_role_ids,
                   cross_post_template, cross_post_append_link,
                   created_at, updated_at
            FROM forum_post_monitor_configs
            WHERE guild_id = ? AND forum_channel_id = ?
        """
        return await db_manager.fetchone(query, (guild_id, forum_channel_id))

    async def _list_configs(self, guild_id: str) -> List[Dict[str, Any]]:
        query = """
            SELECT guild_id, forum_channel_id,
                   auto_role_enabled, auto_role_id,
                   notify_enabled, notify_message,
                   mention_role_enabled, mention_role_id, mention_message,
                   cross_post_enabled, cross_post_channel_id, cross_post_role_ids,
                   cross_post_template, cross_post_append_link,
                   created_at, updated_at
            FROM forum_post_monitor_configs
            WHERE guild_id = ?
            ORDER BY forum_channel_id
        """
        return await db_manager.fetchall(query, (guild_id,))

    async def _upsert_config(
        self,
        guild_id: str,
        forum_channel_id: str,
        auto_role_enabled: bool,
        auto_role_id: Optional[str],
        notify_enabled: bool,
        notify_message: Optional[str],
        mention_role_enabled: bool,
        mention_role_id: Optional[str],
        mention_message: Optional[str],
        cross_post_enabled: bool = False,
        cross_post_channel_id: Optional[str] = None,
        cross_post_role_ids: Optional[str] = None,
        cross_post_template: Optional[str] = None,
        cross_post_append_link: bool = True,
    ) -> None:
        now = _now_iso()
        # 先尝试存在性
        existing = await self._get_config(guild_id, forum_channel_id)
        if existing:
            query = """
                UPDATE forum_post_monitor_configs
                SET auto_role_enabled = ?, auto_role_id = ?,
                    notify_enabled = ?, notify_message = ?,
                    mention_role_enabled = ?, mention_role_id = ?, mention_message = ?,
                    cross_post_enabled = ?, cross_post_channel_id = ?, cross_post_role_ids = ?,
                    cross_post_template = ?, cross_post_append_link = ?,
                    updated_at = ?
                WHERE guild_id = ? AND forum_channel_id = ?
            """
            await db_manager.execute(
                query,
                (
                    1 if auto_role_enabled else 0,
                    auto_role_id,
                    1 if notify_enabled else 0,
                    notify_message,
                    1 if mention_role_enabled else 0,
                    mention_role_id,
                    mention_message,
                    1 if cross_post_enabled else 0,
                    cross_post_channel_id,
                    cross_post_role_ids,
                    cross_post_template,
                    1 if cross_post_append_link else 0,
                    now,
                    guild_id,
                    forum_channel_id,
                ),
            )
        else:
            query = """
                INSERT INTO forum_post_monitor_configs (
                    guild_id, forum_channel_id,
                    auto_role_enabled, auto_role_id,
                    notify_enabled, notify_message,
                    mention_role_enabled, mention_role_id, mention_message,
                    cross_post_enabled, cross_post_channel_id, cross_post_role_ids,
                    cross_post_template, cross_post_append_link,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            await db_manager.execute(
                query,
                (
                    guild_id,
                    forum_channel_id,
                    1 if auto_role_enabled else 0,
                    auto_role_id,
                    1 if notify_enabled else 0,
                    notify_message,
                    1 if mention_role_enabled else 0,
                    mention_role_id,
                    mention_message,
                    1 if cross_post_enabled else 0,
                    cross_post_channel_id,
                    cross_post_role_ids,
                    cross_post_template,
                    1 if cross_post_append_link else 0,
                    now,
                    now,
                ),
            )
        self.logger.info(f"ForumMonitor: upsert config for guild={guild_id} channel={forum_channel_id}")

    async def _delete_config(self, guild_id: str, forum_channel_id: str) -> int:
        query = """
            DELETE FROM forum_post_monitor_configs
            WHERE guild_id = ? AND forum_channel_id = ?
        """
        rows = await db_manager.execute(query, (guild_id, forum_channel_id))
        self.logger.info(f"ForumMonitor: delete config for guild={guild_id} channel={forum_channel_id}, affected={rows}")
        return rows

    # ==== 面板权限（可配置身份组）====

    async def _list_panel_permission_role_ids(self, guild_id: str) -> List[str]:
        rows = await db_manager.fetchall(
            "SELECT role_id FROM forum_monitor_permissions WHERE guild_id = ? ORDER BY role_id",
            (str(guild_id),),
        )
        return [str(r.get("role_id")) for r in rows if r.get("role_id")]

    async def _add_panel_permission_role(self, guild_id: str, role_id: str, added_by: str) -> None:
        await db_manager.execute(
            """
            INSERT OR REPLACE INTO forum_monitor_permissions (guild_id, role_id, added_by, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(guild_id), str(role_id), str(added_by), _now_iso()),
        )

    async def _remove_panel_permission_role(self, guild_id: str, role_id: str) -> int:
        return await db_manager.execute(
            "DELETE FROM forum_monitor_permissions WHERE guild_id = ? AND role_id = ?",
            (str(guild_id), str(role_id)),
        )

    async def _clear_panel_permission_roles(self, guild_id: str) -> int:
        return await db_manager.execute(
            "DELETE FROM forum_monitor_permissions WHERE guild_id = ?",
            (str(guild_id),),
        )

    async def _is_forum_monitor_operator(self, interaction: discord.Interaction) -> bool:
        """是否可修改/删除帖子监控配置：管理员/开发者，或命中授权身份组。"""
        from utils.permissions import is_admin_or_owner

        if await is_admin_or_owner(interaction):
            return True

        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not guild or not member:
            return False

        role_ids = {str(r.id) for r in getattr(member, "roles", [])}
        if not role_ids:
            return False

        allowed = set(await self._list_panel_permission_role_ids(str(guild.id)))
        return len(role_ids.intersection(allowed)) > 0

    # ==== 去重与记录辅助 ====

    async def _is_thread_processed(self, thread_id: str) -> bool:
        row = await db_manager.fetchone(
            "SELECT thread_id FROM forum_posts_processed WHERE thread_id = ?",
            (str(thread_id),)
        )
        return row is not None

    async def _insert_processed_record(
        self,
        thread_id: str,
        guild_id: str,
        forum_channel_id: str,
        thread_created_at: datetime.datetime,
        processed_by: str,
    ) -> bool:
        # 将创建时间统一为UTC ISO字符串
        try:
            if isinstance(thread_created_at, datetime.datetime):
                if thread_created_at.tzinfo is None:
                    created_utc = thread_created_at.replace(tzinfo=datetime.timezone.utc)
                else:
                    created_utc = thread_created_at.astimezone(datetime.timezone.utc)
                thread_created_iso = created_utc.isoformat()
            else:
                thread_created_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        except Exception:
            thread_created_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        processed_at_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            rowcount = await db_manager.execute(
                """
                INSERT OR IGNORE INTO forum_posts_processed
                (thread_id, guild_id, forum_channel_id, thread_created_at, processed_at, processed_by, actions_taken)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(thread_id), str(guild_id), str(forum_channel_id), thread_created_iso, processed_at_iso, processed_by, None),
            )
            return rowcount > 0
        except Exception as e:
            self.logger.error(f"ForumMonitor: insert processed record failed for thread {thread_id}: {e}", exc_info=True)
            return False

    def _summarize_actions(self, config: Dict[str, Any]) -> Dict[str, Any]:
        def _to_bool(v: Any) -> bool:
            if isinstance(v, bool):
                return v
            if v is None:
                return False
            if isinstance(v, (int, float)):
                return v != 0
            s = str(v).strip().lower()
            return s in ("1", "true", "t", "yes", "y")

        return {
            "auto_role_enabled": _to_bool(config.get("auto_role_enabled")),
            "notify_enabled": _to_bool(config.get("notify_enabled")),
            "mention_role_enabled": _to_bool(config.get("mention_role_enabled")),
            "cross_post_enabled": _to_bool(config.get("cross_post_enabled")),
            "auto_role_id": config.get("auto_role_id"),
            "mention_role_id": config.get("mention_role_id"),
            "cross_post_channel_id": config.get("cross_post_channel_id"),
            "cross_post_role_ids": config.get("cross_post_role_ids"),
        }

    async def _update_actions_taken(self, thread_id: str, config: Dict[str, Any]) -> None:
        try:
            summary = self._summarize_actions(config)
            await db_manager.execute(
                "UPDATE forum_posts_processed SET actions_taken = ? WHERE thread_id = ?",
                (json.dumps(summary, ensure_ascii=False), str(thread_id)),
            )
        except Exception as e:
            self.logger.warning(f"ForumMonitor: update actions_taken failed for thread {thread_id}: {e}")

    async def _cleanup_old_records(self) -> None:
        # 清理过期记录，控制表规模
        try:
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=FORUM_RECORD_RETENTION_DAYS)
            await db_manager.execute(
                "DELETE FROM forum_posts_processed WHERE processed_at < ?",
                (cutoff.isoformat(),),
            )
        except Exception as e:
            self.logger.error(f"ForumMonitor: cleanup old records failed: {e}", exc_info=True)

    async def _send_with_retry(
        self,
        thread: discord.Thread,
        content: str,
        *,
        allowed_mentions: Optional[discord.AllowedMentions] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> discord.Message:
        """
        在线程内发送消息的重试封装：
        - 最多重试 max_retries 次
        - 线性退避：base_delay * attempt
        - 记录每次失败日志，最终抛出最后一次异常
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                return await thread.send(content, allowed_mentions=allowed_mentions)
            except discord.HTTPException as e:
                self.logger.warning(
                    f"ForumMonitor: thread.send attempt {attempt}/{max_retries} failed "
                    f"(HTTP {getattr(e, 'status', 'unknown')}): {e}"
                )
                last_exc = e
            except Exception as e:
                self.logger.warning(
                    f"ForumMonitor: thread.send attempt {attempt}/{max_retries} unexpected error: {e}",
                    exc_info=True
                )
                last_exc = e

            if attempt < max_retries:
                await asyncio.sleep(base_delay * attempt)

        assert last_exc is not None
        raise last_exc

    async def _send_to_channel_with_retry(
        self,
        channel: discord.abc.Messageable,
        content: str,
        *,
        allowed_mentions: Optional[discord.AllowedMentions] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> discord.Message:
        """向普通频道发送消息的重试封装。"""
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                return await channel.send(content, allowed_mentions=allowed_mentions)
            except discord.HTTPException as e:
                self.logger.warning(
                    f"ForumMonitor: channel.send attempt {attempt}/{max_retries} failed "
                    f"(HTTP {getattr(e, 'status', 'unknown')}): {e}"
                )
                last_exc = e
            except Exception as e:
                self.logger.warning(
                    f"ForumMonitor: channel.send attempt {attempt}/{max_retries} unexpected error: {e}",
                    exc_info=True
                )
                last_exc = e

            if attempt < max_retries:
                await asyncio.sleep(base_delay * attempt)

        assert last_exc is not None
        raise last_exc

    # ==== 事件监听 ====

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        """
        监听新线程创建，仅处理论坛频道的线程。
        """
        try:
            guild = thread.guild
            if guild is None:
                return

            parent = thread.parent
            if parent is None or parent.type != discord.ChannelType.forum:
                return

            guild_id = str(guild.id)
            forum_channel_id = str(parent.id)

            config = await self._get_config(guild_id, forum_channel_id)
            if not config:
                return

            # 去重：尝试记录处理，若已处理则跳过（避免事件与扫描重复执行）
            try:
                created_at = thread.created_at
                if not isinstance(created_at, datetime.datetime):
                    created_at = datetime.datetime.now(datetime.timezone.utc)
                elif created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=datetime.timezone.utc)
                else:
                    created_at = created_at.astimezone(datetime.timezone.utc)
                inserted = await self._insert_processed_record(
                    thread_id=str(thread.id),
                    guild_id=guild_id,
                    forum_channel_id=forum_channel_id,
                    thread_created_at=created_at,
                    processed_by="event",
                )
                if not inserted:
                    # 已被扫描或其他事件路径处理
                    return
            except Exception as e:
                self.logger.warning(f"ForumMonitor: failed to mark processed for thread {thread.id}: {e}")

            # 获取发帖人（线程创建者）
            poster_id = thread.owner_id
            member: Optional[discord.Member] = None
            if isinstance(poster_id, int):
                member = await _fetch_member_safe(guild, int(poster_id))

            # 如果无法从owner_id拿到成员，尝试获取首帖作者
            if not member:
                try:
                    # 尝试获取首帖信息（论坛线程通常有一个起始消息）
                    # discord.py 可能需要 fetch_message(thread.id) 不可靠，故直接尝试历史消息获取
                    async for msg in thread.history(limit=1, oldest_first=True):
                        if msg.author and isinstance(msg.author, discord.Member):
                            member = msg.author
                            break
                except Exception as e:
                    self.logger.warning(f"ForumMonitor: failed to read starter message for thread {thread.id}: {e}")

            if not member:
                self.logger.warning(f"ForumMonitor: cannot determine poster for thread {thread.id}")
                return

            # 执行动作
            await self._process_actions(thread, guild, member, config)
            # 记录动作摘要（便于后续统计与去重）
            await self._update_actions_taken(thread.id, config)

        except Exception as e:
            self.logger.error(f"ForumMonitor: on_thread_create error: {e}", exc_info=True)

    async def _process_actions(self, thread: discord.Thread, guild: discord.Guild, member: discord.Member, config: Dict[str, Any]):
        """
        根据配置执行加身份组、通知@贴主、@指定身份组发消息、跨频道提醒。
        增强版：详细日志记录每步执行状态
        """
        self.logger.info(f"ForumMonitor: START processing thread {thread.id} in guild {guild.id} for member {member.id}")
        self.logger.debug(f"ForumMonitor: Config data: {config}")

        execution_report = {
            "thread_id": str(thread.id),
            "guild_id": str(guild.id),
            "member_id": str(member.id),
            "auto_role": {"enabled": False, "executed": False, "success": False, "error": None},
            "notify": {"enabled": False, "executed": False, "success": False, "error": None},
            "mention": {"enabled": False, "executed": False, "success": False, "error": None},
            "cross_post": {"enabled": False, "executed": False, "success": False, "error": None},
        }

        def _to_bool(v: Any, default: bool = False) -> bool:
            if isinstance(v, bool):
                return v
            if v is None:
                return default
            if isinstance(v, (int, float)):
                return v != 0
            s = str(v).strip().lower()
            if not s:
                return default
            return s in ("1", "true", "t", "yes", "y", "on")

        def _render_cross_post_message(template: str, append_link: bool) -> str:
            thread_url = getattr(thread, "jump_url", None) or f"https://discord.com/channels/{guild.id}/{thread.id}"
            thread_title = (thread.name or "未命名帖子").strip()
            forum_name = thread.parent.name if thread.parent else "未知论坛"

            rendered = template
            rendered = rendered.replace("{thread_url}", thread_url)
            rendered = rendered.replace("{thread_title}", thread_title)
            rendered = rendered.replace("{forum_name}", forum_name)
            rendered = rendered.replace("{author_mention}", member.mention)

            if append_link and "{thread_url}" not in template:
                rendered = f"{rendered}\n[帖子直达]({thread_url})" if rendered else f"[帖子直达]({thread_url})"

            return rendered[:MESSAGE_CONTENT_LIMIT]

        auto_role_enabled = _to_bool(config.get("auto_role_enabled"))
        notify_enabled = _to_bool(config.get("notify_enabled"), default=True)
        mention_role_enabled = _to_bool(config.get("mention_role_enabled"))
        cross_post_enabled = _to_bool(config.get("cross_post_enabled"))

        self.logger.info(
            f"ForumMonitor: Enabled flags - auto_role:{auto_role_enabled}, notify:{notify_enabled}, "
            f"mention:{mention_role_enabled}, cross_post:{cross_post_enabled}"
        )
        execution_report["auto_role"]["enabled"] = auto_role_enabled
        execution_report["notify"]["enabled"] = notify_enabled
        execution_report["mention"]["enabled"] = mention_role_enabled
        execution_report["cross_post"]["enabled"] = cross_post_enabled

        # 1. 加身份组
        if auto_role_enabled:
            execution_report["auto_role"]["executed"] = True
            self.logger.info(f"ForumMonitor: Executing auto_role for thread {thread.id}")
            rid_csv = config.get("auto_role_id")
            ids = _parse_role_ids_csv(rid_csv)
            roles = []
            for rid in ids:
                role_obj = guild.get_role(rid)
                if role_obj:
                    roles.append(role_obj)
                else:
                    self.logger.warning(f"ForumMonitor: Role {rid} not found in guild")
            if roles:
                try:
                    await member.add_roles(*roles, reason="论坛新帖自动授予身份组")
                    execution_report["auto_role"]["success"] = True
                except Exception as e:
                    error_msg = f"{type(e).__name__}: {str(e)}"
                    execution_report["auto_role"]["error"] = error_msg
                    self.logger.error(f"ForumMonitor: FAILED add_roles - {error_msg}", exc_info=True)
            else:
                self.logger.warning(f"ForumMonitor: No valid roles to grant for config {rid_csv}")

        # 2. 在线程中通知并@贴主
        if notify_enabled:
            execution_report["notify"]["executed"] = True
            message = config.get("notify_message") or "欢迎加入讨论！"
            if isinstance(message, str):
                try:
                    await self._send_with_retry(
                        thread,
                        f"{member.mention} {message[:MESSAGE_CONTENT_LIMIT]}",
                        allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
                    )
                    execution_report["notify"]["success"] = True
                except Exception as e:
                    error_msg = f"{type(e).__name__}: {str(e)}"
                    execution_report["notify"]["error"] = error_msg
                    self.logger.error(f"ForumMonitor: FAILED send notify - {error_msg}", exc_info=True)

        # 3. 在线程中@指定身份组并发送消息
        if mention_role_enabled:
            execution_report["mention"]["executed"] = True
            rid_csv = config.get("mention_role_id")
            mention_msg = (config.get("mention_message") or "").strip()
            ids = _parse_role_ids_csv(rid_csv)
            if ids:
                mentions = " ".join(f"<@&{rid}>" for rid in ids)
                text = f"{mentions} {mention_msg}" if mention_msg else mentions
                try:
                    await self._send_with_retry(
                        thread,
                        text[:MESSAGE_CONTENT_LIMIT],
                        allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
                    )
                    execution_report["mention"]["success"] = True
                except Exception as e:
                    error_msg = f"{type(e).__name__}: {str(e)}"
                    execution_report["mention"]["error"] = error_msg
                    self.logger.error(f"ForumMonitor: FAILED send mention - {error_msg}", exc_info=True)

        # 4. 跨频道提醒（@身份组 + 模板 + 可选自动附加链接）
        if cross_post_enabled:
            execution_report["cross_post"]["executed"] = True
            try:
                target_channel_id_raw = config.get("cross_post_channel_id")
                target_channel_id = int(str(target_channel_id_raw).strip()) if str(target_channel_id_raw).strip().isdigit() else 0
                target_channel = guild.get_channel(target_channel_id) if target_channel_id > 0 else None
                if target_channel is None:
                    raise ValueError("跨频道提醒目标频道不存在或ID无效")

                me = guild.me
                if me and hasattr(target_channel, "permissions_for"):
                    perms = target_channel.permissions_for(me)
                    if not perms.view_channel or not perms.send_messages:
                        raise PermissionError("机器人缺少目标频道的查看或发送消息权限")

                rid_csv = config.get("cross_post_role_ids")
                role_ids = _parse_role_ids_csv(rid_csv)
                role_mentions = " ".join(f"<@&{rid}>" for rid in role_ids) if role_ids else ""

                raw_template = (config.get("cross_post_template") or "").strip()
                default_template = "{author_mention} 在 {forum_name} 发布了新帖子：[{thread_title}]({thread_url})"
                # 兼容旧写法：将 ${var} 自动修正为 {var}
                normalized_template = (raw_template or default_template).replace("${thread_title}", "{thread_title}").replace("${thread_url}", "{thread_url}")
                append_link = _to_bool(config.get("cross_post_append_link"), default=True)

                body = _render_cross_post_message(normalized_template, append_link)
                final_text = f"{role_mentions}\n{body}" if role_mentions and body else (role_mentions or body)
                final_text = final_text[:MESSAGE_CONTENT_LIMIT]

                await self._send_to_channel_with_retry(
                    target_channel,
                    final_text,
                    allowed_mentions=discord.AllowedMentions(users=False, roles=True, everyone=False),
                )
                execution_report["cross_post"]["success"] = True
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                execution_report["cross_post"]["error"] = error_msg
                self.logger.error(f"ForumMonitor: FAILED cross_post - {error_msg}", exc_info=True)

        self.logger.info(f"ForumMonitor: COMPLETE processing thread {thread.id} - Report: {json.dumps(execution_report, ensure_ascii=False)}")

    # ==== 定时扫描遗漏的帖子 ====

    @tasks.loop(seconds=FORUM_SCAN_INTERVAL)
    async def scan_missed_posts(self):
        """
        每隔固定时间扫描最近创建的帖子，兜底处理可能漏掉的 on_thread_create 事件。
        """
        try:
            # Bot 尚未就绪则跳过
            if not getattr(self.bot, "is_ready", lambda: False)():
                return

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            window_start = now_utc - datetime.timedelta(seconds=max(FORUM_SCAN_WINDOW_SECONDS, 60))

            # 遍历所有已加入的公会
            for guild in list(self.bot.guilds):
                try:
                    guild_id = str(guild.id)
                    configs = await self._list_configs(guild_id)
                    if not configs:
                        continue

                    # 构建频道 -> 配置映射，快速过滤只启用监控的论坛频道
                    channel_config_map: Dict[int, Dict[str, Any]] = {}
                    for cfg in configs:
                        try:
                            ch_id = int(cfg["forum_channel_id"])
                            channel_config_map[ch_id] = cfg
                        except Exception:
                            continue
                    if not channel_config_map:
                        continue

                    # 一次性拉取公会所有活跃线程，减少API调用
                    threads: List[discord.Thread] = []
                    try:
                        threads = await guild.active_threads()
                    except Exception as e:
                        self.logger.warning(f"ForumMonitor: fetch active threads failed in guild {guild.id}: {e}")
                        continue

                    for th in threads:
                        try:
                            if not isinstance(th, discord.Thread):
                                continue
                            parent = th.parent
                            if parent is None or parent.type != discord.ChannelType.forum:
                                continue
                            if th.parent_id not in channel_config_map:
                                continue

                            created_at = th.created_at
                            if not isinstance(created_at, datetime.datetime):
                                continue
                            if created_at.tzinfo is None:
                                created_at = created_at.replace(tzinfo=datetime.timezone.utc)
                            else:
                                created_at = created_at.astimezone(datetime.timezone.utc)

                            # 仅处理窗口期内新建的线程
                            if created_at < window_start:
                                continue

                            # 去重：尝试插入处理记录，若已存在则跳过
                            inserted = await self._insert_processed_record(
                                thread_id=str(th.id),
                                guild_id=guild_id,
                                forum_channel_id=str(parent.id),
                                thread_created_at=created_at,
                                processed_by="scan",
                            )
                            if not inserted:
                                continue

                            # 尝试确定发帖人
                            member: Optional[discord.Member] = None
                            if isinstance(th.owner_id, int):
                                member = await _fetch_member_safe(guild, int(th.owner_id))
                            if not member:
                                try:
                                    async for msg in th.history(limit=1, oldest_first=True):
                                        if msg.author and isinstance(msg.author, discord.Member):
                                            member = msg.author
                                            break
                                except Exception as e:
                                    self.logger.warning(f"ForumMonitor: scan read starter failed thread {th.id}: {e}")
                            if not member:
                                self.logger.warning(f"ForumMonitor: scan cannot determine poster for thread {th.id}")
                                continue

                            # 执行与事件一致的处理流程
                            cfg = channel_config_map.get(th.parent_id)
                            if cfg:
                                await self._process_actions(th, guild, member, cfg)
                                await self._update_actions_taken(th.id, cfg)

                        except Exception as e:
                            self.logger.error(f"ForumMonitor: scan loop error for thread {getattr(th, 'id', 'unknown')}: {e}", exc_info=True)

                    # 每轮公会扫描后，顺带做一次过期记录清理
                    await self._cleanup_old_records()

                except Exception as e:
                    self.logger.error(f"ForumMonitor: scan guild error {getattr(guild, 'id', 'unknown')}: {e}", exc_info=True)

        except Exception as e:
            self.logger.error(f"ForumMonitor: scan_missed_posts root error: {e}", exc_info=True)

    # ==== 指令：帖子监控面板 ====

    async def _schedule_panel_cleanup(self, message: discord.Message, hours: int = 24):
        """调度面板消息在指定小时后自动清除"""
        try:
            await asyncio.sleep(hours * 3600)
            try:
                await message.delete()
                self.logger.info(f"ForumMonitor: panel message {message.id} auto-deleted after {hours}h")
            except discord.NotFound:
                # 消息已被手动删除或不存在
                self.logger.info(f"ForumMonitor: panel message {message.id} not found when cleanup")
            except discord.Forbidden:
                # 权限不足无法删除（通常机器人可删除自己的消息，这里记录异常）
                self.logger.warning(f"ForumMonitor: lack permission to delete panel message {message.id}")
        except Exception as e:
            self.logger.error(f"ForumMonitor: panel cleanup task error: {e}", exc_info=True)

    @app_commands.command(name="帖子监控面板", description="打开本服务器的帖子监控配置面板（论坛频道新帖自动处理）")
    async def open_forum_monitor_panel(self, interaction: discord.Interaction):
        """
        管理员/开发者/被授权身份组可用，打开配置面板视图。
        """
        # 黄金法则：先 defer
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # 权限检查：管理员/开发者/被授权身份组
        if not await self._is_forum_monitor_operator(interaction):
            try:
                await interaction.edit_original_response(content="❌ 你没有权限召唤帖子监控面板。")
            except Exception:
                pass
            return

        # 立即结束“正在响应”提示，给出进度反馈
        try:
            await interaction.edit_original_response(content="⌛ 正在发布帖子监控面板……")
        except Exception:
            # 若编辑原始响应失败，不影响后续流程
            pass
        
        try:
            from views.forum_monitor_views import ForumMonitorPanelView
        except Exception as e:
            self.logger.error(f"ForumMonitor: failed to import panel view: {e}", exc_info=True)
            try:
                await interaction.edit_original_response(content="❌ 面板视图加载失败。")
            except Exception:
                pass
            return
        
        embed = discord.Embed(
            title="帖子监控面板",
            description=(
                "在此选择论坛频道并为其配置自动处理策略：\n"
                "- 为新帖贴主自动上身份组\n"
                "- 在线程内通知并@贴主\n"
                "- 在线程内@指定身份组并发送消息\n"
                "- 在其他聊天频道发送带帖子链接的提醒\n\n"
                "提示：所有配置为每个论坛频道独立生效。"
            ),
            color=discord.Color.blue(),
        )
        
        try:
            view = ForumMonitorPanelView(interaction.guild)
            panel_message = await interaction.channel.send(embed=embed, view=view)
            # 24小时后自动清除面板消息
            asyncio.create_task(self._schedule_panel_cleanup(panel_message, hours=24))
            await interaction.edit_original_response(
                content="✅ 帖子监控面板已发布，所有成员可打开与查看；管理员/开发者/被授权身份组可修改配置。此面板将在24小时后自动清除。"
            )
        except discord.Forbidden:
            try:
                await interaction.edit_original_response(
                    content="❌ 我没有权限在此频道发布面板，请为机器人授予“查看频道/发送消息/管理消息”权限。"
                )
            except Exception:
                pass
        except Exception as e:
            self.logger.error(f"ForumMonitor: failed to publish panel: {e}", exc_info=True)
            try:
                await interaction.edit_original_response(content="❌ 发布面板失败：发生未知错误。")
            except Exception:
                pass


    @app_commands.command(name="补发帖子消息", description="为指定帖子线程补发消息（通知/@身份组）")
    @admin_or_owner()
    @app_commands.describe(
        thread_id="帖子线程ID（右键复制ID）",
        resend_notify="是否补发通知并@贴主（默认是）",
        resend_mention="是否补发@身份组消息（默认是）"
    )
    async def resend_thread_messages(
        self,
        interaction: discord.Interaction,
        thread_id: str,
        resend_notify: bool = True,
        resend_mention: bool = True
    ):
        """
        管理员/拥有者使用：为指定帖子补发消息（仅限通知/@身份组，不会重复加身份组）
        用法示例：
        /补发帖子消息 thread_id:123456789012345678 resend_notify:true resend_mention:false
        """
        # 黄金法则：先 defer
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # 解析线程ID
        try:
            tid = int(str(thread_id).strip())
        except Exception:
            await interaction.followup.send("❌ 线程ID格式不正确，请确认输入的是数字ID。", ephemeral=True)
            return

        # 获取线程对象
        thread: Optional[discord.Thread] = None
        try:
            chan = interaction.client.get_channel(tid)
            if isinstance(chan, discord.Thread):
                thread = chan
            else:
                fetched = await interaction.client.fetch_channel(tid)
                if isinstance(fetched, discord.Thread):
                    thread = fetched
        except Exception as e:
            self.logger.error(f"ForumMonitor: fetch thread {tid} failed: {e}", exc_info=True)

        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 未找到对应的帖子线程，或我没有权限访问。", ephemeral=True)
            return

        # 校验父频道为论坛
        parent = thread.parent
        if parent is None or parent.type != discord.ChannelType.forum:
            await interaction.followup.send("❌ 目标并非论坛频道下的帖子线程。", ephemeral=True)
            return

        guild = thread.guild or interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 无法确定所属服务器。", ephemeral=True)
            return

        # 查询频道配置
        guild_id = str(guild.id)
        forum_channel_id = str(parent.id)
        config = await self._get_config(guild_id, forum_channel_id)
        if not config:
            await interaction.followup.send("ℹ️ 此论坛频道尚未配置帖子监控策略，无法补发。", ephemeral=True)
            return

        # 确定发帖人
        member: Optional[discord.Member] = None
        if isinstance(thread.owner_id, int):
            member = await _fetch_member_safe(guild, int(thread.owner_id))
        if not member:
            try:
                async for msg in thread.history(limit=1, oldest_first=True):
                    if msg.author and isinstance(msg.author, discord.Member):
                        member = msg.author
                        break
            except Exception as e:
                self.logger.warning(f"ForumMonitor: resend read starter failed thread {thread.id}: {e}")
        if not member:
            await interaction.followup.send("❌ 无法确定贴主，无法补发通知。", ephemeral=True)
            return

        # 仅补发消息，不重复加身份组
        patched = dict(config)
        patched["auto_role_enabled"] = 0
        patched["notify_enabled"] = 1 if resend_notify else 0
        patched["mention_role_enabled"] = 1 if resend_mention else 0

        try:
            await self._process_actions(thread, guild, member, patched)
            await self._update_actions_taken(thread.id, patched)
            parts = []
            if resend_notify:
                parts.append("通知@贴主")
            if resend_mention:
                parts.append("@身份组消息")
            human = "、".join(parts) if parts else "无"
            await interaction.followup.send(f"✅ 补发完成：{human}", ephemeral=True)
        except Exception as e:
            self.logger.error(f"ForumMonitor: resend_thread_messages failed: {e}", exc_info=True)
            await interaction.followup.send("❌ 补发失败：发生未知错误。", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ForumPostMonitorCog(bot))
    logger.info("ForumPostMonitorCog has been added to bot")