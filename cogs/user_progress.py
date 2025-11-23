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


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(UserProgressCog(bot))