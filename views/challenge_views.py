
# -*- coding: utf-8 -*-
"""
模块名称: challenge_views.py
功能描述: 道馆挑战相关的视图组件
作者: Kilo Code
创建日期: 2024-12-15
最后修改: 2025-10-22
"""

import discord
from discord import ui
import random
import asyncio
import time
import uuid
from typing import Optional, List, Dict, Any, TYPE_CHECKING
import logging

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

        completed_count = sum(
            1 for gym in guild_gyms
            if user_progress.get(gym.get('id'), False)
        )
        incomplete_count = len(guild_gyms) - completed_count
        sorted_gyms = sorted(
            guild_gyms,
            key=lambda gym: user_progress.get(gym.get('id'), False)
        )

        options = []

        if not guild_gyms:
            options.append(discord.SelectOption(
                label="本服务器暂无道馆",
                description="请管理员使用 /道馆 建造 来创建道馆。",
                value="no_gyms",
                emoji="🤷"
            ))
        else:
            for gym in sorted_gyms:
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
            placeholder=f"请选择一个道馆进行挑战（{incomplete_count}/{completed_count}）",
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

        # 先进行封禁检查（严格优先级最高）
        ban_entry = await challenge_cog._get_challenge_ban_entry(guild_id, interaction.user)
        if ban_entry:
            # 清理由旧消息生成的任何挂起会话，并阻止后续流程
            if user_id in challenge_cog.active_challenges:
                del challenge_cog.active_challenges[user_id]

            ban_message = challenge_cog._format_challenge_ban_message(ban_entry, interaction.user)
            await interaction.edit_original_response(
                content=ban_message,
                view=None,
                embed=None
            )
            return

        # 调用挑战Cog的方法来处理选择
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
        self.add_item(CancelChallengeButton(context='tutorial'))

    async def on_timeout(self):
        """视图超时处理 - 清理未开始的挑战会话"""
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
        # 黄金法则：先延迟响应，私密占坑，避免超时与重复响应
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)

        # 获取挑战Cog
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.followup.send(
                "❌ 挑战系统暂时不可用。",
                ephemeral=True
            )
            return

        # 在开始前再次检查封禁状态
        guild_id = str(interaction.guild.id)
        ban_entry = await challenge_cog._get_challenge_ban_entry(guild_id, interaction.user)
        if ban_entry:
            # 清理已存在的挑战会话
            if user_id in challenge_cog.active_challenges:
                del challenge_cog.active_challenges[user_id]

            ban_message = challenge_cog._format_challenge_ban_message(ban_entry, interaction.user)
            # 已 defer，统一使用 followup 发送
            await interaction.followup.send(ban_message, ephemeral=True)
            return

        # 从活跃挑战中获取会话
        session = challenge_cog.active_challenges.get(user_id)
        if session:
            # 停止当前视图的超时计时器
            self.view.stop()
            await challenge_cog.display_question(interaction, session)
        else:
            await interaction.followup.send(
                "❌ 挑战会话已过期，请重新开始。",
                ephemeral=True
            )


class CancelChallengeButton(ui.Button):
    """放弃挑战按钮"""
    def __init__(self, context: str = 'question'):
        self.context = context
        super().__init__(
            label="放弃挑战",
            style=discord.ButtonStyle.danger,
            custom_id=f"challenge_cancel_{context}"
        )

    async def callback(self, interaction: discord.Interaction):
        # 安全占坑：统一延迟响应为私密，避免超时/重复响应
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)

        # 获取挑战Cog
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.followup.send(
                "❌ 挑战系统暂时不可用。",
                ephemeral=True
            )
            return

        # 停止当前视图的计时器，防止与确认流程冲突
        if self.view:
            self.view.stop()

        # 检查会话有效性
        session = challenge_cog.active_challenges.get(user_id)
        if not session:
            await interaction.edit_original_response(
                content="挑战已超时或已结束，请重新开始。",
                view=None,
                embed=None
            )
            return

        # 进入确认流程第一步
        view = ConfirmCancelStep1View(session, self.context)
        
        # 构建文案
        desc = (f"你确定要放弃 **{session.gym_info['name']}** 的挑战吗？\n\n"
                f"**当前进度**: {session.get_progress_info()}\n")
        
        if session.is_ultimate:
            desc += "\n✨ **提示**: 究极道馆挑战失败或放弃**不会**计入失败记录，你可以随时重新开始。"
        else:
            desc += "\n⚠️ **警告**: 主动放弃将被计为一次**失败**，这可能会导致你暂时无法挑战该道馆（冷却惩罚）。"
            
        desc += "\n\n若要继续，请点击下方按钮（将进入最终确认）。"
        
        embed = discord.Embed(
            title="🛑 确认放弃挑战 (1/2)",
            description=desc,
            color=discord.Color.orange()
        )
        
        await interaction.edit_original_response(embed=embed, view=view)


class QuestionView(ui.View):
    """题目展示视图，包含超时处理"""
    def __init__(self, session: Any, interaction: discord.Interaction, **kwargs):
        super().__init__(**kwargs)
        self.session = session
        self.interaction = interaction
        self.answered = False  # 添加标记来跟踪是否已经回答
        # 为本视图实例生成一次性令牌，确保组件 custom_id 唯一，避免客户端缓存导致的渲染抑制
        self.session_token = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        logger.debug(
            f"QuestionView initialized for user {self.session.user_id} "
            f"gym={self.session.gym_id} qidx={self.session.current_question_index} token={self.session_token}"
        )

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

    def _clear_answer_buttons(self):
        """移除已有的答案按钮，避免重复累积导致渲染异常"""
        # children 是只读属性，不能整体赋值；需逐项移除
        to_remove = [child for child in self.children if isinstance(child, QuestionAnswerButton)]
        for child in to_remove:
            try:
                self.remove_item(child)
            except Exception:
                # 移除失败不影响后续添加新按钮
                pass
        logger.debug(f"Cleared {len(to_remove)} existing answer buttons for user {self.session.user_id}")

    def setup_multiple_choice(self, options: list, correct_answer: str):
        """设置选择题按钮（带唯一 custom_id 与视图清理）"""
        # 防御性：清理旧的答案按钮
        self._clear_answer_buttons()
        # 诊断日志
        logger.info(
            f"Setting up multiple choice for user {self.session.user_id} "
            f"gym={self.session.gym_id} qidx={self.session.current_question_index} options={len(options)}"
        )
        for i, option_text in enumerate(options):
            letter = chr(ord('A') + i)
            # 加入一次性令牌，避免客户端缓存导致按钮渲染不完整
            custom_id = f"qa_mc:{self.session.gym_id}:{self.session.current_question_index}:{i}:{self.session_token}"
            button = QuestionAnswerButton(
                label=letter,
                correct_answer=correct_answer,
                value=option_text,
                custom_id=custom_id
            )
            self.add_item(button)
            logger.debug(
                f"Added MC button {letter} (opt='{str(option_text)[:20]}...') cid={custom_id}"
            )

    def setup_true_false(self, correct_answer: str):
        """设置是非题按钮（带唯一 custom_id）"""
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

        # 为判断题的两个按钮生成唯一 custom_id（包含一次性令牌）
        custom_id_true = f"qa_tf:{self.session.gym_id}:{self.session.current_question_index}:T:{self.session_token}"
        custom_id_false = f"qa_tf:{self.session.gym_id}:{self.session.current_question_index}:F:{self.session_token}"
        self.add_item(QuestionAnswerButton(
            label="正确",
            correct_answer=standard_correct,
            value="正确",
            custom_id=custom_id_true
        ))
        self.add_item(QuestionAnswerButton(
            label="错误",
            correct_answer=standard_correct,
            value="错误",
            custom_id=custom_id_false
        ))

    def setup_fill_in_blank(self):
        """设置填空题按钮"""
        self.add_item(FillInBlankButton())

    def add_cancel_button(self):
        """添加取消按钮"""
        self.add_item(CancelChallengeButton(context='question'))


class QuestionAnswerButton(ui.Button):
    """答案选择按钮"""
    def __init__(self, label: str, correct_answer: str, value: str = None, custom_id: Optional[str] = None):
        # 指定 custom_id 确保同一消息中的多个按钮不会因为ID冲突被客户端折叠为单一组件
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=custom_id)
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

        # 使用锁来防止并发（确保锁存在）
        if user_id not in challenge_cog.user_challenge_locks:
            challenge_cog.user_challenge_locks[user_id] = asyncio.Lock()
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

        Returns:
            是否正确
        """
        # 标准化比较 - 转换为小写并去除空格
        user_lower = str(user_value).lower().strip()
        correct_lower = str(correct_value).lower().strip()

        # 直接比较
        if user_lower == correct_lower:
            return True

        # 对于判断题，支持多种表达

        # 对于判断题，支持多种表达
        true_values = ['true', '正确', '对', '是', 'yes', '1']
        false_values = ['false', '错误', '错', '否', 'no', '0']

        if user_lower in true_values and correct_lower in true_values:
            return True
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

        # 确保锁存在后再进入并发保护
        if user_id not in challenge_cog.user_challenge_locks:
            challenge_cog.user_challenge_locks[user_id] = asyncio.Lock()
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

            # 获取挑战Cog
            challenge_cog = interaction.client.get_cog('GymChallengeCog')
            if not challenge_cog:
                await interaction.followup.send(
                    "❌ 挑战系统暂时不可用。",
                    ephemeral=True
                )
                return

            # 在调用主流程前，优先检查封禁（避免任何后续异常导致重复提示）
            guild_id = str(interaction.guild.id)
            ban_entry = await challenge_cog._get_challenge_ban_entry(guild_id, interaction.user)
            if ban_entry:
                ban_message = challenge_cog._format_challenge_ban_message(ban_entry, interaction.user)
                # 已 defer，因此 followup 可用；避免交互失败提示
                await interaction.followup.send(ban_message, ephemeral=True)
                return

            # 调用handle_challenge_start方法（内部再次检查封禁）
            await challenge_cog.handle_challenge_start(interaction)

        except discord.NotFound:
            # 交互已过期或消息被删除
            pass
        except Exception as e:
            # 避免在封禁提示后再次弹出“交互失败”
            try:
                await interaction.followup.send(
                    f"❌ 发生错误: {str(e)}",
                    ephemeral=True
                )
            except:
                pass

# =========================
# 二次确认放弃视图（1/2 与 2/2）
# =========================

class ConfirmCancelStep1View(ui.View):
    """放弃挑战 - 确认步骤 1/2"""
    def __init__(self, session: Any, context: str = 'question'):
        super().__init__(timeout=60)
        self.session = session
        self.context = context
        # 生成一次性令牌，防止组件缓存
        self.token = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        
        # 添加按钮
        self.add_item(Step1ConfirmButton(self.token))
        self.add_item(Step1ReturnButton(self.token))

    async def on_timeout(self):
        """超时处理"""
        for item in self.children:
            item.disabled = True
        # 尝试编辑消息以禁用按钮（如果消息还存在）
        try:
            # 这里的 interaction 只能在 callback 中获取，on_timeout 无法直接访问
            # 因此只能被动等待，或者在此处不做操作，仅禁用按钮
            pass
        except Exception:
            pass


class Step1ConfirmButton(ui.Button):
    """确认步骤1 - 确认放弃"""
    def __init__(self, token: str):
        super().__init__(
            label="确认放弃 (1/2)",
            style=discord.ButtonStyle.danger,
            custom_id=f"confirm_cancel_s1:{token}"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # 切换到步骤2
        view = ConfirmCancelStep2View(self.view.session, self.view.context)
        
        # 构建文案
        session = self.view.session
        desc = (f"你确定要放弃 **{session.gym_info['name']}** 的挑战吗？\n\n"
                f"**当前进度**: {session.get_progress_info()}\n")
        
        if session.is_ultimate:
            desc += "\n✨ **提示**: 究极道馆挑战失败或放弃**不会**计入失败记录，你可以随时重新开始。"
        else:
            desc += "\n⚠️ **警告**: 主动放弃将被计为一次**失败**，这可能会导致你暂时无法挑战该道馆（冷却惩罚）。"
            
        desc += "\n\n请点击下方按钮进行最终确认（此操作不可撤销）。"
        
        embed = discord.Embed(
            title="🛑 最终确认放弃 (2/2)",
            description=desc,
            color=discord.Color.red()
        )
        
        await interaction.edit_original_response(embed=embed, view=view)


class Step1ReturnButton(ui.Button):
    """确认步骤1 - 返回继续"""
    def __init__(self, token: str):
        super().__init__(
            label="返回继续挑战",
            style=discord.ButtonStyle.secondary,
            custom_id=f"return_continue_s1:{token}"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        session = self.view.session
        context = self.view.context
        
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.followup.send("❌ 系统错误：无法恢复会话。", ephemeral=True)
            return

        # 恢复界面逻辑
        if context == 'tutorial':
            await challenge_cog._show_tutorial(interaction, session)
        else:
            # 默认为 question 阶段
            await challenge_cog._display_next_question(interaction, session)


class ConfirmCancelStep2View(ui.View):
    """放弃挑战 - 确认步骤 2/2"""
    def __init__(self, session: Any, context: str = 'question'):
        super().__init__(timeout=60)
        self.session = session
        self.context = context
        self.token = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        
        self.add_item(Step2ConfirmButton(self.token))
        self.add_item(Step2ReturnButton(self.token))


class Step2ConfirmButton(ui.Button):
    """确认步骤2 - 最终确认"""
    def __init__(self, token: str):
        super().__init__(
            label="最终确认放弃",
            style=discord.ButtonStyle.danger,
            custom_id=f"confirm_cancel_s2:{token}"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        user_id = str(interaction.user.id)
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        
        if challenge_cog:
            # 调用实际取消逻辑
            await challenge_cog.handle_challenge_cancel(interaction, user_id)
        else:
            await interaction.followup.send("❌ 系统错误：无法取消挑战。", ephemeral=True)


class Step2ReturnButton(ui.Button):
    """确认步骤2 - 返回继续"""
    def __init__(self, token: str):
        super().__init__(
            label="返回继续挑战",
            style=discord.ButtonStyle.secondary,
            custom_id=f"return_continue_s2:{token}"
        )

    async def callback(self, interaction: discord.Interaction):
        # 复用第一步的返回逻辑，代码完全相同但上下文可能不同
        await interaction.response.defer(ephemeral=True)
        
        session = self.view.session
        context = self.view.context
        
        challenge_cog = interaction.client.get_cog('GymChallengeCog')
        if not challenge_cog:
            await interaction.followup.send("❌ 系统错误：无法恢复会话。", ephemeral=True)
            return

        if context == 'tutorial':
            await challenge_cog._show_tutorial(interaction, session)
        else:
            await challenge_cog._display_next_question(interaction, session)


# 导出所有视图类
__all__ = [
    'GymSelectView',
    'GymSelect',
    'StartChallengeView',
    'StartChallengeButton',
    'CancelChallengeButton',
    'ConfirmCancelStep1View',
    'ConfirmCancelStep2View',
    'QuestionView',
    'QuestionAnswerButton',
    'FillInBlankButton',
    'FillInBlankModal',
    'MainChallengeView'
]
