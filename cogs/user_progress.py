import discord
from discord.ext import commands
from discord import app_commands
import json
from typing import Optional, List
from datetime import datetime

from cogs.base_cog import BaseCog
from core.database import DatabaseManager
from core.models import UserProgress, Gym
from utils.formatters import format_user_progress, format_badge_wall, format_time, format_timedelta
from utils.permissions import has_gym_permission
from utils.logger import get_logger
from utils.time_utils import get_beijing_now, parse_beijing_time, remaining_until, format_beijing_display

logger = get_logger(__name__)


class UserProgressCog(BaseCog):
    """用户进度管理Cog"""

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

    # ========== 用户命令 ==========

    @app_commands.command(name="我的徽章墙", description="查看你已获得的道馆徽章")
    async def my_badges(self, interaction: discord.Interaction):
        """查看徽章墙"""
        await interaction.response.defer(ephemeral=True, thinking=True)

        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)

        try:
            # 获取用户进度
            user_progress = await self._get_user_progress(user_id, guild_id)
            if not user_progress:
                return await interaction.followup.send(
                    "你还没有通过任何道馆的考核。",
                    ephemeral=True
                )

            # 获取已完成的道馆信息
            completed_gyms = await self._get_completed_gyms(guild_id, list(user_progress.keys()))

            if not completed_gyms:
                return await interaction.followup.send(
                    "你还没有通过任何道馆的考核。",
                    ephemeral=True
                )

            # 导入视图
            from views.badge_views import BadgeView
            view = BadgeView(interaction.user, completed_gyms)

            await interaction.followup.send(
                embed=await view.create_embed(),
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in my_badges command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 获取徽章墙时发生错误。",
                ephemeral=True
            )

    @app_commands.command(name="我的进度", description="查看你的道馆挑战进度")
    async def my_progress(self, interaction: discord.Interaction):
        """查看进度"""
        await interaction.response.defer(ephemeral=True, thinking=True)

        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)

        try:
            # 获取用户进度
            user_progress = await self._get_user_progress(user_id, guild_id)

            # 获取所有道馆
            all_gyms = await self._get_all_gyms(guild_id)

            # 统计进度
            total_gyms = len(all_gyms)
            completed_gyms = len(user_progress)

            # 创建Embed
            embed = discord.Embed(
                title="📊 我的道馆挑战进度",
                description=f"你好，{interaction.user.mention}！",
                color=discord.Color.blue()
            )

            # 添加进度信息
            progress_str = format_user_progress(completed_gyms, total_gyms)
            embed.add_field(
                name="总体进度",
                value=progress_str,
                inline=False
            )

            # 添加失败状态
            failure_info = await self._get_failure_summary(user_id, guild_id)
            if failure_info:
                embed.add_field(
                    name="挑战失败记录",
                    value=failure_info,
                    inline=False
                )

            # 添加究极道馆成绩
            ultimate_score = await self._get_ultimate_score(user_id, guild_id)
            if ultimate_score:
                embed.add_field(
                    name="究极道馆最佳成绩",
                    value=ultimate_score,
                    inline=False
                )

            embed.set_footer(text="继续努力，挑战更多道馆！")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in my_progress command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 获取进度时发生错误。",
                ephemeral=True
            )

    # ========== 管理命令 ==========

    @app_commands.command(name="设置进度", description="设置用户直接通过道馆（管理员）")
    @app_commands.describe(
        user="要设置的用户",
        scope="选择操作范围",
        gym_ids="道馆ID（选择'指定道馆'时必填，多个ID用逗号分隔，例如: gym1,gym2）"
    )
    @app_commands.choices(scope=[
        app_commands.Choice(name="全部道馆", value="all"),
        app_commands.Choice(name="指定道馆 (支持批量)", value="specific")
    ])
    async def set_progress(self, interaction: discord.Interaction,
                         user: discord.Member, scope: str,
                         gym_ids: Optional[str] = None):
        """设置用户进度"""
        # 权限检查
        if not await has_gym_permission(interaction, "设置进度"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild_id = str(interaction.guild.id)
        user_id = str(user.id)

        try:
            target_gym_ids = []

            # 确定要操作的道馆ID列表
            if scope == "all":
                # 获取所有启用的道馆
                all_gyms = await self._get_all_gyms(guild_id)
                target_gym_ids = [gym['id'] for gym in all_gyms]
                if not target_gym_ids:
                    return await interaction.followup.send(
                        "❌本服务器还没有创建任何道馆。",
                        ephemeral=True
                    )
            else:
                # 解析输入的道馆ID
                if not gym_ids:
                    return await interaction.followup.send(
                        "❌ 操作失败：选择'指定道馆'时，必须输入道馆ID。",
                        ephemeral=True
                    )

                # 处理逗号分隔的批量输入
                input_ids = [pid.strip() for pid in gym_ids.split(',') if pid.strip()]

                # 验证道馆是否存在
                async with self.db.get_connection() as conn:
                    placeholders = ','.join('?' for _ in input_ids)
                    async with conn.execute(
                        f"SELECT gym_id FROM gyms WHERE guild_id = ? AND gym_id IN ({placeholders})",
                        [guild_id] + input_ids
                    ) as cursor:
                        found_rows = await cursor.fetchall()
                        target_gym_ids = [row[0] for row in found_rows]

                # 检查是否有无效ID
                if len(target_gym_ids) != len(input_ids):
                    found_set = set(target_gym_ids)
                    invalid_ids = [pid for pid in input_ids if pid not in found_set]
                    if invalid_ids:
                        return await interaction.followup.send(
                            f"❌ 操作失败：以下道馆ID不存在: {', '.join(invalid_ids)}",
                            ephemeral=True
                        )

            if not target_gym_ids:
                 return await interaction.followup.send(
                    "❌ 没有找到可操作的道馆。",
                    ephemeral=True
                )

            # 执行数据库更新
            async with self.db.get_connection() as conn:
                # 1. 插入进度记录
                insert_data = [(user_id, guild_id, gid) for gid in target_gym_ids]
                await conn.executemany(
                    "INSERT OR IGNORE INTO user_progress (user_id, guild_id, gym_id) VALUES (?, ?, ?)",
                    insert_data
                )

                # 2. 清除这些道馆的失败/冷却记录
                placeholders = ','.join('?' for _ in target_gym_ids)
                await conn.execute(
                    f"DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ? AND gym_id IN ({placeholders})",
                    [user_id, guild_id] + target_gym_ids
                )

                await conn.commit()

            # 记录日志
            logger.info(f"Admin {interaction.user.id} set progress for user {user_id}: {target_gym_ids}")

            # 构建响应消息
            if scope == "all":
                msg = f"✅ 已成功将用户 {user.mention} 的进度设置为 **全道馆通关** ({len(target_gym_ids)}个)。"
            else:
                gym_list_str = ", ".join(target_gym_ids)
                if len(gym_list_str) > 50:
                    gym_list_str = f"{len(target_gym_ids)}个道馆"
                msg = f"✅ 已成功设置用户 {user.mention} 通过以下道馆: {gym_list_str}"

            await interaction.followup.send(msg, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in set_progress command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 设置进度时发生错误。",
                ephemeral=True
            )

    @app_commands.command(name="重置进度", description="重置用户的道馆进度（管理员）")
    @app_commands.describe(
        user="要重置进度的用户",
        scope="选择要重置的数据范围",
        gym_id="[如果重置特定道馆] 请输入道馆ID"
    )
    @app_commands.choices(scope=[
        app_commands.Choice(name="全部进度 (不可恢复)", value="all"),
        app_commands.Choice(name="仅究极道馆进度", value="ultimate"),
        app_commands.Choice(name="仅特定道馆进度", value="specific_gym")
    ])
    async def reset_progress(self, interaction: discord.Interaction,
                           user: discord.Member, scope: str,
                           gym_id: Optional[str] = None):
        """重置用户进度"""
        # 权限检查
        if not await has_gym_permission(interaction, "重置进度"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild_id = str(interaction.guild.id)
        user_id = str(user.id)

        # 验证输入
        if scope == "specific_gym":
            if not gym_id:
                return await interaction.followup.send(
                    "❌ 操作失败：选择'仅特定道馆进度'时，必须提供道馆ID。",
                    ephemeral=True
                )

            # 检查道馆是否存在
            gym_exists = await self._check_gym_exists(guild_id, gym_id)
            if not gym_exists:
                return await interaction.followup.send(
                    f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。",
                    ephemeral=True
                )

        try:
            if scope == "all":
                await self._fully_reset_user_progress(user_id, guild_id)
                await interaction.followup.send(
                    f"✔️ 已成功重置用户 {user.mention} 的**所有**道馆挑战进度、失败记录和身份组领取记录。",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.id} fully reset progress for user {user_id}")

            elif scope == "ultimate":
                await self._reset_ultimate_progress(user_id, guild_id)
                await interaction.followup.send(
                    f"✔️ 已成功重置用户 {user.mention} 的**究极道馆**排行榜进度。",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.id} reset ultimate progress for user {user_id}")

            elif scope == "specific_gym":
                await self._reset_specific_gym_progress(user_id, guild_id, gym_id)
                await interaction.followup.send(
                    f"✔️ 已成功重置用户 {user.mention} 在道馆 `{gym_id}` 的进度和失败记录。",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.id} reset gym {gym_id} progress for user {user_id}")

        except Exception as e:
            logger.error(f"Error in reset_progress command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 重置进度时发生错误。",
                ephemeral=True
            )

    @app_commands.command(name="解除处罚", description="解除用户在特定道馆的挑战冷却（管理员）")
    @app_commands.describe(
        user="要解除处罚的用户",
        gym_id="要解除处罚的道馆ID"
    )
    async def pardon_user(self, interaction: discord.Interaction,
                         user: discord.Member, gym_id: str):
        """解除处罚"""
        # 权限检查
        if not await has_gym_permission(interaction, "解除处罚"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild_id = str(interaction.guild.id)
        user_id = str(user.id)

        # 检查道馆是否存在
        gym_exists = await self._check_gym_exists(guild_id, gym_id)
        if not gym_exists:
            return await interaction.followup.send(
                f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。",
                ephemeral=True
            )

        try:
            await self._reset_user_failures(user_id, guild_id, gym_id)

            await interaction.followup.send(
                f"✅ 已成功解除用户 {user.mention} 在道馆 `{gym_id}` 的挑战处罚。",
                ephemeral=True
            )

            logger.info(f"Admin {interaction.user.id} pardoned user {user_id} for gym {gym_id}")

        except Exception as e:
            logger.error(f"Error in pardon_user command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 解除处罚时发生错误。",
                ephemeral=True
            )

    # ========== 查询他人进度命令 ==========

    @app_commands.command(name="查询道馆进度", description="查询指定用户的道馆挑战进度（需要权限）")
    @app_commands.describe(
        user="要查询的目标用户"
    )
    async def query_user_progress(self, interaction: discord.Interaction, user: discord.Member):
        """查询他人道馆进度"""
        # 权限检查
        if not await has_gym_permission(interaction, "查询道馆进度"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._query_and_display_progress(interaction, user)

    async def _query_and_display_progress(self, interaction: discord.Interaction, user: discord.Member):
        """查询并展示用户道馆进度（内部方法）"""
        user_id = str(user.id)
        guild_id = str(interaction.guild.id)

        try:
            # 获取用户进度
            user_progress = await self._get_user_progress(user_id, guild_id)

            # 获取所有道馆
            all_gyms = await self._get_all_gyms(guild_id)

            # 检查是否有归档记录
            archive_info = await self._get_archive_info(user_id, guild_id)

            # 区分已完成和未完成
            completed_gym_ids = set(user_progress.keys())
            completed_gyms = []
            incomplete_gyms = []

            for gym in all_gyms:
                if gym['id'] in completed_gym_ids:
                    completed_gyms.append(gym)
                else:
                    incomplete_gyms.append(gym)

            # 创建Embed
            embed = discord.Embed(
                title="📊 用户道馆挑战进度",
                description=f"目标用户: {user.mention}",
                color=discord.Color.orange() if archive_info else discord.Color.blue()
            )

            # 如果有归档记录，显示警示标记
            if archive_info:
                embed.add_field(
                    name="⚠️ 处罚记录警示",
                    value=archive_info['warning_text'],
                    inline=False
                )

            # 添加头像
            embed.set_thumbnail(url=user.display_avatar.url)

            # 已通过道馆
            if completed_gyms:
                completed_text = "\n".join([f"• {g['name']}" for g in completed_gyms[:15]])
                if len(completed_gyms) > 15:
                    completed_text += f"\n... 还有 {len(completed_gyms) - 15} 个"
                embed.add_field(
                    name=f"✅ 已通过道馆 ({len(completed_gyms)}个)",
                    value=completed_text,
                    inline=False
                )
            else:
                embed.add_field(
                    name="✅ 已通过道馆 (0个)",
                    value="*暂无*",
                    inline=False
                )

            # 未通过道馆
            if incomplete_gyms:
                incomplete_text = "\n".join([f"• {g['name']}" for g in incomplete_gyms[:15]])
                if len(incomplete_gyms) > 15:
                    incomplete_text += f"\n... 还有 {len(incomplete_gyms) - 15} 个"
                embed.add_field(
                    name=f"❌ 未通过道馆 ({len(incomplete_gyms)}个)",
                    value=incomplete_text,
                    inline=False
                )
            else:
                embed.add_field(
                    name="❌ 未通过道馆 (0个)",
                    value="*全部通过！*",
                    inline=False
                )

            # 挑战冷却状态
            cooldown_info = await self._get_failure_summary(user_id, guild_id)
            if cooldown_info:
                embed.add_field(
                    name="⏳ 挑战冷却状态",
                    value=cooldown_info,
                    inline=False
                )

            # 究极道馆成绩
            ultimate_score = await self._get_ultimate_score(user_id, guild_id)
            if ultimate_score:
                embed.add_field(
                    name="🏆 究极道馆成绩",
                    value=ultimate_score,
                    inline=False
                )
            else:
                embed.add_field(
                    name="🏆 究极道馆成绩",
                    value="*暂无记录*",
                    inline=False
                )

            # 如果有归档记录，显示历史数据
            if archive_info and archive_info['archives']:
                history_text = await self._format_archive_history(
                    interaction.guild, archive_info['archives']
                )
                embed.add_field(
                    name="📜 被清空前的历史记录",
                    value=history_text,
                    inline=False
                )

            # 添加总体进度
            total = len(all_gyms)
            completed = len(completed_gyms)
            if total > 0:
                percentage = (completed / total) * 100
                progress_bar = self._create_progress_bar(percentage)
                embed.set_footer(text=f"总体进度: {completed}/{total} ({percentage:.1f}%) {progress_bar}")
            else:
                embed.set_footer(text="服务器暂无道馆")

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"User {interaction.user.id} queried progress for user {user_id}")

        except Exception as e:
            logger.error(f"Error in _query_and_display_progress: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 查询进度时发生错误。",
                ephemeral=True
            )

    def _create_progress_bar(self, percentage: float, length: int = 10) -> str:
        """创建进度条"""
        filled = int(length * percentage / 100)
        empty = length - filled
        return "█" * filled + "░" * empty

    # ========== 辅助方法 ==========

    async def show_badge_wall(self, interaction: discord.Interaction):
        """显示徽章墙（供面板调用）"""
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)

        try:
            # 获取用户进度
            user_progress = await self._get_user_progress(user_id, guild_id)
            if not user_progress:
                return await interaction.followup.send(
                    "你还没有通过任何道馆的考核。",
                    ephemeral=True
                )

            # 获取已完成的道馆信息
            completed_gyms = await self._get_completed_gyms(guild_id, list(user_progress.keys()))

            if not completed_gyms:
                return await interaction.followup.send(
                    "你还没有通过任何道馆的考核。",
                    ephemeral=True
                )

            # 导入视图
            from views.badge_views import BadgeView
            view = BadgeView(interaction.user, completed_gyms)

            await interaction.followup.send(
                embed=await view.create_embed(),
                view=view,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in show_badge_wall: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 获取徽章墙时发生错误。",
                ephemeral=True
            )

    async def _get_user_progress(self, user_id: str, guild_id: str) -> dict:
        """获取用户进度"""
        async with self.db.get_connection() as conn:
            async with conn.execute(
                "SELECT gym_id FROM user_progress WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            ) as cursor:
                rows = await cursor.fetchall()
        return {row[0]: True for row in rows}

    async def _get_completed_gyms(self, guild_id: str, gym_ids: List[str]) -> list:
        """获取已完成的道馆信息"""
        if not gym_ids:
            return []

        async with self.db.get_connection() as conn:
            placeholders = ','.join('?' for _ in gym_ids)
            query = f'''
                SELECT gym_id, name, badge_image_url, badge_description
                FROM gyms
                WHERE guild_id = ? AND gym_id IN ({placeholders})
            '''
            params = [guild_id] + gym_ids
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()

        return [{
            'id': row[0],
            'name': row[1],
            'badge_image_url': row[2],
            'badge_description': row[3]
        } for row in rows]

    async def _get_all_gyms(self, guild_id: str) -> list:
        """获取所有道馆"""
        async with self.db.get_connection() as conn:
            async with conn.execute(
                "SELECT gym_id, name FROM gyms WHERE guild_id = ?",
                (guild_id,)
            ) as cursor:
                rows = await cursor.fetchall()
        return [{'id': row[0], 'name': row[1]} for row in rows]

    async def _get_failure_summary(self, user_id: str, guild_id: str) -> Optional[str]:
        """获取失败记录摘要"""
        async with self.db.get_connection() as conn:
            async with conn.execute('''
                SELECT g.name, cf.failure_count, cf.banned_until
                FROM challenge_failures cf
                JOIN gyms g ON cf.gym_id = g.gym_id AND cf.guild_id = g.guild_id
                WHERE cf.user_id = ? AND cf.guild_id = ?
                ORDER BY cf.failure_count DESC
                LIMIT 5
            ''', (user_id, guild_id)) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            return None

        lines = []
        now = get_beijing_now()
        for name, count, banned_until in rows:
            status = f"失败 {count} 次"
            if banned_until:
                banned_dt = parse_beijing_time(banned_until)
                remaining = remaining_until(banned_dt, now)
                if remaining:
                    status += f" (冷却中，剩余 {format_timedelta(remaining)})"
                    status += f"\n   解封时间（北京时间）: `{format_beijing_display(banned_dt)}`"
                else:
                    status += " (冷却已解除)"
            lines.append(f"• **{name}**: {status}")

        return "\n".join(lines)

    async def _get_ultimate_score(self, user_id: str, guild_id: str) -> Optional[str]:
        """获取究极道馆成绩"""
        async with self.db.get_connection() as conn:
            async with conn.execute('''
                SELECT completion_time_seconds, timestamp
                FROM ultimate_gym_leaderboard
                WHERE user_id = ? AND guild_id = ?
            ''', (user_id, guild_id)) as cursor:
                row = await cursor.fetchone()

        if not row:
            return None

        time_seconds = row[0]
        minutes, seconds = divmod(int(time_seconds), 60)
        time_str = f"{minutes}分 {seconds}秒"

        # 获取排名
        async with self.db.get_connection() as conn:
            async with conn.execute('''
                SELECT COUNT(*) + 1
                FROM ultimate_gym_leaderboard
                WHERE guild_id = ? AND completion_time_seconds < ?
            ''', (guild_id, time_seconds)) as cursor:
                rank_row = await cursor.fetchone()

        rank = rank_row[0] if rank_row else 1

        return f"⏱️ **{time_str}** (排名: 第 {rank} 位)"

    async def _check_gym_exists(self, guild_id: str, gym_id: str) -> bool:
        """检查道馆是否存在"""
        async with self.db.get_connection() as conn:
            async with conn.execute(
                "SELECT 1 FROM gyms WHERE guild_id = ? AND gym_id = ?",
                (guild_id, gym_id)
            ) as cursor:
                row = await cursor.fetchone()
        return row is not None

    async def _fully_reset_user_progress(self, user_id: str, guild_id: str):
        """完全重置用户进度"""
        async with self.db.get_connection() as conn:
            # 重置道馆完成记录
            await conn.execute(
                "DELETE FROM user_progress WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )

            # 重置失败记录
            await conn.execute(
                "DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )

            # 重置已领取奖励
            await conn.execute(
                "DELETE FROM claimed_role_rewards WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )

            # 重置究极道馆排行榜
            await conn.execute(
                "DELETE FROM ultimate_gym_leaderboard WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )

            await conn.commit()

    async def _reset_ultimate_progress(self, user_id: str, guild_id: str):
        """重置究极道馆进度"""
        async with self.db.get_connection() as conn:
            await conn.execute(
                "DELETE FROM ultimate_gym_leaderboard WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )
            await conn.commit()

    async def _reset_specific_gym_progress(self, user_id: str, guild_id: str, gym_id: str):
        """重置特定道馆进度"""
        async with self.db.get_connection() as conn:
            # 删除完成记录
            await conn.execute(
                "DELETE FROM user_progress WHERE user_id = ? AND guild_id = ? AND gym_id = ?",
                (user_id, guild_id, gym_id)
            )

            # 删除失败记录
            await conn.execute(
                "DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ? AND gym_id = ?",
                (user_id, guild_id, gym_id)
            )

            await conn.commit()

    async def _reset_user_failures(self, user_id: str, guild_id: str, gym_id: str):
        """重置用户失败记录"""
        async with self.db.get_connection() as conn:
            await conn.execute(
                "DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ? AND gym_id = ?",
                (user_id, guild_id, gym_id)
            )
            await conn.commit()

    async def _get_archive_info(self, user_id: str, guild_id: str) -> Optional[dict]:
        """获取用户的归档信息"""
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute('''
                SELECT archive_id, archive_reason, source_info,
                       completed_gyms, ultimate_score, failure_records, archived_at
                FROM progress_archive
                WHERE user_id = ? AND guild_id = ?
                ORDER BY archived_at DESC
                LIMIT 5
            ''', (user_id, guild_id)) as cursor:
                archives = await cursor.fetchall()

        if not archives:
            return None

        # 生成警示文本
        latest = archives[0]
        reason_map = {
            'cross_bot_punishment': '跨Bot联动处罚',
            'admin_reset': '管理员手动重置',
            'manual': '手动归档'
        }
        reason_text = reason_map.get(latest['archive_reason'], latest['archive_reason'])

        # 格式化归档时间
        try:
            archived_dt = parse_beijing_time(latest['archived_at'])
            time_str = format_beijing_display(archived_dt)
        except Exception:
            time_str = latest['archived_at'][:19] if latest['archived_at'] else "未知时间"

        warning_text = (
            f"⚠️ **此用户曾因 [{reason_text}] 被清空道馆记录**\n"
            f"最近一次归档时间: {time_str}\n"
        )
        if latest['source_info']:
            warning_text += f"来源: {latest['source_info']}\n"

        warning_text += f"\n共有 **{len(archives)}** 条归档记录"

        return {
            'warning_text': warning_text,
            'archives': [dict(a) if hasattr(a, 'keys') else a for a in archives]
        }

    async def _format_archive_history(self, guild: discord.Guild, archives: List[dict]) -> str:
        """格式化归档历史记录"""
        lines = []
        for i, archive in enumerate(archives[:3], 1):  # 最多显示3条
            completed_gyms_data = json.loads(archive['completed_gyms'] or '[]')
            ultimate_score = archive['ultimate_score']
            archived_at = archive['archived_at']

            # 获取道馆名称
            gym_names = []
            if completed_gyms_data:
                # 检查数据格式：旧格式是ID列表，新格式是字典列表[{'id':..., 'name':...}]
                if isinstance(completed_gyms_data[0], str):
                    # 旧格式：只有ID，需要查询当前数据库（如果道馆被删，名字就查不到了）
                    gym_ids = completed_gyms_data
                    async with self.db.get_connection() as conn:
                        placeholders = ','.join('?' for _ in gym_ids)
                        async with conn.execute(
                            f"SELECT gym_id, name FROM gyms WHERE guild_id = ? AND gym_id IN ({placeholders})",
                            [str(guild.id)] + gym_ids
                        ) as cursor:
                            rows = await cursor.fetchall()
                            gym_names = [row[1] for row in rows]
                elif isinstance(completed_gyms_data[0], dict):
                    # 新格式：包含名字快照，直接使用（即使道馆已删也能显示名字）
                    gym_names = [item.get('name', '未知道馆') for item in completed_gyms_data]

            # 格式化时间
            try:
                dt = parse_beijing_time(archived_at)
                time_str = format_beijing_display(dt)
            except Exception:
                time_str = archived_at[:19] if archived_at else "未知时间"

            line = f"**[{i}] {time_str}**\n"
            if gym_names:
                gyms_str = ", ".join(gym_names)
                line += f"  • 已通过: {gyms_str}\n"
            else:
                line += "  • 已通过: 无\n"

            if ultimate_score:
                minutes, seconds = divmod(int(ultimate_score), 60)
                line += f"  • 究极成绩: {minutes}分{seconds}秒\n"

            lines.append(line)

        return "\n".join(lines) if lines else "*无历史记录*"


# 模块级别的 context menu 命令（右键命令不能定义在类内部）
@app_commands.context_menu(name="查询道馆进度")
async def query_progress_context_menu(interaction: discord.Interaction, user: discord.Member):
    """右键用户查询道馆进度"""
    # 权限检查
    if not await has_gym_permission(interaction, "查询道馆进度"):
        return await interaction.response.send_message(
            "❌ 你没有执行此指令所需的权限。",
            ephemeral=True
        )

    await interaction.response.defer(ephemeral=True, thinking=True)

    # 获取 UserProgressCog 实例来调用内部方法
    cog = interaction.client.get_cog('UserProgressCog')
    if cog:
        await cog._query_and_display_progress(interaction, user)
    else:
        await interaction.followup.send(
            "❌ 进度系统暂时不可用。",
            ephemeral=True
        )


@app_commands.context_menu(name="查询发送者进度")
async def query_message_author_progress(interaction: discord.Interaction, message: discord.Message):
    """右键消息查询发送者的道馆进度"""
    # 权限检查
    if not await has_gym_permission(interaction, "查询道馆进度"):
        return await interaction.response.send_message(
            "❌ 你没有执行此指令所需的权限。",
            ephemeral=True
        )

    # 获取消息发送者
    author = message.author

    # 检查是否为机器人
    if author.bot:
        return await interaction.response.send_message(
            "❌ 无法查询机器人的道馆进度。",
            ephemeral=True
        )

    # 先defer，因为后续的fetch_member可能需要时间
    await interaction.response.defer(ephemeral=True, thinking=True)

    # 检查是否为服务器成员
    if not isinstance(author, discord.Member):
        # 尝试从缓存获取成员对象
        member = interaction.guild.get_member(author.id)
        if not member:
            # 缓存中没有，尝试从API获取
            try:
                member = await interaction.guild.fetch_member(author.id)
            except discord.NotFound:
                return await interaction.followup.send(
                    "❌ 该用户不在此服务器中，无法查询其进度。",
                    ephemeral=True
                )
            except discord.HTTPException:
                return await interaction.followup.send(
                    "❌ 获取用户信息时发生错误，请稍后重试。",
                    ephemeral=True
                )
        author = member

    # 获取 UserProgressCog 实例来调用内部方法
    cog = interaction.client.get_cog('UserProgressCog')
    if cog:
        await cog._query_and_display_progress(interaction, author)
    else:
        await interaction.followup.send(
            "❌ 进度系统暂时不可用。",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(UserProgressCog(bot))
    # 添加右键命令到命令树
    bot.tree.add_command(query_progress_context_menu)
    bot.tree.add_command(query_message_author_progress)