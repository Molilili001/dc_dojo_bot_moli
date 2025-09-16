"""
模块名称: formatters.py
功能描述: 格式化工具，用于生成进度条、表格、时间格式化等
作者: @Kilo Code
创建日期: 2024-09-15
最后修改: 2024-09-15
"""

import discord
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import pytz
import re

# 时区配置
BEIJING_TZ = pytz.timezone('Asia/Shanghai')


def format_time(dt: datetime, format_str: str = '%Y-%m-%d %H:%M:%S') -> str:
    """
    格式化时间为北京时间
    
    Args:
        dt: datetime对象
        format_str: 格式化字符串
    
    Returns:
        格式化后的时间字符串
    """
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    beijing_time = dt.astimezone(BEIJING_TZ)
    return beijing_time.strftime(format_str)


def format_duration(seconds: float) -> str:
    """
    格式化持续时间
    
    Args:
        seconds: 秒数
    
    Returns:
        格式化的持续时间字符串
    """
    if seconds < 0:
        return "0秒"
    
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}小时")
    if minutes > 0:
        parts.append(f"{minutes}分钟")
    if secs > 0 or not parts:
        parts.append(f"{secs}秒")
    
    return " ".join(parts)


def format_timedelta(td: timedelta) -> str:
    """
    格式化时间差
    
    Args:
        td: timedelta对象
    
    Returns:
        格式化的时间差字符串
    """
    total_seconds = td.total_seconds()
    hours, remainder = divmod(int(total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours > 0:
        return f"{hours}小时 {minutes}分钟"
    else:
        return f"{minutes}分钟 {seconds}秒"


def create_progress_bar(current: int, total: int, length: int = 20, 
                       filled_char: str = "█", empty_char: str = "░") -> str:
    """
    创建进度条
    
    Args:
        current: 当前值
        total: 总值
        length: 进度条长度
        filled_char: 填充字符
        empty_char: 空字符
    
    Returns:
        进度条字符串
    """
    if total == 0:
        return empty_char * length
    
    filled_length = int(length * current // total)
    empty_length = length - filled_length
    
    progress_bar = filled_char * filled_length + empty_char * empty_length
    percentage = (current / total) * 100
    
    return f"[{progress_bar}] {percentage:.1f}%"


def format_gym_list(gyms: List[Dict[str, Any]]) -> str:
    """
    格式化道馆列表
    
    Args:
        gyms: 道馆列表
    
    Returns:
        格式化的道馆列表字符串
    """
    if not gyms:
        return "暂无道馆"
    
    lines = []
    for gym in gyms:
        status_emoji = "✅" if gym.get('is_enabled', True) else "⏸️"
        badge_emoji = "🖼️" if gym.get('badge_image_url') else "➖"
        lines.append(
            f"{status_emoji} **{gym['name']}** `(ID: {gym['id']})` "
            f"- 徽章: {badge_emoji}"
        )
    
    return "\n".join(lines)


def format_leaderboard(entries: List[Dict[str, Any]], guild_name: str) -> discord.Embed:
    """
    格式化排行榜
    
    Args:
        entries: 排行榜条目
        guild_name: 服务器名称
    
    Returns:
        Discord Embed对象
    """
    embed = discord.Embed(
        title=f"🏆 {guild_name} - 究极道馆排行榜 🏆",
        description="记录着本服最快完成究极道馆挑战的英雄们。",
        color=discord.Color.gold()
    )
    
    if not entries:
        embed.description += "\n\n目前还没有人完成挑战，快来成为第一人吧！"
    else:
        lines = []
        for i, entry in enumerate(entries[:20]):  # 只显示前20名
            rank = i + 1
            time_seconds = entry['completion_time_seconds']
            minutes, seconds = divmod(time_seconds, 60)
            time_str = f"{int(minutes)}分 {seconds:.2f}秒"
            
            # 添加排名表情
            if rank == 1:
                rank_emoji = "🥇"
            elif rank == 2:
                rank_emoji = "🥈"
            elif rank == 3:
                rank_emoji = "🥉"
            else:
                rank_emoji = f"`#{rank:02d}`"
            
            lines.append(f"{rank_emoji} **用户{entry['user_id']}** - `{time_str}`")
        
        embed.description += "\n\n" + "\n".join(lines)
    
    embed.set_footer(text=f"最后更新于: {format_time(datetime.now())}")
    return embed


def format_error_message(error: Exception, context: Optional[str] = None) -> str:
    """
    格式化错误消息
    
    Args:
        error: 异常对象
        context: 错误上下文
    
    Returns:
        格式化的错误消息
    """
    error_type = type(error).__name__
    error_msg = str(error)
    
    if context:
        return f"❌ **错误** ({context})\n类型: `{error_type}`\n详情: {error_msg}"
    else:
        return f"❌ **错误**\n类型: `{error_type}`\n详情: {error_msg}"


def format_user_progress(completed_gyms: int, total_gyms: int) -> str:
    """
    格式化用户进度
    
    Args:
        completed_gyms: 已完成的道馆数
        total_gyms: 总道馆数
    
    Returns:
        格式化的进度字符串
    """
    if total_gyms == 0:
        return "暂无道馆"
    
    percentage = (completed_gyms / total_gyms) * 100
    progress_bar = create_progress_bar(completed_gyms, total_gyms, length=10)
    
    return (f"**进度**: {completed_gyms}/{total_gyms} ({percentage:.1f}%)\n"
            f"{progress_bar}")


def format_badge_wall(badges: List[Dict[str, Any]], user_name: str) -> discord.Embed:
    """
    格式化徽章墙
    
    Args:
        badges: 徽章列表
        user_name: 用户名
    
    Returns:
        Discord Embed对象
    """
    embed = discord.Embed(
        title=f"{user_name}的徽章墙",
        color=discord.Color.gold()
    )
    
    if not badges:
        embed.description = "还没有获得任何徽章"
    else:
        embed.description = f"共获得 **{len(badges)}** 个徽章"
        
        # 添加徽章展示
        for i, badge in enumerate(badges[:25], 1):  # Discord限制25个字段
            gym_name = badge.get('name', f'道馆{i}')
            badge_desc = badge.get('badge_description', '完成道馆挑战获得')
            embed.add_field(
                name=f"🏅 {gym_name}",
                value=badge_desc[:100],  # 限制描述长度
                inline=True
            )
    
    return embed


def format_wrong_answers(wrong_answers: List[tuple], show_correct: bool = True) -> List[Dict[str, Any]]:
    """
    格式化错题列表
    
    Args:
        wrong_answers: 错题列表 [(question, user_answer), ...]
        show_correct: 是否显示正确答案
    
    Returns:
        格式化的字段列表
    """
    if not wrong_answers:
        return []
    
    fields = []
    current_field_text = ""
    
    for i, (question, wrong_answer) in enumerate(wrong_answers):
        question_text = question['text']
        entry_text = f"**题目**: {question_text}\n**你的答案**: `{wrong_answer}`\n"
        
        if show_correct:
            correct_answer = question['correct_answer']
            if isinstance(correct_answer, list):
                correct_answer_str = ' 或 '.join(f"`{ans}`" for ans in correct_answer)
            else:
                correct_answer_str = f"`{correct_answer}`"
            entry_text += f"**正确答案**: {correct_answer_str}\n"
        
        entry_text += "\n"
        
        # Discord embed字段值限制为1024字符
        if len(current_field_text) + len(entry_text) > 1024:
            field_name = "错题回顾" if not fields else "错题回顾 (续)"
            fields.append({
                "name": field_name,
                "value": current_field_text,
                "inline": False
            })
            current_field_text = ""
        
        current_field_text += entry_text
    
    # 添加最后一个字段
    if current_field_text:
        field_name = "错题回顾" if not fields else "错题回顾 (续)"
        fields.append({
            "name": field_name,
            "value": current_field_text,
            "inline": False
        })
    
    return fields


def truncate_text(text: str, max_length: int = 1024, suffix: str = "...") -> str:
    """
    截断文本到指定长度
    
    Args:
        text: 原始文本
        max_length: 最大长度
        suffix: 截断后缀
    
    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix


def format_blacklist_entry(entry: Dict[str, Any]) -> str:
    """
    格式化黑名单条目
    
    Args:
        entry: 黑名单条目
    
    Returns:
        格式化的黑名单条目字符串
    """
    target_type = "用户" if entry['target_type'] == 'user' else "身份组"
    reason = entry.get('reason', '无')
    added_by = f"<@{entry.get('added_by', '未知')}>"
    
    try:
        timestamp = datetime.fromisoformat(entry['timestamp'])
        time_str = format_time(timestamp, '%Y-%m-%d %H:%M')
    except (ValueError, TypeError, KeyError):
        time_str = "未知时间"
    
    return (f"**对象**: {target_type} `{entry['target_id']}`\n"
            f"**原因**: {reason}\n"
            f"**操作人**: {added_by}\n"
            f"**时间**: {time_str}")


class FormatUtils:
    """格式化工具类，提供静态方法接口"""
    
    @staticmethod
    def format_time(dt: datetime, format_str: str = '%Y-%m-%d %H:%M:%S') -> str:
        """格式化时间"""
        return format_time(dt, format_str)
    
    @staticmethod
    def format_duration(seconds: float) -> str:
        """格式化持续时间"""
        return format_duration(seconds)
    
    @staticmethod
    def format_timedelta(td: timedelta) -> str:
        """格式化时间差"""
        return format_timedelta(td)
    
    @staticmethod
    def create_progress_bar(current: int, total: int, length: int = 20,
                           filled_char: str = "█", empty_char: str = "░") -> str:
        """创建进度条"""
        return create_progress_bar(current, total, length, filled_char, empty_char)
    
    @staticmethod
    def format_gym_list(gyms: List[Dict[str, Any]]) -> str:
        """格式化道馆列表"""
        return format_gym_list(gyms)
    
    @staticmethod
    def format_leaderboard(entries: List[Dict[str, Any]], guild_name: str) -> discord.Embed:
        """格式化排行榜"""
        return format_leaderboard(entries, guild_name)
    
    @staticmethod
    def format_error_message(error: Exception, context: Optional[str] = None) -> str:
        """格式化错误消息"""
        return format_error_message(error, context)
    
    @staticmethod
    def format_user_progress(completed_gyms: int, total_gyms: int) -> str:
        """格式化用户进度"""
        return format_user_progress(completed_gyms, total_gyms)
    
    @staticmethod
    def format_badge_wall(badges: List[Dict[str, Any]], user_name: str) -> discord.Embed:
        """格式化徽章墙"""
        return format_badge_wall(badges, user_name)
    
    @staticmethod
    def format_wrong_answers(wrong_answers: List[tuple], show_correct: bool = True) -> List[Dict[str, Any]]:
        """格式化错题列表"""
        return format_wrong_answers(wrong_answers, show_correct)
    
    @staticmethod
    def truncate_text(text: str, max_length: int = 1024, suffix: str = "...") -> str:
        """截断文本"""
        return truncate_text(text, max_length, suffix)
    
    @staticmethod
    def format_blacklist_entry(entry: Dict[str, Any]) -> str:
        """格式化黑名单条目"""
        return format_blacklist_entry(entry)


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """
    清理文件名，移除非法字符
    
    Args:
        filename: 原始文件名
        max_length: 最大长度
    
    Returns:
        清理后的文件名
    """
    # 移除Windows文件名非法字符
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # 移除控制字符
    filename = re.sub(r'[\x00-\x1f\x7f]', '', filename)
    # 去除首尾空格和点
    filename = filename.strip('. ')
    # 限制长度
    if len(filename) > max_length:
        filename = filename[:max_length]
    # 如果结果为空，使用默认名称
    if not filename:
        filename = "unnamed"
    return filename