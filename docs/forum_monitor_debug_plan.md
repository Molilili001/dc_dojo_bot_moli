# 帖子监控部分执行问题诊断方案

## 问题描述
- 现象：执行了上身份组的动作，但没有后续的艾特和自动发消息
- 特征：部分执行，而非完全遗漏

## 诊断分析

### 可能原因
1. **权限问题**
   - Bot可能有管理角色权限，但缺少在线程中发送消息的权限
   - 线程可能被锁定或设置了特殊权限

2. **API限制**
   - Discord API速率限制导致后续请求失败
   - 消息发送遇到429错误但未正确处理

3. **线程状态问题**
   - 线程可能在处理过程中被归档
   - 线程可能被删除或不可访问

4. **异常处理不当**
   - 发送消息的异常被静默捕获，没有重试

## 修复方案

### 1. 增强日志记录
```python
async def _process_actions(self, thread, guild, member, config):
    # 添加执行状态追踪
    execution_status = {
        "thread_id": str(thread.id),
        "auto_role": {"enabled": False, "success": False, "error": None},
        "notify": {"enabled": False, "success": False, "error": None},
        "mention": {"enabled": False, "success": False, "error": None}
    }
```

### 2. 权限预检查
```python
# 发送消息前检查权限
async def _can_send_in_thread(self, thread: discord.Thread) -> bool:
    try:
        # 检查线程是否已归档
        if thread.archived:
            return False
        # 检查Bot权限
        perms = thread.permissions_for(thread.guild.me)
        return perms.send_messages and perms.view_channel
    except:
        return False
```

### 3. 重试机制
```python
async def _send_with_retry(self, thread, content, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await thread.send(content, allowed_mentions=...)
        except discord.HTTPException as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
            else:
                raise
```

### 4. 错误恢复
- 每个动作独立try-except
- 记录失败但继续执行后续动作
- 失败的动作加入重试队列

### 5. 监控增强
```python
# 添加执行统计
forum_monitor_stats = {
    "total_processed": 0,
    "role_success": 0,
    "notify_success": 0,
    "mention_success": 0,
    "partial_failures": []
}
```

## 实施步骤

### 第一阶段：诊断
1. [ ] 添加详细日志到每个动作
2. [ ] 记录每个步骤的执行结果
3. [ ] 收集失败模式数据

### 第二阶段：修复
1. [ ] 实现权限预检查
2. [ ] 添加重试机制
3. [ ] 改进错误处理

### 第三阶段：增强
1. [ ] 添加失败重试队列
2. [ ] 实现监控统计
3. [ ] 添加自动恢复机制

## 临时解决方案

### 手动补发消息
创建一个命令，手动为指定线程补发消息：
```python
@app_commands.command(name="补发帖子消息")
async def resend_thread_messages(self, interaction, thread_id: str):
    # 查询配置
    # 重新发送通知和@消息
```

### 定期检查
添加一个任务，检查有身份组但无消息的线程：
```python
@tasks.loop(minutes=30)
async def check_incomplete_threads(self):
    # 查询已处理但可能不完整的线程
    # 补发缺失的消息
```

## 测试计划

1. **权限测试**
   - 测试不同权限配置下的行为
   - 验证线程锁定状态的处理

2. **压力测试**
   - 批量创建帖子测试
   - 模拟API限制场景

3. **恢复测试**
   - 模拟各种失败场景
   - 验证重试和恢复机制