import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import typing
from datetime import datetime
import aiofiles
import aiosqlite

from cogs.base_cog import BaseCog
from core.database import DatabaseManager
from core.cache import cache_manager
from core.models import Gym, ChallengePanel
from core.exceptions import GymNotFoundError, ValidationError
from utils.validators import validate_gym_json, validate_gym_id, validate_role_input, validate_panel_config
from utils.formatters import format_gym_list, format_time, sanitize_filename
from utils.permissions import has_gym_permission
from utils.logger import get_logger

logger = get_logger(__name__)


class GymManagementCog(BaseCog):
    """道馆管理Cog"""
    
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.gym_group = app_commands.Group(
            name="道馆",
            description="管理本服务器的道馆"
        )
        
        # 添加子命令到组
        self.gym_group.command(name="建造", description="通过上传JSON文件创建一个新道馆")(self.gym_create)
        self.gym_group.command(name="更新", description="用新的JSON文件覆盖一个现有道馆")(self.gym_update)
        self.gym_group.command(name="删除", description="删除一个道馆")(self.gym_delete)
        self.gym_group.command(name="列表", description="列出本服务器所有的道馆及其ID")(self.gym_list)
        self.gym_group.command(name="后门", description="获取一个现有道馆的JSON数据")(self.gym_export)
        self.gym_group.command(name="停业", description="设置一个道馆的营业状态")(self.gym_status)
        
        # 将命令组添加到bot的命令树
        bot.tree.add_command(self.gym_group)
    
    async def cog_unload(self):
        """卸载Cog时移除命令组"""
        self.bot.tree.remove_command(self.gym_group.name)
    
    # ========== 道馆建造命令 ==========
    @app_commands.describe(json_file="包含道馆完整信息的JSON文件")
    async def gym_create(self, interaction: discord.Interaction, json_file: discord.Attachment):
        """创建新道馆"""
        # 权限检查
        if not await has_gym_permission(interaction, "建造"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。", 
                ephemeral=True
            )
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # 验证文件格式
        if not json_file.filename.lower().endswith('.json'):
            return await interaction.followup.send(
                "❌ 文件格式错误，请上传一个 `.json` 文件。", 
                ephemeral=True
            )
        
        # 文件大小检查
        if json_file.size > 1 * 1024 * 1024:  # 1MB
            return await interaction.followup.send(
                "❌ 文件过大，请确保JSON文件大小不超过 1MB。", 
                ephemeral=True
            )
        
        try:
            # 读取JSON文件
            json_bytes = await json_file.read()
            data = json.loads(json_bytes.decode('utf-8-sig'))
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in gym_create: {e}")
            return await interaction.followup.send(
                "❌ 无效的JSON格式。请检查您的文件内容。", 
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error reading attachment in gym_create: {e}")
            return await interaction.followup.send(
                "❌ 读取文件时发生错误。", 
                ephemeral=True
            )
        
        # 验证JSON数据
        is_valid, error_msg = validate_gym_json(data)
        if not is_valid:
            return await interaction.followup.send(
                f"❌ JSON数据验证失败：{error_msg}", 
                ephemeral=True
            )
        
        guild_id = str(interaction.guild.id)
        
        try:
            # 创建道馆
            async with self.db.get_connection() as conn:
                # 检查道馆ID是否已存在
                async with conn.execute(
                    "SELECT 1 FROM gyms WHERE guild_id = ? AND gym_id = ?",
                    (guild_id, data['id'])
                ) as cursor:
                    existing = await cursor.fetchone()
                if existing:
                    return await interaction.followup.send(
                        f"❌ 操作失败：道馆ID `{data['id']}` 已存在。如需修改，请使用 `/道馆 更新` 指令。",
                        ephemeral=True
                    )
                
                # 创建道馆对象
                gym = Gym.from_dict(data, guild_id)
                
                # 保存到数据库
                await conn.execute('''
                    INSERT INTO gyms (
                        guild_id, gym_id, name, description, tutorial, questions,
                        questions_to_ask, allowed_mistakes, badge_image_url, 
                        badge_description, is_enabled, randomize_options
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    guild_id, gym.gym_id, gym.name, gym.description,
                    json.dumps(gym.tutorial, ensure_ascii=False),
                    json.dumps(gym.questions, ensure_ascii=False),
                    gym.questions_to_ask, gym.allowed_mistakes,
                    gym.badge_image_url, gym.badge_description,
                    gym.is_enabled, gym.randomize_options
                ))
                
                # 记录审计日志
                await self._log_gym_action(conn, guild_id, gym.gym_id, str(interaction.user.id), 'create')
                
                await conn.commit()

            # 清除可能存在的幽灵缓存
            await cache_manager.delete(f"{guild_id}:{gym.gym_id}", "gym")
            
            logger.info(f"User {interaction.user.id} created gym '{gym.gym_id}' in guild {guild_id}")
            await interaction.followup.send(
                f"✅ 成功创建了道馆: **{gym.name}**",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Error in gym_create command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 操作失败: 发生了一个未知错误。",
                ephemeral=True
            )
    
    # ========== 道馆更新命令 ==========
    @app_commands.describe(
        gym_id="要更新的道馆ID",
        json_file="新的道馆JSON文件"
    )
    async def gym_update(self, interaction: discord.Interaction, gym_id: str, json_file: discord.Attachment):
        """更新现有道馆"""
        # 权限检查
        if not await has_gym_permission(interaction, "更新"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。",
                ephemeral=True
            )
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        
        # 先备份道馆
        await self._backup_single_gym(guild_id, gym_id)
        
        # 验证文件
        if not json_file.filename.lower().endswith('.json'):
            return await interaction.followup.send(
                "❌ 文件格式错误，请上传一个 `.json` 文件。",
                ephemeral=True
            )
        
        if json_file.size > 1 * 1024 * 1024:
            return await interaction.followup.send(
                "❌ 文件过大，请确保JSON文件大小不超过 1MB。",
                ephemeral=True
            )
        
        try:
            json_bytes = await json_file.read()
            data = json.loads(json_bytes.decode('utf-8-sig'))
        except json.JSONDecodeError:
            return await interaction.followup.send(
                "❌ 无效的JSON格式。请检查您的文件内容。",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error reading attachment in gym_update: {e}")
            return await interaction.followup.send(
                "❌ 读取文件时发生错误。",
                ephemeral=True
            )
        
        # 确保ID匹配
        if 'id' not in data or data['id'] != gym_id:
            return await interaction.followup.send(
                f"❌ JSON数据中的`id`必须是`{gym_id}`。",
                ephemeral=True
            )
        
        # 验证JSON数据
        is_valid, error_msg = validate_gym_json(data)
        if not is_valid:
            return await interaction.followup.send(
                f"❌ JSON数据验证失败：{error_msg}",
                ephemeral=True
            )
        
        try:
            async with self.db.get_connection() as conn:
                # 检查道馆是否存在
                async with conn.execute(
                    "SELECT 1 FROM gyms WHERE guild_id = ? AND gym_id = ?",
                    (guild_id, gym_id)
                ) as cursor:
                    existing = await cursor.fetchone()
                if not existing:
                    return await interaction.followup.send(
                        f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。如需创建，请使用 `/道馆 建造` 指令。",
                        ephemeral=True
                    )
                
                # 创建道馆对象
                gym = Gym.from_dict(data, guild_id)
                
                # 更新数据库
                await conn.execute('''
                    UPDATE gyms SET 
                        name = ?, description = ?, tutorial = ?, questions = ?,
                        questions_to_ask = ?, allowed_mistakes = ?, badge_image_url = ?,
                        badge_description = ?, randomize_options = ?
                    WHERE guild_id = ? AND gym_id = ?
                ''', (
                    gym.name, gym.description,
                    json.dumps(gym.tutorial, ensure_ascii=False),
                    json.dumps(gym.questions, ensure_ascii=False),
                    gym.questions_to_ask, gym.allowed_mistakes,
                    gym.badge_image_url, gym.badge_description,
                    gym.randomize_options,
                    guild_id, gym_id
                ))
                
                # 记录审计日志
                await self._log_gym_action(conn, guild_id, gym_id, str(interaction.user.id), 'update')
                
                await conn.commit()

            # 清除缓存，确保更新立即生效
            await cache_manager.delete(f"{guild_id}:{gym_id}", "gym")
            
            logger.info(f"User {interaction.user.id} updated gym '{gym_id}' in guild {guild_id}")
            await interaction.followup.send(
                f"✅ 成功更新了道馆: **{gym.name}**",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Error in gym_update command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 操作失败: 发生了一个未知错误。",
                ephemeral=True
            )
    
    # ========== 道馆删除命令 ==========
    @app_commands.describe(gym_id="要删除的道馆ID")
    async def gym_delete(self, interaction: discord.Interaction, gym_id: str):
        """删除道馆"""
        # 权限检查
        if not await has_gym_permission(interaction, "删除"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。",
                ephemeral=True
            )
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        
        # 先备份道馆
        await self._backup_single_gym(guild_id, gym_id)
        
        try:
            async with self.db.get_connection() as conn:
                # 检查道馆是否存在
                async with conn.execute(
                    "SELECT 1 FROM gyms WHERE guild_id = ? AND gym_id = ?",
                    (guild_id, gym_id)
                ) as cursor:
                    existing = await cursor.fetchone()
                if not existing:
                    return await interaction.followup.send(
                        f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。",
                        ephemeral=True
                    )
                
                # 删除相关数据
                await conn.execute("DELETE FROM user_progress WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id))
                await conn.execute("DELETE FROM challenge_failures WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id))
                await conn.execute("DELETE FROM gym_audit_log WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id))
                await conn.execute("DELETE FROM gyms WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id))
                
                # 清理挑战面板中的关联
                await self._clean_panel_associations(conn, guild_id, gym_id)
                
                # 记录删除操作
                await self._log_gym_action(conn, guild_id, gym_id, str(interaction.user.id), 'delete')
                
                await conn.commit()

            # 清除缓存，防止幽灵数据
            await cache_manager.delete(f"{guild_id}:{gym_id}", "gym")
            
            logger.info(f"User {interaction.user.id} deleted gym '{gym_id}' from guild {guild_id}")
            await interaction.followup.send(
                f"✅ 道馆 `{gym_id}` 及其所有相关进度已被成功删除。\n"
                "ℹ️ 关联的挑战面板也已自动更新。",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Error in gym_delete command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 操作失败: 发生了一个未知错误。",
                ephemeral=True
            )
    
    # ========== 道馆列表命令 ==========
    @app_commands.command(name="面板列表", description="查看服务器中的所有召唤面板")
    async def panel_list(self, interaction: discord.Interaction):
        """列出所有面板"""
        # 权限检查
        if not await has_gym_permission(interaction, "面板列表"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。",
                ephemeral=True
            )
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        
        try:
            # 获取所有挑战面板
            async with self.db.get_connection() as conn:
                conn.row_factory = self.db.dict_row
                async with conn.execute('''
                    SELECT message_id, channel_id, role_to_add_ids, role_to_remove_ids,
                           associated_gyms, blacklist_enabled, completion_threshold,
                           prerequisite_gyms, is_ultimate_gym
                    FROM challenge_panels
                    WHERE guild_id = ?
                ''', (guild_id,)) as cursor:
                    panels = await cursor.fetchall()
            
            if not panels:
                return await interaction.followup.send(
                    "本服务器还没有创建任何召唤面板。",
                    ephemeral=True
                )
            
            # 创建Embed
            embed = discord.Embed(
                title=f"「{interaction.guild.name}」的召唤面板列表",
                color=discord.Color.purple()
            )
            
            # 构建面板列表描述
            description_lines = []
            for i, panel in enumerate(panels, 1):
                panel_type = "究极道馆" if panel['is_ultimate_gym'] else "普通道馆"
                channel = interaction.guild.get_channel(int(panel['channel_id']))
                channel_mention = channel.mention if channel else f"<#{panel['channel_id']}> (已删除)"
                
                line = f"**{i}.** {panel_type}面板\n"
                line += f"   📍 频道: {channel_mention}\n"
                line += f"   🆔 消息ID: `{panel['message_id']}`\n"
                
                if not panel['is_ultimate_gym']:
                    if panel['blacklist_enabled']:
                        line += f"   🚫 黑名单: 启用\n"
                    if panel['completion_threshold']:
                        line += f"   🎯 通关数量: {panel['completion_threshold']}\n"
                    if panel['associated_gyms']:
                        gyms = json.loads(panel['associated_gyms'])
                        line += f"   🏛️ 关联道馆: {len(gyms)}个\n"
                
                description_lines.append(line)
            
            embed.description = "\n".join(description_lines)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in panel_list command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 获取面板列表时发生错误。",
                ephemeral=True
            )
    
    async def gym_list(self, interaction: discord.Interaction):
        """列出所有道馆"""
        # 权限检查
        if not await has_gym_permission(interaction, "列表"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。",
                ephemeral=True
            )
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        
        try:
            # 获取所有道馆
            gyms = await self._get_guild_gyms(guild_id)
            
            if not gyms:
                return await interaction.followup.send(
                    "本服务器还没有创建任何道馆。",
                    ephemeral=True
                )
            
            # 创建Embed
            embed = discord.Embed(
                title=f"「{interaction.guild.name}」的道馆列表",
                color=discord.Color.purple()
            )
            
            embed.description = format_gym_list(gyms)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in gym_list command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 获取道馆列表时发生错误。",
                ephemeral=True
            )
    
    # ========== 道馆后门命令 ==========
    @app_commands.describe(gym_id="要获取JSON的道馆ID")
    async def gym_export(self, interaction: discord.Interaction, gym_id: str):
        """导出道馆JSON"""
        # 权限检查
        if not await has_gym_permission(interaction, "后门"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。",
                ephemeral=True
            )
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        
        try:
            gym_data = await self._get_single_gym(guild_id, gym_id)
            if not gym_data:
                return await interaction.followup.send(
                    "❌ 在本服务器找不到指定ID的道馆。",
                    ephemeral=True
                )
            
            json_string = json.dumps(gym_data, indent=4, ensure_ascii=False)
            
            # 如果JSON太长，作为文件发送
            if len(json_string) > 1900:
                filepath = f'gym_export_{interaction.user.id}.json'
                try:
                    async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                        await f.write(json_string)
                    await interaction.followup.send(
                        "道馆数据过长，已作为文件发送。",
                        file=discord.File(filepath),
                        ephemeral=True
                    )
                finally:
                    if os.path.exists(filepath):
                        os.remove(filepath)
            else:
                await interaction.followup.send(
                    f"```json\n{json_string}\n```",
                    ephemeral=True
                )
            
        except Exception as e:
            logger.error(f"Error in gym_export command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 导出道馆数据时发生错误。",
                ephemeral=True
            )
    
    # ========== 道馆停业命令 ==========
    @app_commands.describe(
        gym_id="要操作的道馆ID",
        status="选择要执行的操作"
    )
    @app_commands.choices(status=[
        app_commands.Choice(name="开启", value="enable"),
        app_commands.Choice(name="停业", value="disable")
    ])
    async def gym_status(self, interaction: discord.Interaction, gym_id: str, status: str):
        """设置道馆状态"""
        # 权限检查
        if not await has_gym_permission(interaction, "停业"):
            return await interaction.response.send_message(
                "❌ 你没有执行此指令所需的权限。",
                ephemeral=True
            )
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        is_enabled = (status == "enable")
        
        try:
            async with self.db.get_connection() as conn:
                cursor = await conn.execute(
                    "UPDATE gyms SET is_enabled = ? WHERE guild_id = ? AND gym_id = ?",
                    (is_enabled, guild_id, gym_id)
                )
                await conn.commit()
                
                if cursor.rowcount > 0:
                    # 清除缓存，确保状态变更立即生效
                    await cache_manager.delete(f"{guild_id}:{gym_id}", "gym")

                    status_text = "开启" if is_enabled else "停业"
                    await interaction.followup.send(
                        f"✅ 道馆 `{gym_id}` 已{status_text}。",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。",
                        ephemeral=True
                    )
            
        except Exception as e:
            logger.error(f"Error in gym_status command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ 设置道馆状态时发生错误。",
                ephemeral=True
            )
    
    # ========== 辅助方法 ==========
    async def _get_guild_gyms(self, guild_id: str) -> list:
        """获取服务器的所有道馆"""
        async with self.db.get_connection() as conn:
            async with conn.execute('''
                SELECT gym_id, name, description, tutorial, questions,
                       questions_to_ask, allowed_mistakes, badge_image_url,
                       badge_description, is_enabled, randomize_options
                FROM gyms WHERE guild_id = ?
            ''', (guild_id,)) as cursor:
                rows = await cursor.fetchall()
        
        gyms = []
        for row in rows:
            gym_data = {
                'id': row[0],
                'name': row[1],
                'description': row[2],
                'tutorial': json.loads(row[3]),
                'questions': json.loads(row[4]),
                'questions_to_ask': row[5],
                'allowed_mistakes': row[6],
                'badge_image_url': row[7],
                'badge_description': row[8],
                'is_enabled': row[9],
                'randomize_options': row[10]
            }
            gyms.append(gym_data)
        
        return gyms
    
    async def _get_single_gym(self, guild_id: str, gym_id: str) -> dict:
        """获取单个道馆数据"""
        async with self.db.get_connection() as conn:
            async with conn.execute('''
                SELECT gym_id, name, description, tutorial, questions,
                       questions_to_ask, allowed_mistakes, badge_image_url,
                       badge_description, is_enabled, randomize_options
                FROM gyms WHERE guild_id = ? AND gym_id = ?
            ''', (guild_id, gym_id)) as cursor:
                row = await cursor.fetchone()
        
        if not row:
            return None
        
        return {
            'id': row[0],
            'name': row[1],
            'description': row[2],
            'tutorial': json.loads(row[3]),
            'questions': json.loads(row[4]),
            'questions_to_ask': row[5],
            'allowed_mistakes': row[6],
            'badge_image_url': row[7],
            'badge_description': row[8],
            'is_enabled': row[9],
            'randomize_options': row[10]
        }
    
    async def _log_gym_action(self, conn, guild_id: str, gym_id: str, user_id: str, action: str):
        """记录道馆操作审计日志"""
        import pytz
        timestamp = datetime.now(pytz.UTC).isoformat()
        await conn.execute('''
            INSERT INTO gym_audit_log (guild_id, gym_id, user_id, action, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (guild_id, gym_id, user_id, action, timestamp))
    
    async def _backup_single_gym(self, guild_id: str, gym_id: str):
        """备份单个道馆"""
        try:
            gym_data = await self._get_single_gym(guild_id, gym_id)
            if not gym_data:
                logger.warning(f"Attempted to backup non-existent gym '{gym_id}' in guild '{guild_id}'")
                return
            
            # 创建备份目录
            from core.constants import BOT_DIR
            backup_dir = BOT_DIR / 'data' / 'gym_backups' / guild_id / gym_id
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成备份文件名
            import pytz
            timestamp = datetime.now(pytz.UTC).strftime('%Y-%m-%d_%H-%M-%S')
            backup_file = backup_dir / f"{timestamp}.json"
            
            # 保存备份
            async with aiofiles.open(backup_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(gym_data, indent=4, ensure_ascii=False))
            
            logger.info(f"Created backup for gym '{gym_id}' in guild '{guild_id}'")
            
        except Exception as e:
            logger.error(f"Failed to backup gym '{gym_id}' in guild '{guild_id}': {e}")
    
    async def _clean_panel_associations(self, conn, guild_id: str, gym_id: str):
        """清理挑战面板中的道馆关联"""
        # 获取所有面板
        async with conn.execute(
            "SELECT message_id, associated_gyms, prerequisite_gyms FROM challenge_panels WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            panels = await cursor.fetchall()
        
        for panel in panels:
            message_id = panel[0]
            updated = False
            
            # 清理associated_gyms
            if panel[1]:
                associated_gyms = json.loads(panel[1])
                if gym_id in associated_gyms:
                    associated_gyms.remove(gym_id)
                    updated = True
                    new_associated = json.dumps(associated_gyms) if associated_gyms else None
                else:
                    new_associated = panel[1]
            else:
                new_associated = None
            
            # 清理prerequisite_gyms
            if panel[2]:
                prerequisite_gyms = json.loads(panel[2])
                if gym_id in prerequisite_gyms:
                    prerequisite_gyms.remove(gym_id)
                    updated = True
                    new_prerequisite = json.dumps(prerequisite_gyms) if prerequisite_gyms else None
                else:
                    new_prerequisite = panel[2]
            else:
                new_prerequisite = None
            
            # 更新面板
            if updated:
                await conn.execute(
                    "UPDATE challenge_panels SET associated_gyms = ?, prerequisite_gyms = ? WHERE message_id = ?",
                    (new_associated, new_prerequisite, message_id)
                )


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(GymManagementCog(bot))