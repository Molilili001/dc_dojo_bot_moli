"""
模块名称: exceptions.py
功能描述: 自定义异常类定义
作者: @Kilo Code
创建日期: 2024-09-15
最后修改: 2024-09-15
"""

from typing import Optional


class BotBaseException(Exception):
    """机器人基础异常类"""
    
    def __init__(self, message: str, code: Optional[str] = None):
        """
        初始化异常
        
        Args:
            message: 异常消息
            code: 错误代码
        """
        super().__init__(message)
        self.message = message
        self.code = code


class DatabaseException(BotBaseException):
    """数据库相关异常"""
    
    def __init__(self, message: str, code: str = "DB_ERROR"):
        super().__init__(message, code)


class ConnectionPoolException(DatabaseException):
    """数据库连接池异常"""
    
    def __init__(self, message: str):
        super().__init__(message, "DB_POOL_ERROR")


class ValidationException(BotBaseException):
    """数据验证异常"""
    
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message, "VALIDATION_ERROR")
        self.field = field


# 添加别名以兼容旧代码
ValidationError = ValidationException


class PermissionException(BotBaseException):
    """权限相关异常"""
    
    def __init__(self, message: str, required_permission: Optional[str] = None):
        super().__init__(message, "PERMISSION_DENIED")
        self.required_permission = required_permission


class GymException(BotBaseException):
    """道馆相关异常"""
    
    def __init__(self, message: str, gym_id: Optional[str] = None):
        super().__init__(message, "GYM_ERROR")
        self.gym_id = gym_id


class GymNotFoundException(GymException):
    """道馆不存在异常"""
    
    def __init__(self, gym_id: str):
        super().__init__(f"道馆 {gym_id} 不存在", gym_id)
        self.code = "GYM_NOT_FOUND"


# 添加别名以兼容旧代码
GymNotFoundError = GymNotFoundException


class GymDisabledException(GymException):
    """道馆已停用异常"""
    
    def __init__(self, gym_id: str):
        super().__init__(f"道馆 {gym_id} 已停用", gym_id)
        self.code = "GYM_DISABLED"


class ChallengeException(BotBaseException):
    """挑战相关异常"""
    
    def __init__(self, message: str, user_id: Optional[str] = None):
        super().__init__(message, "CHALLENGE_ERROR")
        self.user_id = user_id


class ChallengeCooldownException(ChallengeException):
    """挑战冷却中异常"""
    
    def __init__(self, user_id: str, remaining_seconds: int):
        super().__init__(
            f"用户 {user_id} 正在冷却中，剩余 {remaining_seconds} 秒",
            user_id
        )
        self.code = "CHALLENGE_COOLDOWN"
        self.remaining_seconds = remaining_seconds


class UserBannedException(ChallengeException):
    """用户被封禁异常"""
    
    def __init__(self, user_id: str, reason: Optional[str] = None):
        message = f"用户 {user_id} 已被封禁"
        if reason:
            message += f"，原因：{reason}"
        super().__init__(message, user_id)
        self.code = "USER_BANNED"
        self.reason = reason


class UserBlacklistedException(ChallengeException):
    """用户在黑名单中异常"""
    
    def __init__(self, user_id: str, reason: Optional[str] = None):
        message = f"用户 {user_id} 在黑名单中"
        if reason:
            message += f"，原因：{reason}"
        super().__init__(message, user_id)
        self.code = "USER_BLACKLISTED"
        self.reason = reason


class ConfigurationException(BotBaseException):
    """配置相关异常"""
    
    def __init__(self, message: str, config_key: Optional[str] = None):
        super().__init__(message, "CONFIG_ERROR")
        self.config_key = config_key


class FileOperationException(BotBaseException):
    """文件操作异常"""
    
    def __init__(self, message: str, file_path: Optional[str] = None):
        super().__init__(message, "FILE_ERROR")
        self.file_path = file_path


class BackupException(FileOperationException):
    """备份相关异常"""
    
    def __init__(self, message: str, file_path: Optional[str] = None):
        super().__init__(message, file_path)
        self.code = "BACKUP_ERROR"