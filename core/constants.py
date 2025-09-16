"""
模块名称: constants.py
功能描述: 全局常量和配置定义
作者: @Kilo Code
创建日期: 2024-09-15
最后修改: 2024-09-15
"""

import os
from pathlib import Path

import pytz

# ===== 路径配置 =====
# 项目根目录（bot.py所在目录）
PROJECT_ROOT = Path(__file__).parent.parent
# BOT_DIR 别名（兼容旧代码）
BOT_DIR = PROJECT_ROOT
# 数据目录
DATA_DIR = PROJECT_ROOT / "data"
# DATA_PATH 别名（兼容旧代码）
DATA_PATH = DATA_DIR
# 日志目录
LOG_DIR = DATA_DIR / "logs"
# 备份目录
BACKUP_DIR = DATA_DIR / "gym_backups"
# 数据库路径 - 使用与bot.py同目录的文件
DATABASE_PATH = PROJECT_ROOT / "progress.db"
# 配置文件路径 - 使用与bot.py同目录的文件
CONFIG_PATH = PROJECT_ROOT / "config.json"

# ===== 时区配置 =====
BEIJING_TZ = pytz.timezone('Asia/Shanghai')
DEFAULT_TIMEZONE = BEIJING_TZ

# ===== 日志配置 =====
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_ROTATION = "midnight"
LOG_BACKUP_COUNT = 7

# ===== 数据库配置 =====
DATABASE_TIMEOUT = 10  # 数据库连接超时时间（秒）
CONNECTION_POOL_SIZE = 5  # 连接池大小

# ===== Discord相关常量 =====
# 嵌入消息限制
EMBED_TITLE_LIMIT = 256
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_NAME_LIMIT = 256
EMBED_FIELD_VALUE_LIMIT = 1024
EMBED_FIELD_COUNT_LIMIT = 25
EMBED_TOTAL_LIMIT = 6000

# 按钮标签限制
BUTTON_LABEL_LIMIT = 80

# 消息内容限制
MESSAGE_CONTENT_LIMIT = 2000

# 文件大小限制
FILE_SIZE_LIMIT = 25 * 1024 * 1024  # 25MB

# ===== 道馆挑战配置 =====
# 默认允许错误数
DEFAULT_ALLOWED_MISTAKES = 0

# 挑战超时时间（秒）
CHALLENGE_TIMEOUT = 180

# 失败惩罚时长（小时）
FAILURE_PENALTIES = {
    3: 1,    # 第3次失败：1小时
    4: 6,    # 第4次失败：6小时
    5: 12,   # 第5次及以上：12小时
}

# 究极道馆题目比例
ULTIMATE_GYM_QUESTION_RATIO = 0.5  # 抽取50%的题目

# ===== 排行榜配置 =====
LEADERBOARD_DISPLAY_LIMIT = 20  # 排行榜显示人数
LEADERBOARD_TOP_EMOJIS = {
    1: "🥇",
    2: "🥈",
    3: "🥉"
}

# ===== 分页配置 =====
DEFAULT_PAGE_SIZE = 5  # 默认每页显示条数
MAX_PAGE_SIZE = 25    # 最大每页显示条数

# ===== 备份配置 =====
BACKUP_DAILY_HOUR = 3  # 每日备份时间（小时）
BACKUP_RETENTION_DAYS = 30  # 备份保留天数

# ===== 缓存配置 =====
CACHE_TTL = 300  # 缓存生存时间（秒）

# ===== 开发者配置 =====
# 这些ID应该从配置文件中读取，这里只是默认值
DEVELOPER_IDS = []

# ===== 题目类型 =====
QUESTION_TYPES = {
    "MULTIPLE_CHOICE": "multiple_choice",
    "TRUE_FALSE": "true_false",
    "FILL_IN_BLANK": "fill_in_blank"
}

# ===== 面板类型 =====
PANEL_TYPES = {
    "STANDARD": "standard",
    "ULTIMATE": "ultimate"
}

# ===== 权限级别 =====
PERMISSION_LEVELS = {
    "ALL": "all",
    "OWNER": "owner",
    "ADMIN": "admin",
    "GYM_MASTER": "gym_master",
    "USER": "user"
}