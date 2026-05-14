import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

from core.constants import CONFIG_PATH, DATA_DIR, LOG_DIR, DEVELOPER_IDS
from core.database import db_manager
from utils.logger import get_logger

# 初始化日志
logger = get_logger("bot")


class DiscordBot(commands.Bot):
    """自定义Discord Bot类"""
    
    def __init__(self, config: dict):
        """
        初始化Bot
        
        Args:
            config: 配置字典
        """
        # Cog中文名映射
        self.cog_name_mapping = {
            # 英文名 -> 中文名
            "GymManagementCog": "道馆管理",
            "GymChallengeCog": "道馆挑战",
            "UserProgressCog": "用户进度",
            "LeaderboardCog": "排行榜",
            "ModerationCog": "管理功能",
            "PanelsCog": "面板管理",
            "AdminCog": "管理员命令",
            "DeveloperCog": "开发者工具",
            "AutoMonitorCog": "自动监控",
            "CrossBotSyncCog": "跨bot联动",
            "ForumPostMonitorCog": "投诉监听",
            "TodoListCog": "事件列表",
            "FeedbackCog": "反馈",
            "ThreadCommandCog": "帖子命令",
            "MemberMonitorCog": "成员监控",
            # 中文名 -> 英文名（反向映射）
            "道馆管理": "GymManagementCog",
            "道馆挑战": "GymChallengeCog",
            "用户进度": "UserProgressCog",
            "排行榜": "LeaderboardCog",
            "管理功能": "ModerationCog",
            "面板管理": "PanelsCog",
            "管理员命令": "AdminCog",
            "开发者工具": "DeveloperCog",
            "自动监控": "AutoMonitorCog",
            "跨bot联动": "CrossBotSyncCog",
            "帖子监控": "ForumPostMonitorCog",
            "投诉监听": "ForumPostMonitorCog",
            "事件列表": "TodoListCog",
            "反馈": "FeedbackCog",
            "帖子命令": "ThreadCommandCog",
            "成员监控": "MemberMonitorCog"
        }
        # 设置intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        intents.typing = False  # 优化：禁用typing事件
        intents.presences = False  # 优化：禁用presence事件
        
        # 检查是否需要使用代理
        proxy_config = config.get("PROXY", {})
        if proxy_config.get("enabled", False):
            proxy_url = proxy_config.get("url")
            if proxy_url:
                logger.info(f"使用代理: {proxy_url}")
                # 创建自定义的aiohttp连接器
                connector = aiohttp.TCPConnector(
                    force_close=True,
                    enable_cleanup_closed=True
                )
                # 创建自定义的http session
                session = aiohttp.ClientSession(
                    connector=connector,
                    trust_env=True  # 使用环境变量中的代理设置
                )
                # 设置环境变量以使用代理
                os.environ['HTTP_PROXY'] = proxy_url
                os.environ['HTTPS_PROXY'] = proxy_url
                os.environ['http_proxy'] = proxy_url
                os.environ['https_proxy'] = proxy_url
            else:
                logger.warning("代理已启用但未提供URL")
                session = None
        else:
            session = None
        
        # 初始化父类
        super().__init__(
            command_prefix=config.get("PREFIX", "!"),
            intents=intents,
            chunk_guilds_at_startup=False,
            member_cache_flags=discord.MemberCacheFlags.none(),
            proxy=proxy_config.get("url") if proxy_config.get("enabled", False) else None,
            http_session=session
        )
        
        self.config = config
        self.initial_cogs = [
            # 核心功能模块
            "cogs.gym_management",    # 道馆管理
            "cogs.gym_challenge",     # 道馆挑战
            "cogs.user_progress",     # 用户进度
            "cogs.leaderboard",       # 排行榜
            # 管理功能模块
            "cogs.moderation",        # 管理功能
            "cogs.panels",            # 面板管理
            "cogs.admin",             # 管理员命令
            "cogs.developer",         # 开发者工具
            "cogs.auto_monitor",      # 自动监控
            "cogs.forum_post_monitor",# 帖子监控
            "cogs.cross_bot_sync",    # 跨bot联动
            "cogs.todo_list",         # 事件列表
            "cogs.feedback",          # 反馈
            "cogs.thread_command",    # 帖子命令（回顶功能升级版）
            "cogs.member_monitor",    # 成员监控
        ]
        
        # 可选Cogs（向后兼容）
        self.optional_cogs = [
            # huiding_cog 已被 thread_command 替代，保留以防需要回滚
        ]
    
    async def setup_hook(self) -> None:
        """Bot启动前的设置"""
        logger.info("开始初始化Bot...")
        
        # 初始化数据库
        await self._setup_database()
        
        # 加载Cogs
        await self._load_cogs()
        
        # 注册持久化视图
        await self._register_persistent_views()
        
        # 同步命令树
        await self._sync_commands()
        
        logger.info("Bot初始化完成")
    
    async def _setup_database(self) -> None:
        """设置数据库"""
        try:
            await db_manager.initialize()
            logger.info("数据库初始化成功")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            raise
    
    async def _load_cogs(self) -> None:
        """加载所有Cogs"""
        # 加载必需的Cogs
        for cog in self.initial_cogs:
            try:
                await self.load_extension(cog)
                cog_name = cog.split('.')[-1]
                
                # 为每个Cog添加特定的成功消息（使用print显示在控制台）
                if cog_name == "gym_management":
                    print("🏛️ 道馆管理 Cog 已加载")
                    logger.info("道馆管理 Cog 已加载")
                elif cog_name == "gym_challenge":
                    print("⚔️ 道馆挑战 Cog 已加载")
                    logger.info("道馆挑战 Cog 已加载")
                elif cog_name == "user_progress":
                    print("📊 用户进度 Cog 已加载")
                    logger.info("用户进度 Cog 已加载")
                elif cog_name == "leaderboard":
                    print("🏆 排行榜 Cog 已加载")
                    logger.info("排行榜 Cog 已加载")
                elif cog_name == "moderation":
                    print("🛡️ 管理功能 Cog 已加载")
                    logger.info("管理功能 Cog 已加载")
                elif cog_name == "panels":
                    print("📋 面板管理 Cog 已加载")
                    logger.info("面板管理 Cog 已加载")
                elif cog_name == "admin":
                    print("👑 管理员命令 Cog 已加载")
                    logger.info("管理员命令 Cog 已加载")
                elif cog_name == "developer":
                    print("🔧 开发者工具 Cog 已加载")
                    logger.info("开发者工具 Cog 已加载")
                elif cog_name == "auto_monitor":
                    print("👁️ 自动监控 Cog 已加载")
                    logger.info("自动监控 Cog 已加载")
                elif cog_name == "forum_post_monitor":
                    print("🧾 投诉监听 Cog 已加载")
                    logger.info("投诉监听 Cog 已加载")
                elif cog_name == "cross_bot_sync":
                    print("🔄 跨bot联动 Cog 已加载")
                    logger.info("跨bot联动 Cog 已加载")
                elif cog_name == "todo_list":
                    print("📝 事件列表 Cog 已加载")
                    logger.info("事件列表 Cog 已加载")
                elif cog_name == "feedback":
                    print("💬 反馈 Cog 已加载")
                    logger.info("反馈 Cog 已加载")
                elif cog_name == "thread_command":
                    print("🔝 帖子命令 Cog 已加载")
                    print('🤖 正在监听自定义触发词（包含回顶功能）...')
                    logger.info("帖子命令 Cog 已加载")
                elif cog_name == "member_monitor":
                    print("👥 成员监控 Cog 已加载")
                    logger.info("成员监控 Cog 已加载")
                else:
                    print(f"✅ {cog_name} Cog 已加载")
                    logger.info(f"{cog_name} Cog 已加载")
                    
            except Exception as e:
                print(f"❌ 加载Cog失败 [{cog}]: {e}")
                logger.error(f"加载Cog失败 [{cog}]: {e}")
        
        # 尝试加载可选的Cogs（如果存在）
        for cog in self.optional_cogs:
            try:
                await self.load_extension(cog)
                if cog == "huiding_cog":
                    print("🔝 回顶功能 Cog 已加载")
                    print('🤖 正在监听 "/回顶"、"／回顶" 和 "回顶" 消息...')
                else:
                    logger.info(f"✅ 成功加载可选Cog: {cog}")
            except Exception as e:
                logger.debug(f"可选Cog未加载 [{cog}]: {e}")
    
    async def _sync_commands(self) -> None:
        """同步斜杠命令"""
        try:
            synced = await self.tree.sync()
            logger.info(f"同步了 {len(synced)} 个命令")
        except Exception as e:
            logger.error(f"命令同步失败: {e}")
    
    async def _register_persistent_views(self) -> None:
        """注册持久化视图"""
        try:
            # 延迟导入视图以避免循环导入
            from views.challenge_views import MainChallengeView
            from views.panel_views import BadgePanelView, GraduationPanelView
            # LeaderboardView 在 LeaderboardCog 中注册
            
            # 注册视图
            self.add_view(MainChallengeView())
            self.add_view(BadgePanelView())
            self.add_view(GraduationPanelView())
            
            logger.info("持久化视图注册成功")
        except Exception as e:
            logger.error(f"持久化视图注册失败: {e}")
    
    async def on_ready(self) -> None:
        """Bot准备就绪时触发"""
        print("="*50)
        print(f"🎉 Bot已成功登录!")
        print(f"📛 Bot用户名: {self.user.name}")
        print(f"🆔 Bot ID: {self.user.id}")
        print(f"🌐 已连接到 {len(self.guilds)} 个服务器")
        
        # 列出所有连接的服务器
        for guild in self.guilds:
            print(f"  - {guild.name} (ID: {guild.id})")
        
        print("="*50)
        
        # 同时记录到日志文件
        logger.info(f"Bot已成功登录: {self.user.name} (ID: {self.user.id})")
        logger.info(f"已连接到 {len(self.guilds)} 个服务器")
        
        # 设置状态
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="道馆挑战"
            )
        )
        
        # 启动定时任务
        await self._start_background_tasks()
    
    async def on_message(self, message: discord.Message) -> None:
        """处理消息事件"""
        # 忽略自己的消息或服务器外的消息
        if message.author == self.user or not message.guild:
            return
        
        # 处理自动黑名单监控
        await self._handle_auto_blacklist_monitor(message)
        
        # 处理命令
        await self.process_commands(message)
    
    async def _handle_auto_blacklist_monitor(self, message: discord.Message) -> None:
        """处理自动黑名单监控"""
        # AutoMonitorCog 会通过 on_message 监听器自动处理
        # 这里的方法保留是为了向后兼容，但实际处理已经移到 AutoMonitorCog
        pass
    
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """加入新服务器时触发"""
        logger.info(f"加入新服务器: {guild.name} (ID: {guild.id})")
    
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """离开服务器时触发"""
        logger.info(f"离开服务器: {guild.name} (ID: {guild.id})")
    
    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ) -> None:
        """全局斜杠命令错误处理（兼容交互超时/重复响应场景）。"""
        if isinstance(error, app_commands.CheckFailure):
            msg = "❌ 你没有执行此指令所需的权限。"
        else:
            logger.error(
                f"命令错误 [{interaction.command.name if interaction.command else 'unknown'}]: {error}",
                exc_info=True
            )
            msg = "🤖 执行指令时发生未知错误。"

        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.NotFound:
            # 交互已过期（常见于超过响应窗口）
            logger.warning("on_app_command_error: interaction expired (NotFound)")
        except discord.HTTPException as e:
            logger.warning(f"on_app_command_error: failed to send error response: {e}")
        except Exception as e:
            logger.error(f"on_app_command_error: unexpected handler error: {e}", exc_info=True)
    
    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError
    ) -> None:
        """全局命令错误处理"""
        # 如果错误已在Cog中处理，则忽略
        if hasattr(ctx.command, 'on_error'):
            return
        
        # 忽略的错误类型
        ignored = (commands.CommandNotFound,)
        if isinstance(error, ignored):
            return
        
        # 记录错误
        logger.error(f"命令错误: {error}", exc_info=error)
        
        # 发送错误消息
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ 你没有执行此命令的权限。")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ 缺少必要参数: {error.param.name}")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ 参数错误: {error}")
        else:
            await ctx.send("❌ 执行命令时发生错误，请稍后重试。")
    
    async def _start_background_tasks(self) -> None:
        """启动后台任务"""
        try:
            # 启动道馆备份任务（为每个服务器启动）
            from utils.backup import start_daily_backup_task
            for guild in self.guilds:
                asyncio.create_task(start_daily_backup_task(self, str(guild.id)))
                logger.info(f"为服务器 {guild.name} 启动备份任务")
            logger.info("后台任务启动成功")
        except Exception as e:
            logger.error(f"后台任务启动失败: {e}")
    
    async def close(self) -> None:
        """关闭Bot时的清理工作"""
        logger.info("正在关闭Bot...")
        
        # 停止所有后台任务
        try:
            # 取消所有备份任务
            if hasattr(self, 'backup_tasks'):
                for task in self.backup_tasks.values():
                    if not task.done():
                        task.cancel()
        except:
            pass
        
        # 关闭自定义的http session（如果存在）
        if hasattr(self, 'http') and hasattr(self.http, '_HTTPClient__session'):
            session = self.http._HTTPClient__session
            if session and not session.closed:
                await session.close()
        
        await super().close()
        logger.info("Bot已关闭")


def load_config() -> dict:
    """
    加载配置文件
    
    Returns:
        配置字典
    """
    if not CONFIG_PATH.exists():
        logger.error(f"配置文件不存在: {CONFIG_PATH}")
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}")
    
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info("配置文件加载成功")
        return config
    except json.JSONDecodeError as e:
        logger.error(f"配置文件格式错误: {e}")
        raise
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}")
        raise


def setup_directories() -> None:
    """创建必要的目录"""
    # 使用当前文件夹下的data目录存放日志
    log_dir = Path(__file__).parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 确保其他必要目录存在
    data_dir = Path(__file__).parent / "data"
    backup_dir = data_dir / "gym_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info("目录结构创建完成")


async def main():
    """主函数"""
    try:
        # 设置目录
        setup_directories()
        
        # 加载配置
        config = load_config()
        
        # 输出代理配置信息（用于调试）
        proxy_config = config.get("PROXY", {})
        if proxy_config.get("enabled", False):
            logger.info(f"代理配置已启用: {proxy_config.get('url', '未设置')}")
        else:
            logger.info("未启用代理")
        
        # 创建并启动Bot
        bot = DiscordBot(config)
        
        # 启动Bot
        async with bot:
            await bot.start(config['BOT_TOKEN'])
            
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
    except Exception as e:
        logger.error(f"Bot运行失败: {e}")
        logger.error(f"错误详情: {type(e).__name__}: {str(e)}")
        if "Cannot connect to host discord.com" in str(e):
            logger.error("无法连接到Discord服务器。请检查：")
            logger.error("1. 网络连接是否正常")
            logger.error("2. 是否需要配置代理")
            logger.error("3. 防火墙是否阻止了连接")
        raise


if __name__ == "__main__":
    # 运行主函数
    asyncio.run(main())