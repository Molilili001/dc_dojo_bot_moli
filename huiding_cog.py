import discord
from discord.ext import commands
import json
import os


class HuidingCog(commands.Cog):
    """回顶功能 Cog - 检测 '/回顶'、'／回顶' 或 '回顶' 消息并回复首楼链接"""
    
    def __init__(self, bot):
        self.bot = bot
        self.server_settings = {}
        self.settings_file = 'huiding_settings.json'
        self.load_settings()
    
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
                    
                    embed.set_footer(text=f"首楼作者: {first_message.author.display_name}", 
                                   icon_url=first_message.author.display_avatar.url)
                    
                    # 发送临时回复消息（5分钟后自动删除）
                    await message.reply(embed=embed, delete_after=300)
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


async def setup(bot):
    """Cog 设置函数"""
    await bot.add_cog(HuidingCog(bot))