import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

from core.database import db_manager
from utils.logger import get_logger


class BaseCog(commands.Cog):
    """所有Cog的基类"""
    
    def __init__(self, bot: commands.Bot):
        """
        初始化Cog
        
        Args:
            bot: Discord Bot实例
        """
        self.bot = bot
        self.logger = get_logger(self.__class__.__name__)
        self.db = db_manager
    
    async def cog_load(self) -> None:
        """Cog加载时调用"""
        self.logger.info(f"{self.__class__.__name__} 已加载")
    
    async def cog_unload(self) -> None:
        """Cog卸载时调用"""
        self.logger.info(f"{self.__class__.__name__} 已卸载")
    
    async def cog_check(self, ctx: commands.Context) -> bool:
        """
        全局Cog检查，在执行任何命令前调用
        
        Args:
            ctx: 命令上下文
            
        Returns:
            是否允许执行命令
        """
        # 确保命令在服务器中执行（非私聊）
        if ctx.guild is None:
            await ctx.send("此命令只能在服务器中使用。")
            return False
        return True
    
    async def cog_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError
    ) -> None:
        """
        Cog级别的错误处理
        
        Args:
            ctx: 命令上下文
            error: 错误对象
        """
        # 记录错误
        self.logger.error(
            f"命令错误 [{ctx.command}]: {error}",
            exc_info=error
        )
        
        # 处理常见错误
        if isinstance(error, commands.CommandNotFound):
            return  # 忽略未找到的命令
        
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ 缺少必要参数: {error.param.name}")
        
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ 参数错误: {error}")
        
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("❌ 你没有执行此命令的权限。")
        
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"⏰ 命令冷却中，请在 {error.retry_after:.1f} 秒后重试。"
            )
        
        else:
            # 其他未处理的错误
            await ctx.send("❌ 执行命令时发生错误，请稍后重试。")
    
    async def send_embed(
        self,
        ctx: commands.Context,
        title: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[discord.Color] = None,
        **kwargs
    ) -> discord.Message:
        """
        发送嵌入消息的便捷方法
        
        Args:
            ctx: 命令上下文
            title: 标题
            description: 描述
            color: 颜色
            **kwargs: 其他嵌入参数
            
        Returns:
            发送的消息对象
        """
        embed = discord.Embed(
            title=title,
            description=description,
            color=color or discord.Color.blue()
        )
        
        # 添加其他字段
        for key, value in kwargs.items():
            if key == "fields":
                for field in value:
                    embed.add_field(**field)
            else:
                setattr(embed, key, value)
        
        return await ctx.send(embed=embed)
    
    async def send_error(
        self,
        ctx: commands.Context,
        message: str,
        title: str = "错误"
    ) -> discord.Message:
        """
        发送错误消息
        
        Args:
            ctx: 命令上下文
            message: 错误消息
            title: 错误标题
            
        Returns:
            发送的消息对象
        """
        return await self.send_embed(
            ctx,
            title=f"❌ {title}",
            description=message,
            color=discord.Color.red()
        )
    
    async def send_success(
        self,
        ctx: commands.Context,
        message: str,
        title: str = "成功"
    ) -> discord.Message:
        """
        发送成功消息
        
        Args:
            ctx: 命令上下文
            message: 成功消息
            title: 成功标题
            
        Returns:
            发送的消息对象
        """
        return await self.send_embed(
            ctx,
            title=f"✅ {title}",
            description=message,
            color=discord.Color.green()
        )

    def log_action(
        self,
        action: str,
        user_id: str,
        guild_id: Optional[str] = None,
        extra: Optional[dict] = None,
        level: int = logging.INFO
    ) -> None:
        """
        统一的业务操作日志：包含操作类型与用户ID，便于审计与排查。
        示例：ACTION=CHALLENGE_START user=123 guild=456 panel_id=789
        """
        try:
            parts = [f"ACTION={action}", f"user={user_id}"]
            if guild_id:
                parts.append(f"guild={guild_id}")
            if extra:
                for k, v in extra.items():
                    parts.append(f"{k}={v}")
            self.logger.log(level, " ".join(parts))
        except Exception as e:
            # 保证日志失败不会影响业务流程
            self.logger.error(f"Failed to log action '{action}' for user {user_id}: {e}", exc_info=True)


def setup_cog(bot: commands.Bot, cog_class: type) -> None:
    """
    通用的Cog设置函数
    
    Args:
        bot: Discord Bot实例
        cog_class: Cog类
    """
    asyncio.create_task(bot.add_cog(cog_class(bot)))


# 错误处理装饰器
def handle_errors(func):
    """
    命令错误处理装饰器
    
    Args:
        func: 要装饰的异步函数
        
    Returns:
        装饰后的函数
    """
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except Exception as e:
            # 获取上下文
            ctx = None
            for arg in args:
                if isinstance(arg, commands.Context):
                    ctx = arg
                    break
                elif isinstance(arg, discord.Interaction):
                    # 处理斜杠命令
                    if not arg.response.is_done():
                        await arg.response.send_message(
                            "❌ 执行命令时发生错误。",
                            ephemeral=True
                        )
                    return
            
            if ctx:
                await self.send_error(ctx, str(e))
            
            # 记录错误
            if hasattr(self, 'logger'):
                self.logger.error(f"命令执行错误: {e}", exc_info=True)
    
    return wrapper