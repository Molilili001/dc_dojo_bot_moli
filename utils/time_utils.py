"""时间和时区处理工具函数。"""

from datetime import datetime, timedelta
from typing import Optional

from core.constants import BEIJING_TZ
from utils.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "get_beijing_now",
    "to_beijing_time",
    "parse_beijing_time",
    "format_beijing_iso",
    "format_beijing_display",
    "remaining_until",
    "is_future",
]


def get_beijing_now() -> datetime:
    """返回当前的北京时间（带时区信息）。"""
    return datetime.now(BEIJING_TZ)


def to_beijing_time(dt: Optional[datetime]) -> Optional[datetime]:
    """将任意 datetime 转换为北京时间。"""
    if dt is None:
        return None

    if dt.tzinfo is None:
        try:
            return BEIJING_TZ.localize(dt)
        except Exception:  # pragma: no cover - 兜底保护
            logger.warning("无法直接本地化 datetime 对象，已尝试手动转换为北京时间。")
            naive_clone = datetime(
                dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond
            )
            return BEIJING_TZ.localize(naive_clone)

    return dt.astimezone(BEIJING_TZ)


def parse_beijing_time(time_str: Optional[str]) -> Optional[datetime]:
    """解析时间字符串并返回北京时间的 datetime 对象。"""
    if not time_str:
        return None

    normalized = time_str.strip()
    if not normalized:
        return None

    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        base_segment = normalized
        if len(base_segment) >= 19:
            base_segment = base_segment[:19]
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(base_segment, fmt)
                return BEIJING_TZ.localize(parsed)
            except ValueError:
                continue
        logger.error("无法解析时间字符串 '%s'", normalized)
        return None

    if dt.tzinfo:
        return dt.astimezone(BEIJING_TZ)

    return BEIJING_TZ.localize(dt)


def format_beijing_iso(dt: Optional[datetime] = None, include_timezone: bool = False) -> str:
    """将 datetime 格式化为北京时间 ISO 字符串。"""
    beijing_dt = to_beijing_time(dt) if dt else get_beijing_now()

    if include_timezone:
        return beijing_dt.isoformat()

    return beijing_dt.replace(tzinfo=None).isoformat()


def format_beijing_display(dt: Optional[datetime] = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """将 datetime 格式化为指定的北京时间字符串。"""
    beijing_dt = to_beijing_time(dt) if dt else get_beijing_now()
    return beijing_dt.strftime(fmt)


def remaining_until(target_dt: Optional[datetime], now: Optional[datetime] = None) -> Optional[timedelta]:
    """计算距离目标时间的剩余时长（如果已过期则返回 None）。"""
    target = to_beijing_time(target_dt)
    if target is None:
        return None

    current = to_beijing_time(now) if now else get_beijing_now()
    delta = target - current
    if delta.total_seconds() <= 0:
        return None

    return delta


def is_future(target_dt: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """判断目标时间是否仍在未来。"""
    return remaining_until(target_dt, now) is not None