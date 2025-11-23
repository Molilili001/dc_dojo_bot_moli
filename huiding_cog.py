import discord
from discord.ext import commands
import json
import os
import asyncio


class HuidingCog(commands.Cog):
    """回顶功能 Cog - 检测 '/回顶'、'／回顶' 或 '回顶' 消息并回复首楼链接"""
    
    # 清理延迟（秒）
    CLEANUP_DELAY = 300
    # 无权限删除用户消息时是否静默（False=在频道内提示一次，便于后续切换为静默模式）
    SILENT_ON_PERMISSION_ERROR = False
    
    def __init__(self, bot):
        self.bot = bot
        self.server_settings = {}
        self.settings_file = 'huiding_settings.json'
        # 回顶使用统计（按guild+user记录）
        self.usage_stats = {}
        self.stats_file = 'huiding_stats.json'
        self.load_settings()
        self.load_stats()
    
    def load_settings(self):
        """加载服务器设置"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    self.server_settings = json.load(f)
        except Exception as e:
            print(f'⚠️ 回顶功能加载设置失败: {e}')
            self.server_settings = {}
    
    def save_settings(self):
        """保存服务器设置"""
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.server_settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'⚠️ 回顶功能保存设置失败: {e}')
    
    def load_stats(self):
        """加载回顶使用统计"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    self.usage_stats = json.load(f)
        except Exception as e:
            print(f'⚠️ 回顶统计加载失败: {e}')
            self.usage_stats = {}
    
    def save_stats(self):
        """保存回顶使用统计"""
        try:
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump(self.usage_stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'⚠️ 回顶统计保存失败: {e}')
    
    def get_usage_count(self, guild_id: int, user_id: int) -> int:
        """获取用户的回顶次数（按服务器）"""
        guild_key = str(guild_id)
        user_key = str(user_id)
        return self.usage_stats.get(guild_key, {}).get(user_key, 0)
    
    def increment_usage_count(self, guild_id: int, user_id: int) -> int:
        """增加并返回用户的回顶次数（按服务器）"""
        guild_key = str(guild_id)
        user_key = str(user_id)
        if guild_key not in self.usage_stats:
            self.usage_stats[guild_key] = {}
        current = self.usage_stats[guild_key].get(user_key, 0) + 1
        self.usage_stats[guild_key][user_key] = current
        try:
            self.save_stats()
        except Exception as e:
            print(f'⚠️ 回顶统计写入失败: {e}')
        return current
    
    def is_huiding_enabled(self, guild_id):
        """检查服务器是否启用了回顶功能"""
        return self.server_settings.get(str(guild_id), True)  # 默认启用
    
    @commands.Cog.listener()
    async def on_ready(self):
        """当 Cog 加载完成时触发"""
        print(f'🔝 回顶功能 Cog 已加载')
        print(f'🤖 正在监听 "/回顶"、"／回顶" 和 "回顶" 消息...')
    
    @discord.app_commands.command(name='huiding_toggle', description='开启或关闭回顶检测功能')
    @discord.app_commands.describe(enabled='是否启用回顶检测（True=启用，False=关闭）')
    @discord.app_commands.default_permissions(manage_guild=True)
    async def huiding_toggle(self, interaction: discord.Interaction, enabled: bool):
        """控制回顶功能的开关"""
        
        # 检查权限：需要管理服务器权限
        if not interaction.user.guild_permissions.manage_guild:
            embed = discord.Embed(
                title="❌ 权限不足",
                description="只有具有「管理服务器」权限的用户才能使用此命令。",
                color=0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        guild_id = str(interaction.guild.id)
        self.server_settings[guild_id] = enabled
        self.save_settings()
        
        status = "✅ 已启用" if enabled else "❌ 已关闭"
        embed = discord.Embed(
            title="🔝 回顶功能设置",
            description=f"{status} 回顶检测功能\n\n"
                       f"📋 **当前状态**: {'启用' if enabled else '关闭'}\n"
                       f"🏠 **服务器**: {interaction.guild.name}\n"
                       f"👤 **操作者**: {interaction.user.mention}",
            color=0x00ff00 if enabled else 0xff9900
        )
        
        if enabled:
            embed.add_field(
                name="ℹ️ 使用说明", 
                value="用户现在可以在任意频道发送 `/回顶`、`／回顶` 或 `回顶` 来获取该频道的首楼链接",
                inline=False
            )
        else:
            embed.add_field(
                name="ℹ️ 提醒", 
                value="回顶检测已关闭，用户发送回顶指令时不会有任何响应", 
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        print(f'⚙️ 服务器 {interaction.guild.name} ({guild_id}) 回顶功能: {status}')
    
    @discord.app_commands.command(name='huiding_status', description='查看当前服务器的回顶功能状态')
    @discord.app_commands.default_permissions(manage_guild=True)
    async def huiding_status(self, interaction: discord.Interaction):
        """查看回顶功能状态"""
        
        guild_id = str(interaction.guild.id)
        enabled = self.is_huiding_enabled(interaction.guild.id)
        
        embed = discord.Embed(
            title="📊 回顶功能状态",
            description=f"🏠 **服务器**: {interaction.guild.name}\n"
                       f"📋 **当前状态**: {'✅ 启用' if enabled else '❌ 关闭'}\n"
                       f"👤 **查询者**: {interaction.user.mention}",
            color=0x00ff00 if enabled else 0xff9900
        )
        
        if enabled:
            embed.add_field(
                name="💡 如何使用", 
                value="在任意频道发送 `/回顶`、`／回顶` 或 `回顶` 即可获取该频道的首楼链接",
                inline=False
            )
            embed.add_field(
                name="🔧 管理功能", 
                value="具有管理服务器权限的用户可以使用 `/huiding_toggle` 来开启或关闭此功能", 
                inline=False
            )
        else:
            embed.add_field(
                name="🔧 如何启用", 
                value="具有管理服务器权限的用户可以使用 `/huiding_toggle True` 来启用此功能", 
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """监听消息事件"""
        # 忽略机器人自己发送的消息
        if message.author == self.bot.user:
            return
        
        # 确保在服务器中（而非私信）
        if not message.guild:
            return
        
        # 检查服务器是否启用了回顶功能
        if not self.is_huiding_enabled(message.guild.id):
            return
        
        # 检测是否为 "/回顶"、"／回顶" 或单独的 "回顶" 指令（支持全角斜杠）
        if message.content.strip() in ['/回顶', '／回顶', '回顶']:
            try:
                # 获取当前频道
                channel = message.channel
                
                # 获取频道历史消息，从最早的开始
                messages = []
                async for msg in channel.history(limit=None, oldest_first=True):
                    messages.append(msg)
                    if len(messages) >= 1:  # 只需要第一条消息
                        break
                
                if messages:
                    first_message = messages[0]
                    
                    # 构建首楼消息链接
                    message_url = f"https://discord.com/channels/{message.guild.id}/{channel.id}/{first_message.id}"
                    
                    # 构建回复消息
                    embed = discord.Embed(
                        title="🔝 回到顶楼",
                        description=f"📍 **频道**: {channel.mention}\n"
                                   f"🔗 **首楼链接**: [点击跳转]({message_url})\n"
                                   f"📅 **首楼时间**: {first_message.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
                        color=0x00ff00
                    )
                    
                    # 如果首楼有内容，显示预览
                    if first_message.content:
                        preview = first_message.content[:100] + "..." if len(first_message.content) > 100 else first_message.content
                        embed.add_field(name="📝 首楼内容预览", value=f"```{preview}```", inline=False)
                    
                    # 统计与显示用户使用次数（在页脚小字显示）
                    usage_count = self.increment_usage_count(message.guild.id, message.author.id)
                    footer_text = f"首楼作者: {first_message.author.display_name} • 茉莉已经为你提供了{usage_count}次回顶链接"
                    embed.set_footer(text=footer_text, icon_url=first_message.author.display_avatar.url)
                    
                    # 发送回复消息（不使用 delete_after，改为统一调度清理）
                    reply_msg = await message.reply(embed=embed)
                    # 调度在 CLEANUP_DELAY 秒后同时删除机器人回复与触发消息
                    self.bot.loop.create_task(self._schedule_cleanup(channel, message, reply_msg))
                    # 给原消息添加反应表示已处理
                    await message.add_reaction('✅')
                    
                    print(f'📤 回顶功能已为用户 {message.author} 提供频道 #{channel.name} 的首楼链接')
                    
                else:
                    embed = discord.Embed(
                        title="❌ 操作失败",
                        description="抱歉，无法获取此频道的首楼信息。",
                        color=0xff0000
                    )
                    
                    # 发送临时回复消息（5分钟后自动删除）
                    await message.reply(embed=embed, delete_after=300)
                    await message.add_reaction('❌')
                    
                    print(f'⚠️ 回顶功能无法获取频道 #{channel.name} 的首楼信息')
                    
            except discord.Forbidden:
                embed = discord.Embed(
                    title="❌ 权限不足",
                    description="机器人没有足够的权限访问此频道的历史消息。",
                    color=0xff0000
                )
                
                # 发送临时回复消息（5分钟后自动删除）
                await message.reply(embed=embed, delete_after=300)
                await message.add_reaction('❌')
                
                print(f'⚠️ 回顶功能权限不足，无法访问频道 #{channel.name} 的历史消息')
                
            except discord.HTTPException as e:
                embed = discord.Embed(
                    title="❌ 网络错误",
                    description="访问 Discord API 时发生错误，请稍后再试。",
                    color=0xff0000
                )
                
                # 发送临时回复消息（5分钟后自动删除）
                await message.reply(embed=embed, delete_after=300)
                await message.add_reaction('❌')
                
                print(f'❌ 回顶功能 HTTP 错误: {e}')
                
            except Exception as e:
                embed = discord.Embed(
                    title="❌ 系统错误",
                    description="处理请求时发生未知错误，请联系管理员。",
                    color=0xff0000
                )
                
                # 发送临时回复消息（5分钟后自动删除）
                await message.reply(embed=embed, delete_after=300)
                await message.add_reaction('❌')
                
                print(f'❌ 回顶功能处理指令时发生错误: {e}')


    async def _schedule_cleanup(
        self,
        channel: discord.TextChannel,
        trigger_message: discord.Message,
        reply_message: discord.Message
    ):
        """
        在 CLEANUP_DELAY 秒后同时删除机器人回复与触发回顶的原消息。
        - 已被删除则忽略
        - 无权限删除用户消息时，根据 SILENT_ON_PERMISSION_ERROR 决定是否在频道提示
        """
        try:
            await asyncio.sleep(self.CLEANUP_DELAY)
        except Exception:
            # 即便 sleep 被取消，也不阻塞后续清理尝试
            pass

        # 优先删除机器人回复消息（删除自己消息通常不需要额外权限）
        try:
            await reply_message.delete()
        except (discord.NotFound, AttributeError):
            # 已被删除或对象无效，忽略
            pass
        except discord.HTTPException:
            # 网络/速率限制问题，忽略
            pass

        # 删除触发回顶的原消息
        try:
            await trigger_message.delete()
        except discord.Forbidden:
            # 缺少删除他人消息的权限
            if not self.SILENT_ON_PERMISSION_ERROR:
                try:
                    await channel.send("⚠️ 权限不足：无法删除触发回顶的原消息。", delete_after=10)
                except Exception:
                    # 无法在频道发提示也忽略（例如无发送消息权限或频道已不可用）
                    pass
        except (discord.NotFound, AttributeError):
            # 已被删除或对象无效，忽略
            pass
        except discord.HTTPException:
            # 网络/速率限制问题，忽略
            pass


async def setup(bot):
    """Cog 设置函数"""
    await bot.add_cog(HuidingCog(bot))