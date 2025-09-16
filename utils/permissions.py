"""
模块名称: permissions.py
功能描述: 权限检查工具函数
作者: @Kilo Code
创建日期: 2024-09-15
最后修改: 2024-09-15
"""

import json
from pathlib import Path
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.database import db_manager
from core.exceptions import PermissionException
from utils.logger import get_logger

logger = get_logger(__name__)

# 加载配置文件以获取开发者ID
from core.constants import CONFIG_PATH

if CONFIG_PATH.exists():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
        DEVELOPER_IDS = config.get("DEVELOPER_IDS", [])
else:
    DEVELOPER_IDS = []


async def is_owner_check(interaction: discord.Interaction) -> bool:
    """
    检查用户是否为机器人拥有者
    
    Args:
        interaction: Discord交互对象
        
    Returns:
        是否为拥有者
    """
    # 首先检查是否为应用拥有者
    app_info = await interaction.client.application_info()
    if app_info.owner.id == interaction.user.id:
        return True
    
    # 检查是否在开发者ID列表中（从config.json加载）
    return str(interaction.user.id) in [str(dev_id) for dev_id in DEVELOPER_IDS]


async def is_admin_or_owner(interaction: discord.Interaction) -> bool:
    """
    检查用户是否为管理员或拥有者
    
    Args:
        interaction: Discord交互对象
        
    Returns:
        是否为管理员或拥有者
    """
    # 先检查是否为拥有者
    if await is_owner_check(interaction):
        return True
    
    # 检查是否有管理员权限
    if isinstance(interaction.user, discord.Member):
        return interaction.user.guild_permissions.administrator
    
    return False


async def check_gym_master_permission(
    guild_id: str,
    user: discord.Member,
    permission: str
) -> bool:
    """
    检查用户是否有道馆管理权限
    
    Args:
        guild_id: 服务器ID
        user: 用户对象
        permission: 权限名称
        
    Returns:
        是否有权限
    """
    # 检查用户特定权限
    query = """
        SELECT 1 FROM gym_masters 
        WHERE guild_id = ? AND target_id = ? AND target_type = 'user' 
        AND (permission = ? OR permission = 'all')
    """
    result = await db_manager.fetchone(
        query,
        (guild_id, str(user.id), permission)
    )
    
    if result:
        return True
    
    # 检查角色权限
    role_ids = [str(role.id) for role in user.roles]
    if not role_ids:
        return False
    
    placeholders = ','.join('?' for _ in role_ids)
    query = f"""
        SELECT 1 FROM gym_masters
        WHERE guild_id = ? AND target_type = 'role' 
        AND target_id IN ({placeholders})
        AND (permission = ? OR permission = 'all')
    """
    params = [guild_id] + role_ids + [permission]
    result = await db_manager.fetchone(query, tuple(params))
    
    return result is not None


async def has_gym_management_permission(command_name: str):
    """
    创建道馆管理权限检查装饰器
    
    Args:
        command_name: 命令名称
        
    Returns:
        权限检查函数
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        # 拥有者始终有权限
        if await is_owner_check(interaction):
            return True
        
        if interaction.guild is None:
            return False
        
        # 管理员始终有权限
        if interaction.user.guild_permissions.administrator:
            return True
        
        # 检查特定道馆管理权限
        return await check_gym_master_permission(
            str(interaction.guild.id),
            interaction.user,
            command_name
        )
    
    return app_commands.check(predicate)


async def add_gym_master(
    guild_id: str,
    target_id: str,
    target_type: str,
    permission: str
) -> None:
    """
    添加道馆管理权限
    
    Args:
        guild_id: 服务器ID
        target_id: 目标ID（用户或角色）
        target_type: 目标类型（'user' 或 'role'）
        permission: 权限名称
    """
    query = """
        INSERT OR REPLACE INTO gym_masters 
        (guild_id, target_id, target_type, permission)
        VALUES (?, ?, ?, ?)
    """
    await db_manager.execute(
        query,
        (guild_id, target_id, target_type, permission)
    )
    logger.info(
        f"添加道馆管理权限: guild={guild_id}, target={target_id}, "
        f"type={target_type}, permission={permission}"
    )


async def remove_gym_master(
    guild_id: str,
    target_id: str,
    permission: str
) -> int:
    """
    移除道馆管理权限
    
    Args:
        guild_id: 服务器ID
        target_id: 目标ID
        permission: 权限名称
        
    Returns:
        受影响的行数
    """
    query = """
        DELETE FROM gym_masters 
        WHERE guild_id = ? AND target_id = ? AND permission = ?
    """
    rows = await db_manager.execute(
        query,
        (guild_id, target_id, permission)
    )
    
    if rows > 0:
        logger.info(
            f"移除道馆管理权限: guild={guild_id}, target={target_id}, "
            f"permission={permission}"
        )
    
    return rows


async def get_gym_masters(guild_id: str) -> List[dict]:
    """
    获取服务器的所有道馆管理权限
    
    Args:
        guild_id: 服务器ID
        
    Returns:
        权限列表
    """
    query = """
        SELECT target_id, target_type, permission 
        FROM gym_masters 
        WHERE guild_id = ?
        ORDER BY target_type, permission
    """
    return await db_manager.fetchall(query, (guild_id,))


def owner_only():
    """仅限拥有者的装饰器"""
    return app_commands.check(is_owner_check)


def admin_or_owner():
    """管理员或拥有者的装饰器"""
    return app_commands.check(is_admin_or_owner)


# 添加缺失的函数别名
async def is_owner(interaction: discord.Interaction) -> bool:
    """检查是否为拥有者（用于权限检查）"""
    return await is_owner_check(interaction)

async def is_gym_master(interaction: discord.Interaction, permission: str) -> bool:
    """检查是否有道馆管理权限"""
    # 拥有者始终有权限
    if await is_owner_check(interaction):
        return True
    
    if interaction.guild is None:
        return False
    
    # 管理员始终有权限
    if interaction.user.guild_permissions.administrator:
        return True
    
    # 检查特定道馆管理权限
    return await check_gym_master_permission(
        str(interaction.guild.id),
        interaction.user,
        permission
    )

async def has_gym_permission(interaction: discord.Interaction, permission: str) -> bool:
    """
    检查用户是否有特定道馆的权限
    
    Args:
        interaction: Discord交互对象
        gym_id: 道馆ID
        
    Returns:
        是否有权限
    """
    # 拥有者始终有权限
    if await is_owner_check(interaction):
        return True
    
    # 管理员始终有权限
    if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
        return True
    
    # 检查是否有道馆管理权限
    if interaction.guild:
        return await check_gym_master_permission(
            str(interaction.guild.id),
            interaction.user,
            'gym_management'
        )
    
    return False