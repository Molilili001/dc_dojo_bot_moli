# 道馆封禁功能修复方案 - 统一使用北京时间

## 问题诊断

### 当前问题
1. **时区不一致**：
   - 存储时使用 UTC：`datetime.now(pytz.UTC)`
   - 比较时混用 UTC 和本地时间
   - `datetime.fromisoformat()` 在 Python 3.6 中不支持带时区的 ISO 字符串

2. **具体表现**：
   - 用户挑战失败后，封禁时间不生效
   - 用户可以立即重新挑战，无需等待冷却时间

## 修复方案 - 统一使用北京时间

### 核心原则
- **所有时间存储和比较都使用北京时间（Asia/Shanghai）**
- **数据库存储不带时区信息的 ISO 字符串，约定为北京时间**
- **显示给用户的时间都是北京时间**

### 具体修改

#### 1. 创建通用时间处理工具函数

在 `utils/formatters.py` 或新建 `utils/time_utils.py`：

```python
import pytz
from datetime import datetime

BEIJING_TZ = pytz.timezone('Asia/Shanghai')

def get_beijing_now():
    """获取当前北京时间"""
    return datetime.now(BEIJING_TZ)

def to_beijing_time(dt):
    """将任意时间转换为北京时间"""
    if dt.tzinfo is None:
        # 假定无时区的时间为北京时间
        dt = BEIJING_TZ.localize(dt)
    return dt.astimezone(BEIJING_TZ)

def parse_beijing_time(time_str):
    """解析时间字符串为北京时间
    
    支持两种格式：
    1. 带时区信息的 ISO 字符串
    2. 不带时区的字符串（假定为北京时间）
    """
    try:
        # 尝试解析 ISO 格式
        # 使用更兼容的方法替代 fromisoformat
        if 'T' in time_str:
            if '+' in time_str or 'Z' in time_str:
                # 带时区信息，使用 dateutil 或手动处理
                from dateutil import parser
                dt = parser.parse(time_str)
                return to_beijing_time(dt)
            else:
                # 不带时区，假定为北京时间
                dt = datetime.strptime(time_str[:19], '%Y-%m-%dT%H:%M:%S')
                return BEIJING_TZ.localize(dt)
        else:
            # 其他格式
            dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
            return BEIJING_TZ.localize(dt)
    except Exception as e:
        logger.error(f"Failed to parse time string '{time_str}': {e}")
        return None

def format_beijing_iso(dt=None):
    """格式化为北京时间的 ISO 字符串（不带时区信息）"""
    if dt is None:
        dt = get_beijing_now()
    else:
        dt = to_beijing_time(dt)
    # 返回不带时区信息的 ISO 字符串
    return dt.replace(tzinfo=None).isoformat()
```

#### 2. 修复 gym_challenge.py

**修改 `_increment_failure` 方法（第 622-658 行）**：

```python
async def _increment_failure(self, user_id: str, guild_id: str, gym_id: str) -> timedelta:
    """增加失败次数并计算封禁时间"""
    async with self.db.get_connection() as conn:
        # 获取当前失败次数
        current = await self._get_failure_status(user_id, guild_id, gym_id)
        failure_count = (current['failure_count'] if current else 0) + 1
        
        # 计算封禁时间
        ban_duration = timedelta(seconds=0)
        if failure_count == 3:
            ban_duration = timedelta(hours=1)
        elif failure_count == 4:
            ban_duration = timedelta(hours=6)
        elif failure_count >= 5:
            ban_duration = timedelta(hours=12)
        
        banned_until = None
        if ban_duration.total_seconds() > 0:
            # 使用北京时间
            from utils.time_utils import get_beijing_now, format_beijing_iso
            banned_until_dt = get_beijing_now() + ban_duration
            banned_until = format_beijing_iso(banned_until_dt)
            logger.info(f"User {user_id} banned until {banned_until} (Beijing time)")
        
        # 更新数据库
        await conn.execute('''
            INSERT INTO challenge_failures (user_id, guild_id, gym_id, failure_count, banned_until)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, guild_id, gym_id) DO UPDATE SET
            failure_count = excluded.failure_count,
            banned_until = excluded.banned_until
        ''', (user_id, guild_id, gym_id, failure_count, banned_until))
        
        await conn.commit()
        
        return ban_duration
```

**修改 `start_challenge` 方法（第 491-508 行）**：

```python
# 检查冷却时间
failure_status = await self._get_failure_status(user_id, guild_id, gym_id)
if failure_status and failure_status['banned_until']:
    from utils.time_utils import parse_beijing_time, get_beijing_now
    
    banned_until = parse_beijing_time(failure_status['banned_until'])
    if banned_until:
        now = get_beijing_now()
        
        logger.info(f"Checking ban for user {user_id}: banned_until={banned_until}, now={now}")
        
        if banned_until > now:
            remaining = banned_until - now
            time_str = format_timedelta(remaining)
            await interaction.edit_original_response(
                content=f"❌ **挑战冷却中**\n\n"
                        f"由于多次挑战失败，你暂时无法挑战该道馆。\n"
                        f"请在 **{time_str}** 后再试。\n"
                        f"（解封时间：{banned_until.strftime('%Y-%m-%d %H:%M:%S')} 北京时间）",
                view=None,
                embed=None
            )
            return
```

#### 3. 修复 user_progress.py

**修改封禁状态显示（第 356-366 行）**：

```python
lines = []
for name, count, banned_until_str in rows:
    status = f"失败 {count} 次"
    if banned_until_str:
        from utils.time_utils import parse_beijing_time, get_beijing_now
        
        banned_until = parse_beijing_time(banned_until_str)
        if banned_until:
            now = get_beijing_now()
            if banned_until > now:
                remaining = banned_until - now
                time_str = format_timedelta(remaining)
                status += f" (封禁剩余: {time_str})"
            else:
                status += " (封禁已解除)"
    lines.append(f"• **{name}**: {status}")
```

### 测试要点

1. **封禁生效测试**：
   - 连续失败3次，应封禁1小时
   - 连续失败4次，应封禁6小时
   - 连续失败5次及以上，应封禁12小时

2. **时间显示测试**：
   - 所有时间显示应为北京时间
   - 封禁剩余时间应正确倒计时

3. **跨时区测试**：
   - 确保不同时区的服务器都能正常工作

### 回滚方案

如果修复后出现问题，可以：
1. 恢复原有代码
2. 清理 challenge_failures 表中的 banned_until 字段
3. 重新部署

### 预期效果

修复后：
- ✅ 封禁功能正常生效
- ✅ 所有时间统一使用北京时间
- ✅ 避免时区混淆导致的bug
- ✅ 提高代码可维护性

### 实施步骤

1. 创建时间处理工具函数
2. 修改 gym_challenge.py 中的时间处理
3. 修改 user_progress.py 中的显示逻辑
4. 添加详细的调试日志
5. 进行完整测试
6. 监控修复效果