import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

import pytz


class TimezoneFormatter(logging.Formatter):
    """自定义格式化器，支持特定时区的时间显示"""
    
    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None, tz: Optional[pytz.tzinfo.BaseTzInfo] = None):
        """
        初始化时区格式化器
        
        Args:
            fmt: 日志格式字符串
            datefmt: 时间格式字符串
            tz: 时区对象，默认为UTC
        """
        super().__init__(fmt, datefmt)
        self.tz = tz if tz else pytz.utc

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        """
        格式化日志记录的时间
        
        Args:
            record: 日志记录对象
            datefmt: 时间格式字符串
            
        Returns:
            格式化后的时间字符串
        """
        dt = datetime.fromtimestamp(record.created, self.tz)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime('%Y-%m-%d %H:%M:%S')


def setup_logger(
    name: str = "discord_bot",
    log_dir: str = "logs",
    log_level: int = logging.INFO,
    use_beijing_tz: bool = True
) -> logging.Logger:
    """
    设置并返回配置好的日志记录器
    
    Args:
        name: 日志记录器名称
        log_dir: 日志文件目录
        log_level: 日志级别
        use_beijing_tz: 是否使用北京时区
        
    Returns:
        配置好的日志记录器
    """
    # 创建日志目录
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 创建或获取日志记录器
    logger = logging.getLogger(name)
    
    # 如果已经配置过，直接返回
    if logger.hasHandlers():
        return logger
    
    logger.setLevel(log_level)
    
    # 设置时区
    tz = pytz.timezone('Asia/Shanghai') if use_beijing_tz else pytz.utc
    
    # 创建格式化器
    formatter = TimezoneFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        tz=tz
    )
    
    # 文件处理器 - 每日轮转
    log_path = os.path.join(log_dir, f'{name}.log')
    file_handler = TimedRotatingFileHandler(
        log_path,
        when='midnight',
        interval=1,
        backupCount=7,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)
    
    # 添加处理器
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器
    
    Args:
        name: 日志记录器名称
        
    Returns:
        日志记录器实例
    """
    return logging.getLogger(name)


# 创建默认的全局日志记录器
default_logger = setup_logger()