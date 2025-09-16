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