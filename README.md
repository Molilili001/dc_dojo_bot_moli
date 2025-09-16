# Discord 道馆挑战 Bot

一个功能丰富的 Discord Bot，用于管理道馆挑战系统。

## 功能特点

- 🏛️ **道馆管理** - 创建、更新、删除道馆
- ⚔️ **挑战系统** - 题目挑战、进度追踪
- 🏆 **排行榜** - 究极道馆排行榜
- 📊 **进度管理** - 用户进度查询和管理
- 🛡️ **管理功能** - 黑名单、封禁管理
- 📋 **面板系统** - 各种交互式面板

## 安装步骤

1. 克隆仓库
```bash
git clone [repository-url]
cd bot重构
```

2. 安装依赖
```bash
pip install -r requirements.txt
```

3. 配置设置
```bash
cp config.example.json config.json
```
编辑 `config.json` 文件，填入你的 Bot Token 和其他配置。

4. 运行 Bot
```bash
python bot.py
```

## 配置说明

在 `config.json` 中配置以下内容：

- `BOT_TOKEN`: 你的 Discord Bot Token
- `DEVELOPER_IDS`: 开发者用户 ID 列表
- `AUTO_BLACKLIST_MONITOR`: 自动黑名单监控配置

## 主要模块

### Cogs（功能模块）
- `admin.py` - 管理员命令
- `gym_management.py` - 道馆管理
- `gym_challenge.py` - 道馆挑战
- `user_progress.py` - 用户进度
- `leaderboard.py` - 排行榜系统
- `moderation.py` - 管理功能
- `panels.py` - 面板管理
- `developer.py` - 开发者工具
- `auto_monitor.py` - 自动监控
- `cross_bot_sync.py` - 跨bot联动

### Core（核心模块）
- `database.py` - 数据库管理
- `models.py` - 数据模型
- `constants.py` - 常量定义
- `exceptions.py` - 自定义异常

### Utils（工具模块）
- `logger.py` - 日志系统
- `permissions.py` - 权限管理
- `validators.py` - 数据验证
- `formatters.py` - 格式化工具

## 命令列表

### 道馆管理
- `/道馆 建造` - 创建新道馆
- `/道馆 更新` - 更新道馆
- `/道馆 删除` - 删除道馆
- `/道馆 列表` - 查看道馆列表
- `/道馆 后门` - 导出道馆数据

### 用户命令
- `/我的进度` - 查看挑战进度
- `/我的徽章墙` - 查看获得的徽章

### 管理命令
- `/设置馆主` - 管理道馆权限
- `/重置进度` - 重置用户进度
- `/道馆黑名单` - 管理黑名单
- `/道馆封禁` - 管理封禁列表

## 许可证

本项目采用 MIT 许可证。

## 贡献

欢迎提交 Pull Request 或创建 Issue。

## 支持

如有问题，请创建 Issue 或联系开发者。