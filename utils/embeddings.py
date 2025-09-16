"""
模块名称: embeddings.py
功能描述: 统一的Embed生成器，用于创建各种格式化的Discord嵌入消息
作者: @Kilo Code
创建日期: 2024-09-15
最后修改: 2024-09-15
"""

from typing import Optional, List, Dict, Any, Union
import discord
from datetime import datetime

from core.constants import BEIJING_TZ, EMBED_COLOR


class EmbedBuilder:
    """Embed构建器类"""
    
    @staticmethod
    def create_base_embed(
        title: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[discord.Color] = None,
        timestamp: bool = True
    ) -> discord.Embed:
        """
        创建基础Embed
        
        Args:
            title: 标题
            description: 描述
            color: 颜色
            timestamp: 是否添加时间戳
            
        Returns:
            Discord Embed对象
        """
        embed = discord.Embed(
            title=title,
            description=description,
            color=color or EMBED_COLOR['default']
        )
        
        if timestamp:
            embed.timestamp = datetime.now(BEIJING_TZ)
        
        return embed
    
    @staticmethod
    def create_gym_info_embed(gym_data: Dict[str, Any]) -> discord.Embed:
        """
        创建道馆信息Embed
        
        Args:
            gym_data: 道馆数据
            
        Returns:
            Discord Embed对象
        """
        embed = EmbedBuilder.create_base_embed(
            title=f"道馆信息 - {gym_data['name']}",
            description=gym_data.get('description', '无描述'),
            color=EMBED_COLOR['info']
        )
        
        # 添加道馆详细信息
        if gym_data.get('questions_to_ask'):
            embed.add_field(
                name="题目数量",
                value=f"{gym_data['questions_to_ask']} / {len(gym_data.get('questions', []))}",
                inline=True
            )
        else:
            embed.add_field(
                name="题目数量",
                value=len(gym_data.get('questions', [])),
                inline=True
            )
        
        if gym_data.get('allowed_mistakes') is not None:
            embed.add_field(
                name="允许错误",
                value=gym_data['allowed_mistakes'],
                inline=True
            )
        
        embed.add_field(
            name="状态",
            value="✅ 开放" if gym_data.get('is_enabled', True) else "⏸️ 维护中",
            inline=True
        )
        
        if gym_data.get('badge_image_url'):
            embed.set_thumbnail(url=gym_data['badge_image_url'])
        
        return embed
    
    @staticmethod
    def create_progress_embed(
        user: discord.User,
        completed_gyms: int,
        total_gyms: int,
        recent_completions: Optional[List[str]] = None
    ) -> discord.Embed:
        """
        创建进度展示Embed
        
        Args:
            user: 用户对象
            completed_gyms: 已完成道馆数
            total_gyms: 总道馆数
            recent_completions: 最近完成的道馆列表
            
        Returns:
            Discord Embed对象
        """
        progress_percentage = (completed_gyms / total_gyms * 100) if total_gyms > 0 else 0
        
        embed = EmbedBuilder.create_base_embed(
            title=f"{user.display_name} 的道馆挑战进度",
            description=f"已完成 **{completed_gyms}/{total_gyms}** 个道馆 ({progress_percentage:.1f}%)",
            color=EMBED_COLOR['success'] if completed_gyms == total_gyms else EMBED_COLOR['info']
        )
        
        # 添加进度条
        progress_bar = EmbedBuilder._create_progress_bar(progress_percentage)
        embed.add_field(name="进度", value=progress_bar, inline=False)
        
        # 添加最近完成的道馆
        if recent_completions:
            recent_text = "\n".join([f"• {gym}" for gym in recent_completions[:5]])
            embed.add_field(name="最近完成", value=recent_text, inline=False)
        
        embed.set_footer(text="继续努力，挑战更多道馆！")
        
        return embed
    
    @staticmethod
    def create_error_embed(
        message: str,
        title: str = "错误",
        details: Optional[str] = None
    ) -> discord.Embed:
        """
        创建错误提示Embed
        
        Args:
            message: 错误消息
            title: 错误标题
            details: 详细信息
            
        Returns:
            Discord Embed对象
        """
        embed = EmbedBuilder.create_base_embed(
            title=f"❌ {title}",
            description=message,
            color=EMBED_COLOR['error']
        )
        
        if details:
            embed.add_field(name="详细信息", value=details, inline=False)
        
        return embed
    
    @staticmethod
    def create_success_embed(
        message: str,
        title: str = "成功",
        details: Optional[str] = None
    ) -> discord.Embed:
        """
        创建成功消息Embed
        
        Args:
            message: 成功消息
            title: 成功标题
            details: 详细信息
            
        Returns:
            Discord Embed对象
        """
        embed = EmbedBuilder.create_base_embed(
            title=f"✅ {title}",
            description=message,
            color=EMBED_COLOR['success']
        )
        
        if details:
            embed.add_field(name="详细信息", value=details, inline=False)
        
        return embed
    
    @staticmethod
    def create_leaderboard_embed(
        guild: discord.Guild,
        leaderboard_data: List[Dict[str, Any]],
        title: Optional[str] = None,
        description: Optional[str] = None
    ) -> discord.Embed:
        """
        创建排行榜Embed
        
        Args:
            guild: 服务器对象
            leaderboard_data: 排行榜数据
            title: 自定义标题
            description: 自定义描述
            
        Returns:
            Discord Embed对象
        """
        embed_title = title or f"🏆 {guild.name} - 究极道馆排行榜"
        embed_desc = description or "记录着本服最快完成究极道馆挑战的英雄们。"
        
        embed = EmbedBuilder.create_base_embed(
            title=embed_title,
            description=embed_desc,
            color=EMBED_COLOR['gold']
        )
        
        if not leaderboard_data:
            embed.description += "\n\n目前还没有人完成挑战，快来成为第一人吧！"
        else:
            lines = []
            for i, entry in enumerate(leaderboard_data[:20]):  # 显示前20名
                rank = i + 1
                user_id = entry['user_id']
                time_seconds = entry['completion_time_seconds']
                
                # 格式化时间
                minutes, seconds = divmod(time_seconds, 60)
                time_str = f"{int(minutes)}分 {seconds:.2f}秒"
                
                # 获取用户显示名
                member = guild.get_member(int(user_id))
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
            
            embed.add_field(name="排行榜", value="\n".join(lines), inline=False)
        
        return embed
    
    @staticmethod
    def create_badge_wall_embed(
        user: discord.User,
        badges: List[Dict[str, Any]],
        current_page: int = 0,
        per_page: int = 5
    ) -> discord.Embed:
        """
        创建徽章墙Embed
        
        Args:
            user: 用户对象
            badges: 徽章列表
            current_page: 当前页码
            per_page: 每页显示数量
            
        Returns:
            Discord Embed对象
        """
        total_pages = (len(badges) - 1) // per_page + 1
        start_idx = current_page * per_page
        end_idx = min(start_idx + per_page, len(badges))
        page_badges = badges[start_idx:end_idx]
        
        embed = EmbedBuilder.create_base_embed(
            title=f"🏆 {user.display_name} 的徽章墙",
            description=f"已获得 **{len(badges)}** 个道馆徽章",
            color=EMBED_COLOR['gold']
        )
        
        for badge in page_badges:
            badge_value = badge.get('badge_description', '已通过考核')
            if badge.get('badge_image_url'):
                badge_value += f"\n[查看徽章]({badge['badge_image_url']})"
            
            embed.add_field(
                name=f"🎖️ {badge['name']}",
                value=badge_value,
                inline=False
            )
        
        if total_pages > 1:
            embed.set_footer(text=f"第 {current_page + 1}/{total_pages} 页")
        else:
            embed.set_footer(text="继续努力，收集更多徽章！")
        
        return embed
    
    @staticmethod
    def _create_progress_bar(percentage: float, length: int = 20) -> str:
        """
        创建进度条字符串
        
        Args:
            percentage: 百分比 (0-100)
            length: 进度条长度
            
        Returns:
            进度条字符串
        """
        filled = int(length * percentage / 100)
        empty = length - filled
        
        bar = "█" * filled + "░" * empty
        return f"[{bar}] {percentage:.1f}%"
    
    @staticmethod
    def create_panel_embed(
        panel_type: str,
        introduction: Optional[str] = None,
        guild_name: Optional[str] = None
    ) -> discord.Embed:
        """
        创建面板Embed
        
        Args:
            panel_type: 面板类型
            introduction: 自定义介绍文字
            guild_name: 服务器名称
            
        Returns:
            Discord Embed对象
        """
        if panel_type == "challenge":
            title = "道馆挑战中心"
            default_desc = "欢迎来到道馆挑战中心！在这里，你可以通过挑战不同的道馆来学习和证明你的能力。\n\n点击下方的按钮，开始你的挑战吧！"
            color = EMBED_COLOR['info']
        elif panel_type == "ultimate":
            title = "🏆 究极道馆挑战"
            default_desc = (
                "**欢迎来到究极道馆挑战！**\n\n"
                "在这里，你将面临来自服务器 **所有道馆** 的终极考验。\n"
                "系统将从总题库中随机抽取 **50%** 的题目，你的目标是在最短的时间内全部正确回答。\n\n"
                "**规则:**\n"
                "- **零容错**: 答错任何一题即挑战失败。\n"
                "- **计时排名**: 你的完成时间将被记录，并计入服务器排行榜。\n\n"
                "准备好证明你的实力了吗？"
            )
            color = EMBED_COLOR['special']
        elif panel_type == "graduation":
            title = "道馆毕业资格认证"
            default_desc = f"祝贺所有坚持不懈的挑战者！\n\n当你完成了本服务器 **所有** 的道馆挑战后，点击下方的按钮，即可领取属于你的最终荣誉！"
            color = EMBED_COLOR['gold']
        elif panel_type == "badge":
            title = "徽章墙展示中心"
            default_desc = "这里是徽章墙展示中心。\n\n点击下方的按钮，来展示你通过努力获得的道馆徽章吧！"
            color = EMBED_COLOR['purple']
        else:
            title = "未知面板"
            default_desc = "面板类型未知"
            color = EMBED_COLOR['default']
        
        embed = EmbedBuilder.create_base_embed(
            title=title,
            description=introduction or default_desc,
            color=color,
            timestamp=False
        )
        
        return embed