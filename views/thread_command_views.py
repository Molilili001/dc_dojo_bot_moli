"""
模块名称: thread_command_views.py
功能描述: 帖子自定义命令系统的UI视图组件
作者: Bot重构项目
创建日期: 2024
"""

import discord
from discord import ui
from typing import Optional, List, Callable, Any
from datetime import datetime

from core.models import ThreadCommandRule, ThreadCommandTrigger


# ==================== 选项映射 ====================

# 匹配模式映射
MATCH_MODE_MAP = {
    '精确': 'exact',
    '前缀': 'prefix',
    '包含': 'contains',
    '正则': 'regex',
    # 也支持英文输入
    'exact': 'exact',
    'prefix': 'prefix',
    'contains': 'contains',
    'regex': 'regex',
}

MATCH_MODE_DISPLAY = {
    'exact': '精确',
    'prefix': '前缀',
    'contains': '包含',
    'regex': '正则',
}

# 动作类型映射
ACTION_TYPE_MAP = {
    '回复': 'reply',
    '回顶': 'go_to_top',
    '反应': 'react',
    '回复并反应': 'reply_and_react',
    # 也支持英文输入
    'reply': 'reply',
    'go_to_top': 'go_to_top',
    'react': 'react',
    'reply_and_react': 'reply_and_react',
}

ACTION_TYPE_DISPLAY = {
    'reply': '回复',
    'go_to_top': '回顶',
    'react': '反应',
    'reply_and_react': '回复并反应',
}

# 权限级别映射
PERMISSION_LEVEL_MAP = {
    '全服配置': 'server_config',
    '帖子代理': 'thread_delegate',
    # 也支持英文输入
    'server_config': 'server_config',
    'thread_delegate': 'thread_delegate',
}

PERMISSION_LEVEL_DISPLAY = {
    'server_config': '全服配置',
    'thread_delegate': '帖子代理',
}


# ==================== 规则创建模态框 ====================

class RuleCreateModal(ui.Modal, title="创建自定义命令规则"):
    """创建新规则的模态框"""
    
    trigger_text = ui.TextInput(
        label="触发词",
        placeholder="输入触发词，多个用逗号分隔（支持英文, 和中文，）",
        max_length=200,
        required=True
    )
    
    trigger_mode = ui.TextInput(
        label="匹配模式（精确/前缀/包含/正则）",
        placeholder="精确=完全一致 | 前缀=以此开头 | 包含=包含此文字 | 正则=正则表达式",
        default="精确",
        max_length=20,
        required=True
    )
    
    action_type = ui.TextInput(
        label="动作类型（回复/回顶/反应/回复并反应）",
        placeholder="回复=发送消息 | 回顶=顶帖效果 | 反应=添加表情 | 回复并反应=两者都做",
        default="回复",
        max_length=20,
        required=True
    )
    
    reply_content = ui.TextInput(
        label="回复内容（可选）",
        placeholder="支持变量：{user} {user_name} {channel} {channel_name}",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=False
    )
    
    delete_delay = ui.TextInput(
        label="删除延迟（秒，可选）",
        placeholder="留空不删除，如：300（5分钟后删除触发消息和回复）",
        max_length=10,
        required=False
    )
    
    def __init__(
        self,
        guild_id: str,
        scope: str,
        thread_id: Optional[str],
        on_submit_callback: Callable
    ):
        super().__init__()
        self.guild_id = guild_id
        self.scope = scope
        self.thread_id = thread_id
        self.on_submit_callback = on_submit_callback
    
    async def on_submit(self, interaction: discord.Interaction):
        """处理提交"""
        # 解析触发词
        triggers_text = self.trigger_text.value.strip()
        trigger_list = [t.strip() for t in triggers_text.replace('，', ',').split(',') if t.strip()]
        
        if not trigger_list:
            await interaction.response.send_message("❌ 触发词不能为空", ephemeral=True)
            return
        
        # 验证匹配模式
        mode_input = self.trigger_mode.value.strip()
        mode = MATCH_MODE_MAP.get(mode_input) or MATCH_MODE_MAP.get(mode_input.lower())
        if not mode:
            await interaction.response.send_message(
                "❌ 匹配模式无效，可选：精确 / 前缀 / 包含 / 正则",
                ephemeral=True
            )
            return
        
        # 验证动作类型
        action_input = self.action_type.value.strip()
        action = ACTION_TYPE_MAP.get(action_input) or ACTION_TYPE_MAP.get(action_input.lower())
        if not action:
            await interaction.response.send_message(
                "❌ 动作类型无效，可选：回复 / 回顶 / 反应 / 回复并反应",
                ephemeral=True
            )
            return
        
        # 解析删除延迟
        delete_delay = None
        if self.delete_delay.value.strip():
            try:
                delete_delay = int(self.delete_delay.value.strip())
                if delete_delay < 0:
                    delete_delay = None
            except ValueError:
                await interaction.response.send_message("❌ 删除延迟必须是数字", ephemeral=True)
                return
        
        # 回调处理
        await self.on_submit_callback(
            interaction,
            {
                'guild_id': self.guild_id,
                'scope': self.scope,
                'thread_id': self.thread_id,
                'triggers': [(t, mode) for t in trigger_list],
                'action_type': action,
                'reply_content': self.reply_content.value.strip() or None,
                'delete_trigger_delay': delete_delay,
                'delete_reply_delay': delete_delay,
            }
        )


class RuleEditModal(ui.Modal, title="编辑规则"):
    """编辑现有规则的模态框"""
    
    reply_content = ui.TextInput(
        label="回复内容",
        placeholder="支持变量：{user} {user_name} {channel} {channel_name}",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=False
    )
    
    delete_trigger_delay = ui.TextInput(
        label="触发消息删除延迟（秒）",
        placeholder="留空不删除",
        max_length=10,
        required=False
    )
    
    delete_reply_delay = ui.TextInput(
        label="回复消息删除延迟（秒）",
        placeholder="留空不删除",
        max_length=10,
        required=False
    )
    
    add_reaction = ui.TextInput(
        label="添加反应（emoji）",
        placeholder="如：✅ 或留空",
        max_length=10,
        required=False
    )
    
    priority = ui.TextInput(
        label="优先级",
        placeholder="数字越大优先级越高，默认0",
        default="0",
        max_length=5,
        required=False
    )
    
    def __init__(self, rule_id: int, current_rule: dict, on_submit_callback: Callable):
        super().__init__()
        self.rule_id = rule_id
        self.current_rule = current_rule
        self.on_submit_callback = on_submit_callback
        
        # 填充当前值
        if current_rule.get('reply_content'):
            self.reply_content.default = current_rule['reply_content']
        if current_rule.get('delete_trigger_delay'):
            self.delete_trigger_delay.default = str(current_rule['delete_trigger_delay'])
        if current_rule.get('delete_reply_delay'):
            self.delete_reply_delay.default = str(current_rule['delete_reply_delay'])
        if current_rule.get('add_reaction'):
            self.add_reaction.default = current_rule['add_reaction']
        if current_rule.get('priority') is not None:
            self.priority.default = str(current_rule['priority'])
    
    async def on_submit(self, interaction: discord.Interaction):
        """处理提交"""
        # 解析删除延迟
        delete_trigger = None
        if self.delete_trigger_delay.value.strip():
            try:
                delete_trigger = int(self.delete_trigger_delay.value.strip())
            except ValueError:
                await interaction.response.send_message("❌ 延迟必须是数字", ephemeral=True)
                return
        
        delete_reply = None
        if self.delete_reply_delay.value.strip():
            try:
                delete_reply = int(self.delete_reply_delay.value.strip())
            except ValueError:
                await interaction.response.send_message("❌ 延迟必须是数字", ephemeral=True)
                return
        
        # 解析优先级
        priority = 0
        if self.priority.value.strip():
            try:
                priority = int(self.priority.value.strip())
            except ValueError:
                await interaction.response.send_message("❌ 优先级必须是数字", ephemeral=True)
                return
        
        await self.on_submit_callback(
            interaction,
            self.rule_id,
            {
                'reply_content': self.reply_content.value.strip() or None,
                'delete_trigger_delay': delete_trigger,
                'delete_reply_delay': delete_reply,
                'add_reaction': self.add_reaction.value.strip() or None,
                'priority': priority,
            }
        )


class TriggerAddModal(ui.Modal, title="添加触发器"):
    """添加新触发器的模态框"""
    
    trigger_text = ui.TextInput(
        label="触发词",
        placeholder="输入触发词",
        max_length=100,
        required=True
    )
    
    trigger_mode = ui.TextInput(
        label="匹配模式（精确/前缀/包含/正则）",
        placeholder="精确=完全一致 | 前缀=以此开头 | 包含=包含此文字 | 正则=正则表达式",
        default="精确",
        max_length=20,
        required=True
    )
    
    def __init__(self, rule_id: int, on_submit_callback: Callable):
        super().__init__()
        self.rule_id = rule_id
        self.on_submit_callback = on_submit_callback
    
    async def on_submit(self, interaction: discord.Interaction):
        """处理提交"""
        mode_input = self.trigger_mode.value.strip()
        mode = MATCH_MODE_MAP.get(mode_input) or MATCH_MODE_MAP.get(mode_input.lower())
        if not mode:
            await interaction.response.send_message(
                "❌ 匹配模式无效，可选：精确 / 前缀 / 包含 / 正则",
                ephemeral=True
            )
            return
        
        await self.on_submit_callback(
            interaction,
            self.rule_id,
            self.trigger_text.value.strip(),
            mode
        )


# ==================== 配置面板视图 ====================

class ServerConfigView(ui.View):
    """服务器配置面板"""
    
    def __init__(
        self,
        guild_id: str,
        config: dict,
        on_toggle: Callable,
        on_toggle_owner: Callable,
        on_set_cooldown: Callable,
        timeout: float = 300
    ):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.config = config
        self.on_toggle = on_toggle
        self.on_toggle_owner = on_toggle_owner
        self.on_set_cooldown = on_set_cooldown
        
        self._update_buttons()
    
    def _update_buttons(self):
        """更新按钮状态"""
        is_enabled = self.config.get('is_enabled', True)
        allow_owner = self.config.get('allow_thread_owner_config', True)
        
        self.toggle_btn.label = "关闭功能" if is_enabled else "开启功能"
        self.toggle_btn.style = discord.ButtonStyle.red if is_enabled else discord.ButtonStyle.green
        
        self.toggle_owner_btn.label = "禁止贴主配置" if allow_owner else "允许贴主配置"
        self.toggle_owner_btn.style = discord.ButtonStyle.red if allow_owner else discord.ButtonStyle.green
    
    @ui.button(label="开启功能", style=discord.ButtonStyle.green, row=0)
    async def toggle_btn(self, interaction: discord.Interaction, button: ui.Button):
        """切换功能开关"""
        new_state = not self.config.get('is_enabled', True)
        await self.on_toggle(interaction, new_state)
        self.config['is_enabled'] = new_state
        self._update_buttons()
        await interaction.message.edit(view=self)
    
    @ui.button(label="允许贴主配置", style=discord.ButtonStyle.green, row=0)
    async def toggle_owner_btn(self, interaction: discord.Interaction, button: ui.Button):
        """切换贴主配置权限"""
        new_state = not self.config.get('allow_thread_owner_config', True)
        await self.on_toggle_owner(interaction, new_state)
        self.config['allow_thread_owner_config'] = new_state
        self._update_buttons()
        await interaction.message.edit(view=self)
    
    @ui.button(label="设置限流", style=discord.ButtonStyle.blurple, row=1)
    async def set_cooldown_btn(self, interaction: discord.Interaction, button: ui.Button):
        """打开限流设置"""
        await self.on_set_cooldown(interaction)


class RuleListView(ui.View):
    """规则列表视图（带分页）"""
    
    def __init__(
        self,
        rules: List[dict],
        page: int = 0,
        per_page: int = 5,
        on_select: Callable = None,
        on_create: Callable = None,
        timeout: float = 300
    ):
        super().__init__(timeout=timeout)
        self.rules = rules
        self.page = page
        self.per_page = per_page
        self.on_select = on_select
        self.on_create = on_create
        
        self._update_components()
    
    @property
    def total_pages(self) -> int:
        return max(1, (len(self.rules) + self.per_page - 1) // self.per_page)
    
    @property
    def current_rules(self) -> List[dict]:
        start = self.page * self.per_page
        return self.rules[start:start + self.per_page]
    
    def _update_components(self):
        """更新组件"""
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.total_pages - 1
        self.page_indicator.label = f"{self.page + 1}/{self.total_pages}"
        
        # 更新选择菜单
        if self.current_rules:
            options = []
            for rule in self.current_rules:
                triggers = rule.get('triggers', [])
                trigger_preview = ', '.join([t['text'] for t in triggers[:2]])
                if len(triggers) > 2:
                    trigger_preview += '...'
                
                options.append(discord.SelectOption(
                    label=f"规则 #{rule['rule_id']}",
                    description=f"{rule['action_type']} - {trigger_preview[:50]}",
                    value=str(rule['rule_id'])
                ))
            
            self.rule_select.options = options
            self.rule_select.disabled = False
        else:
            self.rule_select.options = [
                discord.SelectOption(label="暂无规则", value="none")
            ]
            self.rule_select.disabled = True
    
    @ui.select(placeholder="选择规则查看详情", row=0)
    async def rule_select(self, interaction: discord.Interaction, select: ui.Select):
        """选择规则"""
        if select.values[0] == "none":
            return
        
        if self.on_select:
            await self.on_select(interaction, int(select.values[0]))
    
    @ui.button(label="◀", style=discord.ButtonStyle.secondary, row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        """上一页"""
        if self.page > 0:
            self.page -= 1
            self._update_components()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()
    
    @ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True, row=1)
    async def page_indicator(self, interaction: discord.Interaction, button: ui.Button):
        """页码指示"""
        await interaction.response.defer()
    
    @ui.button(label="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        """下一页"""
        if self.page < self.total_pages - 1:
            self.page += 1
            self._update_components()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()
    
    @ui.button(label="➕ 创建规则", style=discord.ButtonStyle.green, row=1)
    async def create_btn(self, interaction: discord.Interaction, button: ui.Button):
        """创建新规则"""
        if self.on_create:
            await self.on_create(interaction)


class RuleDetailView(ui.View):
    """规则详情视图"""
    
    def __init__(
        self,
        rule: dict,
        on_edit: Callable,
        on_delete: Callable,
        on_toggle: Callable,
        on_add_trigger: Callable,
        on_delete_trigger: Callable,
        on_back: Callable,
        timeout: float = 300
    ):
        super().__init__(timeout=timeout)
        self.rule = rule
        self.on_edit = on_edit
        self.on_delete = on_delete
        self.on_toggle = on_toggle
        self.on_add_trigger = on_add_trigger
        self.on_delete_trigger = on_delete_trigger
        self.on_back = on_back
        
        self._update_toggle_button()
    
    def _update_toggle_button(self):
        """更新开关按钮"""
        is_enabled = self.rule.get('is_enabled', True)
        self.toggle_btn.label = "禁用规则" if is_enabled else "启用规则"
        self.toggle_btn.style = discord.ButtonStyle.red if is_enabled else discord.ButtonStyle.green
    
    @ui.button(label="✏️ 编辑", style=discord.ButtonStyle.blurple, row=0)
    async def edit_btn(self, interaction: discord.Interaction, button: ui.Button):
        """编辑规则"""
        await self.on_edit(interaction, self.rule['rule_id'])
    
    @ui.button(label="启用规则", style=discord.ButtonStyle.green, row=0)
    async def toggle_btn(self, interaction: discord.Interaction, button: ui.Button):
        """切换规则状态"""
        new_state = not self.rule.get('is_enabled', True)
        await self.on_toggle(interaction, self.rule['rule_id'], new_state)
        self.rule['is_enabled'] = new_state
        self._update_toggle_button()
        await interaction.message.edit(view=self)
    
    @ui.button(label="🗑️ 删除", style=discord.ButtonStyle.red, row=0)
    async def delete_btn(self, interaction: discord.Interaction, button: ui.Button):
        """删除规则"""
        await self.on_delete(interaction, self.rule['rule_id'])
    
    @ui.button(label="➕ 添加触发器", style=discord.ButtonStyle.green, row=1)
    async def add_trigger_btn(self, interaction: discord.Interaction, button: ui.Button):
        """添加触发器"""
        await self.on_add_trigger(interaction, self.rule['rule_id'])
    
    @ui.button(label="🔙 返回列表", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, button: ui.Button):
        """返回列表"""
        await self.on_back(interaction)


class DeleteConfirmView(ui.View):
    """删除确认视图"""
    
    def __init__(self, on_confirm: Callable, on_cancel: Callable, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
    
    @ui.button(label="确认删除", style=discord.ButtonStyle.red)
    async def confirm_btn(self, interaction: discord.Interaction, button: ui.Button):
        """确认删除"""
        await self.on_confirm(interaction)
    
    @ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: ui.Button):
        """取消"""
        await self.on_cancel(interaction)


# ==================== 快速设置视图 ====================

class QuickSetupView(ui.View):
    """快速设置视图 - 用于帖子内快速配置"""
    
    def __init__(
        self,
        thread_id: str,
        on_add_rule: Callable,
        on_view_rules: Callable,
        on_disable_all: Callable,
        timeout: float = 300
    ):
        super().__init__(timeout=timeout)
        self.thread_id = thread_id
        self.on_add_rule = on_add_rule
        self.on_view_rules = on_view_rules
        self.on_disable_all = on_disable_all
    
    @ui.button(label="➕ 添加规则", style=discord.ButtonStyle.green, row=0)
    async def add_rule_btn(self, interaction: discord.Interaction, button: ui.Button):
        """添加规则"""
        await self.on_add_rule(interaction)
    
    @ui.button(label="📋 查看规则", style=discord.ButtonStyle.blurple, row=0)
    async def view_rules_btn(self, interaction: discord.Interaction, button: ui.Button):
        """查看规则"""
        await self.on_view_rules(interaction)
    
    @ui.button(label="🚫 禁用所有", style=discord.ButtonStyle.red, row=0)
    async def disable_all_btn(self, interaction: discord.Interaction, button: ui.Button):
        """禁用所有规则"""
        await self.on_disable_all(interaction)


class CooldownSettingModal(ui.Modal, title="设置默认限流"):
    """限流设置模态框"""
    
    user_reply_cooldown = ui.TextInput(
        label="用户回复冷却（秒）",
        placeholder="同一用户触发同一规则的回复间隔",
        default="60",
        max_length=10,
        required=True
    )
    
    thread_reply_cooldown = ui.TextInput(
        label="帖子回复冷却（秒）",
        placeholder="同一帖子内触发同一规则的回复间隔",
        default="30",
        max_length=10,
        required=True
    )
    
    channel_delete_cooldown = ui.TextInput(
        label="频道删除冷却（秒）",
        placeholder="同一频道内触发删除的间隔",
        default="10",
        max_length=10,
        required=True
    )
    
    def __init__(self, current_config: dict, on_submit_callback: Callable):
        super().__init__()
        self.current_config = current_config
        self.on_submit_callback = on_submit_callback
        
        # 填充当前值
        if current_config.get('default_user_reply_cooldown'):
            self.user_reply_cooldown.default = str(current_config['default_user_reply_cooldown'])
        if current_config.get('default_thread_reply_cooldown'):
            self.thread_reply_cooldown.default = str(current_config['default_thread_reply_cooldown'])
        if current_config.get('default_channel_delete_cooldown'):
            self.channel_delete_cooldown.default = str(current_config['default_channel_delete_cooldown'])
    
    async def on_submit(self, interaction: discord.Interaction):
        """处理提交"""
        try:
            user_cd = int(self.user_reply_cooldown.value.strip())
            thread_cd = int(self.thread_reply_cooldown.value.strip())
            channel_cd = int(self.channel_delete_cooldown.value.strip())
            
            if any(v < 0 for v in [user_cd, thread_cd, channel_cd]):
                await interaction.response.send_message("❌ 冷却时间不能为负数", ephemeral=True)
                return
            
            await self.on_submit_callback(
                interaction,
                {
                    'default_user_reply_cooldown': user_cd,
                    'default_thread_reply_cooldown': thread_cd,
                    'default_channel_delete_cooldown': channel_cd,
                }
            )
        except ValueError:
            await interaction.response.send_message("❌ 请输入有效的数字", ephemeral=True)


# ==================== 权限管理视图 ====================

class PermissionManageView(ui.View):
    """权限管理视图"""
    
    def __init__(
        self,
        permissions: List[dict],
        on_add_user: Callable,
        on_add_role: Callable,
        on_remove: Callable,
        timeout: float = 300
    ):
        super().__init__(timeout=timeout)
        self.permissions = permissions
        self.on_add_user = on_add_user
        self.on_add_role = on_add_role
        self.on_remove = on_remove
        
        self._update_select()
    
    def _update_select(self):
        """更新权限选择菜单"""
        if self.permissions:
            options = []
            for perm in self.permissions[:25]:
                label = f"{'👤' if perm['target_type'] == 'user' else '🏷️'} {perm['target_id']}"
                desc = f"{perm['permission_level']}"
                options.append(discord.SelectOption(
                    label=label[:100],
                    description=desc[:100],
                    value=f"{perm['target_type']}:{perm['target_id']}"
                ))
            self.perm_select.options = options
            self.perm_select.disabled = False
        else:
            self.perm_select.options = [
                discord.SelectOption(label="暂无权限配置", value="none")
            ]
            self.perm_select.disabled = True
    
    @ui.select(placeholder="选择权限以删除", row=0)
    async def perm_select(self, interaction: discord.Interaction, select: ui.Select):
        """选择权限"""
        if select.values[0] == "none":
            return
        
        target_type, target_id = select.values[0].split(':', 1)
        await self.on_remove(interaction, target_type, target_id)
    
    @ui.button(label="👤 添加用户", style=discord.ButtonStyle.green, row=1)
    async def add_user_btn(self, interaction: discord.Interaction, button: ui.Button):
        """添加用户权限"""
        await self.on_add_user(interaction)
    
    @ui.button(label="🏷️ 添加身份组", style=discord.ButtonStyle.blurple, row=1)
    async def add_role_btn(self, interaction: discord.Interaction, button: ui.Button):
        """添加身份组权限"""
        await self.on_add_role(interaction)


class PermissionAddModal(ui.Modal, title="添加权限"):
    """添加权限模态框"""
    
    target_id = ui.TextInput(
        label="用户/身份组ID",
        placeholder="输入用户ID或身份组ID",
        max_length=30,
        required=True
    )
    
    permission_level = ui.TextInput(
        label="权限级别（全服配置/帖子代理）",
        placeholder="全服配置=可管理全服规则 | 帖子代理=可管理指定帖子规则",
        default="全服配置",
        max_length=20,
        required=True
    )
    
    def __init__(self, target_type: str, on_submit_callback: Callable):
        super().__init__()
        self.target_type = target_type
        self.on_submit_callback = on_submit_callback
        
        if target_type == 'user':
            self.target_id.label = "用户ID"
            self.target_id.placeholder = "输入用户ID"
        else:
            self.target_id.label = "身份组ID"
            self.target_id.placeholder = "输入身份组ID"
    
    async def on_submit(self, interaction: discord.Interaction):
        """处理提交"""
        level_input = self.permission_level.value.strip()
        level = PERMISSION_LEVEL_MAP.get(level_input) or PERMISSION_LEVEL_MAP.get(level_input.lower())
        if not level:
            await interaction.response.send_message(
                "❌ 权限级别无效，可选：全服配置 / 帖子代理",
                ephemeral=True
            )
            return
        
        await self.on_submit_callback(
            interaction,
            self.target_type,
            self.target_id.value.strip(),
            level
        )