"""
模块名称: models.py
功能描述: 数据模型定义，包含所有业务实体的数据结构
作者: @Kilo Code
创建日期: 2024-09-15
最后修改: 2024-09-15
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import re


@dataclass
class Gym:
    """道馆数据模型"""
    gym_id: str
    guild_id: str
    name: str
    description: str
    tutorial: List[str]
    questions: List[Dict[str, Any]]
    is_enabled: bool = True
    questions_to_ask: Optional[int] = None
    allowed_mistakes: Optional[int] = None
    badge_image_url: Optional[str] = None
    badge_description: Optional[str] = None
    randomize_options: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'id': self.gym_id,
            'name': self.name,
            'description': self.description,
            'tutorial': self.tutorial,
            'questions': self.questions,
            'is_enabled': self.is_enabled,
            'questions_to_ask': self.questions_to_ask,
            'allowed_mistakes': self.allowed_mistakes,
            'badge_image_url': self.badge_image_url,
            'badge_description': self.badge_description,
            'randomize_options': self.randomize_options
        }
    
    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=4)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], guild_id: str) -> 'Gym':
        """从字典创建实例"""
        return cls(
            gym_id=data['id'],
            guild_id=guild_id,
            name=data['name'],
            description=data['description'],
            tutorial=data['tutorial'],
            questions=data['questions'],
            is_enabled=data.get('is_enabled', True),
            questions_to_ask=data.get('questions_to_ask'),
            allowed_mistakes=data.get('allowed_mistakes'),
            badge_image_url=data.get('badge_image_url'),
            badge_description=data.get('badge_description'),
            randomize_options=data.get('randomize_options', True)
        )


@dataclass
class UserProgress:
    """用户进度数据模型"""
    user_id: str
    guild_id: str
    completed_gyms: Dict[str, bool] = field(default_factory=dict)
    
    def is_gym_completed(self, gym_id: str) -> bool:
        """检查道馆是否已完成"""
        return self.completed_gyms.get(gym_id, False)
    
    def complete_gym(self, gym_id: str):
        """标记道馆为已完成"""
        self.completed_gyms[gym_id] = True
    
    def get_completion_count(self) -> int:
        """获取已完成的道馆数量"""
        return sum(1 for completed in self.completed_gyms.values() if completed)


@dataclass
class ChallengeFailure:
    """挑战失败记录数据模型"""
    user_id: str
    guild_id: str
    gym_id: str
    failure_count: int = 0
    banned_until: Optional[datetime] = None
    
    def is_banned(self) -> bool:
        """检查是否在封禁期内"""
        if not self.banned_until:
            return False
        return datetime.now() < self.banned_until


@dataclass
class ChallengePanel:
    """挑战面板数据模型"""
    message_id: str
    guild_id: str
    channel_id: str
    role_to_add_ids: Optional[List[str]] = None
    role_to_remove_ids: Optional[List[str]] = None
    associated_gyms: Optional[List[str]] = None
    blacklist_enabled: bool = True
    completion_threshold: Optional[int] = None
    prerequisite_gyms: Optional[List[str]] = None
    is_ultimate_gym: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'message_id': self.message_id,
            'guild_id': self.guild_id,
            'channel_id': self.channel_id,
            'role_to_add_ids': json.dumps(self.role_to_add_ids) if self.role_to_add_ids else None,
            'role_to_remove_ids': json.dumps(self.role_to_remove_ids) if self.role_to_remove_ids else None,
            'associated_gyms': json.dumps(self.associated_gyms) if self.associated_gyms else None,
            'blacklist_enabled': self.blacklist_enabled,
            'completion_threshold': self.completion_threshold,
            'prerequisite_gyms': json.dumps(self.prerequisite_gyms) if self.prerequisite_gyms else None,
            'is_ultimate_gym': self.is_ultimate_gym
        }


@dataclass
class BlacklistEntry:
    """黑名单条目数据模型"""
    guild_id: str
    target_id: str
    target_type: str  # 'user' or 'role'
    reason: Optional[str] = None
    added_by: Optional[str] = None
    timestamp: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'guild_id': self.guild_id,
            'target_id': self.target_id,
            'target_type': self.target_type,
            'reason': self.reason,
            'added_by': self.added_by,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }


@dataclass
class UltimateLeaderboardEntry:
    """究极道馆排行榜条目数据模型"""
    guild_id: str
    user_id: str
    completion_time_seconds: float
    timestamp: datetime
    
    def get_formatted_time(self) -> str:
        """获取格式化的完成时间"""
        minutes, seconds = divmod(int(self.completion_time_seconds), 60)
        return f"{minutes}分 {seconds}秒"


@dataclass
class GymMaster:
    """道馆管理权限数据模型"""
    guild_id: str
    target_id: str
    target_type: str  # 'user' or 'role'
    permission: str  # 'all' or specific command name
    
    def has_permission(self, command_name: str) -> bool:
        """检查是否有特定权限"""
        return self.permission == 'all' or self.permission == command_name


@dataclass
class LeaderboardPanel:
    """排行榜面板数据模型"""
    message_id: str
    guild_id: str
    channel_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'message_id': self.message_id,
            'guild_id': self.guild_id,
            'channel_id': self.channel_id,
            'title': self.title,
            'description': self.description
        }


@dataclass
class Question:
    """题目数据模型"""
    type: str  # 'multiple_choice', 'true_false', 'fill_in_blank'
    text: str
    correct_answer: Any  # 可以是字符串或列表
    options: Optional[List[str]] = None
    
    def is_answer_correct(self, user_answer: str) -> bool:
        """检查答案是否正确"""
        if isinstance(self.correct_answer, list):
            # 填空题可能有多个正确答案
            return any(user_answer.lower() == str(ans).lower() for ans in self.correct_answer)
        else:
            # 单一正确答案
            return user_answer.lower() == str(self.correct_answer).lower()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        data = {
            'type': self.type,
            'text': self.text,
            'correct_answer': self.correct_answer
        }
        if self.options:
            data['options'] = self.options
        return data


@dataclass
class ClaimedReward:
    """已领取奖励记录数据模型"""
    guild_id: str
    user_id: str
    role_id: str
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'guild_id': self.guild_id,
            'user_id': self.user_id,
            'role_id': self.role_id,
            'timestamp': self.timestamp.isoformat()
        }


# 添加别名以兼容旧代码
BanEntry = ChallengeFailure  # 用于兼容旧代码中的 BanEntry 引用

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime

@dataclass
class ForumMonitorConfig:
    """论坛发帖监控配置数据模型"""
    guild_id: str
    forum_channel_id: str
    auto_role_enabled: bool = False
    auto_role_id: Optional[str] = None
    notify_enabled: bool = True
    notify_message: Optional[str] = None
    mention_role_enabled: bool = False
    mention_role_id: Optional[str] = None
    mention_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（适配数据库驱动）"""
        return {
            'guild_id': self.guild_id,
            'forum_channel_id': self.forum_channel_id,
            'auto_role_enabled': 1 if self.auto_role_enabled else 0,
            'auto_role_id': self.auto_role_id,
            'notify_enabled': 1 if self.notify_enabled else 0,
            'notify_message': self.notify_message,
            'mention_role_enabled': 1 if self.mention_role_enabled else 0,
            'mention_role_id': self.mention_role_id,
            'mention_message': self.mention_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'ForumMonitorConfig':
        """从数据库行字典构建实例"""
        def parse_bool(v):
            if isinstance(v, bool):
                return v
            if v is None:
                return False
            # SQLite BOOLEAN 可能以0/1或'TRUE'/'FALSE'出现
            if isinstance(v, (int, float)):
                return v != 0
            s = str(v).strip().lower()
            return s in ('1', 'true', 't', 'yes', 'y')
        def parse_dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None

        return cls(
            guild_id=str(row.get('guild_id')),
            forum_channel_id=str(row.get('forum_channel_id')),
            auto_role_enabled=parse_bool(row.get('auto_role_enabled')),
            auto_role_id=str(row.get('auto_role_id')) if row.get('auto_role_id') else None,
            notify_enabled=parse_bool(row.get('notify_enabled')),
            notify_message=row.get('notify_message') if row.get('notify_message') else None,
            mention_role_enabled=parse_bool(row.get('mention_role_enabled')),
            mention_role_id=str(row.get('mention_role_id')) if row.get('mention_role_id') else None,
            mention_message=row.get('mention_message') if row.get('mention_message') else None,
            created_at=parse_dt(row.get('created_at')),
            updated_at=parse_dt(row.get('updated_at')),
        )

    def to_json(self) -> str:
        """转换为JSON字符串"""
        import json
        return json.dumps({
            'guild_id': self.guild_id,
            'forum_channel_id': self.forum_channel_id,
            'auto_role_enabled': self.auto_role_enabled,
            'auto_role_id': self.auto_role_id,
            'notify_enabled': self.notify_enabled,
            'notify_message': self.notify_message,
            'mention_role_enabled': self.mention_role_enabled,
            'mention_role_id': self.mention_role_id,
            'mention_message': self.mention_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }, ensure_ascii=False, indent=4)


# ==================== 帖子自定义命令系统数据模型 ====================

@dataclass
class ThreadCommandTrigger:
    """
    触发器数据模型
    
    一个规则可以有多个触发器，任一触发器匹配即执行规则动作。
    支持四种匹配模式：exact(精确)、prefix(前缀)、contains(包含)、regex(正则)
    """
    trigger_id: Optional[int]
    rule_id: int                    # 关联的规则ID
    trigger_text: str               # 触发文本或正则表达式
    trigger_mode: str = 'exact'     # 'exact', 'prefix', 'contains', 'regex'
    is_enabled: bool = True
    created_at: Optional[datetime] = None
    
    # 预编译的正则（运行时缓存，不持久化）
    _compiled_regex: Optional[re.Pattern] = field(default=None, repr=False, compare=False)
    
    def compile_regex(self) -> Optional[re.Pattern]:
        """预编译正则表达式，提升匹配性能"""
        if self.trigger_mode == 'regex' and self._compiled_regex is None:
            try:
                self._compiled_regex = re.compile(self.trigger_text, re.IGNORECASE)
            except re.error:
                return None
        return self._compiled_regex
    
    def match(self, content: str) -> bool:
        """检查内容是否匹配此触发器"""
        if not self.is_enabled:
            return False
        content = content.strip()
        trigger = self.trigger_text.strip()
        
        if self.trigger_mode == 'exact':
            return content == trigger
        elif self.trigger_mode == 'prefix':
            return content.startswith(trigger)
        elif self.trigger_mode == 'contains':
            return trigger in content
        elif self.trigger_mode == 'regex':
            pattern = self.compile_regex()
            return bool(pattern.search(content)) if pattern else False
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'trigger_id': self.trigger_id,
            'rule_id': self.rule_id,
            'trigger_text': self.trigger_text,
            'trigger_mode': self.trigger_mode,
            'is_enabled': self.is_enabled,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
    
    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'ThreadCommandTrigger':
        """从数据库行字典构建实例"""
        def parse_dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None
        
        def parse_bool(v):
            if isinstance(v, bool):
                return v
            if v is None:
                return True
            if isinstance(v, (int, float)):
                return v != 0
            s = str(v).strip().lower()
            return s in ('1', 'true', 't', 'yes', 'y')
        
        return cls(
            trigger_id=row.get('trigger_id'),
            rule_id=row.get('rule_id'),
            trigger_text=row.get('trigger_text', ''),
            trigger_mode=row.get('trigger_mode', 'exact'),
            is_enabled=parse_bool(row.get('is_enabled', True)),
            created_at=parse_dt(row.get('created_at')),
        )


@dataclass
class ThreadCommandRule:
    """
    帖子命令规则数据模型
    
    规则定义了触发条件和对应的动作。
    scope='server' 表示全服规则，scope='thread' 表示帖子级规则。
    """
    rule_id: Optional[int]
    guild_id: str
    scope: str                      # 'server' 或 'thread'
    thread_id: Optional[str] = None
    forum_channel_id: Optional[str] = None
    
    # 触发器列表（通过 thread_command_triggers 表关联）
    triggers: List[ThreadCommandTrigger] = field(default_factory=list)
    
    # 动作配置
    action_type: str = 'reply'      # 'reply', 'go_to_top', 'react', 'reply_and_react'
    reply_content: Optional[str] = None
    reply_embed_json: Optional[str] = None
    delete_trigger_delay: Optional[int] = None   # 秒，None=不删除
    delete_reply_delay: Optional[int] = None     # 秒，None=不删除
    add_reaction: Optional[str] = None
    
    # 分级限流配置（None=使用全服默认）
    user_reply_cooldown: Optional[int] = None
    user_delete_cooldown: Optional[int] = None
    thread_reply_cooldown: Optional[int] = None
    thread_delete_cooldown: Optional[int] = None
    channel_reply_cooldown: Optional[int] = None
    channel_delete_cooldown: Optional[int] = None
    
    # 元数据
    is_enabled: bool = True
    priority: int = 0               # 数值越大优先级越高
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def match(self, content: str) -> bool:
        """检查内容是否匹配任一触发器"""
        if not self.is_enabled:
            return False
        for trigger in self.triggers:
            if trigger.match(content):
                return True
        return False
    
    def get_matched_trigger(self, content: str) -> Optional[ThreadCommandTrigger]:
        """返回第一个匹配的触发器"""
        if not self.is_enabled:
            return None
        for trigger in self.triggers:
            if trigger.match(content):
                return trigger
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'rule_id': self.rule_id,
            'guild_id': self.guild_id,
            'scope': self.scope,
            'thread_id': self.thread_id,
            'forum_channel_id': self.forum_channel_id,
            'triggers': [t.to_dict() for t in self.triggers],
            'action_type': self.action_type,
            'reply_content': self.reply_content,
            'reply_embed_json': self.reply_embed_json,
            'delete_trigger_delay': self.delete_trigger_delay,
            'delete_reply_delay': self.delete_reply_delay,
            'add_reaction': self.add_reaction,
            'user_reply_cooldown': self.user_reply_cooldown,
            'user_delete_cooldown': self.user_delete_cooldown,
            'thread_reply_cooldown': self.thread_reply_cooldown,
            'thread_delete_cooldown': self.thread_delete_cooldown,
            'channel_reply_cooldown': self.channel_reply_cooldown,
            'channel_delete_cooldown': self.channel_delete_cooldown,
            'is_enabled': self.is_enabled,
            'priority': self.priority,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
    
    @classmethod
    def from_row(cls, row: Dict[str, Any], triggers: Optional[List[ThreadCommandTrigger]] = None) -> 'ThreadCommandRule':
        """从数据库行字典构建实例"""
        def parse_dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None
        
        def parse_bool(v):
            if isinstance(v, bool):
                return v
            if v is None:
                return True
            if isinstance(v, (int, float)):
                return v != 0
            s = str(v).strip().lower()
            return s in ('1', 'true', 't', 'yes', 'y')
        
        def parse_int(v):
            if v is None:
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None
        
        return cls(
            rule_id=row.get('rule_id'),
            guild_id=str(row.get('guild_id', '')),
            scope=row.get('scope', 'server'),
            thread_id=str(row.get('thread_id')) if row.get('thread_id') else None,
            forum_channel_id=str(row.get('forum_channel_id')) if row.get('forum_channel_id') else None,
            triggers=triggers or [],
            action_type=row.get('action_type', 'reply'),
            reply_content=row.get('reply_content'),
            reply_embed_json=row.get('reply_embed_json'),
            delete_trigger_delay=parse_int(row.get('delete_trigger_delay')),
            delete_reply_delay=parse_int(row.get('delete_reply_delay')),
            add_reaction=row.get('add_reaction'),
            user_reply_cooldown=parse_int(row.get('user_reply_cooldown')),
            user_delete_cooldown=parse_int(row.get('user_delete_cooldown')),
            thread_reply_cooldown=parse_int(row.get('thread_reply_cooldown')),
            thread_delete_cooldown=parse_int(row.get('thread_delete_cooldown')),
            channel_reply_cooldown=parse_int(row.get('channel_reply_cooldown')),
            channel_delete_cooldown=parse_int(row.get('channel_delete_cooldown')),
            is_enabled=parse_bool(row.get('is_enabled', True)),
            priority=parse_int(row.get('priority')) or 0,
            created_by=row.get('created_by'),
            created_at=parse_dt(row.get('created_at')),
            updated_at=parse_dt(row.get('updated_at')),
        )


@dataclass
class ThreadCommandServerConfig:
    """
    服务器级别的帖子命令配置
    
    包含全服开关、贴主配置权限开关、允许的论坛频道、默认限流配置等。
    """
    guild_id: str
    is_enabled: bool = True
    allow_thread_owner_config: bool = True
    allowed_forum_channels: Optional[str] = None  # JSON数组，存储允许的论坛频道ID列表
    default_delete_trigger_delay: Optional[int] = None
    default_delete_reply_delay: Optional[int] = None
    
    # 默认限流配置
    default_user_reply_cooldown: int = 60
    default_user_delete_cooldown: int = 0
    default_thread_reply_cooldown: int = 30
    default_thread_delete_cooldown: int = 0
    default_channel_reply_cooldown: int = 10
    default_channel_delete_cooldown: int = 0
    
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def get_allowed_forum_channels_list(self) -> List[str]:
        """获取允许的论坛频道ID列表"""
        if not self.allowed_forum_channels:
            return []
        try:
            return json.loads(self.allowed_forum_channels)
        except (json.JSONDecodeError, TypeError):
            return []
    
    def set_allowed_forum_channels_list(self, channel_ids: List[str]) -> None:
        """设置允许的论坛频道ID列表"""
        self.allowed_forum_channels = json.dumps(channel_ids) if channel_ids else None
    
    def is_forum_channel_allowed(self, channel_id: str) -> bool:
        """检查指定论坛频道是否在允许列表中（空列表表示允许所有）"""
        allowed = self.get_allowed_forum_channels_list()
        return len(allowed) == 0 or channel_id in allowed
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'guild_id': self.guild_id,
            'is_enabled': self.is_enabled,
            'allow_thread_owner_config': self.allow_thread_owner_config,
            'allowed_forum_channels': self.allowed_forum_channels,
            'default_delete_trigger_delay': self.default_delete_trigger_delay,
            'default_delete_reply_delay': self.default_delete_reply_delay,
            'default_user_reply_cooldown': self.default_user_reply_cooldown,
            'default_user_delete_cooldown': self.default_user_delete_cooldown,
            'default_thread_reply_cooldown': self.default_thread_reply_cooldown,
            'default_thread_delete_cooldown': self.default_thread_delete_cooldown,
            'default_channel_reply_cooldown': self.default_channel_reply_cooldown,
            'default_channel_delete_cooldown': self.default_channel_delete_cooldown,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
    
    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'ThreadCommandServerConfig':
        """从数据库行字典构建实例"""
        def parse_dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None
        
        def parse_bool(v):
            if isinstance(v, bool):
                return v
            if v is None:
                return True
            if isinstance(v, (int, float)):
                return v != 0
            s = str(v).strip().lower()
            return s in ('1', 'true', 't', 'yes', 'y')
        
        def parse_int(v, default=0):
            if v is None:
                return default
            try:
                return int(v)
            except (ValueError, TypeError):
                return default
        
        return cls(
            guild_id=str(row.get('guild_id', '')),
            is_enabled=parse_bool(row.get('is_enabled', True)),
            allow_thread_owner_config=parse_bool(row.get('allow_thread_owner_config', True)),
            allowed_forum_channels=row.get('allowed_forum_channels'),
            default_delete_trigger_delay=parse_int(row.get('default_delete_trigger_delay'), None),
            default_delete_reply_delay=parse_int(row.get('default_delete_reply_delay'), None),
            default_user_reply_cooldown=parse_int(row.get('default_user_reply_cooldown'), 60),
            default_user_delete_cooldown=parse_int(row.get('default_user_delete_cooldown'), 0),
            default_thread_reply_cooldown=parse_int(row.get('default_thread_reply_cooldown'), 30),
            default_thread_delete_cooldown=parse_int(row.get('default_thread_delete_cooldown'), 0),
            default_channel_reply_cooldown=parse_int(row.get('default_channel_reply_cooldown'), 10),
            default_channel_delete_cooldown=parse_int(row.get('default_channel_delete_cooldown'), 0),
            created_at=parse_dt(row.get('created_at')),
            updated_at=parse_dt(row.get('updated_at')),
        )


@dataclass
class ThreadCommandPermission:
    """
    帖子命令权限配置
    
    用于授予用户或身份组管理帖子命令的权限。
    permission_level: 'server_config' 可管理全服规则，'thread_delegate' 仅供贴主使用（预留）
    """
    guild_id: str
    target_id: str                  # 用户ID或身份组ID
    target_type: str                # 'user' 或 'role'
    permission_level: str           # 'server_config' 或 'thread_delegate'
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'guild_id': self.guild_id,
            'target_id': self.target_id,
            'target_type': self.target_type,
            'permission_level': self.permission_level,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
    
    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> 'ThreadCommandPermission':
        """从数据库行字典构建实例"""
        def parse_dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None
        
        return cls(
            guild_id=str(row.get('guild_id', '')),
            target_id=str(row.get('target_id', '')),
            target_type=row.get('target_type', 'user'),
            permission_level=row.get('permission_level', 'server_config'),
            created_by=row.get('created_by'),
            created_at=parse_dt(row.get('created_at')),
        )