# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
from discord import app_commands
import os
import psutil
import time
import datetime
import logging
from typing import Optional

from .base_cog import BaseCog
from core.constants import BEIJING_TZ, LOG_DIR
from utils.permissions import is_owner
from utils.logger import get_logger

logger = get_logger(__name__)


class DeveloperCog(BaseCog):
    """
    开发者工具模块
    提供系统状态监控、日志管理等开发者专用功能
    """
    
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.start_time = time.time()
    
    @app_commands.command(name="状态", description="[仅限开发者] 查看服务器和机器人的当前状态或下载日志")
    @app_commands.describe(action="选择要执行的操作")
    @app_commands.choices(action=[
        app_commands.Choice(name="查看状态", value="view_status"),
        app_commands.Choice(name="下载今日日志", value="download_log"),
    ])
    async def system_status(
        self,
        interaction: discord.Interaction,
        action: str = "view_status"
    ):
        """显示系统状态或下载日志"""
        # 权限检查
        if not await is_owner(interaction):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        if action == "download_log":
            await self.download_log(interaction)
        else:
            await self.view_status(interaction)
    
    async def download_log(self, interaction: discord.Interaction):
        """下载今日日志文件"""
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # 优先返回当前活动日志文件（单一文件：discord_bot.log）
            log_dir = str(LOG_DIR)
            active_log = os.path.join(log_dir, "discord_bot.log")

            candidate_path = None
            note = None

            if os.path.exists(active_log) and os.path.getsize(active_log) > 0:
                candidate_path = active_log
                note = "这是今天的最新日志文件"
            else:
                # 回退：查找同名轮转日志（例如 discord_bot.log.2025-10-11）
                if os.path.exists(log_dir):
                    all_logs = [
                        os.path.join(log_dir, f)
                        for f in os.listdir(log_dir)
                        if f.startswith("discord_bot.log")
                    ]
                    if all_logs:
                        # 取最后修改时间最新的一个
                        all_logs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                        candidate_path = all_logs[0]
                        note = f"未找到当前活动日志，提供最近的日志文件: `{os.path.basename(candidate_path)}`"

            if candidate_path:
                await interaction.followup.send(
                    f"✅ {note}。",
                    file=discord.File(candidate_path),
                    ephemeral=True
                )
                logger.info(
                    f"Developer {interaction.user.id} downloaded log file {os.path.basename(candidate_path)}"
                )
            else:
                await interaction.followup.send(
                    "❌ 未找到任何日志文件。",
                    ephemeral=True
                )

        except Exception as e:
            logger.error(f"Error during log download: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 下载日志文件时发生错误。",
                ephemeral=True
            )
    
    async def view_status(self, interaction: discord.Interaction):
        """查看系统状态"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        try:
            # 系统信息
            cpu_usage = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory()
            ram_usage_percent = ram.percent
            ram_used_gb = ram.used / (1024**3)
            ram_total_gb = ram.total / (1024**3)
            
            try:
                disk = psutil.disk_usage(os.path.abspath(os.sep))
                disk_usage_percent = disk.percent
                disk_used_gb = disk.used / (1024**3)
                disk_total_gb = disk.total / (1024**3)
                disk_str = f"**磁盘空间:** `{disk_usage_percent}%` ({disk_used_gb:.2f} GB / {disk_total_gb:.2f} GB)"
            except FileNotFoundError:
                disk_str = "**磁盘空间:** `无法获取`"
            
            # Bot信息
            process = psutil.Process(os.getpid())
            bot_ram_usage_mb = process.memory_info().rss / (1024**2)
            
            # 运行时间
            uptime_seconds = time.time() - self.start_time
            uptime_delta = datetime.timedelta(seconds=uptime_seconds)
            days = uptime_delta.days
            hours, rem = divmod(uptime_delta.seconds, 3600)
            minutes, _ = divmod(rem, 60)
            uptime_str = f"{days}天 {hours}小时 {minutes}分钟"
            
            # 创建嵌入消息
            embed = discord.Embed(
                title="📊 服务器与机器人状态",
                color=discord.Color.blue()
            )
            embed.timestamp = datetime.datetime.now(BEIJING_TZ)
            
            embed.add_field(
                name="🖥️ 系统资源",
                value=f"**CPU 负载:** `{cpu_usage}%`\n"
                      f"**内存占用:** `{ram_usage_percent}%` ({ram_used_gb:.2f} GB / {ram_total_gb:.2f} GB)\n"
                      f"{disk_str}",
                inline=False
            )
            
            embed.add_field(
                name="🤖 机器人进程",
                value=f"**内存占用:** `{bot_ram_usage_mb:.2f} MB`\n"
                      f"**运行时间:** `{uptime_str}`\n"
                      f"**服务器数:** `{len(self.bot.guilds)}`\n"
                      f"**加载的Cog数:** `{len(self.bot.cogs)}`",
                inline=False
            )
            
            # 添加数据库统计
            await self.add_database_stats(embed)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"Developer {interaction.user.id} viewed system status")
            
        except Exception as e:
            logger.error(f"Error viewing system status: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 获取系统状态时发生错误。",
                ephemeral=True
            )
    
    async def add_database_stats(self, embed: discord.Embed):
        """添加数据库统计信息到嵌入消息"""
        try:
            from core.database import DatabaseManager
            db = DatabaseManager()
            
            stats = {}
            async with db.get_connection() as conn:
                # 统计各表的记录数
                tables = [
                    ('道馆数', 'gyms'),
                    ('用户进度', 'user_progress'),
                    ('挑战面板', 'challenge_panels'),
                    ('黑名单', 'cheating_blacklist'),
                    ('封禁列表', 'challenge_ban_list'),
                    ('排行榜记录', 'ultimate_gym_leaderboard')
                ]
                
                for name, table in tables:
                    async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                        count = await cursor.fetchone()
                        stats[name] = count[0] if count else 0
            
            stats_text = "\n".join([f"**{k}:** `{v}`" for k, v in stats.items()])
            embed.add_field(
                name="📊 数据库统计",
                value=stats_text if stats_text else "暂无数据",
                inline=False
            )
            
        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            embed.add_field(
                name="📊 数据库统计",
                value="无法获取数据库统计信息",
                inline=False
            )
    
    @app_commands.command(name="重载", description="[仅限开发者] 重新加载指定的Cog模块")
    @app_commands.describe(cog_name="要重载的Cog名称（支持中文名，不填则显示所有Cog）")
    async def reload_cog(
        self,
        interaction: discord.Interaction,
        cog_name: Optional[str] = None
    ):
        """重载Cog模块"""
        # 权限检查
        if not await is_owner(interaction):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        if not cog_name:
            # 显示所有已加载的Cog（同时显示中英文名）
            cog_list = []
            for name in self.bot.cogs.keys():
                # 获取中文名（如果有的话）
                if hasattr(self.bot, 'cog_name_mapping') and name in self.bot.cog_name_mapping:
                    chinese_name = self.bot.cog_name_mapping.get(name)
                    if not chinese_name.endswith("Cog"):  # 确保是中文名
                        cog_list.append(f"• `{chinese_name}` ({name})")
                    else:
                        cog_list.append(f"• `{name}`")
                else:
                    cog_list.append(f"• `{name}`")
            
            embed = discord.Embed(
                title="📦 已加载的Cog模块",
                description="\n".join(cog_list) if cog_list else "没有已加载的Cog",
                color=discord.Color.blue()
            )
            embed.set_footer(text="提示：可以使用中文名或英文名来重载")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # 检查是否使用中文名，并转换为英文名
        original_cog_name = cog_name
        if hasattr(self.bot, 'cog_name_mapping') and cog_name in self.bot.cog_name_mapping:
            # 如果输入的是中文名，转换为英文名
            mapped_name = self.bot.cog_name_mapping.get(cog_name)
            if mapped_name and mapped_name.endswith("Cog"):
                cog_name = mapped_name
        
        try:
            # 尝试重载Cog
            if cog_name in self.bot.cogs:
                # 获取模块路径
                cog = self.bot.get_cog(cog_name)
                module_name = cog.__module__
                
                # 使用reload_extension直接重载扩展
                await self.bot.reload_extension(module_name)
                
                # 获取显示名称（优先使用中文名）
                display_name = original_cog_name
                if hasattr(self.bot, 'cog_name_mapping') and cog_name in self.bot.cog_name_mapping:
                    chinese_name = self.bot.cog_name_mapping.get(cog_name)
                    if not chinese_name.endswith("Cog"):
                        display_name = chinese_name
                
                await interaction.followup.send(
                    f"✅ 成功重载Cog: `{display_name}`",
                    ephemeral=True
                )
                logger.info(f"Developer {interaction.user.id} reloaded cog: {cog_name} (input: {original_cog_name})")
            else:
                # 如果Cog未加载，尝试查找并加载它
                # 构建可能的模块路径
                base_name = cog_name.replace("Cog", "").lower()
                possible_paths = [
                    f"bot重构.cogs.{base_name}",
                    f"bot重構.cogs.{base_name}",
                    f"cogs.{base_name}",
                    f"bot重構.cogs.{cog_name.lower()}",
                    f"bot重构.cogs.{cog_name.lower()}",
                    f"cogs.{cog_name.lower()}"
                ]

                # 显式英文Cog名到模块路径映射，确保热重载准确匹配
                english_to_module = {
                    "GymManagementCog": "cogs.gym_management",
                    "GymChallengeCog": "cogs.gym_challenge",
                    "UserProgressCog": "cogs.user_progress",
                    "LeaderboardCog": "cogs.leaderboard",
                    "ModerationCog": "cogs.moderation",
                    "PanelsCog": "cogs.panels",
                    "AdminCog": "cogs.admin",
                    "DeveloperCog": "cogs.developer",
                    "AutoMonitorCog": "cogs.auto_monitor",
                    "CrossBotSyncCog": "cogs.cross_bot_sync",
                    "ForumPostMonitorCog": "cogs.forum_post_monitor",
                    "TodoListCog": "cogs.todo_list",
                }
                if cog_name in english_to_module:
                    possible_paths.insert(0, english_to_module[cog_name])
                
                # 添加一些特殊映射（用于显示中文名）
                special_mappings = {
                    "gym_management": "道馆管理",
                    "gym_challenge": "道馆挑战",
                    "user_progress": "用户进度",
                    "leaderboard": "排行榜",
                    "moderation": "管理功能",
                    "panels": "面板管理",
                    "admin": "管理员命令",
                    "developer": "开发者工具",
                    "auto_monitor": "自动监控",
                    "forum_post_monitor": "投诉监听",
                    "todo_list": "事件列表",
                }
                
                loaded = False
                for module_path in possible_paths:
                    try:
                        await self.bot.load_extension(module_path)
                        
                        # 获取显示名称
                        display_name = original_cog_name
                        module_base = module_path.split('.')[-1]
                        if module_base in special_mappings:
                            display_name = special_mappings[module_base]
                        
                        await interaction.followup.send(
                            f"✅ 成功加载Cog: `{display_name}` (从 `{module_path}`)",
                            ephemeral=True
                        )
                        logger.info(f"Developer {interaction.user.id} loaded cog: {cog_name} from {module_path}")
                        loaded = True
                        break
                    except:
                        continue
                
                if not loaded:
                    await interaction.followup.send(
                        f"❌ 找不到名为 `{original_cog_name}` 的Cog模块。",
                        ephemeral=True
                    )
                
        except Exception as e:
            logger.error(f"Error reloading cog {cog_name}: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 重载Cog时发生错误: {str(e)}",
                ephemeral=True
            )
    
    @app_commands.command(name="调试", description="[仅限开发者] 执行调试命令")
    @app_commands.describe(
        action="调试操作",
        target="目标（用户ID、服务器ID等）"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="清理缓存", value="clear_cache"),
        app_commands.Choice(name="查看活跃挑战", value="view_challenges"),
        app_commands.Choice(name="强制同步命令", value="sync_commands"),
    ])
    async def debug(
        self,
        interaction: discord.Interaction,
        action: str,
        target: Optional[str] = None
    ):
        """执行调试操作"""
        # 权限检查
        if not await is_owner(interaction):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        try:
            if action == "clear_cache":
                # 清理内存缓存
                challenge_cog = self.bot.get_cog('GymChallengeCog')
                if challenge_cog and hasattr(challenge_cog, 'active_challenges'):
                    count = len(challenge_cog.active_challenges)
                    challenge_cog.active_challenges.clear()
                    await interaction.followup.send(
                        f"✅ 已清理 {count} 个活跃挑战会话。",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "ℹ️ 没有找到可清理的缓存。",
                        ephemeral=True
                    )
                logger.info(f"Developer {interaction.user.id} cleared cache")
                
            elif action == "view_challenges":
                # 查看活跃挑战
                challenge_cog = self.bot.get_cog('GymChallengeCog')
                if challenge_cog and hasattr(challenge_cog, 'active_challenges'):
                    challenges = challenge_cog.active_challenges
                    if challenges:
                        lines = []
                        for user_id, session in challenges.items():
                            lines.append(f"• 用户 {user_id}: 道馆 {session.gym_id}")
                        
                        embed = discord.Embed(
                            title="🎮 活跃挑战会话",
                            description="\n".join(lines[:20]),  # 限制显示20个
                            color=discord.Color.green()
                        )
                        embed.set_footer(text=f"共 {len(challenges)} 个活跃会话")
                        await interaction.followup.send(embed=embed, ephemeral=True)
                    else:
                        await interaction.followup.send(
                            "ℹ️ 当前没有活跃的挑战会话。",
                            ephemeral=True
                        )
                else:
                    await interaction.followup.send(
                        "❌ 找不到挑战系统。",
                        ephemeral=True
                    )
                    
            elif action == "sync_commands":
                # 强制同步斜杠命令
                if target and target.isdigit():
                    # 同步到特定服务器
                    guild = discord.Object(id=int(target))
                    synced = await self.bot.tree.sync(guild=guild)
                    await interaction.followup.send(
                        f"✅ 已同步 {len(synced)} 个命令到服务器 {target}。",
                        ephemeral=True
                    )
                else:
                    # 全局同步
                    synced = await self.bot.tree.sync()
                    await interaction.followup.send(
                        f"✅ 已全局同步 {len(synced)} 个命令。",
                        ephemeral=True
                    )
                logger.info(f"Developer {interaction.user.id} synced commands")
                
        except Exception as e:
            logger.error(f"Error in debug command: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 执行调试操作时发生错误: {str(e)}",
                ephemeral=True
            )
    
    @app_commands.command(name="公告", description="[仅限开发者] 向所有服务器发送公告")
    @app_commands.describe(
        title="公告标题",
        content="公告内容",
        color="嵌入消息颜色（十六进制，如 #FF0000）"
    )
    async def announcement(
        self,
        interaction: discord.Interaction,
        title: str,
        content: str,
        color: Optional[str] = None
    ):
        """发送全局公告"""
        # 权限检查
        if not await is_owner(interaction):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # 解析颜色
        embed_color = discord.Color.blue()
        if color:
            try:
                if color.startswith('#'):
                    color = color[1:]
                embed_color = discord.Color(int(color, 16))
            except ValueError:
                await interaction.followup.send(
                    "⚠️ 无效的颜色格式，使用默认颜色。",
                    ephemeral=True
                )
        
        # 创建公告嵌入消息
        embed = discord.Embed(
            title=f"📢 {title}",
            description=content.replace('\\n', '\n'),
            color=embed_color
        )
        embed.set_footer(text="来自机器人开发者的公告")
        embed.timestamp = datetime.datetime.now(BEIJING_TZ)
        
        # 发送到所有服务器的系统频道
        success_count = 0
        fail_count = 0
        
        for guild in self.bot.guilds:
            try:
                # 优先发送到系统频道
                channel = guild.system_channel
                if not channel:
                    # 查找第一个可发送消息的文字频道
                    for ch in guild.text_channels:
                        if ch.permissions_for(guild.me).send_messages:
                            channel = ch
                            break
                
                if channel:
                    await channel.send(embed=embed)
                    success_count += 1
                else:
                    fail_count += 1
                    
            except Exception as e:
                logger.error(f"Failed to send announcement to guild {guild.id}: {e}")
                fail_count += 1
        
        await interaction.followup.send(
            f"✅ 公告发送完成！\n成功: {success_count} 个服务器\n失败: {fail_count} 个服务器",
            ephemeral=True
        )
        logger.info(f"Developer {interaction.user.id} sent announcement to {success_count} guilds")


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(DeveloperCog(bot))
    logger.info("DeveloperCog has been added to bot")