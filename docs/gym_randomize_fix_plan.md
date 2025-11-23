# 道馆选项随机化Bug修复计划

## 问题描述
普通道馆答题时，题目选项没有按预期随机化排序，导致选择题的选项总是以固定顺序显示。

## 问题根源

### 默认值不一致
1. **数据库层**：`randomize_options BOOLEAN DEFAULT FALSE`（默认关闭）
2. **代码逻辑层**：`gym_info.get('randomize_options', True)`（默认开启）

当创建新道馆时，如果没有明确设置 `randomize_options`：
- 数据库存储 `FALSE` (0)
- 代码期望默认值是 `True`
- 导致选项不会被随机化

### 受影响的代码位置
- `core/database.py:205` - 数据库表定义
- `cogs/gym_challenge.py:54` - ChallengeSession初始化
- `cogs/gym_challenge.py:660` - 获取道馆信息时的默认值处理
- `cogs/gym_challenge.py:929` - 实际使用随机化的逻辑

## 修复方案

### 方案一：修改数据库默认值（推荐）✅

**优点**：
- 与代码逻辑期望一致
- 用户体验更好（大多数情况下需要随机化）
- 修改量最小

**实施步骤**：

#### 1. 修改数据库表结构
在 `core/database.py` 第205行：
```python
# 原来：
randomize_options BOOLEAN DEFAULT FALSE

# 改为：
randomize_options BOOLEAN DEFAULT TRUE
```

#### 2. 添加数据迁移逻辑
在 `core/database.py` 的 `_setup_database` 方法末尾添加：
```python
# 修复旧数据的randomize_options默认值
await conn.execute("""
    UPDATE gyms 
    SET randomize_options = 1 
    WHERE randomize_options IS NULL OR randomize_options = 0
""")
logger.info("数据迁移: 已更新道馆的randomize_options默认值为TRUE")
```

### 方案二：修改代码默认值（不推荐）

**缺点**：
- 需要修改多处代码
- 与用户期望不符（通常希望选项随机化）
- 可能影响已经依赖当前行为的道馆

## 实施计划

### 第一步：备份数据库
```bash
cp data/bot.db data/bot.db.backup
```

### 第二步：应用代码修改
1. 修改 `core/database.py:205` 的表定义
2. 在 `core/database.py` 添加数据迁移逻辑（约590行）

### 第三步：重启机器人
机器人重启时会自动执行数据迁移

### 第四步：验证修复
1. 创建新道馆，检查默认是否开启随机化
2. 测试现有道馆的选项是否正确随机化
3. 确认可以手动关闭随机化功能

## 回滚方案

如果出现问题：
1. 恢复数据库备份
2. 还原代码修改
3. 重启机器人

## 测试用例

### 测试1：新道馆默认随机化
1. 创建新道馆，不指定randomize_options
2. 添加选择题
3. 挑战道馆，确认选项顺序随机

### 测试2：现有道馆修复
1. 找一个之前创建的道馆
2. 挑战道馆，确认选项顺序随机

### 测试3：手动关闭随机化
1. 创建道馆时明确设置randomize_options为false
2. 挑战道馆，确认选项顺序固定

## 风险评估

- **低风险**：只影响选项显示顺序，不影响答案判断
- **可回滚**：有完整的回滚方案
- **影响范围**：仅影响道馆挑战功能

## 实施时间
建议在用户活动较少的时段进行，预计需要5分钟完成。