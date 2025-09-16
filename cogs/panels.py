# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
from discord import app_commands
import typing
import json
import logging
import datetime

from .base_cog import BaseCog
from core.database import DatabaseManager
from core.models import ChallengePanel
from utils.permissions import is_gym_master
from utils.logger import get_logger
from views.challenge_views import MainChallengeView
from views.panel_views import BadgePanelView, GraduationPanelView

logger = get_logger(__name__)


class PanelsCog(BaseCog):
    """
    面板管理模块
    负责创建和管理各种交互式面板
    """
    
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.db = DatabaseManager()
    
    async def cog_load(self):
        """Cog加载时注册持久视图"""
        # 注册持久化视图
        view1 = MainChallengeView()
        view2 = BadgePanelView()
        view3 = GraduationPanelView()
        
        self.bot.add_view(view1)
        self.bot.add_view(view2)
        self.bot.add_view(view3)
        
        logger.info(f"PanelsCog loaded and persistent views registered: {view1}, {view2}, {view3}")
    
    async def parse_role_mentions_or_ids(self, guild: discord.Guild, role_input_str: str) -> list[str]:
        """解析逗号分隔的身份组ID或提及"""
        if not role_input_str:
            return []
        
        role_ids = set()
        parts = [part.strip() for part in role_input_str.split(',')]
        
        for part in parts:
            if not part:
                continue
            
            # 检查提及格式 <@&ROLE_ID>
            if part.startswith('<@&') and part.endswith('>'):
                role_id = part[3:-1]
            else:
                role_id = part
            
            if not role_id.isdigit():
                raise ValueError(f"输入 '{part}' 不是一个有效的身份组ID或提及。")
            
            # 检查身份组是否存在
            if guild.get_role(int(role_id)) is None:
                raise ValueError(f"ID为 '{role_id}' 的身份组在本服务器不存在。")
            
            role_ids.add(role_id)
        
        return list(role_ids)
    
    @app_commands.command(name="召唤面板", description="在该频道召唤道馆挑战面板")
    @app_commands.describe(
        panel_type="选择要召唤的面板类型",
        introduction="[可选] 自定义面板的介绍文字",
        button_label="[可选] 自定义主按钮上显示的文字",
        enable_blacklist="[普通] 是否对通过此面板完成挑战的用户启用黑名单检查",
        roles_to_add="[普通] 用户满足条件后将获得的身份组 (多个ID/提及请用逗号隔开)",
        roles_to_remove="[普通] 用户满足条件后将被移除的身份组 (多个ID/提及请用逗号隔开)",
        gym_ids="[普通] 逗号分隔的道馆ID列表，此面板将只包含这些道馆",
        completion_threshold="[普通] 完成多少个道馆后触发奖励，不填则为全部",
        prerequisite_gym_ids="[普通] 逗号分隔的前置道馆ID，需全部完成后才能挑战此面板"
    )
    @app_commands.choices(
        panel_type=[
            app_commands.Choice(name="普通道馆挑战", value="standard"),
            app_commands.Choice(name="究极道馆挑战", value="ultimate"),
        ],
        enable_blacklist=[
            app_commands.Choice(name="是 (默认)", value="yes"),
            app_commands.Choice(name="否", value="no"),
        ]
    )
    async def summon_challenge_panel(
        self,
        interaction: discord.Interaction,
        panel_type: str,
        introduction: typing.Optional[str] = None,
        button_label: typing.Optional[str] = None,
        enable_blacklist: typing.Optional[str] = 'yes',
        roles_to_add: typing.Optional[str] = None,
        roles_to_remove: typing.Optional[str] = None,
        gym_ids: typing.Optional[str] = None,
        completion_threshold: typing.Optional[app_commands.Range[int, 1]] = None,
        prerequisite_gym_ids: typing.Optional[str] = None
    ):
        """召唤道馆挑战面板"""
        # 权限检查
        if not await is_gym_master(interaction, "召唤"):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        
        # 究极道馆面板
        if panel_type == "ultimate":
            if introduction:
                description = introduction.replace('\\n', '\n')
            else:
                description = (
                    "**欢迎来到究极道馆挑战！**\n\n"
                    "在这里，你将面临来自服务器 **所有道馆** 的终极考验。\n"
                    "系统将从总题库中随机抽取 **50%** 的题目，你的目标是在最短的时间内全部正确回答。\n\n"
                    "**规则:**\n"
                    "- **零容错**: 答错任何一题即挑战失败。\n"
                    "- **计时排名**: 你的完成时间将被记录，并计入服务器排行榜。\n\n"
                    "准备好证明你的实力了吗？"
                )
            
            embed = discord.Embed(title="🏆 究极道馆挑战", description=description, color=discord.Color.red())
            view = MainChallengeView()
            view.children[0].label = button_label if button_label else "挑战究极道馆"
            
            try:
                panel_message = await interaction.channel.send(embed=embed, view=view)
                async with self.db.get_connection() as conn:
                    await conn.execute(
                        "INSERT INTO challenge_panels (message_id, guild_id, channel_id, is_ultimate_gym) VALUES (?, ?, ?, TRUE)",
                        (str(panel_message.id), guild_id, str(interaction.channel.id))
                    )
                    await conn.commit()
                await interaction.followup.send(
                    f"✅ 究极道馆面板已成功创建于 {interaction.channel.mention}！",
                    ephemeral=True
                )
            except discord.Forbidden:
                await interaction.followup.send("❌ 设置失败：我没有权限在此频道发送消息。", ephemeral=True)
            except Exception as e:
                logger.error(f"Error in summon_challenge_panel (ultimate): {e}", exc_info=True)
                await interaction.followup.send("❌ 设置失败: 发生了一个未知错误。", ephemeral=True)
            return
        
        # 普通道馆面板
        if panel_type == "standard":
            blacklist_enabled = True if enable_blacklist == 'yes' else False
            
            # 解析身份组
            add_role_ids = []
            if roles_to_add:
                try:
                    add_role_ids = await self.parse_role_mentions_or_ids(interaction.guild, roles_to_add)
                except ValueError as e:
                    return await interaction.followup.send(f'❌ "奖励身份组"格式错误: {e}', ephemeral=True)
            
            remove_role_ids = []
            if roles_to_remove:
                try:
                    remove_role_ids = await self.parse_role_mentions_or_ids(interaction.guild, roles_to_remove)
                except ValueError as e:
                    return await interaction.followup.send(f'❌ "移除身份组"格式错误: {e}', ephemeral=True)
            
            role_add_ids_json = json.dumps(add_role_ids) if add_role_ids else None
            role_remove_ids_json = json.dumps(remove_role_ids) if remove_role_ids else None
            
            associated_gyms_list = [gid.strip() for gid in gym_ids.split(',')] if gym_ids else None
            associated_gyms_json = json.dumps(associated_gyms_list) if associated_gyms_list else None
            
            prerequisite_gyms_list = [gid.strip() for gid in prerequisite_gym_ids.split(',')] if prerequisite_gym_ids else None
            prerequisite_gyms_json = json.dumps(prerequisite_gyms_list) if prerequisite_gyms_list else None
            
            try:
                # 验证道馆ID
                gym_cog = self.bot.get_cog('GymManagementCog')
                if gym_cog:
                    all_guild_gyms = await gym_cog._get_guild_gyms(guild_id)
                    all_gym_ids_set = {gym['id'] for gym in all_guild_gyms}
                    
                    if associated_gyms_list:
                        invalid_ids = [gid for gid in associated_gyms_list if gid not in all_gym_ids_set]
                        if invalid_ids:
                            return await interaction.followup.send(
                                f"❌ 操作失败：以下关联道馆ID在本服务器不存在: `{', '.join(invalid_ids)}`",
                                ephemeral=True
                            )
                    
                    if prerequisite_gyms_list:
                        invalid_ids = [gid for gid in prerequisite_gyms_list if gid not in all_gym_ids_set]
                        if invalid_ids:
                            return await interaction.followup.send(
                                f"❌ 操作失败：以下前置道馆ID在本服务器不存在: `{', '.join(invalid_ids)}`",
                                ephemeral=True
                            )
                    
                    if prerequisite_gyms_list and associated_gyms_list:
                        if set(prerequisite_gyms_list).intersection(set(associated_gyms_list)):
                            return await interaction.followup.send(
                                "❌ 操作失败：一个或多个道馆ID同时存在于前置道馆和关联道馆列表中。",
                                ephemeral=True
                            )
                    
                    if completion_threshold:
                        gym_pool_size = len(associated_gyms_list) if associated_gyms_list is not None else len(all_guild_gyms)
                        if gym_pool_size == 0:
                            return await interaction.followup.send(
                                "❌ 操作失败：服务器内没有任何道馆，无法设置通关数量要求。",
                                ephemeral=True
                            )
                        if completion_threshold > gym_pool_size:
                            return await interaction.followup.send(
                                f"❌ 操作失败：通关数量要求 ({completion_threshold}) 不能大于道馆总数 ({gym_pool_size})。",
                                ephemeral=True
                            )
                
                if introduction:
                    description = introduction.replace('\\n', '\n')
                else:
                    description = (
                        "欢迎来到道馆挑战中心！在这里，你可以通过挑战不同的道馆来学习和证明你的能力。\n\n"
                        "完成所有道馆挑战后，可能会有特殊的身份组奖励或变动。\n\n"
                        "点击下方的按钮，开始你的挑战吧！"
                    )
                
                embed = discord.Embed(title="道馆挑战中心", description=description, color=discord.Color.gold())
                view = MainChallengeView()
                if button_label:
                    view.children[0].label = button_label
                
                panel_message = await interaction.channel.send(embed=embed, view=view)
                
                async with self.db.get_connection() as conn:
                    await conn.execute('''
                        INSERT INTO challenge_panels (
                            message_id, guild_id, channel_id, role_to_add_ids, role_to_remove_ids,
                            associated_gyms, blacklist_enabled, completion_threshold, 
                            prerequisite_gyms, is_ultimate_gym
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)
                    ''', (
                        str(panel_message.id), guild_id, str(interaction.channel.id),
                        role_add_ids_json, role_remove_ids_json, associated_gyms_json,
                        blacklist_enabled, completion_threshold, prerequisite_gyms_json
                    ))
                    await conn.commit()
                
                confirm_messages = [f"✅ 普通道馆面板已成功创建于 {interaction.channel.mention}！"]
                status_text = "启用" if blacklist_enabled else "禁用"
                confirm_messages.append(f"- **黑名单检查**: {status_text}")
                if add_role_ids:
                    mentions = ' '.join(f'<@&{rid}>' for rid in add_role_ids)
                    confirm_messages.append(f"- **奖励身份组**: {mentions}")
                if remove_role_ids:
                    mentions = ' '.join(f'<@&{rid}>' for rid in remove_role_ids)
                    confirm_messages.append(f"- **移除身份组**: {mentions}")
                if associated_gyms_list:
                    confirm_messages.append(f"- **关联道馆**: `{', '.join(associated_gyms_list)}`")
                if completion_threshold:
                    confirm_messages.append(f"- **通关数量**: {completion_threshold} 个")
                if prerequisite_gyms_list:
                    confirm_messages.append(f"- **前置道馆**: `{', '.join(prerequisite_gyms_list)}`")
                
                await interaction.followup.send("\n".join(confirm_messages), ephemeral=True)
                
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ 设置失败：我没有权限在此频道发送消息或管理身份组。请检查我的权限。",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error in summon_challenge_panel (standard): {e}", exc_info=True)
                await interaction.followup.send("❌ 设置失败: 发生了一个未知错误。", ephemeral=True)
    
    @app_commands.command(name="徽章墙", description="在该频道召唤一个徽章墙面板")
    @app_commands.describe(
        introduction="[可选] 自定义徽章墙面板的介绍文字"
    )
    async def summon_badge_panel(
        self,
        interaction: discord.Interaction,
        introduction: typing.Optional[str] = None
    ):
        """召唤徽章墙面板"""
        # 权限检查
        if not await is_gym_master(interaction, "徽章墙面板"):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        try:
            if introduction:
                description = introduction.replace('\\n', '\n')
            else:
                description = (
                    "这里是徽章墙展示中心。\n\n"
                    "点击下方的按钮，来展示你通过努力获得的道馆徽章吧！"
                )
            
            embed = discord.Embed(
                title="徽章墙展示中心",
                description=description,
                color=discord.Color.purple()
            )
            
            await interaction.channel.send(embed=embed, view=BadgePanelView())
            
            await interaction.followup.send(
                f"✅ 徽章墙面板已成功创建于 {interaction.channel.mention}！",
                ephemeral=True
            )
            
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 设置失败：我没有权限在此频道发送消息。请检查我的权限。",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in summon_badge_panel: {e}", exc_info=True)
            await interaction.followup.send("❌ 设置失败: 发生了一个未知错误。", ephemeral=True)
    
    @app_commands.command(name="毕业面板", description='召唤一个用于领取"全部通关"奖励的面板')
    @app_commands.describe(
        role_to_grant="用户完成所有道馆后将获得的身份组",
        introduction="[可选] 自定义面板的介绍文字",
        button_label="[可选] 自定义按钮上显示的文字",
        enable_blacklist="是否对通过此面板领取奖励的用户启用黑名单检查"
    )
    @app_commands.choices(enable_blacklist=[
        app_commands.Choice(name="是 (默认)", value="yes"),
        app_commands.Choice(name="否", value="no"),
    ])
    async def summon_graduation_panel(
        self,
        interaction: discord.Interaction,
        role_to_grant: discord.Role,
        introduction: typing.Optional[str] = None,
        button_label: typing.Optional[str] = "领取毕业奖励",
        enable_blacklist: typing.Optional[str] = 'yes'
    ):
        """召唤毕业面板"""
        # 权限检查
        if not await is_gym_master(interaction, "毕业面板"):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        guild_id = str(interaction.guild.id)
        role_add_id = str(role_to_grant.id)
        blacklist_enabled = True if enable_blacklist == 'yes' else False
        
        try:
            if not introduction:
                introduction = (
                    "祝贺所有坚持不懈的挑战者！\n\n"
                    f"当你完成了本服务器 **所有** 的道馆挑战后，点击下方的按钮，"
                    f"即可领取属于你的最终荣誉：**{role_to_grant.name}** 身份组！"
                )
            
            description = introduction.replace('\\n', '\n')
            
            embed = discord.Embed(
                title="道馆毕业资格认证",
                description=description,
                color=discord.Color.gold()
            )
            
            view = GraduationPanelView()
            view.children[0].label = button_label
            
            panel_message = await interaction.channel.send(embed=embed, view=view)
            
            # 保存配置到数据库
            role_add_ids_json = json.dumps([role_add_id])
            async with self.db.get_connection() as conn:
                await conn.execute('''
                    INSERT INTO challenge_panels (message_id, guild_id, channel_id, role_to_add_ids, blacklist_enabled)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    str(panel_message.id), guild_id, str(interaction.channel.id),
                    role_add_ids_json, blacklist_enabled
                ))
                await conn.commit()
            
            confirm_messages = [f"✅ 毕业面板已成功创建于 {interaction.channel.mention}！"]
            status_text = "启用" if blacklist_enabled else "禁用"
            confirm_messages.append(f"- **奖励身份组**: {role_to_grant.mention}")
            confirm_messages.append(f"- **黑名单检查**: {status_text}")
            
            await interaction.followup.send("\n".join(confirm_messages), ephemeral=True)
            
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 设置失败：我没有权限在此频道发送消息或管理身份组。请检查我的权限。",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in summon_graduation_panel: {e}", exc_info=True)
            await interaction.followup.send("❌ 设置失败: 发生了一个未知错误。", ephemeral=True)
    
    @app_commands.command(name="排行榜", description="在该频道召唤一个自动更新的究极道馆排行榜")
    @app_commands.describe(
        title="[可选] 自定义排行榜的标题",
        description="[可选] 自定义排行榜的描述文字 (使用 \\n 换行)"
    )
    async def summon_leaderboard(
        self,
        interaction: discord.Interaction,
        title: typing.Optional[str] = None,
        description: typing.Optional[str] = None
    ):
        """召唤排行榜面板"""
        # 权限检查
        if not await is_gym_master(interaction, "召唤排行榜"):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        channel_id = str(interaction.channel.id)
        
        # 输入验证
        if title and len(title) > 256:
            return await interaction.followup.send(
                "❌ 操作失败：标题长度不能超过 256 个字符。",
                ephemeral=True
            )
        if description and len(description.replace('\\n', '\n')) > 4096:
            return await interaction.followup.send(
                "❌ 操作失败：描述内容长度不能超过 4096 个字符。",
                ephemeral=True
            )
        
        try:
            # 获取排行榜Cog来创建嵌入消息
            leaderboard_cog = self.bot.get_cog('LeaderboardCog')
            if not leaderboard_cog:
                return await interaction.followup.send(
                    "❌ 排行榜系统暂时不可用。",
                    ephemeral=True
                )
            
            embed = await leaderboard_cog.create_leaderboard_embed(
                interaction.guild, title, description
            )
            # 从 leaderboard_cog 导入 LeaderboardView
            from cogs.leaderboard import LeaderboardView
            panel_message = await interaction.channel.send(embed=embed, view=LeaderboardView())
            
            # 保存面板信息到数据库
            async with self.db.get_connection() as conn:
                await conn.execute(
                    "INSERT INTO leaderboard_panels (message_id, guild_id, channel_id, title, description) VALUES (?, ?, ?, ?, ?)",
                    (str(panel_message.id), guild_id, channel_id, title, description)
                )
                await conn.commit()
            
            await interaction.followup.send(
                f"✅ 排行榜面板已成功创建于 {interaction.channel.mention}！每当有新纪录诞生时，它将自动更新。",
                ephemeral=True
            )
            
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 设置失败：我没有权限在此频道发送消息。",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in summon_leaderboard: {e}", exc_info=True)
            await interaction.followup.send("❌ 设置失败: 发生了一个未知错误。", ephemeral=True)
    
    async def handle_graduation_claim(
        self, 
        interaction: discord.Interaction,
        guild_id: str,
        user_id: str,
        panel_message_id: str
    ):
        """处理毕业奖励领取"""
        member = interaction.user
        
        # 获取面板配置
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                "SELECT role_to_add_ids, blacklist_enabled FROM challenge_panels WHERE message_id = ?",
                (panel_message_id,)
            ) as cursor:
                panel_config = await cursor.fetchone()
        
        if not panel_config or not panel_config['role_to_add_ids']:
            logger.error(f"No role configured for graduation panel {panel_message_id}")
            return await interaction.followup.send(
                "❌ 此面板配置错误，请联系管理员。",
                ephemeral=True
            )
        
        # 毕业面板只使用第一个身份组
        role_to_add_id = json.loads(panel_config['role_to_add_ids'])[0]
        role_to_add = interaction.guild.get_role(int(role_to_add_id))
        
        if not role_to_add:
            logger.error(f"Role {role_to_add_id} not found in guild {guild_id}")
            return await interaction.followup.send(
                "❌ 此面板配置的身份组不存在，请联系管理员。",
                ephemeral=True
            )
        
        # 检查是否已经领取过
        progress_cog = self.bot.get_cog('UserProgressCog')
        # 注意：UserProgressCog 中的 _has_claimed_reward 是私有方法，这里需要添加一个公开方法
        # 或者 gym_challenge.py 中调用的 _has_claimed_reward 方法
        async def check_claimed(guild_id, user_id, role_id):
            async with self.db.get_connection() as conn:
                async with conn.execute(
                    "SELECT 1 FROM claimed_role_rewards WHERE guild_id = ? AND user_id = ? AND role_id = ?",
                    (guild_id, user_id, role_id)
                ) as cursor:
                    result = await cursor.fetchone()
            return result is not None
            
        if await check_claimed(guild_id, user_id, role_to_add_id):
            return await interaction.followup.send(
                f"✅ 你已经领取过 {role_to_add.mention} 这个奖励了！",
                ephemeral=True
            )
        
        # 黑名单检查
        blacklist_enabled = panel_config.get('blacklist_enabled', True)
        if blacklist_enabled:
            moderation_cog = self.bot.get_cog('ModerationCog')
            if moderation_cog:
                blacklist_entry = await moderation_cog.is_user_blacklisted(guild_id, member)
                if blacklist_entry:
                    reason = blacklist_entry.get('reason', '无特定原因')
                    logger.info(f"Blocked graduation role for blacklisted user '{member.id}'")
                    return await interaction.followup.send(
                        f"🚫 **身份组获取失败** 🚫\n\n"
                        f"由于你被记录在处罚名单中，即使完成了所有道馆挑战，也无法领取毕业奖励。\n"
                        f"**原因:** {reason}\n\n"
                        "如有疑问，请联系服务器管理员。",
                        ephemeral=True
                    )
        
        # 检查是否完成所有道馆
        gym_cog = self.bot.get_cog('GymManagementCog')
        if not gym_cog:
            return await interaction.followup.send(
                "❌ 道馆系统暂时不可用。",
                ephemeral=True
            )
        
        all_guild_gyms = await gym_cog._get_guild_gyms(guild_id)
        if not all_guild_gyms:
            return await interaction.followup.send(
                "ℹ️ 本服务器还没有任何道馆，无法判断毕业状态。",
                ephemeral=True
            )
        
        required_gym_ids = {gym['id'] for gym in all_guild_gyms if gym.get('is_enabled', True)}
        
        if progress_cog:
            user_progress = await progress_cog._get_user_progress(user_id, guild_id)
            completed_gym_ids = set(user_progress.keys())
            
            if not required_gym_ids.issubset(completed_gym_ids):
                missing_count = len(required_gym_ids - completed_gym_ids)
                return await interaction.followup.send(
                    f"❌ 你尚未完成所有道馆的挑战，还差 {missing_count} 个。请继续努力！",
                    ephemeral=True
                )
        
        # 授予身份组
        try:
            await member.add_roles(role_to_add, reason="道馆全部通关奖励")
            if progress_cog:
                # 记录奖励领取
                async with self.db.get_connection() as conn:
                    import pytz
                    timestamp = datetime.datetime.now(pytz.UTC).isoformat()
                    await conn.execute(
                        "INSERT OR IGNORE INTO claimed_role_rewards (guild_id, user_id, role_id, timestamp) VALUES (?, ?, ?, ?)",
                        (guild_id, user_id, role_to_add_id, timestamp)
                    )
                    await conn.commit()
            logger.info(f"User '{user_id}' completed all gyms and was granted role '{role_to_add_id}'")
            await interaction.followup.send(
                f"🎉 恭喜！你已完成所有道馆挑战，成功获得身份组：{role_to_add.mention}",
                ephemeral=True
            )
        except discord.Forbidden:
            logger.error(f"Bot lacks permissions to add role {role_to_add_id} in guild {guild_id}")
            await interaction.followup.send(
                "❌ 机器人权限不足，无法为你添加身份组，请联系管理员。",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error granting graduation role: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 发放身份组时发生未知错误，请联系管理员。",
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(PanelsCog(bot))
    logger.info("PanelsCog has been added to bot")