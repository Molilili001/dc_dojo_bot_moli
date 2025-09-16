# -*- coding: utf-8 -*-
"""
模块名称: challenge_views.py
功能描述: 道馆挑战相关的视图组件
作者: Kilo Code
创建日期: 2024-12-15
最后修改: 2024-12-15
"""

import discord
from discord import ui
import random
import asyncio
from typing import Optional, List, Dict, Any, TYPE_CHECKING
import logging

from core.constants import BEIJING_TZ
from utils.logger import get_logger

# 类型检查时才导入，避免循环导入
if TYPE_CHECKING:
    from cogs.gym_challenge import ChallengeSession

logger = get_logger(__name__)


class GymSelectView(ui.View):
    """道馆选择视图"""
    
    def __init__(self, guild_gyms: List[Dict], user_progress: Dict, panel_message_id: int):
        super().__init__(timeout=180)
        self.add_item(GymSelect(guild_gyms, user_progress, panel_message_id))


class GymSelect(ui.Select):
    """道馆选择下拉菜单"""
    
    def __init__(self, guild_gyms: List[Dict], user_progress: Dict, panel_message_id: int):
        self.panel_message_id = panel_message_id
        options = []
        
        if not guild_gyms:
            options.append(discord.SelectOption(
                label="本服务器暂无道馆",
                description="请管理员使用 /道馆 建造 来创建道馆。",
                value="no_gyms",
                emoji="🤷"
            ))
        else:
            for gym in guild_gyms:
                gym_id = gym['id']
                completed = user_progress.get(gym_id, False)
                
                if not gym.get('is_enabled', True):
                    status_emoji = "⏸️"
                    label = f"{status_emoji} {gym['name']}"
                    description = "道馆维护中，暂不可用"
                    options.append(discord.SelectOption(
                        label=label,
                        description=description,
                        value=gym_id
                    ))
                elif completed:
                    status_emoji = "✅"
                    label = f"{status_emoji} {gym['name']}"
                    description = "已通关"
                    options.append(discord.SelectOption(
                        label=label,
                        description=description,
                        value=gym_id
                    ))
                else:
                    status_emoji = "❌"
                    label = f"{status_emoji} {gym['name']}"
                    description = "未通关"
                    options.append(discord.SelectOption(
                        label=label,
                        description=description,
                        value=gym_id
                    ))
        
        super().__init__(
            placeholder="请选择一个道馆进行挑战...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        """选择道馆后的回调"""
        # 延迟响应
        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)
        gym_id = self.values[0]
        
        if gym_id == "no_gyms":
            await interaction.edit_original_response(
                content="本服务器还没有创建任何道馆哦。",
                view=None,
                embed=None
            )
            return
        
        # 获取挑战Cog来处理选择
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.edit_original_response(
                content="❌ 挑战系统暂时不可用。",
                view=None,
                embed=None
            )
            return
        
        # 调用挑战Cog的方法来处理选择
        # 不再先清空消息，让handle_gym_selection直接编辑这个消息
        await challenge_cog.handle_gym_selection(
            interaction,
            gym_id,
            self.panel_message_id
        )


class StartChallengeView(ui.View):
    """开始挑战视图"""
    
    def __init__(self, gym_id: str):
        super().__init__(timeout=60)  # 减少超时时间为60秒
        self.gym_id = gym_id
        self.add_item(StartChallengeButton(gym_id))
        self.add_item(CancelChallengeButton())
    
    async def on_timeout(self):
        """视图超时处理 - 清理未开始的挑战会话"""
        # 清理所有可能的挂起会话
        # 注意：这里无法直接访问user_id，但会在交互失败时自动处理
        for item in self.children:
            item.disabled = True


class StartChallengeButton(ui.Button):
    """开始挑战按钮"""
    
    def __init__(self, gym_id: str):
        super().__init__(
            label="开始考核",
            style=discord.ButtonStyle.success,
            custom_id=f"challenge_begin_{gym_id}"
        )
        self.gym_id = gym_id
    
    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        
        # 获取挑战Cog
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.response.send_message(
                "❌ 挑战系统暂时不可用。",
                ephemeral=True
            )
            return
        
        # 从活跃挑战中获取会话
        session = challenge_cog.active_challenges.get(user_id)
        if session:
            # 停止当前视图的超时计时器
            self.view.stop()
            await challenge_cog.display_question(interaction, session)
        else:
            await interaction.response.send_message(
                "❌ 挑战会话已过期，请重新开始。",
                ephemeral=True
            )


class CancelChallengeButton(ui.Button):
    """放弃挑战按钮"""
    
    def __init__(self):
        super().__init__(
            label="放弃挑战",
            style=discord.ButtonStyle.danger,
            custom_id="challenge_cancel"
        )
    
    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        
        # 获取挑战Cog
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.response.send_message(
                "❌ 挑战系统暂时不可用。",
                ephemeral=True
            )
            return
        
        await challenge_cog.handle_challenge_cancel(interaction, user_id)


class QuestionView(ui.View):
    """题目展示视图，包含超时处理"""
    
    def __init__(self, session: Any, interaction: discord.Interaction, **kwargs):
        super().__init__(**kwargs)
        self.session = session
        self.interaction = interaction
        self.answered = False  # 添加标记来跟踪是否已经回答
    
    async def on_timeout(self):
        """视图超时处理"""
        # 如果已经回答了，不执行超时处理
        if self.answered:
            return
            
        user_id = str(self.session.user_id)
        
        # 获取挑战Cog来处理超时
        challenge_cog = self.interaction.client.get_cog('GymChallengeCog')
        if challenge_cog:
            # 只有当会话还存在时才处理超时
            if user_id in challenge_cog.active_challenges and \
               challenge_cog.active_challenges[user_id] == self.session:
                await challenge_cog.handle_challenge_timeout(user_id, self.session)
        
        # 禁用所有按钮
        for item in self.children:
            item.disabled = True
        
        try:
            timeout_embed = discord.Embed(
                title="⌛ 挑战超时",
                description="本次挑战已超时，请重新开始。",
                color=discord.Color.orange()
            )
            await self.interaction.edit_original_response(embed=timeout_embed, view=self)
        except discord.NotFound:
            pass
        except Exception as e:
            logger.error(f"Error during QuestionView on_timeout: {e}", exc_info=True)
    
    def setup_multiple_choice(self, options: list, correct_answer: str):
        """设置选择题按钮"""
        for i, option_text in enumerate(options):
            letter = chr(ord('A') + i)
            button = QuestionAnswerButton(
                label=letter,
                correct_answer=correct_answer,
                value=option_text
            )
            self.add_item(button)
    
    def setup_true_false(self, correct_answer: str):
        """设置是非题按钮"""
        # 标准化correct_answer以支持多种格式
        # 支持的格式：true/false, True/False, 正确/错误, 对/错
        normalized_answer = str(correct_answer).lower().strip()
        
        # 映射表：将各种可能的答案格式标准化
        true_values = ['true', '正确', '对', '是', 'yes', '1']
        false_values = ['false', '错误', '错', '否', 'no', '0']
        
        # 确定标准化后的正确答案
        if normalized_answer in true_values:
            standard_correct = "正确"
        elif normalized_answer in false_values:
            standard_correct = "错误"
        else:
            # 如果不在预定义列表中，保持原值
            standard_correct = str(correct_answer)
        
        self.add_item(QuestionAnswerButton(
            label="正确",
            correct_answer=standard_correct,
            value="正确"
        ))
        self.add_item(QuestionAnswerButton(
            label="错误",
            correct_answer=standard_correct,
            value="错误"
        ))
    
    def setup_fill_in_blank(self):
        """设置填空题按钮"""
        self.add_item(FillInBlankButton())
    
    def add_cancel_button(self):
        """添加取消按钮"""
        self.add_item(CancelChallengeButton())


class QuestionAnswerButton(ui.Button):
    """答案选择按钮"""
    
    def __init__(self, label: str, correct_answer: str, value: str = None):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.correct_answer = correct_answer
        self.value = value if value is not None else label
    
    async def callback(self, interaction: discord.Interaction):
        # 立即延迟响应以防超时
        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        
        # 获取挑战Cog
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.edit_original_response(
                content="❌ 挑战系统暂时不可用。",
                view=None,
                embed=None
            )
            return
        
        # 使用锁来防止并发
        async with challenge_cog.user_challenge_locks[user_id]:
            session = challenge_cog.active_challenges.get(user_id)
            if not session:
                await interaction.edit_original_response(
                    content="挑战已超时或已结束，请重新开始。",
                    view=None,
                    embed=None
                )
                return
            
            # 标记已回答并停止视图
            self.view.answered = True
            self.view.stop()
            
            # 改进的答案比较逻辑 - 支持多种格式
            is_correct = self._check_answer(self.value, self.correct_answer)
            await challenge_cog.process_answer(interaction, session, self.value, is_correct)
    
    def _check_answer(self, user_value: str, correct_value: str) -> bool:
        """
        改进的答案比较逻辑，支持多种格式
        
        Args:
            user_value: 用户选择的值
            correct_value: 正确答案
            
        Returns:
            是否正确
        """
        # 标准化比较 - 转换为小写并去除空格
        user_lower = str(user_value).lower().strip()
        correct_lower = str(correct_value).lower().strip()
        
        # 直接比较
        if user_lower == correct_lower:
            return True
        
        # 对于判断题，支持多种表达方式
        true_values = ['true', '正确', '对', '是', 'yes', '1']
        false_values = ['false', '错误', '错', '否', 'no', '0']
        
        # 检查两个值是否都是"真"的表达
        if user_lower in true_values and correct_lower in true_values:
            return True
        
        # 检查两个值是否都是"假"的表达
        if user_lower in false_values and correct_lower in false_values:
            return True
        
        return False


class FillInBlankButton(ui.Button):
    """填空题按钮"""
    
    def __init__(self):
        super().__init__(
            label="点击填写答案",
            style=discord.ButtonStyle.blurple
        )
    
    async def callback(self, interaction: discord.Interaction):
        # 获取当前会话
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.response.send_message(
                "❌ 挑战系统暂时不可用。",
                ephemeral=True
            )
            return
        
        session = challenge_cog.active_challenges.get(str(interaction.user.id))
        if session:
            # 传递当前视图到模态框
            await interaction.response.send_modal(
                FillInBlankModal(session.get_current_question(), self.view)
            )


class FillInBlankModal(ui.Modal, title="填写答案"):
    """填空题输入模态框"""
    
    answer_input = ui.TextInput(
        label="你的答案",
        style=discord.TextStyle.short,
        required=True
    )
    
    def __init__(self, question: dict, original_view: ui.View):
        super().__init__()
        self.question = question
        self.original_view = original_view
    
    async def on_submit(self, interaction: discord.Interaction):
        # 立即延迟响应
        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        
        # 获取挑战Cog
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.edit_original_response(
                content="❌ 挑战系统暂时不可用。",
                view=None,
                embed=None
            )
            return
        
        async with challenge_cog.user_challenge_locks[user_id]:
            session = challenge_cog.active_challenges.get(user_id)
            if not session:
                await interaction.edit_original_response(
                    content="挑战已超时或已结束，请重新开始。",
                    view=None,
                    embed=None
                )
                return
            
            # 标记原始视图已回答并停止
            self.original_view.answered = True
            self.original_view.stop()
            
            # 检查答案
            user_answer = self.answer_input.value.strip()
            correct_answer_field = self.question['correct_answer']
            is_correct = False
            
            # 检查答案（支持多个正确答案）
            if isinstance(correct_answer_field, list):
                if any(user_answer.lower() == str(ans).lower() for ans in correct_answer_field):
                    is_correct = True
            else:
                if user_answer.lower() == str(correct_answer_field).lower():
                    is_correct = True
            
            # 处理答案
            await challenge_cog.process_answer(
                interaction,
                session,
                user_answer,
                is_correct,
                from_modal=True
            )


class MainChallengeView(ui.View):
    """主挑战面板视图"""
    
    def __init__(self):
        super().__init__(timeout=None)
    
    @ui.button(
        label="挑战道馆",
        style=discord.ButtonStyle.success,
        custom_id="open_gym_list"
    )
    async def open_gym_list(self, interaction: discord.Interaction, button: ui.Button):
        """打开道馆列表"""
        try:
            # 立即延迟响应，防止交互超时
            await interaction.response.defer(ephemeral=True)
            
            logger.info(f"挑战按钮被点击 - 用户: {interaction.user} ({interaction.user.id})")
            
            # 获取挑战Cog
            challenge_cog = interaction.client.get_cog('GymChallengeCog')
            if not challenge_cog:
                logger.error("GymChallengeCog not found")
                await interaction.followup.send(
                    "❌ 挑战系统暂时不可用。",
                    ephemeral=True
                )
                return
            
            logger.info(f"GymChallengeCog found, calling handle_challenge_start")
            
            # 调用handle_challenge_start方法
            await challenge_cog.handle_challenge_start(interaction)
            
            logger.info(f"handle_challenge_start completed")
            
        except discord.NotFound:
            logger.error("Interaction not found - it may have expired")
        except discord.HTTPException as e:
            logger.error(f"Discord HTTP error: {e}")
            try:
                await interaction.followup.send(
                    "❌ 交互失败，请重试。",
                    ephemeral=True
                )
            except:
                pass
        except Exception as e:
            logger.error(f"Error in open_gym_list: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    f"❌ 发生错误: {str(e)}",
                    ephemeral=True
                )
            except:
                pass


# 导出所有视图类
__all__ = [
    'GymSelectView',
    'GymSelect',
    'StartChallengeView',
    'StartChallengeButton',
    'CancelChallengeButton',
    'QuestionView',
    'QuestionAnswerButton',
    'FillInBlankButton',
    'FillInBlankModal',
    'MainChallengeView'
]