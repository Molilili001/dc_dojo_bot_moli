import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

import pytz
from core.constants import (
    LOG_DIR as DEFAULT_LOG_DIR,
    LOG_BACKUP_COUNT as DEFAULT_BACKUP_COUNT,
    LOG_FORMAT as DEFAULT_LOG_FORMAT,
    LOG_DATE_FORMAT as DEFAULT_LOG_DATE_FORMAT,
)


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
    log_dir: Optional[str] = None,
    log_level: Optional[int] = None,
    use_beijing_tz: bool = True
) -> logging.Logger:
    """
    初始化全局日志系统，并返回指定名称的日志记录器。
    关键变更：
    - 将处理器绑定到 root logger，保证任意模块 logger 都能写入同一套处理器
    - 默认日志目录统一为 data/logs（来自 core.constants.LOG_DIR）
    - 单一文件：data/logs/discord_bot.log（按日轮转，保留7天）
    """
    # 统一日志目录为 data/logs
    log_dir = log_dir or str(DEFAULT_LOG_DIR)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # 若 root 已配置处理器，则只调整级别并返回命名 logger
    root_logger = logging.getLogger()
    if root_logger.handlers:
        # 若已初始化但未传入级别，使用 WARNING 降低日志量
        root_logger.setLevel(log_level or logging.WARNING)
        return logging.getLogger(name)

    # 设置时区与格式
    tz = pytz.timezone('Asia/Shanghai') if use_beijing_tz else pytz.utc

    # 若未指定级别，默认使用 WARNING 降低日志量
    if log_level is None:
        log_level = logging.WARNING

    formatter = TimezoneFormatter(
        DEFAULT_LOG_FORMAT,
        datefmt=DEFAULT_LOG_DATE_FORMAT,
        tz=tz
    )

    # 文件处理器：按日轮转
    log_path = os.path.join(log_dir, f'{name}.log')  # 保持单一文件：discord_bot.log
    file_handler = TimedRotatingFileHandler(
        log_path,
        when='midnight',
        interval=1,
        backupCount=DEFAULT_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    # 绑定到 root，确保所有子 logger 生效
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # 捕获 warnings 到日志
    logging.captureWarnings(True)

    return logging.getLogger(name)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器。
    若全局日志尚未初始化，则进行一次惰性初始化。
    """
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        # 使用默认参数初始化（单文件：data/logs/discord_bot.log）
        setup_logger()
    return logging.getLogger(name)


# 创建默认的全局日志记录器
default_logger = setup_logger()