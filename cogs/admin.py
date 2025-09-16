# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
from discord import app_commands
import typing
import json
import logging

from .base_cog import BaseCog
from core.database import DatabaseManager
from utils.permissions import is_admin_or_owner
from utils.logger import get_logger

logger = get_logger(__name__)


class AdminCog(BaseCog):
    """
    管理员命令模块
    负责权限管理、用户进度重置等管理功能
    """
    
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.db = DatabaseManager()
    
    @app_commands.command(name="设置馆主", description="管理道馆指令权限")
    @app_commands.describe(
        action="选择是'添加'还是'移除'权限",
        target="选择要授权的用户或身份组",
        permission="授予哪个指令的权限 ('all' 代表所有道馆指令)"
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="添加权限", value="add"),
            app_commands.Choice(name="移除权限", value="remove")
        ],
        permission=[
            app_commands.Choice(name="所有管理指令 (包括召唤)", value="all"),
            app_commands.Choice(name="召唤 (/召唤挑战面板)", value="召唤"),
            app_commands.Choice(name="徽章墙面板 (/召唤徽章墙)", value="徽章墙面板"),
            app_commands.Choice(name="毕业面板 (/召唤毕业面板)", value="毕业面板"),
            app_commands.Choice(name="建造 (/道馆 建造)", value="建造"),
            app_commands.Choice(name="更新 (/道馆 更新)", value="更新"),
            app_commands.Choice(name="后门 (/道馆 后门)", value="后门"),
            app_commands.Choice(name="列表 (/道馆 列表)", value="列表"),
            app_commands.Choice(name="列表面板 (/道馆 列表面板)", value="列表面板"),
            app_commands.Choice(name="更新面板 (/道馆 更新面板)", value="更新面板"),
            app_commands.Choice(name="重置进度 (/重置进度)", value="重置进度"),
            app_commands.Choice(name="解除处罚 (/解除处罚)", value="解除处罚"),
            app_commands.Choice(name="停业 (/道馆 停业)", value="停业"),
            app_commands.Choice(name="删除 (/道馆 删除)", value="删除"),
            app_commands.Choice(name="道馆黑名单 (/道馆黑名单)", value="道馆黑名单"),
            app_commands.Choice(name="道馆封禁 (/道馆封禁)", value="道馆封禁"),
            app_commands.Choice(name="召唤排行榜 (/召唤排行榜)", value="召唤排行榜"),
        ]
    )
    async def set_gym_master(
        self,
        interaction: discord.Interaction,
        action: str,
        target: typing.Union[discord.Member, discord.Role],
        permission: str
    ):
        """设置道馆管理权限"""
        # 权限检查
        if not await is_admin_or_owner(interaction):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        # 安全检查：禁止给@everyone授权
        if isinstance(target, discord.Role) and target.is_default():
            return await interaction.response.send_message(
                "❌ **安全警告:** 出于安全考虑，禁止向 `@everyone` 角色授予道馆管理权限。",
                ephemeral=True
            )
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        guild_id = str(interaction.guild.id)
        target_id = str(target.id)
        target_type = 'user' if isinstance(target, (discord.User, discord.Member)) else 'role'
        
        try:
            if action == "add":
                await self.add_gym_master(guild_id, target_id, target_type, permission)
                await interaction.followup.send(
                    f"✅ 已将 `{permission}` 权限授予 {target.mention}。",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.id} granted '{permission}' permission to {target_type} {target_id}")
            
            elif action == "remove":
                await self.remove_gym_master(guild_id, target_id, permission)
                await interaction.followup.send(
                    f"✅ 已从 {target.mention} 移除 `{permission}` 权限。",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.id} removed '{permission}' permission from {target_type} {target_id}")
                
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 操作失败：我没有权限回复此消息。请检查我的权限。",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in set_gym_master: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 操作失败: 发生了一个未知错误。",
                ephemeral=True
            )
    
    async def add_gym_master(self, guild_id: str, target_id: str, target_type: str, permission: str):
        """添加道馆管理权限"""
        async with self.db.get_connection() as conn:
            await conn.execute('''
                INSERT OR REPLACE INTO gym_masters (guild_id, target_id, target_type, permission)
                VALUES (?, ?, ?, ?)
            ''', (guild_id, target_id, target_type, permission))
            await conn.commit()
    
    async def remove_gym_master(self, guild_id: str, target_id: str, permission: str):
        """移除道馆管理权限"""
        async with self.db.get_connection() as conn:
            await conn.execute(
                "DELETE FROM gym_masters WHERE guild_id = ? AND target_id = ? AND permission = ?",
                (guild_id, target_id, permission)
            )
            await conn.commit()
    
    @app_commands.command(name="admin_重置进度", description="重置用户的道馆进度")
    @app_commands.describe(
        user="要重置进度的用户",
        scope="选择要重置的数据范围",
        gym_id="[如果重置特定道馆] 请输入道馆ID"
    )
    @app_commands.choices(scope=[
        app_commands.Choice(name="全部进度 (不可恢复)", value="all"),
        app_commands.Choice(name="仅究极道馆进度", value="ultimate"),
        app_commands.Choice(name="仅特定道馆进度", value="specific_gym"),
    ])
    async def reset_progress(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        scope: str,
        gym_id: typing.Optional[str] = None
    ):
        """重置用户的道馆进度"""
        # 权限检查
        from utils.permissions import is_gym_master
        if not await is_gym_master(interaction, "重置进度"):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        user_id = str(user.id)
        
        # 验证
        if scope == "specific_gym":
            if not gym_id:
                await interaction.followup.send(
                    '❌ 操作失败：选择"仅特定道馆进度"时，必须提供道馆ID。',
                    ephemeral=True
                )
                return
            
            # 检查道馆是否存在
            gym_cog = self.bot.get_cog('GymManagementCog')
            if gym_cog:
                gym = await gym_cog._get_single_gym(guild_id, gym_id)
                if not gym:
                    await interaction.followup.send(
                        f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。",
                        ephemeral=True
                    )
                    return
        
        try:
            progress_cog = self.bot.get_cog('UserProgressCog')
            if not progress_cog:
                await interaction.followup.send(
                    "❌ 进度系统暂时不可用。",
                    ephemeral=True
                )
                return
            
            if scope == "all":
                await progress_cog._fully_reset_user_progress(user_id, guild_id)
                await interaction.followup.send(
                    f"✔️ 已成功重置用户 {user.mention} 的**所有**道馆挑战进度、失败记录和身份组领取记录。",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.id} reset ALL progress for user {user_id}")
            
            elif scope == "ultimate":
                await progress_cog._reset_ultimate_progress(user_id, guild_id)
                await interaction.followup.send(
                    f"✔️ 已成功重置用户 {user.mention} 的**究极道馆**排行榜进度。",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.id} reset ultimate gym progress for user {user_id}")
            
            elif scope == "specific_gym":
                await progress_cog._reset_specific_gym_progress(user_id, guild_id, gym_id)
                await interaction.followup.send(
                    f"✔️ 已成功重置用户 {user.mention} 在道馆 `{gym_id}` 的进度和失败记录。",
                    ephemeral=True
                )
                logger.info(f"Admin {interaction.user.id} reset progress for user {user_id} in gym {gym_id}")
                
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 重置失败：我没有权限回复此消息。请检查我的权限。",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in reset_progress: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 重置失败: 发生了一个未知错误。",
                ephemeral=True
            )
    
    @app_commands.command(name="admin_解除处罚", description="解除用户在特定道馆的挑战冷却")
    @app_commands.describe(
        user="要解除处罚的用户",
        gym_id="要解除处罚的道馆ID"
    )
    async def pardon_user(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        gym_id: str
    ):
        """解除用户的挑战冷却"""
        # 权限检查
        from utils.permissions import is_gym_master
        if not await is_gym_master(interaction, "解除处罚"):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        user_id = str(user.id)
        
        # 检查道馆是否存在
        gym_cog = self.bot.get_cog('GymManagementCog')
        if gym_cog:
            gym = await gym_cog._get_single_gym(guild_id, gym_id)
            if not gym:
                return await interaction.followup.send(
                    f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。",
                    ephemeral=True
                )
        
        try:
            challenge_cog = self.bot.get_cog('GymChallengeCog')
            if not challenge_cog:
                await interaction.followup.send(
                    "❌ 挑战系统暂时不可用。",
                    ephemeral=True
                )
                return
            
            await challenge_cog._reset_failures(user_id, guild_id, gym_id)
            await interaction.followup.send(
                f"✅ 已成功解除用户 {user.mention} 在道馆 `{gym_id}` 的挑战处罚。",
                ephemeral=True
            )
            logger.info(f"Admin {interaction.user.id} pardoned user {user_id} for gym {gym_id}")
            
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 操作失败：我没有权限回复此消息。请检查我的权限。",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in pardon_user: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 操作失败: 发生了一个未知错误。",
                ephemeral=True
            )
    
    @app_commands.command(name="say", description="让机器人发送一条消息，可以附带图片或回复其他消息")
    @app_commands.describe(
        message="要发送的文字内容",
        channel="[可选] 要发送消息的频道 (默认为当前频道)",
        image="[可选] 要附加的图片文件",
        reply_to="[可选] 要回复的消息链接"
    )
    async def say(
        self,
        interaction: discord.Interaction,
        message: str,
        channel: typing.Optional[discord.TextChannel] = None,
        image: typing.Optional[discord.Attachment] = None,
        reply_to: typing.Optional[str] = None
    ):
        """让机器人发送消息"""
        # 权限检查
        if not await is_admin_or_owner(interaction):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # 如果没有指定频道，使用当前频道
        target_channel = channel or interaction.channel
        
        # 验证检查
        if len(message) > 2000:
            await interaction.followup.send(
                "❌ 发送失败：消息内容不能超过 2000 个字符。",
                ephemeral=True
            )
            return
        
        # 检查附件类型
        if image and image.content_type and not image.content_type.startswith('image/'):
            await interaction.followup.send(
                "❌ 文件类型错误，请上传一个图片文件 (e.g., PNG, JPG, GIF)。",
                ephemeral=True
            )
            return
        
        # 检查附件大小（机器人限制25MB）
        if image and image.size > 25 * 1024 * 1024:
            await interaction.followup.send(
                "❌ 发送失败：图片文件大小不能超过 25MB。",
                ephemeral=True
            )
            return
        
        # 如果提供了 reply_to，尝试解析消息链接并获取要回复的消息
        reference_message = None
        if reply_to:
            # 解析 Discord 消息链接
            # 格式: https://discord.com/channels/服务器ID/频道ID/消息ID
            import re
            link_pattern = r'https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)'
            match = re.match(link_pattern, reply_to)
            
            if not match:
                await interaction.followup.send(
                    "❌ 发送失败：无效的消息链接格式。请提供一个完整的Discord消息链接。\n"
                    "格式示例: `https://discord.com/channels/服务器ID/频道ID/消息ID`",
                    ephemeral=True
                )
                return
            
            guild_id_from_link = match.group(1)
            channel_id_from_link = match.group(2)
            message_id = match.group(3)
            
            # 验证服务器ID是否匹配
            if guild_id_from_link != str(interaction.guild.id):
                await interaction.followup.send(
                    "❌ 发送失败：消息链接必须来自当前服务器。",
                    ephemeral=True
                )
                return
            
            # 获取消息所在的频道
            try:
                message_channel = interaction.guild.get_channel(int(channel_id_from_link))
                if not message_channel:
                    await interaction.followup.send(
                        "❌ 发送失败：找不到消息链接中指定的频道。",
                        ephemeral=True
                    )
                    return
                
                # 如果没有指定发送频道，默认使用消息所在的频道
                if not channel:
                    target_channel = message_channel
                # 如果指定了频道但与消息所在频道不同，提示错误
                elif target_channel.id != message_channel.id:
                    await interaction.followup.send(
                        f"❌ 发送失败：要回复的消息在 {message_channel.mention} 频道，"
                        f"但你指定了发送到 {target_channel.mention} 频道。回复必须在同一频道。",
                        ephemeral=True
                    )
                    return
                
                # 尝试获取消息
                reference_message = await message_channel.fetch_message(int(message_id))
                
            except discord.NotFound:
                await interaction.followup.send(
                    f"❌ 发送失败：找不到链接中指定的消息。消息可能已被删除。",
                    ephemeral=True
                )
                return
            except discord.Forbidden:
                await interaction.followup.send(
                    f"❌ 发送失败：我没有权限在 {message_channel.mention} 频道读取消息历史。",
                    ephemeral=True
                )
                return
            except (ValueError, AttributeError) as e:
                await interaction.followup.send(
                    "❌ 发送失败：处理消息链接时出错。",
                    ephemeral=True
                )
                return
        
        try:
            image_file = await image.to_file() if image else None
            
            # 发送消息，如果有 reference_message 则作为回复
            if reference_message:
                await target_channel.send(
                    content=message,
                    file=image_file,
                    reference=reference_message,
                    mention_author=False  # 不@提及原消息作者
                )
                success_msg = "✅ 消息已成功发送并回复了指定消息"
            else:
                await target_channel.send(content=message, file=image_file)
                success_msg = "✅ 消息已成功发送"
            
            if target_channel != interaction.channel:
                success_msg += f" 至 {target_channel.mention}"
            
            await interaction.followup.send(success_msg + "。", ephemeral=True)
            
            log_msg = f"Admin {interaction.user.id} used /say command in channel {target_channel.id}"
            if reply_to:
                log_msg += f" (replying to message from link: {reply_to})"
            logger.info(log_msg)
            
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ 发送失败：我没有权限在 {target_channel.mention} 频道发送消息或上传文件。",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in say command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 发送消息时发生未知错误。",
                ephemeral=True
            )
    
    @app_commands.command(name="更新面板", description="更新一个已存在的普通挑战面板的设置")
    @app_commands.describe(
        message_id="要更新的面板的消息ID",
        enable_blacklist="是否启用黑名单检查 (留空则不更改)",
        roles_to_add="新的奖励身份组 (多个ID/提及用逗号隔开, 留空不更改, 输入'none'则清空)",
        roles_to_remove="新的移除身份组 (多个ID/提及用逗号隔开, 留空不更改, 输入'none'则清空)",
        completion_threshold="新的通关数量要求 (输入0则移除此要求，留空不更改)"
    )
    @app_commands.choices(enable_blacklist=[
        app_commands.Choice(name="是", value="yes"),
        app_commands.Choice(name="否", value="no"),
    ])
    async def update_panel(
        self,
        interaction: discord.Interaction,
        message_id: str,
        enable_blacklist: typing.Optional[str] = None,
        roles_to_add: typing.Optional[str] = None,
        roles_to_remove: typing.Optional[str] = None,
        completion_threshold: typing.Optional[app_commands.Range[int, 0]] = None
    ):
        """更新挑战面板设置"""
        # 权限检查
        from utils.permissions import is_gym_master
        if not await is_gym_master(interaction, "更新面板"):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        
        # 验证消息ID
        if not message_id.isdigit():
            return await interaction.followup.send(
                "❌ 操作失败：提供的消息ID无效。",
                ephemeral=True
            )
        
        # 获取面板配置
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            async with conn.execute(
                "SELECT * FROM challenge_panels WHERE message_id = ? AND guild_id = ?",
                (message_id, guild_id)
            ) as cursor:
                panel = await cursor.fetchone()
        
        if not panel:
            return await interaction.followup.send(
                "❌ 操作失败：在本服务器找不到该ID的挑战面板。",
                ephemeral=True
            )
        
        if panel.get('is_ultimate_gym'):
            return await interaction.followup.send(
                '❌ 操作失败：此指令不能用于更新"究极道馆"面板。',
                ephemeral=True
            )
        
        # 验证通关数量要求
        if completion_threshold is not None and completion_threshold > 0:
            associated_gyms_json = panel.get('associated_gyms')
            gym_pool_size = 0
            
            if associated_gyms_json:
                gym_pool_size = len(json.loads(associated_gyms_json))
            else:
                gym_cog = self.bot.get_cog('GymManagementCog')
                if gym_cog:
                    all_guild_gyms = await gym_cog._get_guild_gyms(guild_id)
                    gym_pool_size = len(all_guild_gyms)
            
            if gym_pool_size == 0:
                return await interaction.followup.send(
                    "❌ 操作失败：服务器内没有任何道馆，无法设置通关数量要求。",
                    ephemeral=True
                )
            if completion_threshold > gym_pool_size:
                return await interaction.followup.send(
                    f"❌ 操作失败：通关数量要求 ({completion_threshold}) 不能大于该面板关联的道馆总数 ({gym_pool_size})。",
                    ephemeral=True
                )
        
        # 构建更新查询
        updates = []
        params = []
        confirm_messages = [f"✅ **面板 `{message_id}` 更新成功！**"]
        
        if enable_blacklist is not None:
            blacklist_enabled = True if enable_blacklist == 'yes' else False
            updates.append("blacklist_enabled = ?")
            params.append(blacklist_enabled)
            status_text = "启用" if blacklist_enabled else "禁用"
            confirm_messages.append(f"- **黑名单检查** 更新为: {status_text}")
        
        panels_cog = self.bot.get_cog('PanelsCog')
        
        if roles_to_add is not None:
            if roles_to_add.lower() == 'none':
                updates.append("role_to_add_ids = ?")
                params.append(None)
                confirm_messages.append("- **奖励身份组** 已被清空。")
            elif panels_cog:
                try:
                    add_role_ids = await panels_cog.parse_role_mentions_or_ids(
                        interaction.guild, roles_to_add
                    )
                    updates.append("role_to_add_ids = ?")
                    params.append(json.dumps(add_role_ids))
                    mentions = ' '.join(f'<@&{rid}>' for rid in add_role_ids)
                    confirm_messages.append(f"- **奖励身份组** 更新为: {mentions}")
                except ValueError as e:
                    return await interaction.followup.send(
                        f'❌ "奖励身份组"格式错误: {e}',
                        ephemeral=True
                    )
        
        if roles_to_remove is not None:
            if roles_to_remove.lower() == 'none':
                updates.append("role_to_remove_ids = ?")
                params.append(None)
                confirm_messages.append("- **移除身份组** 已被清空。")
            elif panels_cog:
                try:
                    remove_role_ids = await panels_cog.parse_role_mentions_or_ids(
                        interaction.guild, roles_to_remove
                    )
                    updates.append("role_to_remove_ids = ?")
                    params.append(json.dumps(remove_role_ids))
                    mentions = ' '.join(f'<@&{rid}>' for rid in remove_role_ids)
                    confirm_messages.append(f"- **移除身份组** 更新为: {mentions}")
                except ValueError as e:
                    return await interaction.followup.send(
                        f'❌ "移除身份组"格式错误: {e}',
                        ephemeral=True
                    )
        
        if completion_threshold is not None:
            if completion_threshold == 0:
                updates.append("completion_threshold = ?")
                params.append(None)
                confirm_messages.append("- **通关数量** 要求已移除。")
            else:
                updates.append("completion_threshold = ?")
                params.append(completion_threshold)
                confirm_messages.append(f"- **通关数量** 更新为: {completion_threshold} 个")
        
        if not updates:
            return await interaction.followup.send(
                "ℹ️ 你没有提供任何要更新的设置。",
                ephemeral=True
            )
        
        # 执行更新
        query = f"UPDATE challenge_panels SET {', '.join(updates)} WHERE message_id = ?"
        params.append(message_id)
        
        try:
            async with self.db.get_connection() as conn:
                await conn.execute(query, tuple(params))
                await conn.commit()
            
            logger.info(f"Admin {interaction.user.id} updated panel {message_id} in guild {guild_id}")
            await interaction.followup.send("\n".join(confirm_messages), ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in update_panel: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 更新面板时发生数据库错误。",
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(AdminCog(bot))
    logger.info("AdminCog has been added to bot")