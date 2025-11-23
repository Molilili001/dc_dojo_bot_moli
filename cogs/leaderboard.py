# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
from discord import app_commands
import datetime
import logging
from typing import Optional, List, Dict

from .base_cog import BaseCog
from core.database import DatabaseManager, get_legacy_db_path
from core.models import UltimateLeaderboardEntry, LeaderboardPanel
from core.constants import BEIJING_TZ
from utils.formatters import FormatUtils
from utils.logger import get_logger

logger = get_logger(__name__)


class LeaderboardView(discord.ui.View):
    """排行榜交互视图"""
    
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(
        label="查询我的排名",
        style=discord.ButtonStyle.primary,
        custom_id="leaderboard:show_my_rank"
    )
    async def show_my_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        """显示用户的排名"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        try:
            cog = interaction.client.get_cog('LeaderboardCog')
            if not cog:
                await interaction.followup.send("❌ 排行榜系统暂时不可用。", ephemeral=True)
                return
            
            guild_id = str(interaction.guild.id)
            user_id = str(interaction.user.id)
            
            rank_data = await cog.get_user_rank(guild_id, user_id)
            
            if rank_data:
                rank = rank_data['rank']
                score = rank_data['completion_time_seconds']
                minutes, seconds = divmod(int(score), 60)
                
                embed = discord.Embed(
                    title="📈 我的究极道馆排名",
                    description=f"你好，{interaction.user.mention}！\n你在 **{interaction.guild.name}** 的排名信息如下：",
                    color=discord.Color.blue()
                )
                embed.add_field(name="当前排名", value=f"**第 {rank} 名**", inline=True)
                embed.add_field(name="最佳成绩", value=f"**{minutes}分 {seconds}秒**", inline=True)
                embed.set_footer(text="继续挑战，刷新你的记录！")
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(
                    title="📜 暂无排名记录",
                    description=f"你好，{interaction.user.mention}！\n我们尚未在 **{interaction.guild.name}** 的究极道馆排行榜上找到你的记录。",
                    color=discord.Color.orange()
                )
                embed.set_footer(text="快去参加究极道馆挑战，榜上留名吧！")
                await interaction.followup.send(embed=embed, ephemeral=True)
                
        except Exception as e:
            logger.error(f"Error in show_my_rank button: {e}", exc_info=True)
            await interaction.followup.send("❌ 查询你的排名时发生错误，请稍后再试或联系管理员。", ephemeral=True)


class LeaderboardCog(BaseCog):
    """
    排行榜模块
    负责究极道馆排行榜的管理和展示
    """
    
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.db = DatabaseManager()
        
    async def cog_load(self):
        """Cog加载时的初始化"""
        # 注册持久视图
        self.bot.add_view(LeaderboardView())
        logger.info("LeaderboardCog loaded and views registered")
    
    async def get_leaderboard(self, guild_id: str, limit: int = 100) -> List[Dict]:
        """
        获取究极道馆排行榜（支持与旧库数据互通：合并新库与旧库的最佳成绩）
        
        Args:
            guild_id: 服务器ID
            limit: 获取的最大数量
            
        Returns:
            排行榜数据列表（按完成时间升序合并去重）
        """
        # 读取新库数据
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                """SELECT user_id, completion_time_seconds, timestamp
                   FROM ultimate_gym_leaderboard
                   WHERE guild_id = ?""",
                (guild_id,)
            ) as cursor:
                new_rows = await cursor.fetchall()
        merged: Dict[str, Dict] = {str(r['user_id']): dict(r) for r in new_rows}

        # 可选：读取旧库并合并（以更优成绩为准）
        try:
            legacy_path = get_legacy_db_path()
            if legacy_path:
                legacy_db = DatabaseManager(db_path=legacy_path)
                async with legacy_db.get_connection() as lconn:
                    lconn.row_factory = legacy_db.dict_row
                    async with lconn.execute(
                        """SELECT user_id, completion_time_seconds, timestamp
                           FROM ultimate_gym_leaderboard
                           WHERE guild_id = ?""",
                        (guild_id,)
                    ) as cursor:
                        legacy_rows = await cursor.fetchall()
                for lr in legacy_rows:
                    uid = str(lr['user_id'])
                    # 若新库无记录或旧库更优（更小的时间），以旧库为准
                    if (uid not in merged) or (lr['completion_time_seconds'] < merged[uid]['completion_time_seconds']):
                        merged[uid] = dict(lr)
        except Exception as e:
            logger.warning(f"读取旧库排行榜失败或未配置，将仅使用新库：{e}")

        # 转换为列表并排序、截取
        result = list(merged.values())
        result.sort(key=lambda x: x['completion_time_seconds'])
        return result[:limit]
    
    async def update_leaderboard(self, guild_id: str, user_id: str, time_seconds: float):
        """
        更新用户的排行榜成绩
        只有在新成绩更好时才更新
        
        Args:
            guild_id: 服务器ID
            user_id: 用户ID
            time_seconds: 完成时间（秒）
        """
        async with self.db.get_connection() as conn:
            # 获取用户当前最佳成绩
            async with conn.execute(
                "SELECT completion_time_seconds FROM ultimate_gym_leaderboard WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            ) as cursor:
                current_best = await cursor.fetchone()
            
            # 如果有旧成绩且新成绩不更好，不更新
            if current_best and time_seconds >= current_best[0]:
                return
            
            # 插入或更新成绩
            timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
            await conn.execute(
                """INSERT INTO ultimate_gym_leaderboard (guild_id, user_id, completion_time_seconds, timestamp)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(guild_id, user_id) DO UPDATE SET
                   completion_time_seconds = excluded.completion_time_seconds,
                   timestamp = excluded.timestamp""",
                (guild_id, user_id, time_seconds, timestamp)
            )
            await conn.commit()
            
            logger.info(f"Updated leaderboard for user {user_id} in guild {guild_id}: {time_seconds}s")
            
            # 触发排行榜面板更新
            await self.trigger_leaderboard_update(int(guild_id))
    
    async def get_user_rank(self, guild_id: str, user_id: str) -> Optional[Dict]:
        """
        获取用户在排行榜上的排名（新库+旧库合并后的排名）
        
        Args:
            guild_id: 服务器ID
            user_id: 用户ID
            
        Returns:
            包含排名和成绩的字典，如果用户不在榜上则返回None
        """
        # 获取合并后的榜单（使用较大上限避免漏数据）
        leaderboard = await self.get_leaderboard(guild_id, limit=1000)
        if not leaderboard:
            return None

        # 排序已在 get_leaderboard 中完成（按完成时间升序）
        # 计算目标用户的排名（1-based）
        rank = None
        best_time = None
        for idx, entry in enumerate(leaderboard, start=1):
            if str(entry['user_id']) == str(user_id):
                rank = idx
                best_time = entry['completion_time_seconds']
                break

        if rank is None:
            return None
        return {'rank': rank, 'completion_time_seconds': best_time}
    
    async def create_leaderboard_embed(
        self, 
        guild: discord.Guild, 
        custom_title: Optional[str] = None,
        custom_description: Optional[str] = None
    ) -> discord.Embed:
        """
        创建排行榜嵌入消息
        
        Args:
            guild: Discord服务器对象
            custom_title: 自定义标题
            custom_description: 自定义描述
            
        Returns:
            Discord嵌入消息
        """
        leaderboard_data = await self.get_leaderboard(str(guild.id), limit=20)
        
        # 使用自定义文本或默认文本
        title = custom_title if custom_title else f"🏆 {guild.name} - 究极道馆排行榜 🏆"
        description = custom_description.replace('\\n', '\n') if custom_description else "记录着本服最快完成究极道馆挑战的英雄们。"
        
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.gold()
        )
        
        if not leaderboard_data:
            embed.description += "\n\n目前还没有人完成挑战，快来成为第一人吧！"
        else:
            lines = []
            for i, entry in enumerate(leaderboard_data):
                rank = i + 1
                user_id = int(entry['user_id'])
                time_seconds = entry['completion_time_seconds']
                
                # 格式化时间
                minutes, seconds = divmod(time_seconds, 60)
                time_str = f"{int(minutes)}分 {seconds:.2f}秒"
                
                # 尝试获取用户信息
                member = guild.get_member(user_id)
                if not member:
                    try:
                        member = await guild.fetch_member(user_id)
                    except discord.NotFound:
                        member = None
                
                user_display = member.display_name if member else f"未知用户 (ID: {user_id})"
                
                # 添加排名表情
                if rank == 1:
                    rank_emoji = "🥇"
                elif rank == 2:
                    rank_emoji = "🥈"
                elif rank == 3:
                    rank_emoji = "🥉"
                else:
                    rank_emoji = f"`#{rank:02d}`"
                
                lines.append(f"{rank_emoji} **{user_display}** - `{time_str}`")
            
            embed.description += "\n\n" + "\n".join(lines)
        
        embed.set_footer(text=f"最后更新于: {datetime.datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
        return embed
    
    async def trigger_leaderboard_update(self, guild_id: int):
        """
        触发指定服务器的所有排行榜面板更新
        
        Args:
            guild_id: 服务器ID
        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.warning(f"Cannot find guild with ID {guild_id} to trigger leaderboard update")
            return
        
        logger.info(f"Triggering leaderboard update for guild '{guild.name}' ({guild_id})")
        
        # 获取该服务器的所有排行榜面板
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                "SELECT message_id, channel_id, title, description FROM leaderboard_panels WHERE guild_id = ?",
                (str(guild_id),)
            ) as cursor:
                panels = await cursor.fetchall()
        
        if not panels:
            logger.info(f"No leaderboard panels found for guild {guild_id}")
            return
        
        # 更新每个面板
        updated_count = 0
        for panel in panels:
            try:
                # 创建特定面板的嵌入消息
                new_embed = await self.create_leaderboard_embed(
                    guild, 
                    panel['title'], 
                    panel['description']
                )
                
                channel = guild.get_channel(int(panel['channel_id']))
                if not channel:
                    try:
                        channel = await self.bot.fetch_channel(int(panel['channel_id']))
                    except (discord.NotFound, discord.Forbidden):
                        channel = None
                
                if channel:
                    message = await channel.fetch_message(int(panel['message_id']))
                    await message.edit(embed=new_embed)
                    updated_count += 1
                else:
                    logger.warning(f"Channel {panel['channel_id']} not found. Deleting panel record from DB.")
                    async with self.db.get_connection() as conn:
                        await conn.execute(
                            "DELETE FROM leaderboard_panels WHERE message_id = ?",
                            (panel['message_id'],)
                        )
                        await conn.commit()
                        
            except discord.NotFound:
                logger.warning(f"Message {panel['message_id']} not found. Deleting panel record from DB.")
                async with self.db.get_connection() as conn:
                    await conn.execute(
                        "DELETE FROM leaderboard_panels WHERE message_id = ?",
                        (panel['message_id'],)
                    )
                    await conn.commit()
            except discord.Forbidden:
                logger.error(f"Bot lacks permission to edit message {panel['message_id']} in channel {panel['channel_id']}")
                # Attempt takeover: recreate the panel message authored by this bot and update DB record
                try:
                    # Ensure channel is available; if not, try fetching
                    if not channel:
                        try:
                            channel = await self.bot.fetch_channel(int(panel['channel_id']))
                        except (discord.NotFound, discord.Forbidden):
                            channel = None
                    if channel:
                        from cogs.leaderboard import LeaderboardView
                        new_message = await channel.send(embed=new_embed, view=LeaderboardView())
                        # Update DB to point to new message id and channel
                        async with self.db.get_connection() as conn2:
                            await conn2.execute(
                                "UPDATE leaderboard_panels SET message_id = ?, channel_id = ? WHERE message_id = ?",
                                (str(new_message.id), str(channel.id), panel['message_id'])
                            )
                            await conn2.commit()
                        updated_count += 1
                        logger.info(f"Recreated leaderboard panel in channel {channel.id} with new message {new_message.id} due to Forbidden edit of old panel {panel['message_id']}")
                    else:
                        logger.warning(f"Cannot recreate leaderboard panel because channel {panel['channel_id']} is unavailable")
                except Exception as recreate_error:
                    logger.error(f"Failed to recreate leaderboard panel for old message {panel['message_id']}: {recreate_error}", exc_info=True)
            except Exception as e:
                logger.error(f"Error updating panel {panel['message_id']}: {e}", exc_info=True)
        
        logger.info(f"Leaderboard update finished for guild {guild_id}. Updated {updated_count}/{len(panels)} panels")


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(LeaderboardCog(bot))
    logger.info("LeaderboardCog has been added to bot")