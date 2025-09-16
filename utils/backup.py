"""
模块名称: backup.py
功能描述: 道馆数据备份管理工具
作者: @Kilo Code
创建日期: 2024-09-15
最后修改: 2024-09-15
"""

import os
import json
import asyncio
import aiofiles
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

from core.constants import BEIJING_TZ, DATA_PATH
from core.database import db_manager
from utils.logger import get_logger

logger = get_logger(__name__)

# 备份目录
BACKUP_DIR = DATA_PATH / "gym_backups"
BACKUP_RETENTION_DAYS = 30  # 备份保留天数


class BackupManager:
    """备份管理器"""
    
    def __init__(self):
        """初始化备份管理器"""
        self.backup_dir = BACKUP_DIR
        self.ensure_backup_directory()
    
    def ensure_backup_directory(self) -> None:
        """确保备份目录存在"""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    async def backup_gym(self, guild_id: str, gym_id: str) -> Optional[Path]:
        """
        备份单个道馆数据
        
        Args:
            guild_id: 服务器ID
            gym_id: 道馆ID
            
        Returns:
            备份文件路径，失败返回None
        """
        try:
            # 获取道馆数据
            gym_data = await self._get_gym_data(guild_id, gym_id)
            if not gym_data:
                logger.warning(f"道馆不存在: guild={guild_id}, gym={gym_id}")
                return None
            
            # 创建备份目录结构
            gym_backup_dir = self.backup_dir / guild_id / gym_id
            gym_backup_dir.mkdir(parents=True, exist_ok=True)
            
            # 检查是否需要备份（与最新备份比较）
            if await self._is_backup_needed(gym_backup_dir, gym_data):
                # 生成备份文件名
                timestamp = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d_%H-%M-%S')
                backup_file = gym_backup_dir / f"{timestamp}.json"
                
                # 写入备份文件
                async with aiofiles.open(backup_file, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(gym_data, indent=4, ensure_ascii=False, sort_keys=True))
                
                logger.info(f"道馆备份成功: {backup_file}")
                return backup_file
            else:
                logger.debug(f"道馆数据未变化，跳过备份: guild={guild_id}, gym={gym_id}")
                return None
                
        except Exception as e:
            logger.error(f"道馆备份失败: guild={guild_id}, gym={gym_id}, error={e}")
            return None
    
    async def backup_all_gyms(self, guild_id: str) -> int:
        """
        备份服务器的所有道馆
        
        Args:
            guild_id: 服务器ID
            
        Returns:
            成功备份的道馆数量
        """
        try:
            # 获取所有道馆ID
            async with db_manager.get_connection() as conn:
                cursor = await conn.execute(
                    "SELECT DISTINCT gym_id FROM gyms WHERE guild_id = ?",
                    (guild_id,)
                )
                gym_ids = [row[0] for row in await cursor.fetchall()]
            
            if not gym_ids:
                logger.info(f"服务器没有道馆需要备份: guild={guild_id}")
                return 0
            
            # 备份每个道馆
            backup_count = 0
            for gym_id in gym_ids:
                result = await self.backup_gym(guild_id, gym_id)
                if result:
                    backup_count += 1
                    await asyncio.sleep(0.1)  # 避免过于频繁的IO操作
            
            logger.info(f"服务器道馆备份完成: guild={guild_id}, 备份数量={backup_count}/{len(gym_ids)}")
            return backup_count
            
        except Exception as e:
            logger.error(f"批量备份失败: guild={guild_id}, error={e}")
            return 0
    
    async def restore_backup(self, guild_id: str, gym_id: str, backup_file: Optional[Path] = None) -> bool:
        """
        恢复道馆备份
        
        Args:
            guild_id: 服务器ID
            gym_id: 道馆ID
            backup_file: 指定的备份文件，不指定则使用最新备份
            
        Returns:
            是否恢复成功
        """
        try:
            # 获取备份文件
            if not backup_file:
                backup_file = await self.get_latest_backup(guild_id, gym_id)
                if not backup_file:
                    logger.error(f"没有找到备份文件: guild={guild_id}, gym={gym_id}")
                    return False
            
            # 读取备份数据
            async with aiofiles.open(backup_file, 'r', encoding='utf-8') as f:
                gym_data = json.loads(await f.read())
            
            # 恢复到数据库
            async with db_manager.get_connection() as conn:
                # 先删除现有数据
                await conn.execute(
                    "DELETE FROM gyms WHERE guild_id = ? AND gym_id = ?",
                    (guild_id, gym_id)
                )
                
                # 插入备份数据
                await conn.execute("""
                    INSERT INTO gyms (
                        guild_id, gym_id, name, description, tutorial, questions,
                        questions_to_ask, allowed_mistakes, badge_image_url,
                        badge_description, is_enabled, randomize_options
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    guild_id, gym_id, gym_data['name'], gym_data['description'],
                    json.dumps(gym_data['tutorial']), json.dumps(gym_data['questions']),
                    gym_data.get('questions_to_ask'), gym_data.get('allowed_mistakes'),
                    gym_data.get('badge_image_url'), gym_data.get('badge_description'),
                    gym_data.get('is_enabled', True), gym_data.get('randomize_options', True)
                ))
                
                await conn.commit()
            
            logger.info(f"道馆恢复成功: guild={guild_id}, gym={gym_id}, backup={backup_file}")
            return True
            
        except Exception as e:
            logger.error(f"道馆恢复失败: guild={guild_id}, gym={gym_id}, error={e}")
            return False
    
    async def get_latest_backup(self, guild_id: str, gym_id: str) -> Optional[Path]:
        """
        获取最新的备份文件
        
        Args:
            guild_id: 服务器ID
            gym_id: 道馆ID
            
        Returns:
            最新备份文件路径，没有则返回None
        """
        gym_backup_dir = self.backup_dir / guild_id / gym_id
        if not gym_backup_dir.exists():
            return None
        
        backup_files = sorted(
            [f for f in gym_backup_dir.glob("*.json")],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        return backup_files[0] if backup_files else None
    
    async def list_backups(self, guild_id: str, gym_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        列出备份文件
        
        Args:
            guild_id: 服务器ID
            gym_id: 道馆ID（可选，不指定则列出所有道馆的备份）
            
        Returns:
            备份文件信息列表
        """
        backups = []
        
        if gym_id:
            # 列出特定道馆的备份
            gym_backup_dir = self.backup_dir / guild_id / gym_id
            if gym_backup_dir.exists():
                for backup_file in gym_backup_dir.glob("*.json"):
                    backups.append({
                        'gym_id': gym_id,
                        'file': backup_file.name,
                        'path': str(backup_file),
                        'size': backup_file.stat().st_size,
                        'created': datetime.fromtimestamp(backup_file.stat().st_mtime, BEIJING_TZ)
                    })
        else:
            # 列出所有道馆的备份
            guild_backup_dir = self.backup_dir / guild_id
            if guild_backup_dir.exists():
                for gym_dir in guild_backup_dir.iterdir():
                    if gym_dir.is_dir():
                        gym_id = gym_dir.name
                        for backup_file in gym_dir.glob("*.json"):
                            backups.append({
                                'gym_id': gym_id,
                                'file': backup_file.name,
                                'path': str(backup_file),
                                'size': backup_file.stat().st_size,
                                'created': datetime.fromtimestamp(backup_file.stat().st_mtime, BEIJING_TZ)
                            })
        
        # 按创建时间排序
        backups.sort(key=lambda x: x['created'], reverse=True)
        return backups
    
    async def cleanup_old_backups(self, retention_days: Optional[int] = None) -> int:
        """
        清理过期的备份文件
        
        Args:
            retention_days: 保留天数，不指定则使用默认值
            
        Returns:
            删除的文件数量
        """
        retention_days = retention_days or BACKUP_RETENTION_DAYS
        cutoff_date = datetime.now(BEIJING_TZ) - timedelta(days=retention_days)
        deleted_count = 0
        
        try:
            for backup_file in self.backup_dir.rglob("*.json"):
                file_time = datetime.fromtimestamp(backup_file.stat().st_mtime, BEIJING_TZ)
                if file_time < cutoff_date:
                    backup_file.unlink()
                    deleted_count += 1
                    logger.debug(f"删除过期备份: {backup_file}")
            
            logger.info(f"清理过期备份完成，删除了 {deleted_count} 个文件")
            return deleted_count
            
        except Exception as e:
            logger.error(f"清理备份失败: {e}")
            return deleted_count
    
    async def schedule_backup(self, guild_id: str, interval_hours: int = 24) -> None:
        """
        定时备份任务
        
        Args:
            guild_id: 服务器ID
            interval_hours: 备份间隔（小时）
        """
        while True:
            try:
                # 执行备份
                await self.backup_all_gyms(guild_id)
                
                # 清理过期备份
                await self.cleanup_old_backups()
                
                # 等待下次备份
                await asyncio.sleep(interval_hours * 3600)
                
            except asyncio.CancelledError:
                logger.info(f"定时备份任务已取消: guild={guild_id}")
                break
            except Exception as e:
                logger.error(f"定时备份任务出错: guild={guild_id}, error={e}")
                await asyncio.sleep(300)  # 出错后等待5分钟再试
    
    async def _get_gym_data(self, guild_id: str, gym_id: str) -> Optional[Dict[str, Any]]:
        """
        从数据库获取道馆数据
        
        Args:
            guild_id: 服务器ID
            gym_id: 道馆ID
            
        Returns:
            道馆数据字典，不存在返回None
        """
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute("""
                SELECT gym_id as id, name, description, tutorial, questions,
                       questions_to_ask, allowed_mistakes, badge_image_url,
                       badge_description, is_enabled, randomize_options
                FROM gyms
                WHERE guild_id = ? AND gym_id = ?
            """, (guild_id, gym_id))
            
            row = await cursor.fetchone()
            if row:
                # 手动转换为字典
                gym_data = {}
                keys = ['id', 'name', 'description', 'tutorial', 'questions',
                       'questions_to_ask', 'allowed_mistakes', 'badge_image_url',
                       'badge_description', 'is_enabled', 'randomize_options']
                for i, key in enumerate(keys):
                    gym_data[key] = row[i]
                
                # 解析JSON字段
                gym_data['tutorial'] = json.loads(gym_data['tutorial'])
                gym_data['questions'] = json.loads(gym_data['questions'])
                return gym_data
            
            return None
    
    async def _is_backup_needed(self, backup_dir: Path, gym_data: Dict[str, Any]) -> bool:
        """
        检查是否需要备份（与最新备份比较）
        
        Args:
            backup_dir: 备份目录
            gym_data: 当前道馆数据
            
        Returns:
            是否需要备份
        """
        # 获取最新备份文件
        backup_files = sorted(
            [f for f in backup_dir.glob("*.json")],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        if not backup_files:
            return True  # 没有备份，需要创建
        
        # 读取最新备份并比较
        try:
            async with aiofiles.open(backup_files[0], 'r', encoding='utf-8') as f:
                last_backup = json.loads(await f.read())
            
            # 比较数据是否相同（使用排序后的JSON字符串比较）
            current_json = json.dumps(gym_data, sort_keys=True, ensure_ascii=False)
            last_json = json.dumps(last_backup, sort_keys=True, ensure_ascii=False)
            
            return current_json != last_json
            
        except Exception as e:
            logger.warning(f"比较备份文件失败: {e}")
            return True  # 出错时创建新备份


# 全局备份管理器实例
backup_manager = BackupManager()


async def start_daily_backup_task(bot, guild_id: str):
    """
    启动每日备份任务
    
    Args:
        bot: Discord Bot实例
        guild_id: 服务器ID
    """
    try:
        # 创建备份任务
        task = asyncio.create_task(backup_manager.schedule_backup(guild_id, interval_hours=24))
        
        # 存储任务引用（可选，用于后续管理）
        if not hasattr(bot, 'backup_tasks'):
            bot.backup_tasks = {}
        bot.backup_tasks[guild_id] = task
        
        logger.info(f"每日备份任务已启动: guild={guild_id}")
        return task
        
    except Exception as e:
        logger.error(f"启动每日备份任务失败: guild={guild_id}, error={e}")
        return None