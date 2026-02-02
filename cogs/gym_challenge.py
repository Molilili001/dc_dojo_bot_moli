import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from cogs.base_cog import BaseCog
from core.database import DatabaseManager
from core.models import Gym, UserProgress, ChallengeFailure, Question
from core.exceptions import ValidationError
from utils.formatters import format_time, format_timedelta, format_wrong_answers
from utils.logger import get_logger
from utils.time_utils import (
    format_beijing_display,
    format_beijing_iso,
    get_beijing_now,
    parse_beijing_time,
    remaining_until,
)

logger = get_logger(__name__)


class ChallengeSession:
    """挑战会话类，管理用户的挑战状态"""
    
    def __init__(self, user_id: str, guild_id: str, gym_id: str, 
                 gym_info: dict, panel_message_id: int):
        """
        初始化挑战会话
        
        Args:
            user_id: 用户ID
            guild_id: 服务器ID
            gym_id: 道馆ID
            gym_info: 道馆信息
            panel_message_id: 触发挑战的面板消息ID
        """
        self.user_id = user_id
        self.guild_id = guild_id
        self.gym_id = gym_id
        self.gym_info = gym_info
        self.panel_message_id = panel_message_id
        self.is_ultimate = gym_info.get('is_ultimate', False)
        self.start_time = time.time()
        self.current_question_index = 0
        self.mistakes_made = 0
        self.wrong_answers = []  # [(question, user_answer), ...]
        self.allowed_mistakes = gym_info.get('allowed_mistakes', 0)
        self.randomize_options = gym_info.get('randomize_options', True)
        
        # 随机题目逻辑
        self.questions_for_session = gym_info.get('questions', [])
        num_to_ask = gym_info.get('questions_to_ask')
        orig_total = len(self.questions_for_session)
        
        if num_to_ask and isinstance(num_to_ask, int) and num_to_ask > 0:
            # 对于究极道馆，抽样已在创建会话前完成
            if not self.is_ultimate and num_to_ask <= orig_total:
                self.questions_for_session = random.sample(self.questions_for_session, num_to_ask)
                try:
                    logger.warning(f"[session-init] user={self.user_id} gym={self.gym_id} is_ultimate={self.is_ultimate} total={orig_total} to_ask={num_to_ask} sampled={len(self.questions_for_session)}")
                except Exception:
                    pass
            else:
                try:
                    logger.warning(f"[session-init] user={self.user_id} gym={self.gym_id} is_ultimate={self.is_ultimate} total={orig_total} to_ask={num_to_ask} no-sample")
                except Exception:
                    pass
        else:
            try:
                logger.warning(f"[session-init] user={self.user_id} gym={self.gym_id} is_ultimate={self.is_ultimate} total={orig_total} to_ask={num_to_ask} (ignored or invalid)")
            except Exception:
                pass
    
    def get_current_question(self) -> Optional[dict]:
        """
        获取当前题目
        
        Returns:
            当前题目字典，如果没有则返回None
        """
        if self.current_question_index < len(self.questions_for_session):
            return self.questions_for_session[self.current_question_index]
        return None
    
    def check_answer(self, user_answer: str) -> bool:
        """
        检查答案是否正确
        
        Args:
            user_answer: 用户答案
        
        Returns:
            是否正确
        """
        question = self.get_current_question()
        if not question:
            return False
        
        correct_answer = question['correct_answer']
        
        # 处理多答案的情况（填空题）
        if isinstance(correct_answer, list):
            return any(user_answer.lower() == str(ans).lower() for ans in correct_answer)
        else:
            return user_answer.lower() == str(correct_answer).lower()
    
    def record_mistake(self, user_answer: str):
        """
        记录错误答案
        
        Args:
            user_answer: 用户的错误答案
        """
        question = self.get_current_question()
        if question:
            self.mistakes_made += 1
            self.wrong_answers.append((question, user_answer))
    
    def is_failed(self) -> bool:
        """检查是否挑战失败"""
        if self.is_ultimate:
            # 究极道馆不允许任何错误
            return self.mistakes_made > 0
        else:
            # 普通道馆根据允许的错误数判断
            return self.mistakes_made > self.allowed_mistakes
    
    def advance_to_next_question(self):
        """前进到下一题"""
        self.current_question_index += 1
    
    def is_completed(self) -> bool:
        """检查是否完成所有题目"""
        return self.current_question_index >= len(self.questions_for_session)
    
    def get_completion_time(self) -> float:
        """获取完成时间（秒）"""
        return time.time() - self.start_time
    
    def get_progress_info(self) -> str:
        """获取进度信息字符串"""
        current = self.current_question_index + 1
        total = len(self.questions_for_session)
        return f"题目 {current}/{total}"


class GymChallengeCog(BaseCog):
    """道馆挑战Cog"""
    
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.active_challenges: Dict[str, ChallengeSession] = {}
        self.user_challenge_locks: Dict[str, asyncio.Lock] = {}
    
    async def cog_unload(self):
        """卸载Cog时清理"""
        self.active_challenges.clear()
        self.user_challenge_locks.clear()
    
    def _cleanup_user_session(self, user_id: str):
        """
        清理用户的挑战会话和锁对象
        
        仅在挑战真正结束时调用（成功/失败/取消/超时），
        不在"清理旧会话以开始新挑战"的场景中调用。
        """
        if user_id in self.active_challenges:
            del self.active_challenges[user_id]
        if user_id in self.user_challenge_locks:
            del self.user_challenge_locks[user_id]
    
    # ========== 挑战管理方法 ==========
    
    async def handle_challenge_start(self, interaction: discord.Interaction):
        """处理挑战开始（从面板按钮调用）"""
        try:
            guild_id = str(interaction.guild.id)
            user_id = str(interaction.user.id)
            panel_message_id = str(interaction.message.id)
            
            logger.info(f"handle_challenge_start called - User: {user_id}, Guild: {guild_id}, Panel: {panel_message_id}")
            
            # 检查封禁名单
            ban_entry = await self._get_challenge_ban_entry(guild_id, interaction.user)
            if ban_entry:
                ban_message = self._format_challenge_ban_message(ban_entry, interaction.user)
                if interaction.response.is_done():
                    await interaction.followup.send(ban_message, ephemeral=True)
                else:
                    await interaction.response.send_message(ban_message, ephemeral=True)
                return
            
            # 检查并清理任何可能存在的旧会话
            if user_id in self.active_challenges:
                logger.warning(f"Found existing challenge session for user {user_id}, cleaning up")
                del self.active_challenges[user_id]
            
            # 获取面板配置
            async with self.db.get_connection() as conn:
                conn.row_factory = self.db.dict_row
                async with conn.execute(
                    "SELECT is_ultimate_gym, associated_gyms, prerequisite_gyms FROM challenge_panels WHERE message_id = ?",
                    (panel_message_id,)
                ) as cursor:
                    panel_config = await cursor.fetchone()
            
            logger.info(f"Panel config: {panel_config}")
            
            if not panel_config:
                # 老面板兼容性处理 - 自动创建默认配置
                logger.info(f"No panel config found for message {panel_message_id}, creating default config for legacy panel")
                
                # 检查消息内容来判断面板类型
                is_ultimate = False
                try:
                    # 通过embed标题判断是否是究极道馆
                    if interaction.message.embeds:
                        embed_title = interaction.message.embeds[0].title or ""
                        embed_desc = interaction.message.embeds[0].description or ""
                        # 检查标题或描述中是否包含究极道馆的关键词
                        if "究极" in embed_title or "究极" in embed_desc or "ultimate" in embed_title.lower():
                            is_ultimate = True
                except Exception as e:
                    logger.warning(f"Error checking embed content: {e}")
                
                # 为老面板创建默认配置
                async with self.db.get_connection() as conn:
                    await conn.execute('''
                        INSERT OR IGNORE INTO challenge_panels
                        (message_id, guild_id, channel_id, is_ultimate_gym)
                        VALUES (?, ?, ?, ?)
                    ''', (panel_message_id, guild_id, str(interaction.channel.id), is_ultimate))
                    await conn.commit()
                
                logger.info(f"Created default config for legacy panel: ultimate={is_ultimate}")
                
                # 使用默认配置继续
                panel_config = {
                    'is_ultimate_gym': is_ultimate,
                    'associated_gyms': None,
                    'prerequisite_gyms': None
                }
            
            # 如果是究极道馆
            if panel_config['is_ultimate_gym']:
                logger.info("Starting ultimate challenge")
                await self.start_ultimate_challenge(interaction, panel_message_id)
            else:
                # 普通道馆，显示道馆列表
                logger.info("Showing gym list")
                await self.show_gym_list(interaction, panel_message_id)
                
        except Exception as e:
            logger.error(f"Error in handle_challenge_start: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 处理挑战时发生错误: {str(e)}",
                ephemeral=True
            )
    
    async def show_gym_list(self, interaction: discord.Interaction, panel_message_id: str = None):
        """显示道馆列表供选择"""
        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)
        
        # 全局封禁检查：即使面板关闭黑名单功能也不可挑战
        ban_entry = await self._get_challenge_ban_entry(guild_id, interaction.user)
        if ban_entry:
            ban_message = self._format_challenge_ban_message(ban_entry, interaction.user)
            if interaction.response.is_done():
                await interaction.followup.send(ban_message, ephemeral=True)
            else:
                await interaction.response.send_message(ban_message, ephemeral=True)
            return
        
        # 检查并清理可能存在的旧会话
        if user_id in self.active_challenges:
            logger.info(f"Cleaning up stale challenge session for user {user_id} before showing gym list")
            del self.active_challenges[user_id]
        
        # 获取面板配置（如果有）
        panel_config = None
        if panel_message_id:
            async with self.db.get_connection() as conn:
                conn.row_factory = self.db.dict_row
                async with conn.execute(
                    "SELECT associated_gyms, prerequisite_gyms FROM challenge_panels WHERE message_id = ?",
                    (panel_message_id,)
                ) as cursor:
                    panel_config = await cursor.fetchone()
        
        # 获取所有道馆
        all_gyms = await self._get_all_guild_gyms(guild_id)
        
        # 筛选可用道馆
        available_gyms = []
        user_progress = await self._get_user_progress(user_id, guild_id)
        
        for gym in all_gyms:
            # 跳过禁用的道馆
            if not gym.get('is_enabled', True):
                continue
            
            # 如果有关联道馆配置，只显示关联的道馆
            if panel_config and panel_config['associated_gyms']:
                import json
                associated_gym_ids = json.loads(panel_config['associated_gyms'])
                if gym['id'] not in associated_gym_ids:
                    continue
            
            # 检查前置道馆
            if panel_config and panel_config['prerequisite_gyms']:
                import json
                prerequisite_gym_ids = json.loads(panel_config['prerequisite_gyms'])
                if not all(prereq in user_progress for prereq in prerequisite_gym_ids):
                    continue
            
            available_gyms.append(gym)
        
        if not available_gyms:
            # 确保正确响应交互
            if interaction.response.is_done():
                await interaction.followup.send(
                    "没有可用的道馆可供挑战。可能所有道馆都已完成或不满足前置条件。",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "没有可用的道馆可供挑战。可能所有道馆都已完成或不满足前置条件。",
                    ephemeral=True
                )
            return
        
        # 创建道馆选择视图
        from views.challenge_views import GymSelectView
        view = GymSelectView(available_gyms, user_progress, int(panel_message_id) if panel_message_id else 0)
        
        embed = discord.Embed(
            title="选择道馆",
            description="请选择一个道馆进行挑战：",
            color=discord.Color.blue()
        )
        
        # 发送道馆列表 - 确保正确响应交互
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def handle_gym_selection(self, interaction: discord.Interaction, gym_id: str, panel_message_id: int):
        """处理道馆选择"""
        # 注意：interaction已经在GymSelect.callback中延迟响应了
        await self.start_challenge(interaction, gym_id, panel_message_id)
    
    async def start_ultimate_challenge(self, interaction: discord.Interaction, panel_message_id: str):
        """开始究极道馆挑战"""
        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)
        
        # 自动清理旧的挑战会话
        if user_id in self.active_challenges:
            logger.info(f"Auto-clearing old challenge session for user {user_id} before starting ultimate challenge")
            del self.active_challenges[user_id]
        
        # 检查封禁名单
        ban_entry = await self._get_challenge_ban_entry(guild_id, interaction.user)
        if ban_entry:
            ban_message = self._format_challenge_ban_message(ban_entry, interaction.user)
            if interaction.response.is_done():
                await interaction.followup.send(ban_message, ephemeral=True)
            else:
                await interaction.response.send_message(ban_message, ephemeral=True)
            return
        
        # 获取所有道馆题目
        all_gyms = await self._get_all_guild_gyms(guild_id)
        enabled_gyms = [gym for gym in all_gyms if gym.get('is_enabled', True)]
        
        if not enabled_gyms:
            await interaction.followup.send(
                "❌ 服务器内没有任何已启用的道馆，无法进行究极挑战。",
                ephemeral=True
            )
            return
        
        # 收集所有题目
        all_questions = []
        for gym in enabled_gyms:
            gym_info = await self._get_gym_info(guild_id, gym['id'])
            if gym_info and gym_info.get('questions'):
                questions = gym_info['questions']
                for q in questions:
                    q['gym_name'] = gym_info['name']  # 添加道馆名称标记
                all_questions.extend(questions)
        
        if not all_questions:
            await interaction.followup.send(
                "❌ 服务器内的道馆没有配置题目，无法进行究极挑战。",
                ephemeral=True
            )
            return
        
        # 随机抽取50%的题目
        num_questions = max(1, len(all_questions) // 2)
        selected_questions = random.sample(all_questions, num_questions)
        
        # 创建究极道馆会话
        gym_info = {
            'id': 'ultimate',
            'name': '究极道馆挑战',
            'description': '来自所有道馆的终极考验',
            'tutorial': [
                "**欢迎来到究极道馆挑战！**",
                "",
                f"你将面对从服务器所有道馆随机抽取的 **{num_questions}** 道题目。",
                "**规则：**",
                "• 零容错 - 答错任何一题即挑战失败",
                "• 计时排名 - 你的完成时间将被记录到排行榜",
                "",
                "准备好了吗？点击下方按钮开始挑战！"
            ],
            'questions': selected_questions,
            'questions_to_ask': num_questions,
            'allowed_mistakes': 0,
            'is_ultimate': True,
            'is_enabled': True,
            'randomize_options': True
        }
        
        # 创建挑战会话
        from cogs.gym_challenge import ChallengeSession
        session = ChallengeSession(user_id, guild_id, 'ultimate', gym_info, int(panel_message_id))
        self.active_challenges[user_id] = session
        
        logger.info(f"Ultimate challenge session created for user {user_id}")
        
        # 显示教程
        await self._show_tutorial(interaction, session)
    
    async def display_question(self, interaction: discord.Interaction, session):
        """显示第一个问题"""
        await self._display_next_question(interaction, session)
    
    async def handle_challenge_cancel(self, interaction: discord.Interaction, user_id: str):
        """处理挑战取消"""
        await self.cancel_challenge(interaction)
    
    async def handle_challenge_timeout(self, user_id: str, session):
        """处理挑战超时"""
        # 清理会话和锁
        self._cleanup_user_session(user_id)
        logger.info(f"Challenge session timed out for user {user_id}")
    
    async def process_answer(self, interaction: discord.Interaction, session,
                            answer: str, is_correct: bool, from_modal: bool = False):
        """处理用户答案（新版本）"""
        user_id = session.user_id
        guild_id = session.guild_id

        # 按优先级再次检查封禁状态
        ban_entry = await self._get_challenge_ban_entry(guild_id, interaction.user)
        if ban_entry:
            self._cleanup_user_session(user_id)
            ban_message = self._format_challenge_ban_message(ban_entry, interaction.user)
            try:
                await interaction.edit_original_response(content=ban_message, embed=None, view=None)
            except Exception:
                await interaction.followup.send(ban_message, ephemeral=True)
            return
        
        # 确保用户锁存在
        if user_id not in self.user_challenge_locks:
            self.user_challenge_locks[user_id] = asyncio.Lock()
        
        # 添加日志来追踪处理流程
        logger.info(f"Processing answer for user {user_id}. Answer: {answer}, Correct: {is_correct}")
        
        if not is_correct:
            session.record_mistake(answer)
            logger.info(f"User {user_id} answered incorrectly. "
                      f"Mistakes: {session.mistakes_made}/{session.allowed_mistakes}")
            
            # 究极道馆立即失败
            if session.is_ultimate:
                await self._handle_challenge_failure(interaction, session, from_modal)
                return
            
            # 检查是否超过允许的错误数
            if session.is_failed():
                await self._handle_challenge_failure(interaction, session, from_modal)
                return
        else:
            logger.info(f"User {user_id} answered correctly.")
        
        # 前进到下一题
        session.advance_to_next_question()
        logger.info(f"Advanced to question {session.current_question_index + 1}/{len(session.questions_for_session)}")
        
        # 检查是否完成
        if session.is_completed():
            logger.info(f"User {user_id} completed all questions.")
            await self._handle_challenge_success(interaction, session, from_modal)
        else:
            logger.info(f"Displaying next question for user {user_id}")
            await self._display_next_question(interaction, session, from_modal)
    
    async def start_challenge(self, interaction: discord.Interaction,
                            gym_id: str, panel_message_id: int):
        """
        开始挑战
        
        Args:
            interaction: Discord交互
            gym_id: 道馆ID
            panel_message_id: 面板消息ID
        """
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)
        
        # 获取用户锁
        if user_id not in self.user_challenge_locks:
            self.user_challenge_locks[user_id] = asyncio.Lock()
        
        async with self.user_challenge_locks[user_id]:
            # 自动清理旧的挑战会话，而不是报错
            if user_id in self.active_challenges:
                logger.info(f"Auto-clearing old challenge session for user {user_id} in gym {self.active_challenges[user_id].gym_id}")
                del self.active_challenges[user_id]
            
            # 获取道馆信息
            gym_info = await self._get_gym_info(guild_id, gym_id)
            if not gym_info:
                await interaction.edit_original_response(
                    content="❌ 找不到该道馆的数据，可能已被删除。",
                    view=None,
                    embed=None
                )
                return
            
            # 检查道馆是否启用
            if not gym_info.get('is_enabled', True):
                await interaction.edit_original_response(
                    content="⏸️ 此道馆正在维护中，暂时无法挑战。",
                    view=None,
                    embed=None
                )
                return
            
            # 检查挑战封禁名单
            ban_entry = await self._get_challenge_ban_entry(guild_id, interaction.user)
            if ban_entry:
                ban_message = self._format_challenge_ban_message(ban_entry, interaction.user)
                await interaction.edit_original_response(
                    content=ban_message,
                    view=None,
                    embed=None
                )
                return
            
            # 检查用户是否已完成该道馆
            user_progress = await self._get_user_progress(user_id, guild_id)
            if gym_id in user_progress:
                await interaction.edit_original_response(
                    content="✅ 你已经完成过这个道馆的挑战了！",
                    view=None,
                    embed=None
                )
                return
            
            # 检查冷却时间
            failure_status = await self._get_failure_status(user_id, guild_id, gym_id)
            if failure_status and failure_status['banned_until']:
                banned_until = parse_beijing_time(failure_status['banned_until'])
                remaining = remaining_until(banned_until)
                if remaining:
                    time_str = format_timedelta(remaining)
                    unlock_at = format_beijing_display(banned_until)
                    logger.info(
                        "User %s is still banned from gym %s until %s (remaining %s)",
                        user_id,
                        gym_id,
                        unlock_at,
                        time_str,
                    )
                    await interaction.edit_original_response(
                        content=(
                            "❌ **挑战冷却中**\n\n"
                            "由于多次挑战失败，你暂时无法挑战该道馆。\n"
                            f"请在 **{time_str}** 后再试。\n"
                            f"解封时间（北京时间）：`{unlock_at}`"
                        ),
                        view=None,
                        embed=None
                    )
                    return
            
            # 创建挑战会话
            session = ChallengeSession(user_id, guild_id, gym_id, gym_info, panel_message_id)
            self.active_challenges[user_id] = session
            
            logger.info(f"Challenge session created for user {user_id} in gym {gym_id}")
            
            # 显示教程
            await self._show_tutorial(interaction, session)
    
    
    async def cancel_challenge(self, interaction: discord.Interaction):
        """
        取消挑战
        
        Args:
            interaction: Discord交互
        """
        user_id = str(interaction.user.id)
        session = self.active_challenges.get(user_id)
        
        if not session:
            # 根据响应状态选择编辑方法，兼容已 defer 的组件回调
            if interaction.response.is_done():
                return await interaction.edit_original_response(
                    content="没有正在进行的挑战或已超时。",
                    view=None,
                    embed=None
                )
            else:
                return await interaction.response.edit_message(
                    content="没有正在进行的挑战或已超时。",
                    view=None,
                    embed=None
                )
        
        guild_id = str(session.guild_id)
        
        # 只对普通道馆计算失败惩罚
        if not session.is_ultimate:
            await self._increment_failure(user_id, guild_id, session.gym_id)
            fail_desc = "你主动放弃了本次挑战，这被计为一次失败。"
            title = "❌ 挑战已取消并计为失败"
        else:
            fail_desc = "你主动放弃了本次究极道馆挑战。"
            title = "↩️ 挑战已取消"
        
        # 清理会话和锁
        self._cleanup_user_session(user_id)
        logger.info(f"Challenge session cancelled by user {user_id} in gym {session.gym_id}")
        
        embed = discord.Embed(
            title=title,
            description=fail_desc,
            color=discord.Color.red()
        )
        
        # 根据响应状态选择编辑方法，兼容已 defer 的组件回调
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=None,
                embed=embed,
                view=None
            )
        else:
            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=None
            )
    
    # ========== 辅助方法 ==========
    
    async def _get_gym_info(self, guild_id: str, gym_id: str) -> Optional[dict]:
        """获取道馆信息"""
        async with self.db.get_connection() as conn:
            async with conn.execute('''
                SELECT name, description, tutorial, questions,
                       questions_to_ask, allowed_mistakes, badge_image_url,
                       badge_description, is_enabled, randomize_options
                FROM gyms WHERE guild_id = ? AND gym_id = ?
            ''', (guild_id, gym_id)) as cursor:
                row = await cursor.fetchone()
        
        if not row:
            return None
        
        return {
            'id': gym_id,
            'name': row[0],
            'description': row[1],
            'tutorial': json.loads(row[2]),
            'questions': json.loads(row[3]),
            'questions_to_ask': row[4],
            'allowed_mistakes': row[5] if row[5] is not None else 0,
            'badge_image_url': row[6],
            'badge_description': row[7],
            'is_enabled': row[8],
            'randomize_options': row[9] if row[9] is not None else True
        }
    
    async def _get_user_progress(self, user_id: str, guild_id: str) -> dict:
        """获取用户进度"""
        async with self.db.get_connection() as conn:
            async with conn.execute(
                "SELECT gym_id FROM user_progress WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            ) as cursor:
                rows = await cursor.fetchall()
        # 返回字典而不是集合，保持与UserProgressCog一致
        return {row[0]: True for row in rows}
    
    async def _get_failure_status(self, user_id: str, guild_id: str, gym_id: str) -> Optional[dict]:
        """获取失败状态"""
        async with self.db.get_connection() as conn:
            async with conn.execute(
                "SELECT failure_count, banned_until FROM challenge_failures "
                "WHERE user_id = ? AND guild_id = ? AND gym_id = ?",
                (user_id, guild_id, gym_id)
            ) as cursor:
                row = await cursor.fetchone()
        
        if row:
            return {
                'failure_count': row[0],
                'banned_until': row[1]
            }
        return None
    
    async def _get_challenge_ban_entry(self, guild_id: str, member: discord.Member) -> Optional[dict]:
        """检查挑战封禁名单"""
        async with self.db.get_connection() as conn:
            conn.row_factory = self.db.dict_row
            # 先检查用户被单独封禁
            async with conn.execute(
                """
                SELECT reason, added_by, timestamp, target_type, target_id
                FROM challenge_ban_list
                WHERE guild_id = ? AND target_type = 'user' AND target_id = ?
                LIMIT 1
                """,
                (guild_id, str(member.id))
            ) as cursor:
                entry = await cursor.fetchone()
                if entry:
                    return dict(entry)
            
            # 再检查用户的身份组是否被封禁
            role_ids = [str(role.id) for role in member.roles if role is not None]
            if not role_ids:
                return None
            
            placeholders = ','.join('?' for _ in role_ids)
            query = f"""
                SELECT reason, added_by, timestamp, target_type, target_id
                FROM challenge_ban_list
                WHERE guild_id = ? AND target_type = 'role'
                AND target_id IN ({placeholders})
                ORDER BY timestamp DESC
                LIMIT 1
            """
            params = [guild_id] + role_ids
            async with conn.execute(query, params) as cursor:
                role_entry = await cursor.fetchone()
                if role_entry:
                    return dict(role_entry)
        
        return None
    
    def _format_challenge_ban_message(self, entry: dict, member: discord.Member) -> str:
        """格式化挑战封禁通知（不显示封禁人）"""
        reason = entry.get('reason') or "未提供"
        
        timestamp = parse_beijing_time(entry.get('timestamp'))
        timestamp_str = format_beijing_display(timestamp) if timestamp else "未知时间"
        
        target_type = entry.get('target_type')
        target_id = entry.get('target_id')
        if target_type == 'role':
            role = member.guild.get_role(int(target_id)) if member.guild else None
            target_display = role.mention if role else f"身份组 ID `{target_id}`"
        else:
            target_display = member.mention
        
        return (
            "🚫 **挑战封禁限制**\n\n"
            "你目前被禁止挑战本服务器的道馆。\n\n"
            f"• 封禁对象: {target_display}\n"
            f"• 封禁原因: {reason}\n"
            f"• 执行时间: {timestamp_str}\n\n"
            "如需解除封禁，请联系服务器管理人员。"
        )
    
    async def _increment_failure(self, user_id: str, guild_id: str, gym_id: str) -> timedelta:
        """增加失败次数并计算封禁时间"""
        async with self.db.get_connection() as conn:
            # 获取当前失败次数
            current = await self._get_failure_status(user_id, guild_id, gym_id)
            failure_count = (current['failure_count'] if current else 0) + 1
            
            # 计算封禁时间
            ban_duration = timedelta(seconds=0)
            if failure_count == 3:
                ban_duration = timedelta(hours=1)
            elif failure_count == 4:
                ban_duration = timedelta(hours=6)
            elif failure_count >= 5:
                ban_duration = timedelta(hours=12)
            
            banned_until = None
            if ban_duration.total_seconds() > 0:
                banned_until_dt = get_beijing_now() + ban_duration
                banned_until = format_beijing_iso(banned_until_dt)
                logger.info(
                    "User %s banned from gym %s until %s (Beijing time)",
                    user_id,
                    gym_id,
                    banned_until,
                )
            
            # 更新数据库
            await conn.execute('''
                INSERT INTO challenge_failures (user_id, guild_id, gym_id, failure_count, banned_until)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, gym_id) DO UPDATE SET
                failure_count = excluded.failure_count,
                banned_until = excluded.banned_until
            ''', (user_id, guild_id, gym_id, failure_count, banned_until))
            
            await conn.commit()
            
            if ban_duration.total_seconds() > 0:
                logger.info(f"User {user_id} banned from gym {gym_id} for {ban_duration}")
            
            return ban_duration
    
    async def _reset_failures(self, user_id: str, guild_id: str, gym_id: str):
        """重置失败记录"""
        async with self.db.get_connection() as conn:
            await conn.execute(
                "DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ? AND gym_id = ?",
                (user_id, guild_id, gym_id)
            )
            await conn.commit()
    
    async def _set_gym_completed(self, user_id: str, guild_id: str, gym_id: str):
        """标记道馆为已完成"""
        async with self.db.get_connection() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO user_progress (user_id, guild_id, gym_id) VALUES (?, ?, ?)",
                (user_id, guild_id, gym_id)
            )
            await conn.commit()
        logger.info(f"Gym {gym_id} marked as completed for user {user_id}")
    
    async def _update_ultimate_leaderboard(self, guild_id: str, user_id: str, time_seconds: float):
        """更新究极道馆排行榜（新库），并可选同步到旧库以实现数据互通"""
        async with self.db.get_connection() as conn:
            # 检查是否有更好的成绩（新库）
            async with conn.execute(
                "SELECT completion_time_seconds FROM ultimate_gym_leaderboard "
                "WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            ) as cursor:
                existing = await cursor.fetchone()
            
            if existing and time_seconds >= existing[0]:
                # 新成绩不如旧成绩，仍尝试进行旧库同步（保证旧库至少不更差）
                pass
            else:
                # 更新或插入新库成绩
                import pytz
                timestamp = datetime.now(pytz.UTC).isoformat()
                await conn.execute('''
                    INSERT INTO ultimate_gym_leaderboard (guild_id, user_id, completion_time_seconds, timestamp)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    completion_time_seconds = excluded.completion_time_seconds,
                    timestamp = excluded.timestamp
                ''', (guild_id, user_id, time_seconds, timestamp))
                await conn.commit()
                logger.info(f"Updated ultimate leaderboard (new DB) for user {user_id}: {time_seconds:.2f}s")
        
        # 可选：同步到旧库（根据配置启用），实现“数据互通”
        try:
            # 延迟导入，避免循环依赖/启动阶段问题
            from core.database import get_legacy_db_path, DatabaseManager
            from core.constants import BEIJING_TZ
            legacy_path = get_legacy_db_path()
            if legacy_path:
                # 连接旧库
                legacy_db = DatabaseManager(db_path=legacy_path)
                async with legacy_db.get_connection() as lconn:
                    # 查询旧库当前最佳
                    async with lconn.execute(
                        "SELECT completion_time_seconds FROM ultimate_gym_leaderboard WHERE guild_id = ? AND user_id = ?",
                        (guild_id, user_id)
                    ) as cursor:
                        lexisting = await cursor.fetchone()
                    
                    # 仅在新成绩更好时写入旧库（保持“最佳成绩”语义一致）
                    if not lexisting or time_seconds < float(lexisting[0]):
                        l_timestamp = datetime.now(BEIJING_TZ).isoformat()
                        await lconn.execute("""
                            INSERT INTO ultimate_gym_leaderboard (guild_id, user_id, completion_time_seconds, timestamp)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                                completion_time_seconds = excluded.completion_time_seconds,
                                timestamp = excluded.timestamp
                        """, (guild_id, user_id, time_seconds, l_timestamp))
                        await lconn.commit()
                        logger.info(f"Synced ultimate leaderboard to legacy DB for user {user_id}: {time_seconds:.2f}s")
                    else:
                        logger.info(f"Legacy DB has better or equal record for user {user_id}; skip legacy update")
        except Exception as e:
            logger.warning(f"Legacy leaderboard sync failed or disabled: {e}")
    
    async def _show_tutorial(self, interaction: discord.Interaction, session: ChallengeSession):
        """显示教程"""
        tutorial_text = "\n".join(session.gym_info['tutorial'])
        embed = discord.Embed(
            title=f"欢迎来到 {session.gym_info['name']}",
            description=tutorial_text,
            color=discord.Color.blue()
        )
        
        # 导入视图（避免循环导入）
        from views.challenge_views import StartChallengeView
        view = StartChallengeView(session.gym_id)
        
        # 设置超时回调来清理会话
        async def cleanup_on_timeout():
            """超时时清理会话和锁"""
            self._cleanup_user_session(session.user_id)
            logger.info(f"Tutorial view timed out, cleaned up session for user {session.user_id}")
        
        # 保存原始的on_timeout方法
        original_on_timeout = view.on_timeout
        
        # 重写on_timeout方法以包含清理逻辑
        async def enhanced_on_timeout():
            await cleanup_on_timeout()
            if original_on_timeout:
                await original_on_timeout()
        
        view.on_timeout = enhanced_on_timeout
        
        # 究极道馆教程使用私密消息，不修改原面板；普通道馆保持原有编辑行为
        if session.is_ultimate:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                logger.info(f"Sent ultimate challenge tutorial as ephemeral message for user {session.user_id}")
            except Exception as e:
                logger.error(f"Failed to send ultimate tutorial ephemeral message: {e}", exc_info=True)
                # 兜底：若私密消息失败，尝试编辑原始响应以避免交互卡死
                try:
                    await interaction.edit_original_response(content=None, embed=embed, view=view)
                except Exception:
                    # 最后兜底：尝试followup公开消息（极端情况）
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            # 普通道馆：编辑原始消息（选择列表消息）
            # 这样教程会替换选择列表，实现平滑过渡
            await interaction.edit_original_response(
                content=None,  # 清空之前的content
                embed=embed,
                view=view
            )
            logger.info(f"Edited response with tutorial for user {session.user_id} in gym {session.gym_id}")
    
    async def _display_next_question(self, interaction: discord.Interaction,
                                    session: ChallengeSession, from_modal: bool = False):
        """显示下一个题目"""
        # 先执行封禁检查，防止进入题目阶段
        ban_entry = await self._get_challenge_ban_entry(session.guild_id, interaction.user)
        if ban_entry:
            self._cleanup_user_session(session.user_id)
            ban_message = self._format_challenge_ban_message(ban_entry, interaction.user)
            if interaction.response.is_done():
                try:
                    await interaction.edit_original_response(content=ban_message, embed=None, view=None)
                except Exception:
                    await interaction.followup.send(ban_message, ephemeral=True)
            else:
                try:
                    await interaction.response.edit_message(content=ban_message, embed=None, view=None)
                except Exception:
                    await interaction.response.send_message(ban_message, ephemeral=True)
            return

        question = session.get_current_question()
        if not question:
            logger.error(f"No question found for user {session.user_id} at index {session.current_question_index}")
            return
        
        logger.info(f"Displaying question {session.current_question_index + 1} for user {session.user_id}")
        
        # 创建Embed
        # 清理题目文本，防止特殊字符导致显示截断
        safe_q_text = str(question.get('text', '')).replace('\x00', '').strip()
        
        # 长度保护：Description 限制在 2000 字符以内（Discord上限4096，留足空间给其他部分）
        if len(safe_q_text) > 2000:
            safe_q_text = safe_q_text[:2000] + "...\n(题目过长已截断)"

        # 格式保护：自动闭合未匹配的代码块
        # 如果代码块标记 ``` 是奇数个，说明有一个未闭合，补全它
        if safe_q_text.count("```") % 2 != 0:
            safe_q_text += "\n```"

        embed = discord.Embed(
            title=f"{session.gym_info['name']} - {session.get_progress_info()}",
            description=safe_q_text,
            color=discord.Color.orange()
        )
        
        # 导入视图
        from views.challenge_views import QuestionView
        # 设置3分钟（180秒）超时
        view = QuestionView(session, interaction, timeout=180)
        
        # 根据题目类型设置视图
        if question['type'] == 'multiple_choice':
            # 数据完整性验证与诊断日志
            options = question.get('options') or []
            correct_field = question.get('correct_answer')
            if not isinstance(options, list) or len(options) < 2:
                logger.error(f"Invalid MC options for user {session.user_id}: options={options}")
                try:
                    await interaction.followup.send(
                        "❌ 题目数据异常，请联系管理员。",
                        ephemeral=True
                    )
                except Exception:
                    pass
                return

            # 将正确答案统一解析为“选项文本”，以兼容 'A'/'B'/索引 等数据格式
            def _resolve_correct_text(field, opts):
                try:
                    if field is None:
                        return None
                    # 如果本身就是选项文本，直接返回
                    if isinstance(field, str) and field in opts:
                        return field
                    # 字母索引（A/B/C...）
                    if isinstance(field, str):
                        letter = field.strip().upper()
                        if len(letter) == 1 and 'A' <= letter <= 'Z':
                            idx = ord(letter) - ord('A')
                            if 0 <= idx < len(opts):
                                return opts[idx]
                    # 数字索引
                    if isinstance(field, int):
                        if 0 <= field < len(opts):
                            return opts[field]
                    # 列表：尝试解析首项
                    if isinstance(field, list) and field:
                        first = field[0]
                        return _resolve_correct_text(first, opts)
                except Exception:
                    pass
                # 无法解析，返回原始字段字符串化（允许自由文本答案）
                return str(field) if field is not None else None

            correct_text = _resolve_correct_text(correct_field, options)
            if correct_text is None:
                logger.warning(f"MC question missing or unresolvable correct_answer for user {session.user_id} raw={correct_field}")
            else:
                # 诊断：若原始字段不是选项文本且解析成功，记录一次信息日志
                try:
                    if isinstance(correct_field, (str, int, list)) and not (isinstance(correct_field, str) and correct_field in options):
                        logger.info(f"Resolved correct_answer '{correct_field}' -> '{correct_text}' for user {session.user_id}")
                except Exception:
                    pass

            # 选项随机化（与正确答案文本无关，按钮以选项文本比对）
            if session.randomize_options:
                shuffled_options = options[:]
                random.shuffle(shuffled_options)
            else:
                shuffled_options = options
            try:
                logger.warning(f"[mc-render] user={session.user_id} qidx={session.current_question_index} randomize={session.randomize_options} opts={options} shuffled={shuffled_options}")
            except Exception:
                pass

            # 格式化选项并作为独立字段添加
            # 使用独立字段可以彻底隔离不同选项的渲染上下文
            # 即使选项A包含未闭合的代码块，也不会吞噬选项B的显示
            for i, option_text in enumerate(shuffled_options):
                # 清理选项文本
                safe_option = str(option_text).replace('\x00', '').strip()
                
                # 长度保护：单个字段值不能超过1024字符
                if len(safe_option) > 1000:
                    safe_option = safe_option[:1000] + "..."

                # 格式保护：自动闭合选项中未匹配的代码块
                # 解释用户反馈的"偶发性"：如果某个选项包含未闭合的代码块，
                # 当它被随机排在前面时，会吞掉后续的Field；排在最后时则看起来正常。
                if safe_option.count("```") % 2 != 0:
                    safe_option += "\n```"
                
                letter = chr(ord('A') + i)
                embed.add_field(
                    name=f"选项 {letter}", 
                    value=safe_option if safe_option else "‎", # 使用不可见字符占位防止空值报错
                    inline=False
                )

            # 为视图添加选项按钮（unique custom_id 在视图内部实现）
            view.setup_multiple_choice(shuffled_options, correct_text)
            
        elif question['type'] == 'true_false':
            view.setup_true_false(question['correct_answer'])
            
        elif question['type'] == 'fill_in_blank':
            view.setup_fill_in_blank()
        
        # 添加取消按钮
        view.add_cancel_button()
        
        try:
            # 发送或编辑消息
            if from_modal:
                await interaction.edit_original_response(embed=embed, view=view)
            elif interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=view)
            else:
                await interaction.response.edit_message(embed=embed, view=view)
            
            logger.info(f"Successfully displayed question for user {session.user_id}")
        except Exception as e:
            logger.error(f"Error displaying question for user {session.user_id}: {e}", exc_info=True)
            # 尝试使用followup作为备选方案
            try:
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            except Exception as followup_error:
                logger.error(f"Followup also failed: {followup_error}")
    
    async def _handle_challenge_success(self, interaction: discord.Interaction,
                                       session: ChallengeSession, from_modal: bool = False):
        """处理挑战成功"""
        user_id = session.user_id
        guild_id = session.guild_id
        
        if session.is_ultimate:
            # 究极道馆成功
            completion_time = session.get_completion_time()
            await self._update_ultimate_leaderboard(guild_id, user_id, completion_time)
            
            # 清理会话和锁
            self._cleanup_user_session(user_id)
            
            logger.info(f"Ultimate challenge success for user {user_id}. Time: {completion_time:.2f}s")
            
            # 触发排行榜更新
            await self._trigger_leaderboard_update(int(guild_id))
            
            # 格式化时间
            minutes, seconds = divmod(completion_time, 60)
            time_str = f"{int(minutes)}分 {seconds:.2f}秒"
            
            success_desc = (f"你成功征服了 **{session.gym_info['name']}**！\n\n"
                          f"**用时**: `{time_str}`\n"
                          f"**总题数**: **{len(session.questions_for_session)}**\n\n"
                          "你的成绩已被记录到排行榜！")
            
            embed = discord.Embed(
                title="🏆 究极挑战成功！",
                description=success_desc,
                color=discord.Color.gold()
            )
            
        else:
            # 普通道馆成功
            await self._reset_failures(user_id, guild_id, session.gym_id)
            await self._set_gym_completed(user_id, guild_id, session.gym_id)
            
            # 清理会话和锁
            self._cleanup_user_session(user_id)
            
            logger.info(f"Challenge success for user {user_id} in gym {session.gym_id}")
            
            success_desc = (f"你成功通过了 **{session.gym_info['name']}** 的考核！\n\n"
                          f"总题数: **{len(session.questions_for_session)}**\n"
                          f"答错题数: **{session.mistakes_made}**\n"
                          f"允许错题数: **{session.allowed_mistakes}**\n\n"
                          "你的道馆挑战失败记录已被清零。")
            
            embed = discord.Embed(
                title="🎉 恭喜你，挑战成功！",
                description=success_desc,
                color=discord.Color.green()
            )
            
            # 检查并管理完成奖励
            await self._check_completion_rewards(interaction.user, session)
        
        # 添加错题回顾
        if session.wrong_answers:
            wrong_fields = format_wrong_answers(session.wrong_answers, show_correct=True)
            for field in wrong_fields[:25]:  # Discord限制25个字段
                embed.add_field(**field)
        
        # 发送成功消息
        if from_modal:
            await interaction.edit_original_response(embed=embed, view=None)
        elif interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=None)
    
    async def _handle_challenge_failure(self, interaction: discord.Interaction,
                                       session: ChallengeSession, from_modal: bool = False):
        """处理挑战失败"""
        user_id = session.user_id
        guild_id = session.guild_id
        
        ban_duration = timedelta(seconds=0)
        banned_until_time = None
        
        # 只对普通道馆应用失败惩罚
        if not session.is_ultimate:
            ban_duration = await self._increment_failure(user_id, guild_id, session.gym_id)
            failure_status = await self._get_failure_status(user_id, guild_id, session.gym_id)
            if failure_status and failure_status.get('banned_until'):
                banned_until_time = parse_beijing_time(failure_status['banned_until'])
        
        # 清理会话和锁
        self._cleanup_user_session(user_id)
        
        logger.info(f"Challenge failed for user {user_id} in gym {session.gym_id}")
        
        # 构建失败消息
        fail_desc = (f"本次挑战失败。\n\n"
                    f"总题数: **{len(session.questions_for_session)}**\n"
                    f"答错题数: **{session.mistakes_made}**\n")
        
        if not session.is_ultimate:
            fail_desc += (f"允许错题数: **{session.allowed_mistakes}**\n\n"
                         "你答错的题目数量超过了允许的最大值。")
        else:
            fail_desc += "\n究极道馆挑战要求零错误。"
        
        if ban_duration.total_seconds() > 0:
            time_str = format_timedelta(ban_duration)
            fail_desc += f"\n\n由于累计挑战失败次数过多，你已被禁止挑战该道馆 **{time_str}**。"
            if banned_until_time:
                fail_desc += f"\n解封时间（北京时间）：`{format_beijing_display(banned_until_time)}`"
        else:
            if not session.is_ultimate:
                fail_desc += "\n\n请稍后重试。"
            else:
                fail_desc += "\n\n你可以立即再次尝试！"
        
        title = "⚔️ 究极挑战失败" if session.is_ultimate else "❌ 挑战失败"
        
        embed = discord.Embed(
            title=title,
            description=fail_desc,
            color=discord.Color.red()
        )
        
        # 添加错题回顾（不显示正确答案）
        if session.wrong_answers:
            wrong_fields = format_wrong_answers(session.wrong_answers, show_correct=False)
            for field in wrong_fields[:25]:
                embed.add_field(**field)
        
        # 发送失败消息
        if from_modal:
            await interaction.edit_original_response(embed=embed, view=None)
        elif interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=None)
    
    async def _check_completion_rewards(self, member: discord.Member, session: ChallengeSession):
        """检查并发放完成奖励"""
        guild_id = str(member.guild.id)
        user_id = str(member.id)
        panel_message_id = str(session.panel_message_id)
        
        # 获取用户进度
        user_progress = await self._get_user_progress(user_id, guild_id)
        
        # 获取面板配置
        async with self.db.get_connection() as conn:
            async with conn.execute('''
                SELECT role_to_add_ids, role_to_remove_ids, associated_gyms,
                       blacklist_enabled, completion_threshold
                FROM challenge_panels WHERE message_id = ?
            ''', (panel_message_id,)) as cursor:
                panel_config = await cursor.fetchone()
        
        if not panel_config:
            return
        
        # 解析配置
        role_to_add_ids = json.loads(panel_config[0]) if panel_config[0] else []
        role_to_remove_ids = json.loads(panel_config[1]) if panel_config[1] else []
        associated_gyms = json.loads(panel_config[2]) if panel_config[2] else None
        blacklist_enabled = panel_config[3]
        completion_threshold = panel_config[4]
        
        # 检查黑名单
        if blacklist_enabled:
            if await self._is_user_blacklisted(guild_id, member):
                logger.info(f"Blocked role reward for blacklisted user {user_id}")
                try:
                    await member.send(
                        f"🚫 **身份组获取失败**\n\n"
                        f"你在服务器 **{member.guild.name}** 的道馆挑战奖励发放被阻止。\n"
                        "由于你被记录在处罚名单中，即使完成了道馆挑战，也无法获得相关身份组。"
                    )
                except discord.Forbidden:
                    pass
                return
        
        # 获取所有道馆
        all_gyms = await self._get_all_guild_gyms(guild_id)
        all_gym_ids = {gym['id'] for gym in all_gyms}
        
        # 确定需要完成的道馆
        if associated_gyms:
            required_gym_ids = set(associated_gyms) & all_gym_ids
        else:
            required_gym_ids = all_gym_ids
        
        # 检查是否满足完成条件
        completed_gym_ids = set(user_progress)
        all_checks_passed = False
        
        if completion_threshold and completion_threshold > 0:
            # 需要完成特定数量的道馆
            completed_required = completed_gym_ids & required_gym_ids
            if len(completed_required) >= completion_threshold:
                all_checks_passed = True
        else:
            # 需要完成所有道馆
            if required_gym_ids.issubset(completed_gym_ids):
                all_checks_passed = True
        
        if not all_checks_passed:
            return
        
        # 发放奖励
        messages = []
        
        # 添加身份组
        for role_id in role_to_add_ids:
            # 检查是否已领取
            if await self._has_claimed_reward(guild_id, user_id, role_id):
                continue
            
            role = member.guild.get_role(int(role_id))
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"Panel {panel_message_id} completion")
                    await self._record_reward_claim(guild_id, user_id, role_id)
                    messages.append(f"✅ **获得了身份组**: {role.mention}")
                    logger.info(f"Granted role {role_id} to user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to add role {role_id}: {e}")
        
        # 移除身份组
        for role_id in role_to_remove_ids:
            role = member.guild.get_role(int(role_id))
            if role and role in member.roles:
                try:
                    await member.remove_roles(role, reason=f"Panel {panel_message_id} completion")
                    messages.append(f"✅ **移除了身份组**: {role.mention}")
                except Exception as e:
                    logger.error(f"Failed to remove role {role_id}: {e}")
        
        # 发送通知
        if messages:
            try:
                header = f"🎉 恭喜你！你已在 **{member.guild.name}** 服务器完成了指定道馆挑战！"
                full_message = header + "\n\n" + "\n".join(messages)
                await member.send(full_message)
            except discord.Forbidden:
                logger.warning(f"Cannot send DM to user {user_id}")
    
    async def _trigger_leaderboard_update(self, guild_id: int):
        """触发排行榜更新"""
        try:
            # 尝试调用排行榜Cog的更新方法
            leaderboard_cog = self.bot.get_cog('LeaderboardCog')
            if leaderboard_cog:
                await leaderboard_cog.trigger_leaderboard_update(guild_id)
                logger.info(f"Triggered leaderboard update for guild {guild_id} via LeaderboardCog")
            else:
                logger.warning(f"LeaderboardCog not found when attempting to trigger leaderboard update for guild {guild_id}")
        except Exception as e:
            logger.error(f"Error triggering leaderboard update for guild {guild_id}: {e}", exc_info=True)
    
    async def _is_user_blacklisted(self, guild_id: str, member: discord.Member) -> bool:
        """检查用户是否在黑名单中"""
        async with self.db.get_connection() as conn:
            # 检查用户黑名单
            async with conn.execute(
                "SELECT 1 FROM cheating_blacklist WHERE guild_id = ? AND target_id = ? AND target_type = 'user'",
                (guild_id, str(member.id))
            ) as cursor:
                user_blacklist = await cursor.fetchone()
            if user_blacklist:
                return True
            
            # 检查身份组黑名单
            role_ids = [str(role.id) for role in member.roles]
            if role_ids:
                placeholders = ','.join('?' for _ in role_ids)
                query = f"SELECT 1 FROM cheating_blacklist WHERE guild_id = ? AND target_type = 'role' AND target_id IN ({placeholders})"
                params = [guild_id] + role_ids
                async with conn.execute(query, params) as cursor:
                    role_blacklist = await cursor.fetchone()
                if role_blacklist:
                    return True
        
        return False
    
    async def _has_claimed_reward(self, guild_id: str, user_id: str, role_id: str) -> bool:
        """检查是否已领取奖励"""
        async with self.db.get_connection() as conn:
            async with conn.execute(
                "SELECT 1 FROM claimed_role_rewards WHERE guild_id = ? AND user_id = ? AND role_id = ?",
                (guild_id, user_id, role_id)
            ) as cursor:
                result = await cursor.fetchone()
        return result is not None
    
    async def _record_reward_claim(self, guild_id: str, user_id: str, role_id: str):
        """记录奖励领取"""
        async with self.db.get_connection() as conn:
            import pytz
            timestamp = datetime.now(pytz.UTC).isoformat()
            await conn.execute(
                "INSERT OR IGNORE INTO claimed_role_rewards (guild_id, user_id, role_id, timestamp) VALUES (?, ?, ?, ?)",
                (guild_id, user_id, role_id, timestamp)
            )
            await conn.commit()
    
    async def _get_all_guild_gyms(self, guild_id: str) -> list:
        """获取服务器所有道馆"""
        async with self.db.get_connection() as conn:
            async with conn.execute(
                "SELECT gym_id, name, is_enabled FROM gyms WHERE guild_id = ?",
                (guild_id,)
            ) as cursor:
                rows = await cursor.fetchall()
        return [{'id': row[0], 'name': row[1], 'is_enabled': row[2]} for row in rows]


async def setup(bot: commands.Bot):
    """设置函数，用于添加Cog到bot"""
    await bot.add_cog(GymChallengeCog(bot))