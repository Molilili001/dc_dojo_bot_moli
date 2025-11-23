目标
- 新增一个模块化 Cog，用于监听多个论坛频道的新帖（新线程），并对每个频道分别进行自定义配置：
  - 为新帖贴主自动上指定身份组
  - 在该帖子线程内自动发送通知并@帖主
  - 在该帖子下@指定身份组发送指定消息
- 提供一个服务器内可操作的“帖子监控面板”指令，支持图形化配置与管理
- 遵循统一的交互延迟与回复策略，避免 Unknown interaction 错误


整体架构与集成点
- 数据层
  - 在[core/database.py](core/database.py:144)新增表 forum_post_monitor_configs，用于跨频道独立配置与持久化
  - 在[core/models.py](core/models.py)新增数据类 ForumMonitorConfig 封装配置项，简化业务逻辑读写
- 业务层
  - 新增监听 Cog [cogs/forum_post_monitor.py](cogs/forum_post_monitor.py)，基于[BaseCog](cogs/base_cog.py:11)实现
  - 监听事件：论坛新帖子对应的“线程创建事件”，按频道查询配置后执行处理
  - 权限策略：加身份组需要请确保 Bot 拥有 Manage Roles 且角色层级高于目标身份组
- 展示与交互层
  - 新增视图 [views/forum_monitor_views.py](views/forum_monitor_views.py)，提供“帖子监控面板”
  - 指令入口：在[cogs/forum_post_monitor.py](cogs/forum_post_monitor.py)内定义斜杠命令“帖子监控面板”，权限校验使用[admin_or_owner()](utils/permissions.py:242)
  - 面板支持：多频道独立配置，增删改查，使用 Modal 录入字段；所有交互遵循“黄金法则：先 defer，再 followup 或 edit”


数据库设计
- 新表 forum_post_monitor_configs
  - 字段
    - guild_id TEXT NOT NULL
    - forum_channel_id TEXT NOT NULL
    - auto_role_enabled BOOLEAN DEFAULT FALSE
    - auto_role_id TEXT
    - notify_enabled BOOLEAN DEFAULT TRUE
    - notify_message TEXT
    - mention_role_enabled BOOLEAN DEFAULT FALSE
    - mention_role_id TEXT
    - mention_message TEXT
    - created_at TEXT NOT NULL
    - updated_at TEXT NOT NULL
  - 复合唯一约束
    - UNIQUE(guild_id, forum_channel_id)
  - 索引
    - idx_forum_monitor_guild_channel ON (guild_id, forum_channel_id)
    - idx_forum_monitor_guild_enabled ON (guild_id, auto_role_enabled, mention_role_enabled, notify_enabled)
- 迁移与初始化
  - 在[core/database.py](core/database.py:144)的_setup_database中创建表与索引
  - 与现有连接管理沿用[DatabaseManager](core/database.py:15)


模型定义
- 在[core/models.py](core/models.py)新增数据类 ForumMonitorConfig
  - 字段
    - guild_id: str
    - forum_channel_id: str
    - auto_role_enabled: bool
    - auto_role_id: Optional[str]
    - notify_enabled: bool
    - notify_message: Optional[str]
    - mention_role_enabled: bool
    - mention_role_id: Optional[str]
    - mention_message: Optional[str]
    - created_at: datetime
    - updated_at: datetime
  - 方法
    - to_dict()、from_dict() 与 JSON 序列化辅助（保持与现有模型一致的风格）
- 读取写入
  - 由 Cog 通过[db_manager](core/database.py:406) fetchone/fetchall/execute 进行持久化读写


监听与处理逻辑
- 新增 [cogs/forum_post_monitor.py](cogs/forum_post_monitor.py)
  - 初始化
    - 继承[BaseCog](cogs/base_cog.py:11)，使用其 logger 与 db
  - 事件监听
    - 监听论坛频道的新帖对应的“线程创建事件”
      - 通过 thread.parent 判断其父频道为论坛频道
      - 使用 thread.owner_id 作为发帖贴主 ID；若不存在则尝试获取 starter message 或 fallback fetch
    - 针对 thread.parent.id 按 guild_id+channel_id 查询配置
  - 执行步骤（按每个频道配置）
    - 加身份组
      - 如果 auto_role_enabled 且 auto_role_id 存在，取 guild.get_role 并尝试 add_roles
      - 权限不足或层级错误时仅记录日志与在帖子内告知失败原因
    - 在帖子内通知并@帖主
      - 如果 notify_enabled，则在该 thread 中发送 notify_message（无模板高级变量，仅固定文本）
      - 消息中包含 @发帖人（基于贴主 ID 构造 mention）
    - 在帖子下@指定身份组并发送指定消息
      - 如果 mention_role_enabled 且 mention_role_id 存在，则在 thread 中发送 <@&role_id> + mention_message
    - 串行执行，失败不阻塞后续操作，统一日志记录
  - 健壮性
    - 频道无配置：不做处理
    - 身份组或频道不存在：记录日志与在帖子内友好提示
    - 速率限制：必要时添加小延迟
    - 异常捕获：确保不会终止事件循环


配置面板设计
- 指令入口
  - 在[cogs/forum_post_monitor.py](cogs/forum_post_monitor.py)定义斜杠命令“帖子监控面板”
  - 权限校验：仅管理员或拥有者可用，复用[admin_or_owner()](utils/permissions.py:242)
- 面板视图
  - 在[views/forum_monitor_views.py](views/forum_monitor_views.py)实现一个持久化 View
  - 交互元素
    - 频道选择器：列出服务器内的论坛频道，支持当前列表中已配置与未配置
    - 配置按钮
      - 新增频道配置：弹出 Modal 录入 auto_role_id、notify_message、mention_role_id、mention_message 与三个开关
      - 编辑频道配置：读取并显示当前配置，再通过 Modal 更新
      - 删除频道配置：确认后删除该 guild_id+channel_id 的配置
    - 状态展示：当前已配置频道列表与每个频道的开关状态
  - 交互规范
    - 按照用户全局规则，所有交互入口在函数开头执行 defer（ephemeral=True）
    - 弹 Modal 时不调用 defer；Modal 回调内继续遵循 defer 与 followup/edit 的规范
- 文本内容限制
  - notify_message 与 mention_message 需遵守[core/constants.py](core/constants.py:59) MESSAGE_CONTENT_LIMIT
  - 身份组 ID 基本校验复用[utils/validators.py](utils/validators.py:214) validate_discord_id 与本地存在性检查


Bot 集成
- 在[bot.py](bot.py:104)新增加载条目：cogs.forum_post_monitor
- 在[bot.py](bot.py:35)为中英文映射新增
  - 英文名 ForumPostMonitorCog 映射中文名“帖子监控”
  - 中文名“帖子监控”反向映射 ForumPostMonitorCog


权限与错误处理
- 权限要求
  - 发送消息到贴子线程
  - 添加身份组（Manage Roles 且 Bot 角色需高于目标身份组）
- 错误处理
  - 身份组层级错误或权限不足：在帖子中发送失败提示并记录日志
  - 无配置或不完整配置：跳过并记录
  - 线程不可达或获取贴主失败：降级仅发送频道级提示或跳过
- 交互错误
  - 遵循“黄金法则”：先 defer，再 followup 或 edit，避免重复响应引发的 Unknown interaction


测试清单
- 多频道分别配置：不同论坛频道各自独立行为
- 功能开关单独启用测试：仅加身份组、仅通知、仅@身份组消息
- 身份组层级与权限不足场景：行为降级与日志提示
- 频道配置删除与更新：更新后的新帖行为符合预期
- Bot 重启后配置持久生效
- 指令权限：非管理员不可打开面板


Mermaid 流程图
```mermaid
flowchart TD
  A[用户在论坛频道创建新线程] --> B[触发线程创建事件]
  B --> C[读取 guild_id + forum_channel_id 配置]
  C --> D{auto_role_enabled?}
  D -->|是| E[为贴主添加身份组]
  D -->|否| F[跳过加身份组]
  C --> G{notify_enabled?}
  G -->|是| H[在线程内发送通知并@贴主]
  G -->|否| I[跳过通知]
  C --> J{mention_role_enabled?}
  J -->|是| K[在线程内@指定身份组并发送消息]
  J -->|否| L[跳过@身份组消息]
  E --> M[记录日志并处理异常]
  H --> M
  K --> M
```


实现注意事项
- 与现有工具的一致性
  - 继承[BaseCog](cogs/base_cog.py:11)以复用 logger 与 db
  - 权限校验统一使用[admin_or_owner()](utils/permissions.py:242)
- 交互安全
  - 面板与 Modal 遵循先 defer 原则，弹 Modal 例外
- 文本与 ID 校验
  - 使用[utils/validators.py](utils/validators.py:267)校验消息长度
  - 使用[utils/validators.py](utils/validators.py:214)校验身份组 ID 格式
- 配置数据约束
  - 每个论坛频道可独立配置与开关，未配置频道不执行任何动作
  - 不引入模板变量，消息按用户输入固定文本发送


后续代码变更摘要
- [core/database.py](core/database.py:144)
  - 新增论坛监控表结构与索引
- [core/models.py](core/models.py)
  - 新增 ForumMonitorConfig 数据类
- [cogs/forum_post_monitor.py](cogs/forum_post_monitor.py)
  - 新增 Cog，监听线程创建并执行动作
  - 新增斜杠命令“帖子监控面板”
- [views/forum_monitor_views.py](views/forum_monitor_views.py)
  - 新增配置面板视图与 Modal
- [utils/permissions.py](utils/permissions.py:242)
  - 复用 admin_or_owner 检查
- [bot.py](bot.py:104)
  - initial_cogs 添加 cogs.forum_post_monitor
- [bot.py](bot.py:35)
  - 中英文名映射添加 ForumPostMonitorCog 和“帖子监控”