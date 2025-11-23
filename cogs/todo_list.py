# -*- coding: utf-8 -*-
"""
模块: cogs/todo_list.py
功能: 个人/频道 ToDo 事件列表 + 定时提醒 + 监听频道自动回复
说明:
- 严格遵守交互黄金法则：所有交互入口先 defer，唯一例外 send_modal
- 时间均以北京时间为准（core.constants.BEIJING_TZ）
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple

import discord
from discord import app_commands, ui
from discord.ext import commands

from core.database import DatabaseManager
from core.constants import BEIJING_TZ
from utils.permissions import admin_or_owner, is_owner_check, is_admin_or_owner
from utils.logger import get_logger

logger = get_logger(__name__)
# 模块级斜杠命令组 /事件
todo = app_commands.Group(name="事件", description="事件管理")

LIST_TYPE_PERSON = "person"
LIST_TYPE_CHANNEL = "channel"
STATUS_OPEN = "open"
STATUS_COMPLETED = "completed"


async def safe_defer(interaction: discord.Interaction, ephemeral: bool = True):
    """
    交互黄金法则：确保只在未响应时进行一次 defer（ephemeral=True）
    """
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)


def now_bj() -> datetime:
    """获取当前北京时间"""
    return datetime.now(BEIJING_TZ)


def iso_bj(dt: datetime) -> str:
    """格式化为ISO字符串"""
    # 确保dt带有时区信息
    if dt.tzinfo is None:
        dt = BEIJING_TZ.localize(dt)
    return dt.isoformat()


def parse_daily_time_to_next_run(hhmm: str) -> Optional[datetime]:
    """
    将 "HH:MM" 转换为今天的北京时间触发点；若已过去则返回明天同一时间
    """
    try:
        hh, mm = hhmm.strip().split(":")
        h = int(hh)
        m = int(mm)
        today = now_bj().date()
        candidate = BEIJING_TZ.localize(datetime(today.year, today.month, today.day, h, m, 0))
        if candidate <= now_bj():
            candidate = candidate + timedelta(days=1)
        return candidate
    except Exception:
        return None


def try_format_message_link(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    # 粗略校验Discord消息链接
    if "discord.com/channels/" in url or "discordapp.com/channels/" in url:
        return url
    return url  # 放行任意字符串，避免过度限制


def parse_countdown_to_seconds(text: str) -> Optional[int]:
    """
    解析倒计时字符串为秒数:
    - 纯数字: 视为秒, 例如 "60"
    - 后缀单位:
      - s: 秒, 如 "60s"
      - m: 分钟, 如 "10m"
      - h: 小时, 如 "2h"
      - d: 天, 如 "1d"
    """
    if not text:
        return None
    t = text.strip().lower()
    if t.isdigit():
        val = int(t)
        return val if val > 0 else None
    try:
        num = int(t[:-1])
        unit = t[-1]
        if num <= 0:
            return None
        if unit == "s":
            return num
        if unit == "m":
            return num * 60
        if unit == "h":
            return num * 3600
        if unit == "d":
            return num * 86400
        return None
    except Exception:
        return None


def parse_index_list(text: str) -> List[int]:
    """
    将用户输入的序号列表解析为整数列表:
    - 支持: "1,2,3" 或 "1, 2, 3"
    - 忽略空白与重复, 且过滤非正整数
    """
    if not text:
        return []
    parts = [p.strip() for p in str(text).split(',')]
    nums: List[int] = []
    for p in parts:
        if not p:
            continue
        if p.isdigit():
            n = int(p)
            if n > 0:
                nums.append(n)
    # 去重保持顺序
    seen = set()
    deduped = []
    for n in nums:
        if n not in seen:
            seen.add(n)
            deduped.append(n)
    return deduped




class AddTodoModalBase(ui.Modal, title="添加事件备注"):
    def __init__(self, on_submit_cb):
        super().__init__(timeout=180)
        self.on_submit_cb = on_submit_cb
        self.remark = ui.TextInput(
            label="备注内容",
            placeholder="请输入事件备注（必填）",
            required=True,
            max_length=1000
        )
        self.add_item(self.remark)

    async def on_submit(self, interaction: discord.Interaction):
        await self.on_submit_cb(interaction, str(self.remark.value).strip())

class ReorderView(ui.View):
    """
    可视化排序视图：选择要移动的事件与目标位置，然后确认移动。
    始终以ephemeral交互呈现，避免刷屏。
    """
    def __init__(
        self,
        cog: 'TodoListCog',
        guild_id: str,
        list_type: str,
        user_id: Optional[str],
        channel_id: Optional[str],
        items: List[Dict]
    ):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id
        self.list_type = list_type
        self.user_id = user_id
        self.channel_id = channel_id
        self.items = items
        self.source_index: Optional[int] = None
        self.target_index: Optional[int] = None

        options = []
        for idx, it in enumerate(self.items, start=1):
            label = f"#{idx} {str(it.get('content',''))[:50]}"
            options.append(discord.SelectOption(label=label, value=str(idx)))

        self.source_select = ui.Select(
            placeholder="选择要移动的事件（源）",
            min_values=1,
            max_values=1,
            options=options
        )
        self.target_select = ui.Select(
            placeholder="选择目标位置（目标）",
            min_values=1,
            max_values=1,
            options=options
        )

        async def on_source_select(interaction: discord.Interaction):
            await safe_defer(interaction, ephemeral=True)
            try:
                self.source_index = int(self.source_select.values[0])
                await interaction.edit_original_response(content=f"已选择源：#{self.source_index}", view=self)
            except Exception as e:
                logger.error(f"ReorderView on_source_select error: {e}", exc_info=True)

        async def on_target_select(interaction: discord.Interaction):
            await safe_defer(interaction, ephemeral=True)
            try:
                self.target_index = int(self.target_select.values[0])
                await interaction.edit_original_response(content=f"已选择目标：#{self.target_index}", view=self)
            except Exception as e:
                logger.error(f"ReorderView on_target_select error: {e}", exc_info=True)

        self.source_select.callback = on_source_select
        self.target_select.callback = on_target_select

        self.add_item(self.source_select)
        self.add_item(self.target_select)

        confirm_btn = ui.Button(label="确认移动", style=discord.ButtonStyle.primary)

        async def on_confirm(interaction: discord.Interaction):
            await safe_defer(interaction, ephemeral=True)
            if self.source_index is None or self.target_index is None:
                return await interaction.followup.send("❌ 请先选择源与目标序号。", ephemeral=True)
            try:
                src, dst = await self.cog._reorder_by_indices(
                    guild_id=self.guild_id,
                    list_type=self.list_type,
                    source_index=self.source_index,
                    target_index=self.target_index,
                    user_id=self.user_id,
                    channel_id=self.channel_id
                )
                # 重新拉取最新列表并刷新视图
                if self.list_type == LIST_TYPE_PERSON:
                    items = await self.cog._fetch_personal_items(self.guild_id, self.user_id)
                    embed = self.cog._build_list_embed("📋 个人事件列表（已更新排序）", items, ephemeral_hint="此列表仅你可见")
                else:
                    items = await self.cog._fetch_channel_items(self.guild_id, self.channel_id)
                    embed = self.cog._build_list_embed("📋 频道事件列表（已更新排序）", items)

                new_view = ReorderView(
                    cog=self.cog,
                    guild_id=self.guild_id,
                    list_type=self.list_type,
                    user_id=self.user_id,
                    channel_id=self.channel_id,
                    items=items
                )
                await interaction.edit_original_response(
                    content=f"✅ 已移动：#{src} -> #{dst}",
                    embed=embed,
                    view=new_view
                )
            except ValueError as ve:
                await interaction.followup.send(f"❌ {ve}", ephemeral=True)
            except Exception as e:
                logger.error(f"ReorderView confirm error: {e}", exc_info=True)
                await interaction.followup.send("❌ 排序失败。", ephemeral=True)

        confirm_btn.callback = on_confirm
        self.add_item(confirm_btn)

    async def on_timeout(self) -> None:
        try:
            for item in self.children:
                if isinstance(item, (ui.Select, ui.Button)):
                    item.disabled = True
        except Exception:
            pass

class TodoListCog(commands.Cog):
    """ToDo 列表 + 提醒 + 监听"""

    # 斜杠命令组 /事件
    todo = app_commands.Group(name="事件", description="事件管理")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = DatabaseManager()
        self._reminder_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    # ========== 生命周期 ==========
    async def cog_load(self):
        logger.info("TodoListCog loaded, starting reminder loop")
        # 启动提醒轮询任务
        self._stop_event.clear()
        self._reminder_task = asyncio.create_task(self._reminder_loop())
        # 启动每日自动清理任务
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def cog_unload(self):
        logger.info("TodoListCog unloaded, stopping background loops")
        if self._reminder_task and not self._reminder_task.done():
            self._stop_event.set()
            try:
                await asyncio.wait_for(self._reminder_task, timeout=5)
            except asyncio.TimeoutError:
                self._reminder_task.cancel()
        if self._cleanup_task and not self._cleanup_task.done():
            # _stop_event 已经 set，无需重复设置
            try:
                await asyncio.wait_for(self._cleanup_task, timeout=5)
            except asyncio.TimeoutError:
                self._cleanup_task.cancel()

    # ========== 数据访问层 ==========
    async def _create_item(
        self,
        guild_id: str,
        list_type: str,
        author: discord.Member,
        content: str,
        channel_id: Optional[str] = None,
        message_link: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> Tuple[int, int]:
        """创建一条 todo_items 记录, 返回 (item_id, 可见序号sort_order)；新项目追加到末尾"""
        created_at = iso_bj(now_bj())
        content = content.strip()
        message_link = try_format_message_link(message_link)

        # 计算新项目在该列表中的末尾序号
        next_sort = await self._compute_next_sort_order(
            guild_id=guild_id,
            list_type=list_type,
            user_id=user_id,
            channel_id=channel_id
        )

        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO todo_items (
                    guild_id, list_type, user_id, channel_id, content, message_link,
                    status, created_by, created_by_name, created_at, deleted, sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, 0, ?)
                """,
                (
                    guild_id,
                    list_type,
                    user_id,
                    channel_id,
                    content,
                    message_link,
                    str(author.id),
                    author.display_name,
                    created_at,
                    next_sort
                )
            )
            await conn.commit()
            return cursor.lastrowid, next_sort

    async def _update_item(
        self,
        guild_id: str,
        item_id: int,
        editor: discord.Member,
        new_content: Optional[str] = None,
        new_status: Optional[str] = None,
        new_message_link: Optional[str] = None
    ) -> int:
        """更新 todo_items；返回受影响行数"""
        fields = []
        params: List = []

        if new_content is not None:
            fields.append("content = ?")
            params.append(new_content.strip())

        if new_status is not None:
            if new_status not in (STATUS_OPEN, STATUS_COMPLETED):
                raise ValueError("无效的状态（必须是 open/completed）")
            fields.append("status = ?")
            params.append(new_status)

        if new_message_link is not None:
            fields.append("message_link = ?")
            params.append(try_format_message_link(new_message_link))

        fields.extend([
            "last_modified_by = ?",
            "last_modified_by_name = ?",
            "last_modified_at = ?"
        ])
        params.extend([
            str(editor.id),
            editor.display_name,
            iso_bj(now_bj())
        ])

        params.extend([guild_id, item_id])

        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                f"""
                UPDATE todo_items
                SET {", ".join(fields)}
                WHERE guild_id = ? AND item_id = ? AND deleted = 0
                """,
                tuple(params)
            )
            await conn.commit()
            return cursor.rowcount

    async def _soft_delete_item(self, guild_id: str, item_id: int) -> int:
        """软删除; 返回受影响行数"""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE todo_items
                SET deleted = 1
                WHERE guild_id = ? AND item_id = ? AND deleted = 0
                """,
                (guild_id, item_id)
            )
            await conn.commit()
            return cursor.rowcount

    async def _get_item(self, guild_id: str, item_id: int) -> Optional[Dict]:
        """获取单条 item"""
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                """
                SELECT * FROM todo_items
                WHERE guild_id = ? AND item_id = ? AND deleted = 0
                """,
                (guild_id, item_id)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def _fetch_personal_items(self, guild_id: str, user_id: str) -> List[Dict]:
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                """
                SELECT * FROM todo_items
                WHERE guild_id = ? AND list_type = 'person' AND user_id = ? AND deleted = 0
                ORDER BY
                    CASE WHEN sort_order IS NULL THEN 1 ELSE 0 END,
                    sort_order ASC,
                    created_at ASC
                """,
                (guild_id, user_id)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def _fetch_channel_items(self, guild_id: str, channel_id: str) -> List[Dict]:
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                """
                SELECT * FROM todo_items
                WHERE guild_id = ? AND list_type = 'channel' AND channel_id = ? AND deleted = 0
                ORDER BY
                    CASE WHEN sort_order IS NULL THEN 1 ELSE 0 END,
                    sort_order ASC,
                    created_at ASC
                """,
                (guild_id, channel_id)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def _add_or_update_reminder(
        self,
        guild_id: str,
        user_id: str,
        channel_id: str,
        reminder_type: str,
        countdown_seconds: Optional[int],
        daily_time: Optional[str]
    ) -> int:
        """新增提醒记录，返回 reminder_id"""
        created_at = iso_bj(now_bj())
        next_run: Optional[datetime] = None

        if reminder_type == "countdown":
            if not isinstance(countdown_seconds, int) or countdown_seconds <= 0:
                raise ValueError("倒计时秒数必须是正整数")
            next_run = now_bj() + timedelta(seconds=countdown_seconds)
        elif reminder_type == "daily":
            if not isinstance(daily_time, str):
                raise ValueError("每日提醒需要提供 HH:MM")
            next = parse_daily_time_to_next_run(daily_time)
            if not next:
                raise ValueError("每日时间格式应为 HH:MM")
            next_run = next
        else:
            raise ValueError("reminder_type 必须是 countdown 或 daily")

        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO todo_reminders (
                    guild_id, user_id, channel_id,
                    reminder_type, countdown_seconds, daily_time,
                    next_run, created_at, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    guild_id, user_id, channel_id,
                    reminder_type, countdown_seconds, daily_time,
                    iso_bj(next_run), created_at
                )
            )
            await conn.commit()
            return cursor.lastrowid

    async def _list_monitored_channels(self, guild_id: str) -> List[int]:
        async with self.db.get_connection() as conn:
            async with conn.execute(
                "SELECT channel_id FROM todo_monitor_channels WHERE guild_id = ?",
                (guild_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [int(r[0]) for r in rows]

    async def _add_monitored_channel(self, guild_id: str, channel_id: str):
        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO todo_monitor_channels (guild_id, channel_id)
                VALUES (?, ?)
                """,
                (guild_id, channel_id)
            )
            await conn.commit()

    async def _remove_monitored_channel(self, guild_id: str, channel_id: str) -> int:
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM todo_monitor_channels WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id)
            )
            await conn.commit()
            return cursor.rowcount

    # ====== 事件权限：仅管理员/开发者或被授权的用户/身份组可使用 ======
    async def _add_permission(self, guild_id: str, target_id: str, target_type: str, added_by: str):
        created_at = iso_bj(now_bj())
        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO todo_permissions
                (guild_id, target_id, target_type, added_by, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, target_id, target_type, added_by, created_at)
            )
            await conn.commit()

    async def _remove_permission(self, guild_id: str, target_id: str, target_type: str) -> int:
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM todo_permissions WHERE guild_id = ? AND target_id = ? AND target_type = ?",
                (guild_id, target_id, target_type)
            )
            await conn.commit()
            return cursor.rowcount

    async def _compute_next_sort_order(
        self,
        guild_id: str,
        list_type: str,
        user_id: Optional[str] = None,
        channel_id: Optional[str] = None
    ) -> int:
        """
        计算该列表的下一个末尾序号（sort_order = 当前最大 + 1）
        """
        async with self.db.get_connection() as conn:
            if list_type == LIST_TYPE_PERSON:
                params = (guild_id, list_type, user_id)
                query = """
                    SELECT COALESCE(MAX(sort_order), 0)
                    FROM todo_items
                    WHERE guild_id = ? AND list_type = ? AND user_id = ? AND deleted = 0
                """
            else:
                params = (guild_id, list_type, channel_id)
                query = """
                    SELECT COALESCE(MAX(sort_order), 0)
                    FROM todo_items
                    WHERE guild_id = ? AND list_type = ? AND channel_id = ? AND deleted = 0
                """
            async with conn.execute(query, params) as cursor:
                row = await cursor.fetchone()
                current_max = int(row[0]) if row and row[0] is not None else 0
                return current_max + 1

    async def _reindex_list(
        self,
        guild_id: str,
        list_type: str,
        user_id: Optional[str] = None,
        channel_id: Optional[str] = None
    ) -> None:
        """
        将列表内所有项目按当前排序重新编号 sort_order = 1..n，保持连续性
        """
        async with self.db.get_connection() as conn:
            if list_type == LIST_TYPE_PERSON:
                params = (guild_id, list_type, user_id)
                sel = """
                    SELECT item_id FROM todo_items
                    WHERE guild_id = ? AND list_type = ? AND user_id = ? AND deleted = 0
                    ORDER BY
                        CASE WHEN sort_order IS NULL THEN 1 ELSE 0 END,
                        sort_order ASC,
                        created_at ASC
                """
            else:
                params = (guild_id, list_type, channel_id)
                sel = """
                    SELECT item_id FROM todo_items
                    WHERE guild_id = ? AND list_type = ? AND channel_id = ? AND deleted = 0
                    ORDER BY
                        CASE WHEN sort_order IS NULL THEN 1 ELSE 0 END,
                        sort_order ASC,
                        created_at ASC
                """
            ids: List[int] = []
            async with conn.execute(sel, params) as cursor:
                rows = await cursor.fetchall()
                ids = [int(r[0]) for r in rows]
            updates = [(i, item_id) for i, item_id in enumerate(ids, start=1)]
            if updates:
                await conn.executemany("UPDATE todo_items SET sort_order = ? WHERE item_id = ?", updates)
                await conn.commit()

    async def _reorder_by_indices(
        self,
        guild_id: str,
        list_type: str,
        source_index: int,
        target_index: int,
        user_id: Optional[str] = None,
        channel_id: Optional[str] = None
    ) -> Tuple[int, int]:
        """
        将当前列表中的第 source_index 项移动到 target_index 位置，并落库更新所有项的 sort_order
        返回 (source_index, target_index)
        """
        if list_type == LIST_TYPE_PERSON:
            items = await self._fetch_personal_items(guild_id, user_id)
        else:
            items = await self._fetch_channel_items(guild_id, channel_id)

        n = len(items)
        if n < 2:
            raise ValueError("当前列表少于2条，无法排序。")
        if source_index < 1 or source_index > n or target_index < 1 or target_index > n:
            raise ValueError("序号超出范围。")

        if source_index == target_index:
            return source_index, target_index

        arr = items.copy()
        moving = arr.pop(source_index - 1)
        arr.insert(target_index - 1, moving)

        async with self.db.get_connection() as conn:
            updates = [(i, int(it["item_id"])) for i, it in enumerate(arr, start=1)]
            await conn.executemany("UPDATE todo_items SET sort_order = ? WHERE item_id = ?", updates)
            await conn.commit()

        return source_index, target_index

    async def _list_permissions(self, guild_id: str) -> List[Dict]:
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                "SELECT guild_id, target_id, target_type, added_by, created_at FROM todo_permissions WHERE guild_id = ?",
                (guild_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def _is_allowed(self, interaction: discord.Interaction) -> bool:
        # 管理员/拥有者始终允许
        try:
            if await is_admin_or_owner(interaction):
                return True
        except Exception:
            pass

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False

        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        # 用户直接授权
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                "SELECT 1 FROM todo_permissions WHERE guild_id = ? AND target_type = 'user' AND target_id = ? LIMIT 1",
                (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return True

            # 身份组授权
            role_ids = [str(r.id) for r in interaction.user.roles if hasattr(r, 'id')]
            if role_ids:
                placeholders = ",".join("?" for _ in role_ids)
                query = f"""
                    SELECT 1 FROM todo_permissions
                    WHERE guild_id = ? AND target_type = 'role' AND target_id IN ({placeholders})
                    LIMIT 1
                """
                params = [guild_id] + role_ids
                async with conn.execute(query, params) as cursor2:
                    row2 = await cursor2.fetchone()
                    if row2:
                        return True

        return False

    # ========== Embed 格式化 ==========
    @staticmethod
    def _status_emoji(status: str) -> str:
        return "✅" if status == STATUS_COMPLETED else "⏳"

    @staticmethod
    def _fmt_item_line_with_index(item: Dict, index: int) -> str:
        parts = []
        parts.append(f"{TodoListCog._status_emoji(item.get('status','open'))} `#{index}` {item['content']}")
        if item.get("message_link"):
            parts.append(f"[消息链接]({item['message_link']})")
        # 审计信息
        created = item.get("created_by_name") or item.get("created_by")
        parts.append(f"创建: {created}")
        if item.get("last_modified_by_name") or item.get("last_modified_by"):
            mod = item.get("last_modified_by_name") or item.get("last_modified_by")
            parts.append(f"修改: {mod}")
        return " | ".join(parts)

    def _build_list_embed(self, title: str, items: List[Dict], ephemeral_hint: Optional[str] = None) -> discord.Embed:
        embed = discord.Embed(title=title, color=discord.Color.blue())
        if not items:
            embed.description = "暂无事件"
        else:
            lines = []
            # 使用列表内序号（从1开始），而不是数据库自增ID
            for idx, item in enumerate(items[:20], start=1):  # 简单控制最多20条
                lines.append(self._fmt_item_line_with_index(item, idx))
            embed.description = "\n".join(lines)
            if len(items) > 20:
                embed.set_footer(text=f"仅显示前20条，共 {len(items)} 条")
        if ephemeral_hint:
            embed.add_field(name="提示", value=ephemeral_hint, inline=False)
        return embed

    # ========== 斜杠命令 ==========
    @todo.command(name="添加", description="添加事件（个人或频道）")
    @app_commands.describe(
        类型="选择个人或频道（默认个人）",
        备注="事件备注（必填）",
        消息链接="仅当类型是频道时可选填写"
    )
    @app_commands.choices(类型=[
        app_commands.Choice(name="个人", value=LIST_TYPE_PERSON),
        app_commands.Choice(name="频道", value=LIST_TYPE_CHANNEL),
    ])
    async def add_event(
        self,
        interaction: discord.Interaction,
        类型: Optional[str] = LIST_TYPE_PERSON,
        备注: Optional[str] = None,
        消息链接: Optional[str] = None
    ):
        await safe_defer(interaction, ephemeral=True)
        if not await self._is_allowed(interaction):
            return await interaction.followup.send("❌ 你没有使用事件指令的权限。请联系管理员授权。", ephemeral=True)

        if not 备注 or not 备注.strip():
            return await interaction.followup.send("❌ 备注为必填项。", ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user
        if not guild or not channel:
            return await interaction.followup.send("❌ 只能在服务器频道中使用此命令。", ephemeral=True)

        guild_id = str(guild.id)
        try:
            if 类型 == LIST_TYPE_CHANNEL:
                item_id, new_index = await self._create_item(
                    guild_id=guild_id,
                    list_type=LIST_TYPE_CHANNEL,
                    author=user,
                    content=备注,
                    channel_id=str(channel.id),
                    message_link=消息链接
                )
                await interaction.followup.send(f"✅ 已添加频道事件 `#{new_index}`。", ephemeral=True)
            else:
                item_id, new_index = await self._create_item(
                    guild_id=guild_id,
                    list_type=LIST_TYPE_PERSON,
                    author=user,
                    content=备注,
                    user_id=str(user.id)
                )
                await interaction.followup.send(f"✅ 已添加个人事件 `#{new_index}`。", ephemeral=True)

        except Exception as e:
            logger.error(f"add_event error: {e}", exc_info=True)
            await interaction.followup.send("❌ 添加事件时发生错误。", ephemeral=True)

    @todo.command(name="编辑", description="编辑事件（根据列表内序号：个人/频道）")
    @app_commands.describe(
        类型="选择个人或频道（必选）",
        事件序号="列表内显示的序号（例如 1、2、3，不带#）",
        新备注="新的备注内容（可选）",
        新状态="open 或 completed（可选）",
        新消息链接="仅用于频道事件，可选"
    )
    @app_commands.choices(类型=[
        app_commands.Choice(name="个人", value=LIST_TYPE_PERSON),
        app_commands.Choice(name="频道", value=LIST_TYPE_CHANNEL),
    ])
    async def edit_event(
        self,
        interaction: discord.Interaction,
        类型: str,
        事件序号: int,
        新备注: Optional[str] = None,
        新状态: Optional[str] = None,
        新消息链接: Optional[str] = None
    ):
        await safe_defer(interaction, ephemeral=True)
        if not await self._is_allowed(interaction):
            return await interaction.followup.send("❌ 你没有使用事件指令的权限。请联系管理员授权。", ephemeral=True)

        guild = interaction.guild
        user = interaction.user
        channel = interaction.channel
        if not guild or not channel:
            return await interaction.followup.send("❌ 只能在服务器频道中使用此命令。", ephemeral=True)
        guild_id = str(guild.id)

        try:
            # 先根据类型与上下文获取“当前可见列表”，用序号映射到具体 item_id
            if 类型 == LIST_TYPE_PERSON:
                items = await self._fetch_personal_items(guild_id, str(user.id))
            else:
                items = await self._fetch_channel_items(guild_id, str(channel.id))

            if not items:
                return await interaction.followup.send("❌ 当前列表为空，无法编辑。", ephemeral=True)

            if 事件序号 <= 0 or 事件序号 > len(items):
                return await interaction.followup.send("❌ 事件序号超出范围。", ephemeral=True)

            target = items[事件序号 - 1]
            item_id = int(target["item_id"])

            # 权限逻辑：个人事件仅本人可改；频道事件任何人可改（记录修改人）
            if target["list_type"] == LIST_TYPE_PERSON and str(user.id) != str(target.get("user_id")):
                return await interaction.followup.send("❌ 你无权编辑他人的个人事件。", ephemeral=True)

            affected = await self._update_item(
                guild_id=guild_id,
                item_id=item_id,
                editor=user,
                new_content=新备注 if 新备注 is not None else None,
                new_status=新状态 if 新状态 is not None else None,
                new_message_link=新消息链接 if 新消息链接 is not None else None
            )
            if affected > 0:
                await interaction.followup.send(f"✅ 已更新事件 `#{事件序号}`（{ '个人' if 类型==LIST_TYPE_PERSON else '频道' }列表）。", ephemeral=True)
            else:
                await interaction.followup.send("ℹ️ 没有任何变更。", ephemeral=True)

        except ValueError as ve:
            await interaction.followup.send(f"❌ {ve}", ephemeral=True)
        except Exception as e:
            logger.error(f"edit_event error: {e}", exc_info=True)
            await interaction.followup.send("❌ 编辑事件时发生错误。", ephemeral=True)

    @todo.command(name="删除", description="删除事件（根据列表内序号：个人/频道，支持批量）")
    @app_commands.describe(
        类型="选择个人或频道（必选）",
        序号列表="要删除的事件序号，逗号分隔（如 1,2,3，不带#）"
    )
    @app_commands.choices(类型=[
        app_commands.Choice(name="个人", value=LIST_TYPE_PERSON),
        app_commands.Choice(name="频道", value=LIST_TYPE_CHANNEL),
    ])
    async def delete_event(
        self,
        interaction: discord.Interaction,
        类型: str,
        序号列表: str
    ):
        await safe_defer(interaction, ephemeral=True)
        if not await self._is_allowed(interaction):
            return await interaction.followup.send("❌ 你没有使用事件指令的权限。请联系管理员授权。", ephemeral=True)

        guild = interaction.guild
        user = interaction.user
        channel = interaction.channel
        if not guild or not channel:
            return await interaction.followup.send("❌ 只能在服务器频道中使用此命令。", ephemeral=True)
        guild_id = str(guild.id)

        try:
            indexes = parse_index_list(序号列表)
            if not indexes:
                return await interaction.followup.send("❌ 请输入有效的序号列表，例如 1,2,3。", ephemeral=True)

            # 获取当前上下文的事件列表
            if 类型 == LIST_TYPE_PERSON:
                items = await self._fetch_personal_items(guild_id, str(user.id))
            else:
                items = await self._fetch_channel_items(guild_id, str(channel.id))

            if not items:
                return await interaction.followup.send("❌ 当前列表为空，无法删除。", ephemeral=True)

            # 映射序号到 item_id
            valid_targets: List[int] = []
            invalid_indexes: List[int] = []
            unauthorized_indexes: List[int] = []

            for idx in indexes:
                if idx <= 0 or idx > len(items):
                    invalid_indexes.append(idx)
                    continue
                target = items[idx - 1]
                # 权限校验：个人事件仅本人可删；频道事件任何人可删
                if target["list_type"] == LIST_TYPE_PERSON and str(user.id) != str(target.get("user_id")):
                    unauthorized_indexes.append(idx)
                    continue
                valid_targets.append(int(target["item_id"]))

            deleted = 0
            for item_id in valid_targets:
                affected = await self._soft_delete_item(guild_id, item_id)
                if affected > 0:
                    deleted += 1

            # 删除后重排序号，保持连续性
            try:
                if 类型 == LIST_TYPE_PERSON:
                    await self._reindex_list(guild_id, LIST_TYPE_PERSON, user_id=str(user.id))
                else:
                    await self._reindex_list(guild_id, LIST_TYPE_CHANNEL, channel_id=str(channel.id))
            except Exception as reidx_err:
                logger.warning(f"重排 sort_order 失败: {reidx_err}")

            parts = []
            parts.append(f"✅ 已删除: {deleted} 条")
            if invalid_indexes:
                parts.append(f"❌ 无效序号: {', '.join(str(i) for i in invalid_indexes)}")
            if unauthorized_indexes:
                parts.append(f"⛔ 无权限序号: {', '.join(str(i) for i in unauthorized_indexes)}")

            await interaction.followup.send("\n".join(parts), ephemeral=True)
        except Exception as e:
            logger.error(f"delete_event error: {e}", exc_info=True)
            await interaction.followup.send("❌ 删除事件时发生错误。", ephemeral=True)

    @todo.command(name="排序", description="可视化排序（个人/频道）")
    @app_commands.describe(类型="选择个人或频道（必选）")
    @app_commands.choices(类型=[
        app_commands.Choice(name="个人", value=LIST_TYPE_PERSON),
        app_commands.Choice(name="频道", value=LIST_TYPE_CHANNEL),
    ])
    async def reorder_events(
        self,
        interaction: discord.Interaction,
        类型: str
    ):
        await safe_defer(interaction, ephemeral=True)
        if not await self._is_allowed(interaction):
            return await interaction.followup.send("❌ 你没有使用事件指令的权限。请联系管理员授权。", ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user
        if not guild or not channel:
            return await interaction.followup.send("❌ 只能在服务器频道中使用此命令。", ephemeral=True)

        guild_id = str(guild.id)
        try:
            if 类型 == LIST_TYPE_PERSON:
                items = await self._fetch_personal_items(guild_id, str(user.id))
                if not items or len(items) < 2:
                    return await interaction.followup.send("ℹ️ 列表不足2条，无法进行排序。", ephemeral=True)
                embed = self._build_list_embed("📋 个人事件列表（排序）", items, ephemeral_hint="此列表仅你可见")
                view = ReorderView(self, guild_id, LIST_TYPE_PERSON, str(user.id), None, items)
            else:
                items = await self._fetch_channel_items(guild_id, str(channel.id))
                if not items or len(items) < 2:
                    return await interaction.followup.send("ℹ️ 列表不足2条，无法进行排序。", ephemeral=True)
                embed = self._build_list_embed("📋 频道事件列表（排序）", items)
                view = ReorderView(self, guild_id, LIST_TYPE_CHANNEL, None, str(channel.id), items)

            await interaction.followup.send(
                content="请选择源与目标序号，然后点击“确认移动”。",
                embed=embed,
                view=view,
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"reorder_events error: {e}", exc_info=True)
            await interaction.followup.send("❌ 打开排序视图失败。", ephemeral=True)
    @todo.command(name="列表", description="查看事件列表（个人/频道）")
    @app_commands.describe(类型="默认为个人；频道则显示当前频道的事件")
    @app_commands.choices(类型=[
        app_commands.Choice(name="个人", value=LIST_TYPE_PERSON),
        app_commands.Choice(name="频道", value=LIST_TYPE_CHANNEL),
    ])
    async def list_events(self, interaction: discord.Interaction, 类型: Optional[str] = LIST_TYPE_PERSON):
        # 个人列表 -> 私密；频道列表 -> 公共
        ephemeral_flag = True if 类型 != LIST_TYPE_CHANNEL else False
        await safe_defer(interaction, ephemeral=ephemeral_flag)
        if not await self._is_allowed(interaction):
            return await interaction.followup.send("❌ 你没有使用事件指令的权限。请联系管理员授权。", ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user
        if not guild or not channel:
            return await interaction.followup.send("❌ 只能在服务器频道中使用此命令。", ephemeral=True)

        guild_id = str(guild.id)
        try:
            if 类型 == LIST_TYPE_CHANNEL:
                items = await self._fetch_channel_items(guild_id, str(channel.id))
                embed = self._build_list_embed(f"📋 频道事件列表（#{channel.name}）", items)
                await interaction.followup.send(embed=embed, ephemeral=ephemeral_flag)
            else:
                items = await self._fetch_personal_items(guild_id, str(user.id))
                embed = self._build_list_embed("📋 个人事件列表", items, ephemeral_hint="此列表仅你可见")
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"list_events error: {e}", exc_info=True)
            await interaction.followup.send("❌ 获取列表时发生错误。", ephemeral=True)
    @todo.command(name="提醒", description="设置提醒（倒计时/每日）")
    @app_commands.describe(
        模式="选择倒计时或每日",
        倒计时="倒计时字符串: 如 60s, 10m, 2h, 1d 或纯数字秒数（仅在模式为倒计时）",
        每日时间="仅在模式为每日时填写 HH:MM（北京时间）"
    )
    @app_commands.choices(模式=[
        app_commands.Choice(name="倒计时", value="countdown"),
        app_commands.Choice(name="每日", value="daily"),
    ])
    async def set_reminder(
        self,
        interaction: discord.Interaction,
        模式: str,
        倒计时: Optional[str] = None,
        每日时间: Optional[str] = None
    ):
        # 提醒在“当前频道”触发并@自己
        await safe_defer(interaction, ephemeral=True)
        if not await self._is_allowed(interaction):
            return await interaction.followup.send("❌ 你没有使用事件指令的权限。请联系管理员授权。", ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user
        if not guild or not channel:
            return await interaction.followup.send("❌ 只能在服务器频道中使用此命令。", ephemeral=True)

        guild_id = str(guild.id)
        try:
            countdown_seconds: Optional[int] = None
            if 模式 == "countdown":
                if not 倒计时:
                    return await interaction.followup.send("❌ 请输入倒计时（如 60s/10m/2h/1d 或纯数字秒数）。", ephemeral=True)
                countdown_seconds = parse_countdown_to_seconds(倒计时)
                if not countdown_seconds:
                    return await interaction.followup.send("❌ 倒计时格式无效，应为 60s/10m/2h/1d 或纯数字秒数。", ephemeral=True)

            reminder_id = await self._add_or_update_reminder(
                guild_id=guild_id,
                user_id=str(user.id),
                channel_id=str(channel.id),
                reminder_type=模式,
                countdown_seconds=countdown_seconds if 模式 == "countdown" else None,
                daily_time=每日时间 if 模式 == "daily" else None
            )
            if 模式 == "countdown":
                await interaction.followup.send(f"✅ 已创建倒计时提醒（ID: {reminder_id}）。", ephemeral=True)
            else:
                await interaction.followup.send(f"✅ 已创建每日提醒（ID: {reminder_id}），时间 {每日时间}（北京时间）。", ephemeral=True)

        except ValueError as ve:
            await interaction.followup.send(f"❌ {ve}", ephemeral=True)
        except Exception as e:
            logger.error(f"set_reminder error: {e}", exc_info=True)
            await interaction.followup.send("❌ 设置提醒时发生错误。", ephemeral=True)

    @todo.command(name="设置监听频道", description="设置监听频道（严格匹配关键词，管理员/开发者）")
    @app_commands.describe(
        频道="目标文字频道",
        移除="勾选则从监听列表移除该频道（默认添加）"
    )
    @admin_or_owner()
    async def set_monitor_channel(
        self,
        interaction: discord.Interaction,
        频道: discord.TextChannel,
        移除: Optional[bool] = False
    ):
        await safe_defer(interaction, ephemeral=True)
        if not interaction.guild:
            return await interaction.followup.send("❌ 只能在服务器中使用。", ephemeral=True)

        guild_id = str(interaction.guild.id)
        try:
            if 移除:
                affected = await self._remove_monitored_channel(guild_id, str(频道.id))
                if affected > 0:
                    await interaction.followup.send(f"✅ 已从监听列表移除频道 {频道.mention}", ephemeral=True)
                else:
                    await interaction.followup.send("ℹ️ 该频道不在监听列表中。", ephemeral=True)
            else:
                await self._add_monitored_channel(guild_id, str(频道.id))
                await interaction.followup.send(f"✅ 已将频道 {频道.mention} 加入监听列表", ephemeral=True)
        except Exception as e:
            logger.error(f"set_monitor_channel error: {e}", exc_info=True)
            await interaction.followup.send("❌ 更新监听列表时发生错误。", ephemeral=True)

    # ========== 授权管理（管理员/开发者） ==========
    @todo.command(name="授权", description="管理可使用事件指令的用户/身份组（管理员/开发者）")
    @app_commands.describe(
        操作="选择操作",
        用户="目标用户（添加/移除时可选其一）",
        身份组="目标身份组（添加/移除时可选其一）"
    )
    @app_commands.choices(操作=[
        app_commands.Choice(name="添加", value="add"),
        app_commands.Choice(name="移除", value="remove"),
        app_commands.Choice(name="查看", value="list"),
    ])
    @admin_or_owner()
    async def manage_permissions(
        self,
        interaction: discord.Interaction,
        操作: str,
        用户: Optional[discord.Member] = None,
        身份组: Optional[discord.Role] = None
    ):
        await safe_defer(interaction, ephemeral=True)
        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("❌ 只能在服务器中使用。", ephemeral=True)

        guild_id = str(guild.id)
        try:
            if 操作 == "list":
                entries = await self._list_permissions(guild_id)
                if not entries:
                    return await interaction.followup.send("ℹ️ 当前未授权任何用户或身份组。", ephemeral=True)
                lines = []
                for e in entries[:50]:
                    if e['target_type'] == 'user':
                        lines.append(f"• 用户 <@{e['target_id']}> | 授权于 {e.get('created_at','')}")
                    else:
                        lines.append(f"• 身份组 <@&{e['target_id']}> | 授权于 {e.get('created_at','')}")
                more = f"\n… 共 {len(entries)} 条" if len(entries) > 50 else ""
                return await interaction.followup.send("✅ 授权列表：\n" + "\n".join(lines) + more, ephemeral=True)

            if not 用户 and not 身份组:
                return await interaction.followup.send("❌ 请选择“用户”或“身份组”。", ephemeral=True)

            target_type = 'user' if 用户 else 'role'
            target_id = str(用户.id) if 用户 else str(身份组.id)

            if 操作 == "add":
                await self._add_permission(guild_id, target_id, target_type, str(interaction.user.id))
                await interaction.followup.send(
                    f"✅ 已授权 {'用户 ' + 用户.mention if 用户 else '身份组 ' + 身份组.mention} 使用事件指令。",
                    ephemeral=True
                )
            elif 操作 == "remove":
                removed = await self._remove_permission(guild_id, target_id, target_type)
                if removed > 0:
                    await interaction.followup.send("✅ 已取消授权。", ephemeral=True)
                else:
                    await interaction.followup.send("ℹ️ 未找到对应授权记录。", ephemeral=True)
        except Exception as e:
            logger.error(f"manage_permissions error: {e}", exc_info=True)
            await interaction.followup.send("❌ 处理授权请求时发生错误。", ephemeral=True)

    # ========== 右键消息命令（移至模块级） ==========

    # ========== 监听消息 ==========
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        监听配置中的频道：
        - 严格匹配 "个人事件列表" -> 私信作者
        - 严格匹配 "频道事件列表" -> 在该频道公开回复
        """
        try:
            if message.author.bot:
                return
            if not message.guild or not isinstance(message.channel, discord.TextChannel):
                return

            guild_id = str(message.guild.id)
            monitored_channels = await self._list_monitored_channels(guild_id)
            if int(message.channel.id) not in monitored_channels:
                return

            content = message.content.strip()
            if content == "个人事件列表":
                items = await self._fetch_personal_items(guild_id, str(message.author.id))
                embed = self._build_list_embed("📋 个人事件列表", items, ephemeral_hint="仅你可见")
                # 私密回复 -> DM
                try:
                    await message.author.send(embed=embed)
                except discord.Forbidden:
                    # 回退提示（公共提醒但不包含列表内容）
                    await message.channel.send(f"{message.author.mention} 无法私信你，请检查私信设置。")
            elif content == "频道事件列表":
                items = await self._fetch_channel_items(guild_id, str(message.channel.id))
                embed = self._build_list_embed(f"📋 频道事件列表（#{message.channel.name}）", items)
                await message.channel.send(embed=embed)
            else:
                return
        except Exception as e:
            logger.error(f"on_message in TodoListCog error: {e}", exc_info=True)

    # ========== 提醒轮询 ==========
    async def _reminder_loop(self):
        """
        每30秒检查一次到期提醒：
        - 倒计时: 触发后 is_active = 0
        - 每日: 触发后 next_run + 1 天
        发送内容：@用户 + 个人列表 + 当前频道列表
        """
        try:
            while not self._stop_event.is_set():
                try:
                    await self._process_due_reminders()
                except Exception as e:
                    logger.error(f"reminder loop iteration error: {e}", exc_info=True)
                # 等待30秒或被停止
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"reminder loop fatal error: {e}", exc_info=True)

    async def _process_due_reminders(self):
        now_iso = iso_bj(now_bj())
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                """
                SELECT * FROM todo_reminders
                WHERE is_active = 1 AND next_run IS NOT NULL AND next_run <= ?
                """,
                (now_iso,)
            ) as cursor:
                rows = await cursor.fetchall()
                reminders = [dict(r) for r in rows]

        for r in reminders:
            guild_id = r["guild_id"]
            user_id = r["user_id"]
            channel_id = r["channel_id"]
            rtype = r["reminder_type"]
            daily_time = r.get("daily_time")
            reminder_id = r["reminder_id"]

            # 发送提醒
            try:
                await self._send_reminder_payload(guild_id, user_id, channel_id)
            except Exception as e:
                logger.error(f"send reminder payload error (id={reminder_id}): {e}", exc_info=True)

            # 更新提醒状态
            try:
                if rtype == "countdown":
                    # 只触发一次
                    await self._deactivate_reminder(reminder_id)
                else:
                    # 每日：滚动到下一天
                    next_dt = parse_daily_time_to_next_run(daily_time) if daily_time else None
                    if next_dt is None:
                        # 解析失败则停用
                        await self._deactivate_reminder(reminder_id)
                    else:
                        await self._update_next_run(reminder_id, iso_bj(next_dt))
            except Exception as e:
                logger.error(f"update reminder state error (id={reminder_id}): {e}", exc_info=True)

    async def _send_reminder_payload(self, guild_id: str, user_id: str, channel_id: str):
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            try:
                guild = await self.bot.fetch_guild(int(guild_id))
            except Exception:
                return

        channel = guild.get_channel(int(channel_id))
        if not channel or not isinstance(channel, discord.TextChannel):
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except Exception:
                return

        # 获取列表快照
        personal = await self._fetch_personal_items(guild_id, user_id)
        channel_items = await self._fetch_channel_items(guild_id, channel_id)

        user_mention = f"<@{user_id}>"
        header = f"{user_mention} 你的提醒到了！"

        # 个人列表 embed
        embed_personal = self._build_list_embed("📋 个人事件列表快照", personal)
        embed_channel = self._build_list_embed(f"📋 频道事件列表快照（#{channel.name}）", channel_items)

        await channel.send(content=header, embeds=[embed_personal, embed_channel])

    async def _deactivate_reminder(self, reminder_id: int):
        async with self.db.get_connection() as conn:
            await conn.execute(
                "UPDATE todo_reminders SET is_active = 0 WHERE reminder_id = ?",
                (reminder_id,)
            )
            await conn.commit()

    async def _update_next_run(self, reminder_id: int, next_run_iso: str):
        async with self.db.get_connection() as conn:
            await conn.execute(
                "UPDATE todo_reminders SET next_run = ? WHERE reminder_id = ?",
                (next_run_iso, reminder_id)
            )
            await conn.commit()

    # ========== 自动清理（每日03:00，30天未修改，软删除） ==========
    async def _cleanup_loop(self):
        """
        每日北京时间03:00执行一次清理：
        - 软删除所有 COALESCE(last_modified_at, created_at) <= 30天前 的记录（deleted=0）
        - 静默执行，不通知
        """
        try:
            while not self._stop_event.is_set():
                try:
                    next_run = parse_daily_time_to_next_run("03:00")
                    if not next_run:
                        # 理论不可达；兜底为明天03:00
                        next_run = now_bj().replace(hour=3, minute=0, second=0, microsecond=0) + timedelta(days=1)

                    wait_seconds = (next_run - now_bj()).total_seconds()
                    if wait_seconds > 0:
                        try:
                            await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                            # stop_event 触发则退出
                            break
                        except asyncio.TimeoutError:
                            pass  # 到点执行
                    if self._stop_event.is_set():
                        break

                    await self._cleanup_stale_items()
                except Exception as e:
                    logger.error(f"cleanup loop iteration error: {e}", exc_info=True)
                    # 避免热循环
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"cleanup loop fatal error: {e}", exc_info=True)

    async def _cleanup_stale_items(self, cutoff_days: int = 30, batch_size: int = 1000) -> None:
        """
        软删除 30 天未修改（以 last_modified_at 为准，若为空用 created_at）的所有事件（open/completed 不区分，deleted=0）
        为利用索引，分两类处理：
          1) last_modified_at 非空 且 <= cutoff
          2) last_modified_at 为空 且 created_at <= cutoff
        逐批按 item_id 递增扫描，减少大 OFFSET 的成本。

        Args:
            cutoff_days: 判定天数阈值，默认30天
            batch_size: 单次处理的最大记录数，默认1000
        """
        start_ts = now_bj()
        cutoff_iso = iso_bj(now_bj() - timedelta(days=cutoff_days))
        total_deleted = 0

        logger.info(f"[TodoCleanup] Start cleanup: cutoff={cutoff_iso}, batch_size={batch_size}")

        # Pass 1: last_modified_at 非空
        last_id = 0
        while not self._stop_event.is_set():
            rows: List[Dict] = []
            async with self.db.get_connection() as conn:
                conn.row_factory = self.db.dict_row
                async with conn.execute(
                    """
                    SELECT item_id, guild_id
                    FROM todo_items
                    WHERE deleted = 0
                      AND last_modified_at IS NOT NULL
                      AND last_modified_at <= ?
                      AND item_id > ?
                    ORDER BY item_id ASC
                    LIMIT ?
                    """,
                    (cutoff_iso, last_id, batch_size)
                ) as cursor:
                    rows = await cursor.fetchall()
                    rows = [dict(r) for r in rows] if rows else []

            if not rows:
                break

            for r in rows:
                if self._stop_event.is_set():
                    break
                item_id = int(r["item_id"])
                guild_id = str(r["guild_id"])
                try:
                    affected = await self._soft_delete_item(guild_id, item_id)
                    if affected > 0:
                        total_deleted += 1
                except Exception as e:
                    logger.error(f"[TodoCleanup] soft delete failed (item_id={item_id}, guild_id={guild_id}): {e}", exc_info=True)
                last_id = item_id

        # Pass 2: last_modified_at 为空，按 created_at 判定
        last_id = 0
        while not self._stop_event.is_set():
            rows: List[Dict] = []
            async with self.db.get_connection() as conn:
                conn.row_factory = self.db.dict_row
                async with conn.execute(
                    """
                    SELECT item_id, guild_id
                    FROM todo_items
                    WHERE deleted = 0
                      AND last_modified_at IS NULL
                      AND created_at <= ?
                      AND item_id > ?
                    ORDER BY item_id ASC
                    LIMIT ?
                    """,
                    (cutoff_iso, last_id, batch_size)
                ) as cursor:
                    rows = await cursor.fetchall()
                    rows = [dict(r) for r in rows] if rows else []

            if not rows:
                break

            for r in rows:
                if self._stop_event.is_set():
                    break
                item_id = int(r["item_id"])
                guild_id = str(r["guild_id"])
                try:
                    affected = await self._soft_delete_item(guild_id, item_id)
                    if affected > 0:
                        total_deleted += 1
                except Exception as e:
                    logger.error(f"[TodoCleanup] soft delete failed (item_id={item_id}, guild_id={guild_id}): {e}", exc_info=True)
                last_id = item_id

        elapsed = (now_bj() - start_ts).total_seconds()
        logger.info(f"[TodoCleanup] Done. Deleted={total_deleted}, elapsed={elapsed:.2f}s")

# ========== 右键消息命令（模块级） ==========
@app_commands.context_menu(name="添加到个人事件")
async def ctx_add_personal(interaction: discord.Interaction, message: discord.Message):
    cog = interaction.client.get_cog('TodoListCog')
    if not cog:
        await interaction.response.send_message("❌ 事件系统未加载。", ephemeral=True)
        return
    # 权限检查：不允许未授权用户使用右键事件
    if not await cog._is_allowed(interaction):
        await interaction.response.send_message("❌ 你没有使用事件指令的权限。请联系管理员授权。", ephemeral=True)
        return

    async def _on_submit(inter: discord.Interaction, remark: str):
        try:
            guild = inter.guild
            user = inter.user
            if not guild:
                await inter.response.send_message("❌ 只能在服务器中使用。", ephemeral=True)
                return
            item_id, new_index = await cog._create_item(
                guild_id=str(guild.id),
                list_type=LIST_TYPE_PERSON,
                author=user,
                content=remark,
                user_id=str(user.id),
                message_link=str(message.jump_url)
            )
            await inter.response.send_message(f"✅ 已添加个人事件 `#{new_index}`。", ephemeral=True)
        except Exception as e:
            logger.error(f"ctx_add_personal error: {e}", exc_info=True)
            if not inter.response.is_done():
                await inter.response.send_message("❌ 添加个人事件失败。", ephemeral=True)
    await interaction.response.send_modal(AddTodoModalBase(_on_submit))

@app_commands.context_menu(name="添加到频道事件")
async def ctx_add_channel(interaction: discord.Interaction, message: discord.Message):
    cog = interaction.client.get_cog('TodoListCog')
    if not cog:
        await interaction.response.send_message("❌ 事件系统未加载。", ephemeral=True)
        return
    # 权限检查
    if not await cog._is_allowed(interaction):
        await interaction.response.send_message("❌ 你没有使用事件指令的权限。请联系管理员授权。", ephemeral=True)
        return

    async def _on_submit(inter: discord.Interaction, remark: str):
        try:
            guild = inter.guild
            user = inter.user
            if not guild:
                await inter.response.send_message("❌ 只能在服务器中使用。", ephemeral=True)
                return
            item_id, new_index = await cog._create_item(
                guild_id=str(guild.id),
                list_type=LIST_TYPE_CHANNEL,
                author=user,
                content=remark,
                channel_id=str(message.channel.id),
                message_link=str(message.jump_url)
            )
            await inter.response.send_message(f"✅ 已添加频道事件 `#{new_index}`。", ephemeral=True)
        except Exception as e:
            logger.error(f"ctx_add_channel error: {e}", exc_info=True)
            if not inter.response.is_done():
                await inter.response.send_message("❌ 添加频道事件失败。", ephemeral=True)
    await interaction.response.send_modal(AddTodoModalBase(_on_submit))

async def setup(bot: commands.Bot):
    await bot.add_cog(TodoListCog(bot))
    try:
        bot.tree.add_command(ctx_add_personal)
    except Exception as e:
        logger.debug(f"register ctx_add_personal failed: {e}")
    try:
        bot.tree.add_command(ctx_add_channel)
    except Exception as e:
        logger.debug(f"register ctx_add_channel failed: {e}")
    logger.info("TodoListCog has been added to bot")