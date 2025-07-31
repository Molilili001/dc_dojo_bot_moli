import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import aiosqlite
import typing
import datetime
import aiohttp
import random
import logging
from logging.handlers import TimedRotatingFileHandler
import asyncio
import aiofiles
from collections import defaultdict
import pytz
import psutil
import time

# --- Timezone Configuration ---
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

# --- Configuration Loading ---
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.json')
db_path = os.path.join(script_dir, 'progress.db')
log_dir = os.path.join(script_dir, 'botlog')
os.makedirs(log_dir, exist_ok=True)
log_path = os.path.join(log_dir, 'bot.log')

# --- Logging Setup ---
class TimezoneFormatter(logging.Formatter):
    """Custom formatter to use a specific timezone."""
    def __init__(self, fmt=None, datefmt=None, tz=None):
        super().__init__(fmt, datefmt)
        self.tz = tz if tz else pytz.utc

    def formatTime(self, record, datefmt=None):
        # Use the timezone to format the record's creation time
        dt = datetime.datetime.fromtimestamp(record.created, self.tz)
        if datefmt:
            return dt.strftime(datefmt)
        # Fallback to a readable ISO-like format
        return dt.strftime('%Y-%m-%d %H:%M:%S')

# Configure the root logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Create a formatter instance with Beijing timezone
formatter = TimezoneFormatter('%(asctime)s - %(levelname)s - %(message)s', tz=BEIJING_TZ)

# Create a file handler that rotates daily
# The rotation time will be based on the server's local time, but the log timestamps will be in Beijing time.
file_handler = TimedRotatingFileHandler(log_path, when='midnight', interval=1, backupCount=7, encoding='utf-8')
file_handler.setFormatter(formatter)

# Create a stream handler for console output
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

# Add handlers to the logger, but clear existing ones first to avoid duplicates
if logger.hasHandlers():
    logger.handlers.clear()
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

# --- Concurrency Locks ---
# Create a defaultdict of asyncio.Locks to prevent race conditions on a per-user basis.
user_db_locks = defaultdict(asyncio.Lock)

# --- Database Management ---
async def setup_database():
    """Creates the database and tables if they don't exist."""
    async with aiosqlite.connect(db_path) as conn:
        # User progress table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_progress (
                user_id TEXT,
                guild_id TEXT,
                gym_id TEXT,
                completed BOOLEAN DEFAULT TRUE,
                PRIMARY KEY (user_id, guild_id, gym_id)
            )
        ''')
        # Server configuration table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS challenge_panels (
                message_id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                role_to_add_id TEXT,
                role_to_remove_id TEXT,
                associated_gyms TEXT, -- JSON list of gym IDs, or NULL for all
                blacklist_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                completion_threshold INTEGER -- How many gyms to complete for the role
            )
        ''')
        # Safely add the new column for blacklist checking
        try:
            await conn.execute("ALTER TABLE challenge_panels ADD COLUMN blacklist_enabled BOOLEAN NOT NULL DEFAULT TRUE;")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise # Re-raise other errors
        # Safely add the new column for the completion threshold
        try:
            await conn.execute("ALTER TABLE challenge_panels ADD COLUMN completion_threshold INTEGER;")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise # Re-raise other errors
        # Safely add the new column for prerequisite gyms
        try:
            await conn.execute("ALTER TABLE challenge_panels ADD COLUMN prerequisite_gyms TEXT;")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise # Re-raise other errors
        # Gym master (gym owner) permissions table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS gym_masters (
                guild_id TEXT NOT NULL,
                target_id TEXT NOT NULL, -- User ID or Role ID
                target_type TEXT NOT NULL, -- 'user' or 'role'
                permission TEXT NOT NULL, -- 'all' or specific command name like 'gym_create'
                PRIMARY KEY (guild_id, target_id, permission)
            )
        ''')
        # Server-specific gyms table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS gyms (
                gym_id TEXT,
                guild_id TEXT,
                name TEXT,
                description TEXT,
                tutorial TEXT, -- Stored as JSON string
                questions TEXT, -- Stored as JSON string
                questions_to_ask INTEGER, -- Number of questions to randomly select
                allowed_mistakes INTEGER, -- Number of allowed mistakes before failing
                badge_image_url TEXT, -- URL for the badge image
                badge_description TEXT, -- Custom description for the badge
                is_enabled BOOLEAN DEFAULT TRUE,
                PRIMARY KEY (guild_id, gym_id)
            )
        ''')
        # Safely add the new column if it doesn't exist
        try:
            await conn.execute("ALTER TABLE gyms ADD COLUMN is_enabled BOOLEAN DEFAULT TRUE;")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise # Re-raise other errors
        # Safely add the new columns if they don't exist for existing databases
        try:
            await conn.execute("ALTER TABLE gyms ADD COLUMN questions_to_ask INTEGER;")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise # Re-raise other errors
        try:
            await conn.execute("ALTER TABLE gyms ADD COLUMN allowed_mistakes INTEGER;")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise # Re-raise other errors
        # Safely add the new column for badge images
        try:
            await conn.execute("ALTER TABLE gyms ADD COLUMN badge_image_url TEXT;")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise # Re-raise other errors
        # Safely add the new column for badge descriptions
        try:
            await conn.execute("ALTER TABLE gyms ADD COLUMN badge_description TEXT;")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise # Re-raise other errors
        # Table to track user failures and cooldowns
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS challenge_failures (
                user_id TEXT,
                guild_id TEXT,
                gym_id TEXT,
                failure_count INTEGER DEFAULT 0,
                banned_until TEXT, -- ISO 8601 format timestamp
                PRIMARY KEY (user_id, guild_id, gym_id)
            )
        ''')
        # Table for gym management audit logs
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS gym_audit_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                gym_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL, -- 'create', 'update', 'delete'
                timestamp TEXT NOT NULL
            )
        ''')
        # Table for cheating blacklist
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS cheating_blacklist (
                guild_id TEXT NOT NULL,
                target_id TEXT NOT NULL, -- User ID or Role ID
                target_type TEXT NOT NULL, -- 'user' or 'role'
                reason TEXT,
                added_by TEXT,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (guild_id, target_id)
            )
        ''')
        # Table for absolute challenge ban
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS challenge_ban_list (
                guild_id TEXT NOT NULL,
                target_id TEXT NOT NULL, -- User ID or Role ID
                target_type TEXT NOT NULL, -- 'user' or 'role'
                reason TEXT,
                added_by TEXT,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (guild_id, target_id)
            )
        ''')
        # Table to track one-time role rewards
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS claimed_role_rewards (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, role_id)
            )
        ''')
 
        # --- Create Indexes for Performance ---
        # These indexes significantly speed up common queries in a large server.
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_progress_user_guild ON user_progress (user_id, guild_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_challenge_failures_user_guild ON challenge_failures (user_id, guild_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_gyms_guild ON gyms (guild_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_gym_masters_guild_target ON gym_masters (guild_id, target_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_cheating_blacklist_guild_target ON cheating_blacklist (guild_id, target_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_challenge_ban_list_guild_target ON challenge_ban_list (guild_id, target_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_claimed_rewards_guild_user_role ON claimed_role_rewards (guild_id, user_id, role_id);")
 
        await conn.commit()

# --- Gym Data Functions ---
async def get_guild_gyms(guild_id: str) -> list:
    """Gets all gyms for a specific guild."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT gym_id, name, description, tutorial, questions, questions_to_ask, allowed_mistakes, badge_image_url, badge_description, is_enabled FROM gyms WHERE guild_id = ?", (guild_id,)) as cursor:
            rows = await cursor.fetchall()
    
    gyms_list = []
    for row in rows:
        gym_data = {
            "id": row["gym_id"],
            "name": row["name"],
            "description": row["description"],
            "tutorial": json.loads(row["tutorial"]),
            "questions": json.loads(row["questions"]),
            "is_enabled": row["is_enabled"],
            "badge_image_url": row["badge_image_url"],
            "badge_description": row["badge_description"]
        }
        if row["questions_to_ask"]:
            gym_data["questions_to_ask"] = row["questions_to_ask"]
        if row["allowed_mistakes"] is not None:
            gym_data["allowed_mistakes"] = row["allowed_mistakes"]
        gyms_list.append(gym_data)
    return gyms_list

async def get_single_gym(guild_id: str, gym_id: str) -> dict:
    """Gets a single gym's data for a guild."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT gym_id, name, description, tutorial, questions, questions_to_ask, allowed_mistakes, badge_image_url, badge_description, is_enabled FROM gyms WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id)) as cursor:
            row = await cursor.fetchone()

    if not row:
        return None
    gym_data = {
        "id": row["gym_id"],
        "name": row["name"],
        "description": row["description"],
        "tutorial": json.loads(row["tutorial"]),
        "questions": json.loads(row["questions"]),
        "is_enabled": row["is_enabled"],
        "badge_image_url": row["badge_image_url"],
        "badge_description": row["badge_description"]
    }
    if row["questions_to_ask"]:
        gym_data["questions_to_ask"] = row["questions_to_ask"]
    if row["allowed_mistakes"] is not None:
        gym_data["allowed_mistakes"] = row["allowed_mistakes"]
    return gym_data

async def create_gym(guild_id: str, gym_data: dict, conn: aiosqlite.Connection):
    """Creates a new gym using the provided connection."""
    await conn.execute('''
        INSERT INTO gyms (guild_id, gym_id, name, description, tutorial, questions, questions_to_ask, allowed_mistakes, badge_image_url, badge_description, is_enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
    ''', (
        guild_id, gym_data['id'], gym_data['name'], gym_data['description'],
        json.dumps(gym_data['tutorial']), json.dumps(gym_data['questions']),
        gym_data.get('questions_to_ask'), gym_data.get('allowed_mistakes'),
        gym_data.get('badge_image_url'), gym_data.get('badge_description')
    ))

async def update_gym(guild_id: str, gym_id: str, gym_data: dict, conn: aiosqlite.Connection) -> int:
    """Updates an existing gym. Returns rowcount."""
    cursor = await conn.execute('''
        UPDATE gyms SET name = ?, description = ?, tutorial = ?, questions = ?, questions_to_ask = ?, allowed_mistakes = ?, badge_image_url = ?, badge_description = ?
        WHERE guild_id = ? AND gym_id = ?
    ''', (
        gym_data['name'], gym_data['description'], json.dumps(gym_data['tutorial']),
        json.dumps(gym_data['questions']), gym_data.get('questions_to_ask'), gym_data.get('allowed_mistakes'),
        gym_data.get('badge_image_url'), gym_data.get('badge_description'),
        guild_id, gym_id
    ))
    return cursor.rowcount

async def delete_gym(guild_id: str, gym_id: str, conn: aiosqlite.Connection):
    """Deletes a gym for a guild using the provided connection."""
    await conn.execute("DELETE FROM gyms WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id))

# --- Gym Audit Log Functions ---
async def log_gym_action(guild_id: str, gym_id: str, user_id: str, action: str, conn: aiosqlite.Connection):
    """Logs a gym management action using the provided connection."""
    timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
    await conn.execute('''
        INSERT INTO gym_audit_log (guild_id, gym_id, user_id, action, timestamp)
        VALUES (?, ?, ?, ?, ?)
    ''', (guild_id, gym_id, user_id, action, timestamp))

# --- User Progress Functions ---
async def get_user_progress(user_id: str, guild_id: str) -> dict:
    """Gets a user's completed gyms for a specific guild."""
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute("SELECT gym_id FROM user_progress WHERE user_id = ? AND guild_id = ?", (user_id, guild_id)) as cursor:
            rows = await cursor.fetchall()
    return {row[0]: True for row in rows}

# --- Failure Tracking Functions ---
async def get_user_failure_status(user_id: str, guild_id: str, gym_id: str) -> tuple:
    """Gets a user's failure count and ban status for a specific gym."""
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            "SELECT failure_count, banned_until FROM challenge_failures WHERE user_id = ? AND guild_id = ? AND gym_id = ?",
            (user_id, guild_id, gym_id)
        ) as cursor:
            row = await cursor.fetchone()
    if row:
        # Parse the timestamp string back into a datetime object
        banned_until_dt = datetime.datetime.fromisoformat(row[1]) if row[1] else None
        return row[0], banned_until_dt
    return 0, None

async def increment_user_failure(user_id: str, guild_id: str, gym_id: str) -> datetime.timedelta:
    """Increments failure count, calculates and sets ban duration. Returns the duration."""
    async with user_db_locks[user_id]:
        current_failures, _ = await get_user_failure_status(user_id, guild_id, gym_id)
        new_failure_count = current_failures + 1

        ban_duration = datetime.timedelta(seconds=0)
        if new_failure_count == 3:
            ban_duration = datetime.timedelta(hours=1)
        elif new_failure_count == 4:
            ban_duration = datetime.timedelta(hours=6)
        elif new_failure_count >= 5:
            ban_duration = datetime.timedelta(hours=12)

        banned_until_dt = datetime.datetime.now(datetime.timezone.utc) + ban_duration
        banned_until_iso = banned_until_dt.isoformat()

        async with aiosqlite.connect(db_path) as conn:
            await conn.execute('''
                INSERT INTO challenge_failures (user_id, guild_id, gym_id, failure_count, banned_until)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, gym_id) DO UPDATE SET
                failure_count = excluded.failure_count,
                banned_until = excluded.banned_until
            ''', (user_id, guild_id, gym_id, new_failure_count, banned_until_iso))
            await conn.commit()
        return ban_duration

async def reset_user_failures_for_gym(user_id: str, guild_id: str, gym_id: str):
    """Resets a user's failure count for a specific gym upon success."""
    async with user_db_locks[user_id]:
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ? AND gym_id = ?",
                (user_id, guild_id, gym_id)
            )
            await conn.commit()

async def set_gym_completed(user_id: str, guild_id: str, gym_id: str):
    """Marks a gym as completed for a user in a specific guild."""
    async with user_db_locks[user_id]:
        try:
            async with aiosqlite.connect(db_path, timeout=10) as conn:
                await conn.execute("INSERT OR IGNORE INTO user_progress (user_id, guild_id, gym_id) VALUES (?, ?, ?)", (user_id, guild_id, gym_id))
                await conn.commit()
            logging.info(f"DATABASE: Marked gym '{gym_id}' as completed for user '{user_id}'.")
        except aiosqlite.OperationalError as e:
            logging.error(f"DATABASE_LOCKED: Failed to set gym completed for user '{user_id}'. Reason: {e}")
        except Exception as e:
            logging.error(f"DATABASE_ERROR: An unexpected error occurred in set_gym_completed: {e}")

async def fully_reset_user_progress(user_id: str, guild_id: str):
    """Resets a user's gym completions, failure records, and claimed rewards for a guild."""
    async with user_db_locks[user_id]:
        async with aiosqlite.connect(db_path) as conn:
            # Reset gym completions
            p_cursor = await conn.execute("DELETE FROM user_progress WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
            # Reset failure records
            f_cursor = await conn.execute("DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
            # Reset claimed rewards
            r_cursor = await conn.execute("DELETE FROM claimed_role_rewards WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
            await conn.commit()
            
            p_count = p_cursor.rowcount
            f_count = f_cursor.rowcount
            r_count = r_cursor.rowcount
            
            logging.info(
                f"PROGRESS_RESET: Fully reset user '{user_id}' in guild '{guild_id}'. "
                f"Removed {p_count} progress, {f_count} failures, {r_count} rewards."
            )

# --- Role Reward Claim Functions ---
async def has_claimed_reward(guild_id: str, user_id: str, role_id: str) -> bool:
   """Checks if a user has already claimed a specific role reward in a guild."""
   async with aiosqlite.connect(db_path) as conn:
       async with conn.execute(
           "SELECT 1 FROM claimed_role_rewards WHERE guild_id = ? AND user_id = ? AND role_id = ?",
           (guild_id, user_id, role_id)
       ) as cursor:
           return await cursor.fetchone() is not None

async def record_reward_claim(guild_id: str, user_id: str, role_id: str):
   """Records that a user has claimed a specific role reward."""
   timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
   async with aiosqlite.connect(db_path) as conn:
       await conn.execute('''
           INSERT OR IGNORE INTO claimed_role_rewards (guild_id, user_id, role_id, timestamp)
           VALUES (?, ?, ?, ?)
       ''', (guild_id, user_id, role_id, timestamp))
       await conn.commit()

# --- Permission Functions ---
async def add_gym_master(guild_id: str, target_id: str, target_type: str, permission: str):
    """Adds a gym master permission to the database."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute('''
            INSERT OR REPLACE INTO gym_masters (guild_id, target_id, target_type, permission)
            VALUES (?, ?, ?, ?)
        ''', (guild_id, target_id, target_type, permission))
        await conn.commit()

async def remove_gym_master(guild_id: str, target_id: str, permission: str):
    """Removes a gym master permission from the database."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "DELETE FROM gym_masters WHERE guild_id = ? AND target_id = ? AND permission = ?",
            (guild_id, target_id, permission)
        )
        await conn.commit()

async def check_gym_master_permission(guild_id: str, user: discord.Member, permission: str) -> bool:
    """Checks if a user has a specific gym master permission."""
    async with aiosqlite.connect(db_path) as conn:
        # Check for user-specific permission
        async with conn.execute(
            "SELECT 1 FROM gym_masters WHERE guild_id = ? AND target_id = ? AND target_type = 'user' AND (permission = ? OR permission = 'all')",
            (guild_id, str(user.id), permission)
        ) as cursor:
            if await cursor.fetchone():
                return True

        # Check for role-specific permission
        role_ids = [str(role.id) for role in user.roles]
        if role_ids:
            placeholders = ','.join('?' for _ in role_ids)
            query = f"""
                SELECT 1 FROM gym_masters
                WHERE guild_id = ? AND target_type = 'role' AND target_id IN ({placeholders})
                AND (permission = ? OR permission = 'all')
            """
            params = [guild_id] + role_ids + [permission]
            async with conn.execute(query, params) as cursor:
                if await cursor.fetchone():
                    return True
    return False

# --- Blacklist Functions ---
async def add_to_blacklist(guild_id: str, target_id: str, target_type: str, reason: str, added_by: str):
    """Adds a user or role to the cheating blacklist."""
    timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute('''
            INSERT OR REPLACE INTO cheating_blacklist (guild_id, target_id, target_type, reason, added_by, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (guild_id, target_id, target_type, reason, added_by, timestamp))
        await conn.commit()

async def add_to_blacklist_bulk(guild_id: str, members: list[discord.Member], reason: str, added_by: str):
    """Adds a list of members to the cheating blacklist in a single transaction."""
    timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
    records = [
        (guild_id, str(member.id), 'user', reason, added_by, timestamp)
        for member in members
    ]
    
    if not records:
        return 0

    async with aiosqlite.connect(db_path) as conn:
        await conn.executemany('''
            INSERT OR REPLACE INTO cheating_blacklist (guild_id, target_id, target_type, reason, added_by, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)
        await conn.commit()
    return len(records)

async def remove_from_blacklist(guild_id: str, target_id: str):
    """Removes a user or role from the cheating blacklist."""
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "DELETE FROM cheating_blacklist WHERE guild_id = ? AND target_id = ?",
            (guild_id, target_id)
        )
        await conn.commit()
        return cursor.rowcount

async def clear_blacklist(guild_id: str) -> int:
    """Removes all entries from the cheating blacklist for a specific guild. Returns the number of rows deleted."""
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "DELETE FROM cheating_blacklist WHERE guild_id = ?",
            (guild_id,)
        )
        await conn.commit()
        return cursor.rowcount


async def get_blacklist(guild_id: str) -> list:
   """Gets all blacklist entries for a specific guild."""
   async with aiosqlite.connect(db_path) as conn:
       conn.row_factory = aiosqlite.Row
       async with conn.execute("SELECT * FROM cheating_blacklist WHERE guild_id = ? ORDER BY timestamp DESC", (guild_id,)) as cursor:
           rows = await cursor.fetchall()
   return [dict(row) for row in rows]

async def is_user_blacklisted(guild_id: str, user: discord.Member) -> typing.Optional[dict]:
    """
    Checks if a user or any of their roles are in the blacklist.
    Returns the blacklist entry dictionary if found, otherwise None.
    """
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        
        # 1. Check user ID directly
        async with conn.execute(
            "SELECT * FROM cheating_blacklist WHERE guild_id = ? AND target_id = ? AND target_type = 'user'",
            (guild_id, str(user.id))
        ) as cursor:
            user_blacklist_entry = await cursor.fetchone()
            if user_blacklist_entry:
                return dict(user_blacklist_entry)

        # 2. Check all user roles
        role_ids = [str(role.id) for role in user.roles]
        if not role_ids:
            return None # No roles to check

        placeholders = ','.join('?' for _ in role_ids)
        query = f"""
            SELECT * FROM cheating_blacklist
            WHERE guild_id = ? AND target_type = 'role' AND target_id IN ({placeholders})
        """
        params = [guild_id] + role_ids
        async with conn.execute(query, params) as cursor:
            role_blacklist_entry = await cursor.fetchone()
            if role_blacklist_entry:
                return dict(role_blacklist_entry)

    return None

# --- Challenge Ban List Functions ---

async def add_to_ban_list(guild_id: str, target_id: str, target_type: str, reason: str, added_by: str):
    """Adds a user or role to the absolute challenge ban list."""
    timestamp = datetime.datetime.now(BEIJING_TZ).isoformat()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute('''
            INSERT OR REPLACE INTO challenge_ban_list (guild_id, target_id, target_type, reason, added_by, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (guild_id, target_id, target_type, reason, added_by, timestamp))
        await conn.commit()

async def remove_from_ban_list(guild_id: str, target_id: str):
    """Removes a user or role from the absolute challenge ban list."""
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "DELETE FROM challenge_ban_list WHERE guild_id = ? AND target_id = ?",
            (guild_id, target_id)
        )
        await conn.commit()
        return cursor.rowcount

async def get_ban_list(guild_id: str) -> list:
   """Gets all ban list entries for a specific guild."""
   async with aiosqlite.connect(db_path) as conn:
       conn.row_factory = aiosqlite.Row
       async with conn.execute("SELECT * FROM challenge_ban_list WHERE guild_id = ? ORDER BY timestamp DESC", (guild_id,)) as cursor:
           rows = await cursor.fetchall()
   return [dict(row) for row in rows]

async def is_user_banned(guild_id: str, user: discord.Member) -> typing.Optional[dict]:
    """
    Checks if a user or any of their roles are in the absolute challenge ban list.
    Returns the ban entry dictionary if found, otherwise None.
    """
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        
        # 1. Check user ID directly
        async with conn.execute(
            "SELECT * FROM challenge_ban_list WHERE guild_id = ? AND target_id = ? AND target_type = 'user'",
            (guild_id, str(user.id))
        ) as cursor:
            user_ban_entry = await cursor.fetchone()
            if user_ban_entry:
                return dict(user_ban_entry)

        # 2. Check all user roles
        role_ids = [str(role.id) for role in user.roles]
        if not role_ids:
            return None # No roles to check

        placeholders = ','.join('?' for _ in role_ids)
        query = f"""
            SELECT * FROM challenge_ban_list
            WHERE guild_id = ? AND target_type = 'role' AND target_id IN ({placeholders})
        """
        params = [guild_id] + role_ids
        async with conn.execute(query, params) as cursor:
            role_ban_entry = await cursor.fetchone()
            if role_ban_entry:
                return dict(role_ban_entry)

    return None

# --- State Management ---
active_challenges = {}

class ChallengeSession:
    """Represents a user's current challenge session."""
    def __init__(self, user_id: int, guild_id: int, gym_id: str, gym_info: dict, panel_message_id: int):
        self.user_id = user_id
        self.guild_id = guild_id
        self.gym_id = gym_id
        self.gym_info = gym_info
        self.panel_message_id = panel_message_id # The ID of the panel message this challenge originated from
        self.current_question_index = 0
        self.mistakes_made = 0
        self.wrong_answers = [] # To store tuples of (question, user_answer)
        self.allowed_mistakes = self.gym_info.get('allowed_mistakes', 0)
        
        # --- Random Question Logic ---
        self.questions_for_session = self.gym_info.get('questions', [])
        num_to_ask = self.gym_info.get('questions_to_ask')
        
        if num_to_ask and isinstance(num_to_ask, int) and num_to_ask > 0:
            if num_to_ask <= len(self.questions_for_session):
                self.questions_for_session = random.sample(self.questions_for_session, num_to_ask)

    def get_current_question(self):
        """Returns the current question dictionary."""
        if self.gym_info and self.current_question_index < len(self.questions_for_session):
            return self.questions_for_session[self.current_question_index]
        return None

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True # Members intent is still required to receive member objects from interactions and to use fetch_member.
intents.typing = False # Optimization: disable typing events if not used.
intents.presences = False # Optimization: disable presence events if not used.

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    chunk_guilds_at_startup=False,  # Key: Do not request all members on startup.
    member_cache_flags=discord.MemberCacheFlags.none()  # Key: Do not cache members.
)
bot.start_time = time.time() # Store bot start time

# --- Views ---
class GymSelect(discord.ui.Select):
    def __init__(self, guild_gyms: list, user_progress: dict, panel_message_id: int):
        self.panel_message_id = panel_message_id
        options = []
        if not guild_gyms:
            options.append(discord.SelectOption(label="本服务器暂无道馆", description="请管理员使用 /道馆 建造 来创建道馆。", value="no_gyms", emoji="🤷"))
        else:
            for gym in guild_gyms:
                gym_id = gym['id']
                completed = user_progress.get(gym_id, False)
                
                if not gym.get('is_enabled', True):
                    status_emoji = "⏸️"
                    label = f"{status_emoji} {gym['name']}"
                    description = "道馆维护中，暂不可用"
                    options.append(discord.SelectOption(label=label, description=description, value=gym_id))
                elif completed:
                    status_emoji = "✅"
                    label = f"{status_emoji} {gym['name']}"
                    description = "已通关"
                    options.append(discord.SelectOption(label=label, description=description, value=gym_id))
                else:
                    status_emoji = "❌"
                    label = f"{status_emoji} {gym['name']}"
                    description = "未通关"
                    options.append(discord.SelectOption(label=label, description=description, value=gym_id))
        
        super().__init__(placeholder="请选择一个道馆进行挑战...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Defer the component interaction response, allowing us to edit the message later.
        await interaction.response.defer()
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)
        gym_id = self.values[0]
        panel_message_id = self.panel_message_id # Get the ID from the view state

        if gym_id == "no_gyms":
            await interaction.edit_original_response(content="本服务器还没有创建任何道馆哦。", view=None)
            return

        gym_info = await get_single_gym(guild_id, gym_id)
        if not gym_info:
            await interaction.edit_original_response(content="错误：找不到该道馆的数据。可能已被删除。", view=None)
            return
        
        if not gym_info.get('is_enabled', True):
            await interaction.edit_original_response(content="此道馆正在维护中，暂时无法挑战。", view=None)
            return

        user_progress = await get_user_progress(user_id, guild_id)
        if user_progress.get(gym_id, False):
            await interaction.edit_original_response(content="你已经完成过这个道馆的挑战了！", view=None)
            return

        # Check for ban/cooldown status
        _, banned_until = await get_user_failure_status(user_id, guild_id, gym_id)
        if banned_until and banned_until > datetime.datetime.now(datetime.timezone.utc):
            # Calculate remaining time
            remaining_time = banned_until - datetime.datetime.now(datetime.timezone.utc)
            # Format the timedelta into a more readable string
            hours, remainder = divmod(int(remaining_time.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            time_str = f"{hours}小时 {minutes}分钟" if hours > 0 else f"{minutes}分钟 {seconds}秒"
            await interaction.edit_original_response(content=f"❌ **挑战冷却中** ❌\n\n由于多次挑战失败，你暂时无法挑战该道馆。\n请在 **{time_str}** 后再试。", view=None)
            return
        
        if user_id in active_challenges:
            del active_challenges[user_id]

            
        session = ChallengeSession(user_id, interaction.guild.id, gym_id, gym_info, panel_message_id)
        active_challenges[user_id] = session
        logging.info(f"CHALLENGE: Session created for user '{user_id}' in gym '{gym_id}' from panel '{panel_message_id}'.")
        
        tutorial_text = "\n".join(session.gym_info['tutorial'])
        embed = discord.Embed(title=f"欢迎来到 {session.gym_info['name']}", description=tutorial_text, color=discord.Color.blue())
        
        view = discord.ui.View()
        view.add_item(StartChallengeButton(gym_id))
        # After deferring, we must use edit_original_response to update the message.
        await interaction.edit_original_response(content=None, embed=embed, view=view)

class GymSelectView(discord.ui.View):
    def __init__(self, guild_gyms: list, user_progress: dict, panel_message_id: int):
        super().__init__(timeout=180)
        self.add_item(GymSelect(guild_gyms, user_progress, panel_message_id))

class MainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="挑战道馆", style=discord.ButtonStyle.success, custom_id="open_gym_list")
    async def open_gym_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)
        panel_message_id = interaction.message.id # Get the ID of the panel message this button belongs to
        
        # --- Absolute Ban Check ---
        ban_entry = await is_user_banned(guild_id, interaction.user)
        if ban_entry:
            reason = ban_entry.get('reason', '无特定原因')
            await interaction.followup.send(
                f"🚫 **挑战资格已被封禁** 🚫\n\n"
                f"由于你被记录在封禁名单中，你无法开始任何道馆挑战。\n"
                f"**原因:** {reason}\n\n"
                "如有疑问，请联系服务器管理员。",
                ephemeral=True
            )
            return

        user_gym_progress = await get_user_progress(user_id, guild_id)
        all_guild_gyms = await get_guild_gyms(guild_id)
        
        # Get the specific configuration for THIS panel from the database
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT associated_gyms, prerequisite_gyms FROM challenge_panels WHERE message_id = ?", (str(panel_message_id),)) as cursor:
                panel_config = await cursor.fetchone()

        # --- Prerequisite Check ---
        if panel_config and panel_config['prerequisite_gyms']:
            prerequisite_ids = set(json.loads(panel_config['prerequisite_gyms']))
            completed_ids = set(user_gym_progress.keys())
            
            if not prerequisite_ids.issubset(completed_ids):
                missing_gyms = prerequisite_ids - completed_ids
                
                # Fetch names of missing gyms for a user-friendly message
                missing_gym_names = []
                all_gyms_dict = {gym['id']: gym['name'] for gym in all_guild_gyms}
                for gym_id in missing_gyms:
                    missing_gym_names.append(all_gyms_dict.get(gym_id, gym_id))

                await interaction.followup.send(
                    f"❌ **前置条件未满足** ❌\n\n"
                    f"你需要先完成以下道馆的挑战，才能挑战此面板中的道馆：\n"
                    f"**- {', '.join(missing_gym_names)}**",
                    ephemeral=True
                )
                return

        associated_gyms = json.loads(panel_config['associated_gyms']) if panel_config and panel_config['associated_gyms'] else None

        if associated_gyms:
            # Filter the gyms to only those specified for this panel
            gyms_for_this_panel = [gym for gym in all_guild_gyms if gym['id'] in associated_gyms]
        else:
            # If no specific list, show all gyms
            gyms_for_this_panel = all_guild_gyms
            
        try:
            await interaction.followup.send(
                "请从下面的列表中选择你要挑战的道馆。",
                view=GymSelectView(gyms_for_this_panel, user_gym_progress, panel_message_id),
                ephemeral=True
            )
        except aiohttp.ClientConnectorError:
            # This error happens due to network instability.
            # We can try to send an ephemeral message to the user to inform them.
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("🤖 抱歉，与 Discord 的连接出现网络波动，请稍后再试。", ephemeral=True)
            except Exception:
                # If sending the response also fails, just ignore it.
                pass

class StartChallengeButton(discord.ui.Button):
    def __init__(self, gym_id: str):
        super().__init__(label="开始考核", style=discord.ButtonStyle.success, custom_id=f"challenge_begin_{gym_id}")

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        if user_id in active_challenges:
            await display_question(interaction, active_challenges[user_id])

class CancelChallengeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="放弃挑战", style=discord.ButtonStyle.danger, custom_id="challenge_cancel")

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        if user_id in active_challenges:
            del active_challenges[user_id]
            logging.info(f"CHALLENGE: Session cancelled by user '{user_id}'.")
            await interaction.response.edit_message(content="挑战已取消。", view=None, embed=None)
        else:
            await interaction.response.edit_message(content="没有正在进行的挑战或已超时。", view=None, embed=None)

class BadgePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Persistent view

    @discord.ui.button(label="我的徽章墙", style=discord.ButtonStyle.primary, custom_id="show_my_badges")
    async def show_my_badges_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)
        
        user_progress = await get_user_progress(user_id, guild_id)
        if not user_progress:
            await interaction.followup.send("你还没有通过任何道馆的考核。", ephemeral=True)
            return
            
        completed_gym_ids = list(user_progress.keys())
        all_guild_gyms = await get_guild_gyms(guild_id)
        
        completed_gyms = [gym for gym in all_guild_gyms if gym['id'] in completed_gym_ids]
        
        if not completed_gyms:
            await interaction.followup.send("你还没有通过任何道馆的考核。", ephemeral=True)
            return
            
        view = BadgeView(interaction.user, completed_gyms)
        await interaction.followup.send(embed=await view.create_embed(), view=view, ephemeral=True)

class GraduationPanelView(discord.ui.View):
    """A persistent view for the graduation panel."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="领取毕业奖励", style=discord.ButtonStyle.success, custom_id="claim_graduation_role")
    async def claim_graduation_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)
        member = interaction.user
        panel_message_id = str(interaction.message.id)

        # 1. Get the role associated with this specific panel
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT role_to_add_id FROM challenge_panels WHERE message_id = ?", (panel_message_id,)) as cursor:
                panel_config = await cursor.fetchone()
        
        if not panel_config or not panel_config['role_to_add_id']:
            logging.error(f"GRADUATION_PANEL: No role_to_add_id configured for panel {panel_message_id} in guild {guild_id}.")
            return await interaction.followup.send("❌ 此面板配置错误，请联系管理员。", ephemeral=True)
        
        role_to_add_id = panel_config['role_to_add_id']
        role_to_add = interaction.guild.get_role(int(role_to_add_id))

        if not role_to_add:
            logging.error(f"GRADUATION_PANEL: Role {role_to_add_id} not found in guild {guild_id}.")
            return await interaction.followup.send("❌ 此面板配置的身份组不存在，请联系管理员。", ephemeral=True)

        # 2. Check if the user has already claimed this specific reward
        if await has_claimed_reward(guild_id, user_id, role_to_add_id):
            return await interaction.followup.send(f"✅ 你已经领取过 {role_to_add.mention} 这个奖励了！", ephemeral=True)

        # 3. Check if the user has completed ALL gyms
        all_guild_gyms = await get_guild_gyms(guild_id)
        if not all_guild_gyms:
            return await interaction.followup.send("ℹ️ 本服务器还没有任何道馆，无法判断毕业状态。", ephemeral=True)

        required_gym_ids = {gym['id'] for gym in all_guild_gyms if gym.get('is_enabled', True)}
        user_progress = await get_user_progress(user_id, guild_id)
        completed_gym_ids = set(user_progress.keys())

        if not required_gym_ids.issubset(completed_gym_ids):
            missing_count = len(required_gym_ids - completed_gym_ids)
            return await interaction.followup.send(f"❌ 你尚未完成所有道馆的挑战，还差 {missing_count} 个。请继续努力！", ephemeral=True)

        # 4. All checks passed, grant the role
        try:
            await member.add_roles(role_to_add, reason="道馆全部通关奖励")
            await record_reward_claim(guild_id, user_id, role_to_add_id)
            logging.info(f"GRADUATION_REWARD: User '{user_id}' has completed all gyms and was granted role '{role_to_add_id}' in guild '{guild_id}'.")
            await interaction.followup.send(f"🎉 恭喜！你已完成所有道馆挑战，成功获得身份组：{role_to_add.mention}", ephemeral=True)
        except discord.Forbidden:
            logging.error(f"GRADUATION_PANEL: Bot lacks permissions to add role {role_to_add_id} in guild {guild_id}.")
            await interaction.followup.send("❌ 机器人权限不足，无法为你添加身份组，请联系管理员。", ephemeral=True)
        except Exception as e:
            logging.error(f"GRADUATION_PANEL: An unexpected error occurred while granting role: {e}", exc_info=True)
            await interaction.followup.send("❌ 发放身份组时发生未知错误，请联系管理员。", ephemeral=True)


def _create_wrong_answers_embed_fields(wrong_answers: list, show_correct_answer: bool) -> list:
    """
    Creates a list of embed field dictionaries for displaying wrong answers.
    Handles embed field character limits and formats answers correctly.
    """
    if not wrong_answers:
        return []

    fields_to_add = []
    current_field_text = ""

    for i, (question, wrong_answer) in enumerate(wrong_answers):
        question_text = question['text']
        
        # Base entry text
        entry_text = f"**题目**: {question_text}\n**你的答案**: `{wrong_answer}`\n"

        # Conditionally add the correct answer
        if show_correct_answer:
            correct_answer = question['correct_answer']
            # Format correct answer if it's a list (for fill-in-the-blank)
            if isinstance(correct_answer, list):
                correct_answer_str = ' 或 '.join(f"`{ans}`" for ans in correct_answer)
            else:
                correct_answer_str = f"`{correct_answer}`"
            entry_text += f"**正确答案**: {correct_answer_str}\n"
        
        entry_text += "\n" # Add final newline for spacing

        # Discord embed field value limit is 1024 characters
        if len(current_field_text) + len(entry_text) > 1024:
            # Current field is full, add it to the list and start a new one
            field_name = "错题回顾" if not fields_to_add else "错题回顾 (续)"
            fields_to_add.append({"name": field_name, "value": current_field_text, "inline": False})
            current_field_text = ""

        current_field_text += entry_text

    # Add the last or only field
    if current_field_text:
        field_name = "错题回顾" if not fields_to_add else "错题回顾 (续)"
        fields_to_add.append({"name": field_name, "value": current_field_text, "inline": False})
    
    return fields_to_add

async def display_question(interaction: discord.Interaction, session: ChallengeSession, from_modal: bool = False):
    # This function builds and sends/edits the message for the current question or final result.
 
    # --- Part 1: Build the Embed and View ---
    embed = None
    view = discord.ui.View(timeout=180)
    is_final_result = False
 
    # Check if all questions have been answered
    if session.current_question_index >= len(session.questions_for_session):
        is_final_result = True
        user_id_str = str(session.user_id)
        guild_id_str = str(session.guild_id)
        
        if session.mistakes_made <= session.allowed_mistakes:
            # --- CHALLENGE SUCCESS ---
            await reset_user_failures_for_gym(user_id_str, guild_id_str, session.gym_id)
            await set_gym_completed(user_id_str, guild_id_str, session.gym_id)
            if user_id_str in active_challenges: del active_challenges[user_id_str]
            logging.info(f"CHALLENGE: Session SUCCESS for user '{user_id_str}' in gym '{session.gym_id}'. Mistakes: {session.mistakes_made}/{session.allowed_mistakes}")
            
            success_desc = f"你成功通过了 **{session.gym_info['name']}** 的考核！\n\n" \
                           f"总题数: **{len(session.questions_for_session)}**\n" \
                           f"答错题数: **{session.mistakes_made}**\n" \
                           f"允许错题数: **{session.allowed_mistakes}**\n\n" \
                           "你的道馆挑战失败记录已被清零。"
            embed = discord.Embed(title="🎉 恭喜你，挑战成功！", description=success_desc, color=discord.Color.green())

            # Use the helper to generate and add fields
            wrong_answer_fields = _create_wrong_answers_embed_fields(session.wrong_answers, show_correct_answer=True)
            total_embed_length = len(embed.title) + len(embed.description)
            for field in wrong_answer_fields:
                total_embed_length += len(field['name']) + len(field['value'])
                if total_embed_length < 6000 and len(embed.fields) < 25:
                    embed.add_field(**field)
                else:
                    break # Stop if limits are approached
            
            await check_and_manage_completion_roles(interaction.user, session)
        else:
            # --- CHALLENGE FAILURE ---
            ban_duration = await increment_user_failure(user_id_str, guild_id_str, session.gym_id)
            if user_id_str in active_challenges: del active_challenges[user_id_str]
            logging.info(f"CHALLENGE: Session FAILED for user '{user_id_str}' in gym '{session.gym_id}'. Mistakes: {session.mistakes_made}/{session.allowed_mistakes}")

            fail_desc = f"本次挑战失败。\n\n" \
                        f"总题数: **{len(session.questions_for_session)}**\n" \
                        f"答错题数: **{session.mistakes_made}**\n" \
                        f"允许错题数: **{session.allowed_mistakes}**\n\n" \
                        "你答错的题目数量超过了允许的最大值。"

            if ban_duration.total_seconds() > 0:
                hours, remainder = divmod(int(ban_duration.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                time_str = f"{hours}小时" if hours > 0 else f"{minutes}分钟"
                fail_desc += f"\n\n由于累计挑战失败次数过多，你已被禁止挑战该道馆 **{time_str}**。"
            else:
                fail_desc += "\n\n请稍后重试。"
            
            embed = discord.Embed(title="❌ 挑战失败", description=fail_desc, color=discord.Color.red())

            # Use the helper to generate and add fields
            wrong_answer_fields = _create_wrong_answers_embed_fields(session.wrong_answers, show_correct_answer=False)
            total_embed_length = len(embed.title) + len(embed.description)
            for field in wrong_answer_fields:
                total_embed_length += len(field['name']) + len(field['value'])
                if total_embed_length < 6000 and len(embed.fields) < 25:
                    embed.add_field(**field)
                else:
                    break # Stop if limits are approached
    else:
        # --- Display Next Question ---
        question = session.get_current_question()
        q_num = session.current_question_index + 1
        total_q = len(session.questions_for_session)
        embed = discord.Embed(title=f"{session.gym_info['name']} 问题 {q_num}/{total_q}", description=question['text'], color=discord.Color.orange())
        
        if question['type'] == 'multiple_choice':
            # Format the question with A, B, C options in the embed
            formatted_options = []
            for i, option_text in enumerate(question['options']):
                letter = chr(ord('A') + i)
                formatted_options.append(f"**{letter}:** {option_text}")
            
            embed.description = question['text'] + "\n\n" + "\n".join(formatted_options)

            # Create buttons with letter labels
            for i, option_text in enumerate(question['options']):
                letter = chr(ord('A') + i)
                view.add_item(QuestionAnswerButton(label=letter, correct_answer=question['correct_answer'], value=option_text))

        elif question['type'] == 'true_false':
            view.add_item(QuestionAnswerButton('正确', question['correct_answer']))
            view.add_item(QuestionAnswerButton('错误', question['correct_answer']))
        elif question['type'] == 'fill_in_blank':
            view.add_item(FillInBlankButton())
        view.add_item(CancelChallengeButton())

    # --- Part 2: Send or Edit the Message ---
    final_view = None if is_final_result else view
    
    if from_modal:
        # The interaction comes from a modal submission that has been deferred.
        # We must edit the original response to the interaction.
        await interaction.edit_original_response(embed=embed, view=final_view)
    elif interaction.response.is_done():
        # The interaction has already been responded to (e.g., deferred).
        await interaction.edit_original_response(embed=embed, view=final_view)
    else:
        # This is a direct response to a component interaction (e.g., a button click).
        await interaction.response.edit_message(embed=embed, view=final_view)

class QuestionAnswerButton(discord.ui.Button):
    # The `value` parameter holds the actual answer content to check.
    # If not provided, it defaults to the button's visible `label`.
    def __init__(self, label: str, correct_answer: str, value: str = None):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.correct_answer = correct_answer
        self.value = value if value is not None else label

    async def callback(self, interaction: discord.Interaction):
        # Defer the interaction immediately to prevent it from timing out during long operations.
        await interaction.response.defer()

        session = active_challenges.get(str(interaction.user.id))
        if not session:
            await interaction.edit_original_response(content="挑战已超时，请重新开始。", view=None, embed=None)
            return
        
        # Check the button's actual value against the correct answer
        if self.value != self.correct_answer:
            session.mistakes_made += 1
            question_info = session.get_current_question()
            session.wrong_answers.append((question_info, self.value))
            logging.info(f"CHALLENGE: User '{interaction.user.id}' answered incorrectly. Mistakes: {session.mistakes_made}/{session.allowed_mistakes}")

        session.current_question_index += 1
        await display_question(interaction, session)

class FillInBlankButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="点击填写答案", style=discord.ButtonStyle.blurple)

    async def callback(self, interaction: discord.Interaction):
        session = active_challenges.get(str(interaction.user.id))
        if session:
            await interaction.response.send_modal(FillInBlankModal(session.get_current_question()))

class FillInBlankModal(discord.ui.Modal, title="填写答案"):
    answer_input = discord.ui.TextInput(label="你的答案", style=discord.TextStyle.short, required=True)

    def __init__(self, question: dict):
        super().__init__()
        self.question = question

    async def on_submit(self, interaction: discord.Interaction):
        # Defer the interaction immediately to prevent it from timing out.
        await interaction.response.defer()

        session = active_challenges.get(str(interaction.user.id))
        if not session:
            # If the session has timed out, edit the deferred response to inform the user.
            await interaction.edit_original_response(content="挑战已超时，请重新开始。", view=None, embed=None)
            return

        user_answer = self.answer_input.value.strip()
        correct_answer_field = self.question['correct_answer']
        is_correct = False

        # Check if correct_answer_field is a list or a string
        if isinstance(correct_answer_field, list):
            # If it's a list, check if the user's answer is in the list (case-insensitive)
            if any(user_answer.lower() == str(ans).lower() for ans in correct_answer_field):
                is_correct = True
        else:
            # Maintain compatibility with the old format (string)
            if user_answer.lower() == str(correct_answer_field).lower():
                is_correct = True
        
        if not is_correct:
            session.mistakes_made += 1
            session.wrong_answers.append((self.question, user_answer))
            logging.info(f"CHALLENGE: User '{interaction.user.id}' answered incorrectly. Mistakes: {session.mistakes_made}/{session.allowed_mistakes}")

        session.current_question_index += 1
        # The interaction is already deferred, now update the original message with the next question.
        await display_question(interaction, session, from_modal=True)

class ConfirmClearView(discord.ui.View):
    def __init__(self, guild_id: str, original_interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.original_interaction = original_interaction

    @discord.ui.button(label="确认清空", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await self.original_interaction.edit_original_response(view=self)

        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            deleted_count = await clear_blacklist(self.guild_id)
            logging.info(f"BLACKLIST: User '{interaction.user.id}' cleared the blacklist for guild '{self.guild_id}'. Deleted {deleted_count} entries.")
            await interaction.followup.send(f"✅ 黑名单已成功清空，共删除了 {deleted_count} 条记录。", ephemeral=True)
        except Exception as e:
            logging.error(f"Error in blacklist clear confirmation: {e}", exc_info=True)
            await interaction.followup.send("❌ 清空黑名单时发生错误。", ephemeral=True)
        
        self.stop()

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await self.original_interaction.edit_original_response(content="操作已取消。", view=self)
        await interaction.response.defer()
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.original_interaction.edit_original_response(content="操作已超时，请重新发起指令。", view=self)
        except discord.NotFound:
            pass # The original message might have been deleted.

class BlacklistPaginatorView(discord.ui.View):
   def __init__(self, interaction: discord.Interaction, entries: list, entries_per_page: int = 5):
       super().__init__(timeout=180)
       self.interaction = interaction
       self.entries = entries
       self.entries_per_page = entries_per_page
       self.current_page = 0
       self.total_pages = (len(self.entries) - 1) // self.entries_per_page + 1
       self.update_buttons()

   def update_buttons(self):
       self.children[0].disabled = self.current_page == 0
       self.children[1].disabled = self.current_page >= self.total_pages - 1

   async def create_embed(self) -> discord.Embed:
       start_index = self.current_page * self.entries_per_page
       end_index = start_index + self.entries_per_page
       page_entries = self.entries[start_index:end_index]

       embed = discord.Embed(
           title=f"「{self.interaction.guild.name}」黑名单列表 (共 {len(self.entries)} 人)",
           color=discord.Color.dark_red()
       )

       if not page_entries:
           embed.description = "这一页没有内容。"
       else:
           description_lines = []
           guild = self.interaction.guild
           for entry in page_entries:
               target_id_str = entry['target_id']
               target_type = entry['target_type']
               target_id = int(target_id_str)
               
               # Attempt to resolve the user/role for a more descriptive name
               target_display = ""
               if target_type == 'user':
                   member = guild.get_member(target_id)
                   if member:
                       target_display = f"{member.display_name} (<@{target_id}>)"
                   else:
                       target_display = f"[已离开的用户] (`{target_id_str}`)"
               elif target_type == 'role':
                   role = guild.get_role(target_id)
                   if role:
                       target_display = f"{role.name} (<@&{target_id}>)"
                   else:
                       target_display = f"[已删除的身份组] (`{target_id_str}`)"

               reason = entry.get('reason', '无')
               added_by_id = entry.get('added_by', '未知')
               
               # If added_by_id is a numeric user ID, format as a mention. Otherwise, display as text.
               operator_str = f"<@{added_by_id}>" if added_by_id.isdigit() else added_by_id

               # Parse timestamp
               try:
                   timestamp_dt = datetime.datetime.fromisoformat(entry['timestamp']).astimezone(BEIJING_TZ)
                   timestamp_str = timestamp_dt.strftime('%Y-%m-%d %H:%M')
               except (ValueError, TypeError):
                   timestamp_str = "未知时间"

               line = (
                   f"**对象**: {target_display}\n"
                   f"**原因**: {reason}\n"
                   f"**操作人**: {operator_str}\n"
                   f"**时间**: {timestamp_str}\n"
                   "---"
               )
               description_lines.append(line)
           
           embed.description = "\n".join(description_lines)

       embed.set_footer(text=f"第 {self.current_page + 1}/{self.total_pages} 页")
       return embed

   async def show_page(self, interaction: discord.Interaction):
       self.update_buttons()
       embed = await self.create_embed()
       await interaction.response.edit_message(embed=embed, view=self)

   @discord.ui.button(label="◀️ 上一页", style=discord.ButtonStyle.secondary)
   async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
       if self.current_page > 0:
           self.current_page -= 1
           await self.show_page(interaction)

   @discord.ui.button(label="下一页 ▶️", style=discord.ButtonStyle.secondary)
   async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
       if self.current_page < self.total_pages - 1:
           self.current_page += 1
           await self.show_page(interaction)

   async def on_timeout(self):
       for item in self.children:
           item.disabled = True
       try:
           # Use the original interaction to edit the message
           await self.interaction.edit_original_response(view=self)
       except discord.NotFound:
           pass

class BanListPaginatorView(discord.ui.View):
  def __init__(self, interaction: discord.Interaction, entries: list, entries_per_page: int = 5):
      super().__init__(timeout=180)
      self.interaction = interaction
      self.entries = entries
      self.entries_per_page = entries_per_page
      self.current_page = 0
      self.total_pages = (len(self.entries) - 1) // self.entries_per_page + 1
      self.update_buttons()

  def update_buttons(self):
      self.children[0].disabled = self.current_page == 0
      self.children[1].disabled = self.current_page >= self.total_pages - 1

  async def create_embed(self) -> discord.Embed:
      start_index = self.current_page * self.entries_per_page
      end_index = start_index + self.entries_per_page
      page_entries = self.entries[start_index:end_index]

      embed = discord.Embed(
          title=f"「{self.interaction.guild.name}」挑战封禁列表 (共 {len(self.entries)} 条)",
          color=discord.Color.from_rgb(139, 0, 0) # Dark Red
      )

      if not page_entries:
          embed.description = "这一页没有内容。"
      else:
          description_lines = []
          guild = self.interaction.guild
          for entry in page_entries:
              target_id_str = entry['target_id']
              target_type = entry['target_type']
              target_id = int(target_id_str)
              
              target_display = ""
              if target_type == 'user':
                  member = guild.get_member(target_id)
                  if member:
                      target_display = f"{member.display_name} (<@{target_id}>)"
                  else:
                      target_display = f"[已离开的用户] (`{target_id_str}`)"
              elif target_type == 'role':
                  role = guild.get_role(target_id)
                  if role:
                      target_display = f"{role.name} (<@&{target_id}>)"
                  else:
                      target_display = f"[已删除的身份组] (`{target_id_str}`)"

              reason = entry.get('reason', '无')
              added_by_id = entry.get('added_by', '未知')
              
              operator_str = f"<@{added_by_id}>" if added_by_id.isdigit() else added_by_id

              try:
                  timestamp_dt = datetime.datetime.fromisoformat(entry['timestamp']).astimezone(BEIJING_TZ)
                  timestamp_str = timestamp_dt.strftime('%Y-%m-%d %H:%M')
              except (ValueError, TypeError):
                  timestamp_str = "未知时间"

              line = (
                  f"**对象**: {target_display}\n"
                  f"**原因**: {reason}\n"
                  f"**操作人**: {operator_str}\n"
                  f"**时间**: {timestamp_str}\n"
                  "---"
              )
              description_lines.append(line)
          
          embed.description = "\n".join(description_lines)

      embed.set_footer(text=f"第 {self.current_page + 1}/{self.total_pages} 页")
      return embed

  async def show_page(self, interaction: discord.Interaction):
      self.update_buttons()
      embed = await self.create_embed()
      await interaction.response.edit_message(embed=embed, view=self)

  @discord.ui.button(label="◀️ 上一页", style=discord.ButtonStyle.secondary)
  async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
      if self.current_page > 0:
          self.current_page -= 1
          await self.show_page(interaction)

  @discord.ui.button(label="下一页 ▶️", style=discord.ButtonStyle.secondary)
  async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
      if self.current_page < self.total_pages - 1:
          self.current_page += 1
          await self.show_page(interaction)

  async def on_timeout(self):
      for item in self.children:
          item.disabled = True
      try:
          await self.interaction.edit_original_response(view=self)
      except discord.NotFound:
          pass

async def check_and_manage_completion_roles(member: discord.Member, session: ChallengeSession):
    """Checks if a user has completed all gyms required by a specific panel and manages roles."""
    guild_id = str(member.guild.id)
    user_id = str(member.id)
    panel_message_id = str(session.panel_message_id)
    
    user_progress = await get_user_progress(user_id, guild_id)

    # Get the specific configuration for the panel the user interacted with
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT role_to_add_id, role_to_remove_id, associated_gyms, blacklist_enabled, completion_threshold FROM challenge_panels WHERE message_id = ?", (panel_message_id,)) as cursor:
            panel_config = await cursor.fetchone()

    if not panel_config:
        logging.warning(f"Could not find panel config for message_id {panel_message_id} during role check.")
        return

    role_to_add_id = panel_config['role_to_add_id']
    role_to_remove_id = panel_config['role_to_remove_id']
    associated_gyms = json.loads(panel_config['associated_gyms']) if panel_config['associated_gyms'] else None
    blacklist_enabled_for_panel = panel_config['blacklist_enabled']
    completion_threshold = panel_config['completion_threshold']

    # Get all gyms that currently exist in the server
    all_guild_gyms = await get_guild_gyms(guild_id)
    all_existing_gym_ids = {gym['id'] for gym in all_guild_gyms}

    # Determine the set of gyms required for role changes based on the panel's config
    if associated_gyms:
        # If a specific list is defined, the requirement is to complete all *existing* gyms from that list.
        required_gyms_set = set(associated_gyms)
        required_ids_for_panel = required_gyms_set.intersection(all_existing_gym_ids)
    else:
        # Otherwise, use all existing gyms in the server
        required_ids_for_panel = all_existing_gym_ids

    if not user_progress or not required_ids_for_panel:
        return

    completed_gym_ids = set(user_progress.keys())

    # Check if the user has met the completion criteria
    all_checks_passed = False
    if completion_threshold and completion_threshold > 0:
        # Case 1: A specific number of gyms is required.
        # Count how many of the *required* gyms for this panel the user has completed.
        completed_required_gyms = completed_gym_ids.intersection(required_ids_for_panel)
        if len(completed_required_gyms) >= completion_threshold:
            all_checks_passed = True
    else:
        # Case 2: Default behavior - all required gyms for this panel must be completed.
        if required_ids_for_panel.issubset(completed_gym_ids):
            all_checks_passed = True

    # Proceed only if the user has met the completion criteria
    if all_checks_passed:
        # --- Blacklist Check (only if enabled for this panel) ---
        if blacklist_enabled_for_panel:
            blacklist_entry = await is_user_blacklisted(guild_id, member)
            if blacklist_entry:
                reason = blacklist_entry.get('reason', '无特定原因')
                logging.info(f"BLACKLIST: Blocked role assignment for blacklisted user '{member.id}' in guild '{guild_id}'. Reason: {reason}")
                try:
                    await member.send(
                        f"🚫 **身份组获取失败** 🚫\n\n"
                        f"你在服务器 **{member.guild.name}** 的道馆挑战奖励发放被阻止。\n"
                        f"**原因:** {reason}\n\n"
                        "由于你被记录在处罚名单中，即使完成了道馆挑战，也无法获得相关身份组。如有疑问，请联系服务器管理员。"
                    )
                except discord.Forbidden:
                    logging.warning(f"Cannot send blacklist notification DM to {member.name} (ID: {member.id}).")
                return # Stop further processing

        messages = []

        # --- Role to Add ---
        if role_to_add_id:
            # First, check if the user has already claimed this specific reward.
            if await has_claimed_reward(guild_id, user_id, role_to_add_id):
                logging.info(f"REWARD_BLOCK: User '{user_id}' in guild '{guild_id}' has already claimed role '{role_to_add_id}'. Skipping role assignment.")
            else:
                role_to_add = member.guild.get_role(int(role_to_add_id))
                if role_to_add and role_to_add not in member.roles:
                    try:
                        await member.add_roles(role_to_add)
                        # IMPORTANT: Record the claim immediately after successfully adding the role.
                        await record_reward_claim(guild_id, user_id, role_to_add_id)
                        messages.append(f"✅ **获得了身份组**: {role_to_add.mention}")
                        logging.info(f"REWARD_GRANT: Granted and recorded role '{role_to_add_id}' for user '{user_id}' in guild '{guild_id}'.")
                    except Exception as e:
                        logging.error(f"Failed to add role {role_to_add_id} to {member.id} in {member.guild.name}: {e}")
        
        # --- Role to Remove ---
        if role_to_remove_id:
            role_to_remove = member.guild.get_role(int(role_to_remove_id))
            if role_to_remove and role_to_remove in member.roles:
                try:
                    await member.remove_roles(role_to_remove)
                    messages.append(f"✅ **移除了身份组**: {role_to_remove.mention}")
                except Exception as e:
                    logging.error(f"Failed to remove role {role_to_remove_id} from {member.id} in {member.guild.name}: {e}")

        # --- Send DM Notification ---
        if messages:
            header = f"🎉 恭喜你！你已在 **{member.guild.name}** 服务器完成了指定道馆挑战！"
            full_message = header + "\n\n" + "\n".join(messages)
            try:
                await member.send(full_message)
            except discord.Forbidden:
                logging.warning(f"Cannot send DM to {member.name} (ID: {member.id}). They may have DMs disabled.")

# --- Bot Events ---
@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    # --- Auto Blacklist Monitor ---
    monitor_config = config.get("AUTO_BLACKLIST_MONITOR", {})
    if not monitor_config.get("enabled", False):
        return

    target_bot_id = monitor_config.get("target_bot_id")
    if not target_bot_id:
        return # Exit if no target bot is configured

    # --- New: Auto Blacklist via DM JSON ---
    if message.guild is None and str(message.author.id) == str(target_bot_id):
        logging.info(f"AUTO_DM_HANDLER: Received DM from target bot {message.author.id}")
        try:
            data = json.loads(message.content)
            
            # --- Feature: Auto Blacklist via DM ---
            punished_user_id = data.get("punish")
            if isinstance(punished_user_id, (str, int)) and str(punished_user_id).isdigit():
                punished_user_id_str = str(punished_user_id)
                reason = "因答题处罚被自动同步"
                added_by = f"自动同步自 ({message.author.name})"
                
                synced_guilds_count = 0
                for guild in bot.guilds:
                    # Check if the user is actually in the guild before proceeding
                    member = guild.get_member(int(punished_user_id_str))
                    if not member:
                        try:
                            member = await guild.fetch_member(int(punished_user_id_str))
                        except discord.NotFound:
                            continue # Skip if user is not in this guild

                    try:
                        # Add to blacklist for the current guild
                        await add_to_blacklist(str(guild.id), punished_user_id_str, 'user', reason, added_by)
                        
                        # Reset all progress for the user in the current guild
                        await fully_reset_user_progress(punished_user_id_str, str(guild.id))
                        
                        synced_guilds_count += 1
                    except Exception as e:
                        logging.error(f"AUTO_BLACKLIST_DM: Failed to process punishment for user '{punished_user_id_str}' in guild '{guild.id}'. Reason: {e}")
                
                logging.info(f"AUTO_BLACKLIST_DM: Successfully processed punishment for user '{punished_user_id_str}' in {synced_guilds_count} guilds.")

            # --- Feature: Grant Role via DM ---
            passed_user_id = data.get("pass")
            pass_role_id = monitor_config.get("pass_role_id")
            if isinstance(passed_user_id, (str, int)) and str(passed_user_id).isdigit() and pass_role_id:
                passed_user_id_str = str(passed_user_id)
                granted_guilds_count = 0
                for guild in bot.guilds:
                    try:
                        role = guild.get_role(int(pass_role_id))
                        if not role:
                            logging.warning(f"PASS_ROLE_DM: Role ID '{pass_role_id}' not found in guild '{guild.name}' ({guild.id}).")
                            continue
                        
                        member = guild.get_member(int(passed_user_id_str))
                        if not member:
                            # If member is not in cache, try fetching
                            try:
                                member = await guild.fetch_member(int(passed_user_id_str))
                            except discord.NotFound:
                                logging.info(f"PASS_ROLE_DM: User '{passed_user_id_str}' not found in guild '{guild.name}' ({guild.id}).")
                                continue
                        
                        if role not in member.roles:
                            await member.add_roles(role, reason=f"通过私信自动授予 by {message.author.name}")
                            granted_guilds_count += 1
                            logging.info(f"PASS_ROLE_DM: Granted role '{role.name}' to user '{member.name}' in guild '{guild.name}'.")

                    except discord.Forbidden:
                        logging.error(f"PASS_ROLE_DM: Bot lacks permissions to grant role '{pass_role_id}' in guild '{guild.id}'.")
                    except Exception as e:
                        logging.error(f"PASS_ROLE_DM: Failed to grant role for user '{passed_user_id_str}' in guild '{guild.id}'. Reason: {e}")
                
                if granted_guilds_count > 0:
                    logging.info(f"PASS_ROLE_DM: Successfully granted role to user '{passed_user_id_str}' in {granted_guilds_count} guild(s).")

        except json.JSONDecodeError:
            logging.warning(f"AUTO_DM_HANDLER: Received a non-JSON DM from target bot {message.author.id}. Content: {message.content}")
        except Exception as e:
            logging.error(f"AUTO_DM_HANDLER: An unexpected error occurred while processing DM: {e}", exc_info=True)
        
        return # Stop further processing of this DM

    # --- Legacy: Auto Blacklist via Embed in public channels ---
    elif message.guild is not None and str(message.author.id) == str(target_bot_id):
        trigger_embed_title = monitor_config.get("trigger_embed_title")
        if not trigger_embed_title:
            return

        if not message.embeds:
            return

        for embed in message.embeds:
            # Use 'in' for flexible matching and strip whitespace/emojis
            if embed.title and trigger_embed_title in embed.title.strip() and embed.description:
                if embed.mentions:
                    punished_user = embed.mentions[0]
                    reason = "因答题处罚被自动记录"
                    try:
                        if "因" in embed.description and "被" in embed.description:
                            start_index = embed.description.find("因") + 1
                            end_index = embed.description.find("被")
                            parsed_reason = embed.description[start_index:end_index].strip()
                            if parsed_reason:
                                reason = f"因“{parsed_reason}”被自动记录"
                    except Exception:
                        pass

                    await add_to_blacklist(
                        guild_id=str(message.guild.id),
                        target_id=str(punished_user.id),
                        target_type='user',
                        reason=reason,
                        added_by=f"自动监控 ({bot.user.name})"
                    )
                    logging.info(f"AUTO_BLACKLIST_EMBED: SUCCESS! User '{punished_user.id}' automatically added to blacklist in guild '{message.guild.id}'. Reason: {reason}")
                else:
                    logging.warning("AUTO_BLACKLIST_EMBED: Embed title matched, but no user was mentioned.")
                break

@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user.name}')
    await setup_database()
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} command(s) globally.")
    except Exception as e:
        logging.error(f"Error syncing commands globally: {e}")
    logging.info('Bot is ready to accept commands.')
    bot.add_view(MainView())
    bot.add_view(BadgePanelView())
    bot.add_view(GraduationPanelView()) # Register the new persistent view
    daily_backup_task.start()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """A global error handler for all slash commands."""
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ 你没有执行此指令所需的权限。", ephemeral=True)
    else:
        # For other errors, you might want to log them and send a generic message.
        logging.error(f"Unhandled error in command {interaction.command.name if interaction.command else 'unknown'}: {error}", exc_info=True)
        await interaction.response.send_message("🤖 执行指令时发生未知错误。", ephemeral=True)

# --- Permission Check Functions ---
async def is_owner_check(interaction: discord.Interaction) -> bool:
    return await bot.is_owner(interaction.user)

# --- Dev Commands for Syncing ---
@bot.tree.command(name="茉莉记忆咒", description="[仅限开发者] 强制同步所有指令。")
@app_commands.check(is_owner_check)
async def sync(interaction: discord.Interaction):
    """Manually syncs the command tree."""
    try:
        synced = await bot.tree.sync()
        await interaction.response.send_message(f"✅ 记忆咒施法成功！全局同步了 {len(synced)} 条指令。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 施法失败: {e}", ephemeral=True)

@bot.tree.command(name="茉莉失忆咒", description="[仅限开发者] 清除本服务器的指令缓存。")
@app_commands.check(is_owner_check)
async def clear_commands(interaction: discord.Interaction):
    """Clears all commands for the current guild and re-syncs."""
    if interaction.guild is None:
        return await interaction.response.send_message("此咒语不能在私聊中施展。", ephemeral=True)
    try:
        bot.tree.clear_commands(guild=interaction.guild)
        await bot.tree.sync(guild=interaction.guild)
        await interaction.response.send_message("✅ 失忆咒施法成功！已清除本服务器的指令。重启机器人后，它们将重新同步。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 施法失败: {e}", ephemeral=True)

# --- Admin & Owner Commands ---

@bot.tree.command(name="状态", description="[仅限开发者] 查看服务器和机器人的当前状态。")
@app_commands.check(is_owner_check)
async def system_status(interaction: discord.Interaction):
    """Displays the current status of the VPS and the bot."""
    await interaction.response.defer(ephemeral=True, thinking=True)

    # --- System Info ---
    cpu_usage = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    ram_usage_percent = ram.percent
    ram_used_gb = ram.used / (1024**3)
    ram_total_gb = ram.total / (1024**3)
    
    try:
        disk = psutil.disk_usage('/')
        disk_usage_percent = disk.percent
        disk_used_gb = disk.used / (1024**3)
        disk_total_gb = disk.total / (1024**3)
        disk_str = f"**磁盘空间:** `{disk_usage_percent}%` ({disk_used_gb:.2f} GB / {disk_total_gb:.2f} GB)"
    except FileNotFoundError:
        # Handle cases where '/' might not be the correct path (e.g., on Windows)
        disk_str = "**磁盘空间:** `无法获取`"


    # --- Bot Info ---
    process = psutil.Process(os.getpid())
    bot_ram_usage_mb = process.memory_info().rss / (1024**2)
    
    # Uptime
    uptime_seconds = time.time() - bot.start_time
    uptime_delta = datetime.timedelta(seconds=uptime_seconds)
    days = uptime_delta.days
    hours, rem = divmod(uptime_delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    uptime_str = f"{days}天 {hours}小时 {minutes}分钟"

    # --- Create Embed ---
    embed = discord.Embed(title="📊 服务器与机器人状态", color=discord.Color.blue())
    embed.timestamp = datetime.datetime.now(BEIJING_TZ)

    embed.add_field(
        name="🖥️ 系统资源",
        value=f"**CPU 负载:** `{cpu_usage}%`\n"
              f"**内存占用:** `{ram_usage_percent}%` ({ram_used_gb:.2f} GB / {ram_total_gb:.2f} GB)\n"
              f"{disk_str}",
        inline=False
    )
    
    embed.add_field(
        name="🤖 机器人进程",
        value=f"**内存占用:** `{bot_ram_usage_mb:.2f} MB`\n"
              f"**运行时间:** `{uptime_str}`",
        inline=False
    )
    
    await interaction.followup.send(embed=embed, ephemeral=True)


def has_gym_management_permission(command_name: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        # Always allow bot owner
        if await bot.is_owner(interaction.user):
            return True
        
        if interaction.guild is None:
            return False # Should not be used in DMs
            
        # Always allow server administrators
        if interaction.user.guild_permissions.administrator:
            return True
            
        # Check for specific gym master permission
        if await check_gym_master_permission(str(interaction.guild.id), interaction.user, command_name):
            return True
        
        return False # Deny access if no permissions match
    return app_commands.check(predicate)

# Custom check for Admin OR Owner
def is_admin_or_owner():
    async def predicate(interaction: discord.Interaction) -> bool:
        # First, check for bot owner status.
        if await bot.is_owner(interaction.user):
            return True
        # Then, check for server administrator permissions.
        # This implicitly handles the guild context, as guild_permissions only exists on Member objects.
        if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
            return True
        return False
    return app_commands.check(predicate)

# --- Gym Management Command Group ---
gym_management_group = app_commands.Group(name="道馆", description="管理本服务器的道馆")

@gym_management_group.command(name="召唤", description="在该频道召唤道馆挑战面板 (馆主、管理员、开发者)。")
@has_gym_management_permission("召唤")
@app_commands.describe(
    enable_blacklist="是否对通过此面板完成挑战的用户启用黑名单检查。",
    role_to_add="[可选] 用户完成所有道馆后将获得的身份组。",
    role_to_remove="[可选] 用户完成所有道馆后将被移除的身份组。",
    introduction="[可选] 自定义挑战面板的介绍文字。",
    gym_ids="[可选] 逗号分隔的道馆ID列表，此面板将只包含这些道馆。",
    completion_threshold="[可选] 完成多少个道馆后触发奖励，不填则为全部。",
    prerequisite_gym_ids="[可选] 逗号分隔的前置道馆ID，需全部完成后才能挑战此面板。"
)
@app_commands.rename(enable_blacklist='启用黑名单', completion_threshold='通关数量', prerequisite_gym_ids='前置道馆')
@app_commands.choices(enable_blacklist=[
    app_commands.Choice(name="是 (默认)", value="yes"),
    app_commands.Choice(name="否", value="no"),
])
async def gym_summon(interaction: discord.Interaction, enable_blacklist: str, role_to_add: typing.Optional[discord.Role] = None, role_to_remove: typing.Optional[discord.Role] = None, introduction: typing.Optional[str] = None, gym_ids: typing.Optional[str] = None, completion_threshold: typing.Optional[app_commands.Range[int, 1]] = None, prerequisite_gym_ids: typing.Optional[str] = None):
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    guild_id = str(interaction.guild.id)
    role_add_id = str(role_to_add.id) if role_to_add else None
    role_remove_id = str(role_to_remove.id) if role_to_remove else None
    blacklist_enabled = True if enable_blacklist == 'yes' else False
    
    associated_gyms_list = [gid.strip() for gid in gym_ids.split(',')] if gym_ids else None
    associated_gyms_json = json.dumps(associated_gyms_list) if associated_gyms_list else None

    prerequisite_gyms_list = [gid.strip() for gid in prerequisite_gym_ids.split(',')] if prerequisite_gym_ids else None
    prerequisite_gyms_json = json.dumps(prerequisite_gyms_list) if prerequisite_gyms_list else None

    try:
        # --- Validation Block ---
        all_guild_gyms = await get_guild_gyms(guild_id)
        all_gym_ids_set = {gym['id'] for gym in all_guild_gyms}

        # 1. Validate that specified gym_ids actually exist
        if associated_gyms_list:
            invalid_ids = [gid for gid in associated_gyms_list if gid not in all_gym_ids_set]
            if invalid_ids:
                await interaction.followup.send(f"❌ 操作失败：以下关联道馆ID在本服务器不存在: `{', '.join(invalid_ids)}`", ephemeral=True)
                return
        
        # 1.5 Validate that prerequisite_gym_ids actually exist
        if prerequisite_gyms_list:
            invalid_ids = [gid for gid in prerequisite_gyms_list if gid not in all_gym_ids_set]
            if invalid_ids:
                await interaction.followup.send(f"❌ 操作失败：以下前置道馆ID在本服务器不存在: `{', '.join(invalid_ids)}`", ephemeral=True)
                return
        
        # 1.6 Validate that prerequisite gyms are not also associated gyms
        if prerequisite_gyms_list and associated_gyms_list:
            overlap = set(prerequisite_gyms_list).intersection(set(associated_gyms_list))
            if overlap:
                await interaction.followup.send(f"❌ 操作失败：一个或多个道馆ID同时存在于前置道馆和关联道馆列表中: `{', '.join(overlap)}`", ephemeral=True)
                return

        # 2. Validate completion_threshold against the correct gym pool
        if completion_threshold:
            # Determine the size of the gym pool this panel applies to
            gym_pool_size = len(associated_gyms_list) if associated_gyms_list is not None else len(all_guild_gyms)

            if gym_pool_size == 0:
                await interaction.followup.send(f"❌ 操作失败：服务器内没有任何道馆，无法设置通关数量要求。", ephemeral=True)
                return

            if completion_threshold > gym_pool_size:
                await interaction.followup.send(
                    f"❌ 操作失败：设置的通关数量要求 ({completion_threshold}) 不能大于将要应用的道馆总数 ({gym_pool_size})。",
                    ephemeral=True
                )
                return
        # --- End Validation Block ---

        # Use the custom introduction if provided, otherwise use the default text.
        if introduction:
            # Replace the user-provided newline marker with an actual newline character.
            description = introduction.replace('\\n', '\n')
        else:
            description = (
                "欢迎来到道馆挑战中心！在这里，你可以通过挑战不同的道馆来学习和证明你的能力。\n\n"
                "完成所有道馆挑战后，可能会有特殊的身份组奖励或变动。\n\n"
                "点击下方的按钮，开始你的挑战吧！"
            )
        
        embed = discord.Embed(
            title="道馆挑战中心",
            description=description,
            color=discord.Color.gold()
        )
        
        # Send the panel message first to get its ID
        panel_message = await interaction.channel.send(embed=embed, view=MainView())
        
        # Now, save the configuration for this specific panel to the database
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute('''
                INSERT INTO challenge_panels (message_id, guild_id, channel_id, role_to_add_id, role_to_remove_id, associated_gyms, blacklist_enabled, completion_threshold, prerequisite_gyms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (str(panel_message.id), guild_id, str(interaction.channel.id), role_add_id, role_remove_id, associated_gyms_json, blacklist_enabled, completion_threshold, prerequisite_gyms_json))
            await conn.commit()

        # Build confirmation message
        confirm_messages = [f"✅ 道馆面板已成功创建于 {interaction.channel.mention}！"]
        status_text = "启用" if blacklist_enabled else "禁用"
        confirm_messages.append(f"- **黑名单检查**: {status_text}")
        if role_to_add:
            confirm_messages.append(f"- **通关奖励身份组**: {role_to_add.mention}")
        if role_to_remove:
            confirm_messages.append(f"- **通关移除身份组**: {role_to_remove.mention}")
        if associated_gyms_list:
            confirm_messages.append(f"- **关联道馆列表**: `{', '.join(associated_gyms_list)}`")
        if completion_threshold:
            confirm_messages.append(f"- **通关数量要求**: {completion_threshold} 个")
        if prerequisite_gyms_list:
            confirm_messages.append(f"- **前置道馆要求**: `{', '.join(prerequisite_gyms_list)}`")
        
        await interaction.followup.send("\n".join(confirm_messages), ephemeral=True)

    except discord.Forbidden:
        await interaction.followup.send(f"❌ 设置失败：我没有权限在此频道发送消息或管理身份组。请检查我的权限。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error in /道馆 召唤 command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ 设置失败: 发生了一个未知错误。", ephemeral=True)

@gym_management_group.command(name="徽章墙面板", description="在该频道召唤一个徽章墙面板 (馆主、管理员、开发者)。")
@has_gym_management_permission("徽章墙面板")
@app_commands.describe(
    introduction="[可选] 自定义徽章墙面板的介绍文字。"
)
async def summon_badge_panel(interaction: discord.Interaction, introduction: typing.Optional[str] = None):
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        if introduction:
            # Replace the user-provided newline marker with an actual newline character.
            description = introduction.replace('\\n', '\n')
        else:
            description = (
                "这里是徽章墙展示中心。\n\n"
                "点击下方的按钮，来展示你通过努力获得的道馆徽章吧！"
            )
        
        embed = discord.Embed(
            title="徽章墙展示中心",
            description=description,
            color=discord.Color.purple()
        )
        
        await interaction.channel.send(embed=embed, view=BadgePanelView())
        
        await interaction.followup.send(f"✅ 徽章墙面板已成功创建于 {interaction.channel.mention}！", ephemeral=True)

    except discord.Forbidden:
        await interaction.followup.send(f"❌ 设置失败：我没有权限在此频道发送消息。请检查我的权限。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error in /道馆 徽章墙面板 command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ 设置失败: 发生了一个未知错误。", ephemeral=True)

@gym_management_group.command(name="毕业面板", description="召唤一个用于领取“全部通关”奖励的面板 (馆主、管理员、开发者)。")
@has_gym_management_permission("毕业面板")
@app_commands.describe(
    role_to_grant="用户完成所有道馆后将获得的身份组。",
    introduction="[可选] 自定义面板的介绍文字。",
    button_label="[可选] 自定义按钮上显示的文字。"
)
async def gym_graduation_panel(interaction: discord.Interaction, role_to_grant: discord.Role, introduction: typing.Optional[str] = None, button_label: typing.Optional[str] = "领取毕业奖励"):
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    guild_id = str(interaction.guild.id)
    role_add_id = str(role_to_grant.id)

    try:
        # Default description if none is provided
        if not introduction:
            introduction = (
                "祝贺所有坚持不懈的挑战者！\n\n"
                f"当你完成了本服务器 **所有** 的道馆挑战后，点击下方的按钮，即可领取属于你的最终荣誉：**{role_to_grant.name}** 身份组！"
            )
        
        # Replace newline markers
        description = introduction.replace('\\n', '\n')

        embed = discord.Embed(
            title="道馆毕业资格认证",
            description=description,
            color=discord.Color.gold()
        )
        
        # Create a view and update the button label
        view = GraduationPanelView()
        view.children[0].label = button_label

        # Send the panel message to get its ID
        panel_message = await interaction.channel.send(embed=embed, view=view)
        
        # Save the configuration for this specific panel to the database
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute('''
                INSERT INTO challenge_panels (message_id, guild_id, channel_id, role_to_add_id)
                VALUES (?, ?, ?, ?)
            ''', (str(panel_message.id), guild_id, str(interaction.channel.id), role_add_id))
            await conn.commit()

        await interaction.followup.send(
            f"✅ 毕业面板已成功创建于 {interaction.channel.mention}！\n"
            f"- **奖励身份组**: {role_to_grant.mention}",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.followup.send(f"❌ 设置失败：我没有权限在此频道发送消息或管理身份组。请检查我的权限。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error in /道馆 毕业面板 command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ 设置失败: 发生了一个未知错误。", ephemeral=True)


def validate_gym_json(data: dict) -> str:
    """Validates the structure and content length of the gym JSON. Returns an error string or empty string if valid."""
    # Discord Limits
    EMBED_DESC_LIMIT = 4096
    BUTTON_LABEL_LIMIT = 80

    required_keys = ['id', 'name', 'description', 'tutorial', 'questions']
    if not all(key in data for key in required_keys):
        return "JSON数据缺少顶层键 (id, name, description, tutorial, questions)。"
    if not isinstance(data['questions'], list):
        return "`questions` 字段必须是一个列表。"
    
    # Validate optional questions_to_ask
    if 'questions_to_ask' in data:
        if not isinstance(data.get('questions_to_ask'), int):
            return "`questions_to_ask` 必须是一个整数。"
        if data['questions_to_ask'] <= 0:
            return "`questions_to_ask` 必须是大于0的整数。"
        if data['questions_to_ask'] > len(data.get('questions', [])):
            return f"`questions_to_ask` 的数量 ({data['questions_to_ask']}) 不能超过题库中的总问题数 ({len(data.get('questions', []))})。"
    
    # Validate optional allowed_mistakes
    if 'allowed_mistakes' in data:
        if not isinstance(data.get('allowed_mistakes'), int):
            return "`allowed_mistakes` 必须是一个整数。"
        if data['allowed_mistakes'] < 0:
            return "`allowed_mistakes` 不能是负数。"

    # Validate optional badge_image_url
    if 'badge_image_url' in data and data['badge_image_url']:
        url = data['badge_image_url']
        if not isinstance(url, str):
            return "`badge_image_url` 必须是一个字符串。"
        if not (url.startswith('http://') or url.startswith('https://')):
            return "`badge_image_url` 必须是一个有效的URL (以 http:// 或 https:// 开头)。"
        if not any(url.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
             return "`badge_image_url` 似乎不是一个有效的图片直链 (应以 .png, .jpg, .jpeg, .gif, .webp 结尾)。"

    # Validate optional badge_description
    if 'badge_description' in data and data['badge_description']:
        desc = data['badge_description']
        if not isinstance(desc, str):
            return "`badge_description` 必须是一个字符串。"
        if len(desc) > 1024:
            return f"`badge_description` 的长度不能超过 1024 个字符。"

    # Validate tutorial length
    if isinstance(data.get('tutorial'), list) and len("\n".join(data['tutorial'])) > EMBED_DESC_LIMIT:
        return f"`tutorial` 的总长度超出了Discord {EMBED_DESC_LIMIT} 字符的限制。"

    for i, q in enumerate(data['questions']):
        q_num = i + 1
        if not isinstance(q, dict):
            return f"问题 {q_num} 不是一个有效的JSON对象。"
        
        required_q_keys = ['type', 'text', 'correct_answer']
        if not all(key in q for key in required_q_keys):
            return f"问题 {q_num} 缺少必要的键 (type, text, correct_answer)。"
        
        # Validate question text length
        if len(q.get('text', '')) > EMBED_DESC_LIMIT:
            return f"问题 {q_num} 的 `text` 字段长度超出了Discord {EMBED_DESC_LIMIT} 字符的限制。"

        if q['type'] not in ['multiple_choice', 'fill_in_blank', 'true_false']:
            return f"问题 {q_num} 的 `type` 无效，必须是 'multiple_choice', 'fill_in_blank' 或 'true_false'。"
            
        if q['type'] == 'multiple_choice':
            if 'options' not in q or not isinstance(q['options'], list) or len(q['options']) < 2:
                return f"问题 {q_num} (选择题) 必须包含一个至少有2个选项的 `options` 列表。"
            if q['correct_answer'] not in q['options']:
                return f"问题 {q_num} (选择题) 的 `correct_answer` 必须是 `options` 列表中的一个。"

        if q['type'] == 'true_false':
            if q['correct_answer'] not in ['正确', '错误']:
                return f"问题 {q_num} (判断题) 的 `correct_answer` 必须是 '正确' 或 '错误'。"

    return "" # All good

@gym_management_group.command(name="建造", description="通过上传JSON文件创建一个新道馆 (馆主、管理员、开发者)。")
@has_gym_management_permission("建造")
@app_commands.describe(json_file="包含道馆完整信息的JSON文件。")
async def gym_create(interaction: discord.Interaction, json_file: discord.Attachment):
    await interaction.response.defer(ephemeral=True, thinking=True)

    if not json_file.filename.lower().endswith('.json'):
        return await interaction.followup.send("❌ 文件格式错误，请上传一个 `.json` 文件。", ephemeral=True)
    
    # Add a file size check (e.g., 1MB) to prevent abuse
    if json_file.size > 1 * 1024 * 1024:
        return await interaction.followup.send("❌ 文件过大，请确保JSON文件大小不超过 1MB。", ephemeral=True)

    try:
        json_bytes = await json_file.read()
        # Use 'utf-8-sig' to handle potential BOM in the file
        data = json.loads(json_bytes.decode('utf-8-sig'))
    except json.JSONDecodeError:
        return await interaction.followup.send("❌ 无效的JSON格式。请检查您的文件内容。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error reading attachment in /道馆 建造: {e}")
        return await interaction.followup.send("❌ 读取文件时发生错误。", ephemeral=True)

    validation_error = validate_gym_json(data)
    if validation_error:
        return await interaction.followup.send(f"❌ JSON数据验证失败：{validation_error}", ephemeral=True)

    try:
        async with aiosqlite.connect(db_path) as conn:
            await create_gym(str(interaction.guild.id), data, conn)
            await log_gym_action(str(interaction.guild.id), data['id'], str(interaction.user.id), 'create', conn)
            await conn.commit()
        logging.info(f"ADMIN: User '{interaction.user.id}' created gym '{data['id']}' in guild '{interaction.guild.id}'.")
        await interaction.followup.send(f"✅ 成功创建了道馆: **{data['name']}**", ephemeral=True)
    except aiosqlite.IntegrityError:
        gym_id = data.get('id', '未知')
        await interaction.followup.send(f"❌ 操作失败：道馆ID `{gym_id}` 已存在。如需修改，请使用 `/道馆 更新` 指令。", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"❌ 操作失败：我没有权限回复此消息。请检查我的权限。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error in /道馆 建造 command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ 操作失败: 发生了一个未知错误。", ephemeral=True)

@gym_management_group.command(name="更新", description="用新的JSON文件覆盖一个现有道馆 (馆主、管理员、开发者)。")
@has_gym_management_permission("更新")
@app_commands.describe(gym_id="要更新的道馆ID", json_file="新的道馆JSON文件。")
async def gym_update(interaction: discord.Interaction, gym_id: str, json_file: discord.Attachment):
    await interaction.response.defer(ephemeral=True, thinking=True)

    # Trigger a backup for the specific gym before making changes
    await backup_single_gym(str(interaction.guild.id), gym_id)

    if not json_file.filename.lower().endswith('.json'):
        return await interaction.followup.send("❌ 文件格式错误，请上传一个 `.json` 文件。", ephemeral=True)

    # Add a file size check (e.g., 1MB) to prevent abuse
    if json_file.size > 1 * 1024 * 1024:
        return await interaction.followup.send("❌ 文件过大，请确保JSON文件大小不超过 1MB。", ephemeral=True)

    try:
        json_bytes = await json_file.read()
        # Use 'utf-8-sig' to handle potential BOM in the file
        data = json.loads(json_bytes.decode('utf-8-sig'))
    except json.JSONDecodeError:
        return await interaction.followup.send("❌ 无效的JSON格式。请检查您的文件内容。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error reading attachment in /道馆 更新: {e}")
        return await interaction.followup.send("❌ 读取文件时发生错误。", ephemeral=True)

    # Ensure the ID in the JSON matches the provided gym_id
    if 'id' not in data or data['id'] != gym_id:
            return await interaction.followup.send(f"❌ JSON数据中的`id`必须是`{gym_id}`。", ephemeral=True)

    # Deep validation of the JSON data
    validation_error = validate_gym_json(data)
    if validation_error:
        return await interaction.followup.send(f"❌ JSON数据验证失败：{validation_error}", ephemeral=True)

    try:
        async with aiosqlite.connect(db_path) as conn:
            updated_rows = await update_gym(str(interaction.guild.id), gym_id, data, conn)
            if updated_rows > 0:
                await log_gym_action(str(interaction.guild.id), gym_id, str(interaction.user.id), 'update', conn)
                await conn.commit()
                logging.info(f"ADMIN: User '{interaction.user.id}' updated gym '{gym_id}' in guild '{interaction.guild.id}'.")
                await interaction.followup.send(f"✅ 成功更新了道馆: **{data['name']}**", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。如需创建，请使用 `/道馆 建造` 指令。", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"❌ 操作失败：我没有权限回复此消息。请检查我的权限。", ephemeral=True)
    except Exception as e:
       logging.error(f"Error in /道馆 更新 command: {e}", exc_info=True)
       await interaction.followup.send(f"❌ 操作失败: 发生了一个未知错误。", ephemeral=True)

@gym_management_group.command(name="删除", description="删除一个道馆 (馆主、管理员、开发者)。")
@has_gym_management_permission("删除")
@app_commands.describe(gym_id="要删除的道馆ID。")
async def gym_delete(interaction: discord.Interaction, gym_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    guild_id = str(interaction.guild.id)
    
    # First, check if the gym exists. If not, we can't back it up or delete it.
    if not await get_single_gym(guild_id, gym_id):
        return await interaction.followup.send(f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。", ephemeral=True)

    # Now that we know the gym exists, trigger a backup before deleting.
    await backup_single_gym(guild_id, gym_id)

    try:
        async with aiosqlite.connect(db_path) as conn:
            # --- Perform Deletion ---
            await log_gym_action(guild_id, gym_id, str(interaction.user.id), 'delete', conn)
            await conn.execute("DELETE FROM user_progress WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id))
            await delete_gym(guild_id, gym_id, conn)

            # --- Clean up associated_gyms in challenge_panels ---
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT message_id, associated_gyms FROM challenge_panels WHERE guild_id = ?", (guild_id,)) as cursor:
                panels_to_update = []
                all_panels = await cursor.fetchall()
                for panel in all_panels:
                    if panel['associated_gyms']:
                        associated_gyms_list = json.loads(panel['associated_gyms'])
                        if gym_id in associated_gyms_list:
                            associated_gyms_list.remove(gym_id)
                            new_json = json.dumps(associated_gyms_list) if associated_gyms_list else None
                            panels_to_update.append((new_json, panel['message_id']))
                
                if panels_to_update:
                    await conn.executemany(
                        "UPDATE challenge_panels SET associated_gyms = ? WHERE message_id = ?",
                        panels_to_update
                    )
            
            # --- Commit all changes ---
            await conn.commit()

        logging.info(f"ADMIN: User '{interaction.user.id}' deleted gym '{gym_id}' from guild '{guild_id}'. Also cleaned up challenge panels.")
        await interaction.followup.send(f"✅ 道馆 `{gym_id}` 及其所有相关进度已被成功删除。\nℹ️ 关联的挑战面板也已自动更新。", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"❌ 操作失败：我没有权限回复此消息。请检查我的权限。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error in /道馆 更新 command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ 操作失败: 发生了一个未知错误。", ephemeral=True)

@gym_management_group.command(name="后门", description="获取一个现有道馆的JSON数据 (馆主、管理员、开发者)。")
@has_gym_management_permission("后门")
@app_commands.describe(gym_id="要获取JSON的道馆ID。")
async def gym_get_json(interaction: discord.Interaction, gym_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    gym_data = await get_single_gym(str(interaction.guild.id), gym_id)
    if not gym_data:
        return await interaction.followup.send("❌ 在本服务器找不到指定ID的道馆。", ephemeral=True)
    
    json_string = json.dumps(gym_data, indent=4, ensure_ascii=False)
    # Use a file for long JSON strings
    if len(json_string) > 1900:
        # Create a unique filename to prevent race conditions
        filepath = f'gym_export_{interaction.user.id}.json'
        try:
            async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                await f.write(json_string)
            await interaction.followup.send("道馆数据过长，已作为文件发送。", file=discord.File(filepath), ephemeral=True)
        finally:
            # Ensure the temporary file is always removed
            # os.path.exists is sync, but it's extremely fast and acceptable here.
            if os.path.exists(filepath):
                os.remove(filepath)
    else:
        await interaction.followup.send(f"```json\n{json_string}\n```", ephemeral=True)

@gym_management_group.command(name="列表", description="列出本服务器所有的道馆及其ID (馆主、管理员、开发者)。")
@has_gym_management_permission("列表")
async def gym_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild_id = str(interaction.guild.id)
    guild_gyms = await get_guild_gyms(guild_id)

    if not guild_gyms:
        return await interaction.followup.send("本服务器还没有创建任何道馆。", ephemeral=True)

    embed = discord.Embed(title=f"「{interaction.guild.name}」的道馆列表", color=discord.Color.purple())
    
    description_lines = []
    for gym in guild_gyms:
        status_emoji = "✅" if gym.get('is_enabled', True) else "⏸️"
        status_text = "开启" if gym.get('is_enabled', True) else "关闭"
        badge_text = "🖼️" if gym.get('badge_image_url') else "➖"
        description_lines.append(f"{status_emoji} **{gym['name']}** `(ID: {gym['id']})` - **状态:** {status_text} | **徽章:** {badge_text}")
    
    embed.description = "\n".join(description_lines)
    await interaction.followup.send(embed=embed, ephemeral=True)

# --- Permission Management Command ---
@gym_management_group.command(name="设置馆主", description="管理道馆指令权限 (管理员或开发者)。")
@is_admin_or_owner()
@app_commands.describe(
    action="选择是'添加'还是'移除'权限",
    target="选择要授权的用户或身份组",
    permission="授予哪个指令的权限 ('all' 代表所有道馆指令)"
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="添加权限", value="add"),
        app_commands.Choice(name="移除权限", value="remove")
    ],
    permission=[
        app_commands.Choice(name="所有管理指令 (包括召唤)", value="all"),
        app_commands.Choice(name="召唤 (/道馆 召唤)", value="召唤"),
        app_commands.Choice(name="徽章墙面板 (/道馆 徽章墙面板)", value="徽章墙面板"),
        app_commands.Choice(name="毕业面板 (/道馆 毕业面板)", value="毕业面板"),
        app_commands.Choice(name="建造 (/道馆 建造)", value="建造"),
        app_commands.Choice(name="更新 (/道馆 更新)", value="更新"),
        app_commands.Choice(name="后门 (/道馆 后门)", value="后门"),
        app_commands.Choice(name="列表 (/道馆 列表)", value="列表"),
        app_commands.Choice(name="重置进度 (/道馆 重置进度)", value="重置进度"),
        app_commands.Choice(name="解除处罚 (/道馆 解除处罚)", value="解除处罚"),
        app_commands.Choice(name="停业 (/道馆 停业)", value="停业"),
        app_commands.Choice(name="删除 (/道馆 删除)", value="删除"),
        app_commands.Choice(name="道馆黑名单 (/道馆黑名单)", value="道馆黑名单"),
        app_commands.Choice(name="道馆封禁 (/道馆封禁)", value="道馆封禁")
    ]
)
async def set_gym_master(interaction: discord.Interaction, action: str, target: typing.Union[discord.Member, discord.Role], permission: str):
    await interaction.response.defer(ephemeral=True, thinking=True)

    # --- Security Check for @everyone role ---
    if isinstance(target, discord.Role) and target.is_default():
        return await interaction.followup.send("❌ **安全警告:** 出于安全考虑，禁止向 `@everyone` 角色授予道馆管理权限。", ephemeral=True)

    guild_id = str(interaction.guild.id)
    target_id = str(target.id)
    target_type = 'user' if isinstance(target, discord.User) or isinstance(target, discord.Member) else 'role'
    
    try:
        if action == "add":
            await add_gym_master(guild_id, target_id, target_type, permission)
            await interaction.followup.send(f"✅ 已将 `{permission}` 权限授予 {target.mention}。", ephemeral=True)
        elif action == "remove":
            await remove_gym_master(guild_id, target_id, permission)
            await interaction.followup.send(f"✅ 已从 {target.mention} 移除 `{permission}` 权限。", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"❌ 操作失败：我没有权限回复此消息。请检查我的权限。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error in /道馆 设置馆主 command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ 操作失败: 发生了一个未知错误。", ephemeral=True)

@gym_management_group.command(name="重置进度", description="重置一个用户的道馆挑战进度 (馆主、管理员、开发者)。")
@has_gym_management_permission("重置进度")
@app_commands.describe(user="要重置进度的用户。")
async def reset_progress(interaction: discord.Interaction, user: discord.Member):
    """Resets a user's gym progress for the guild."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild_id = str(interaction.guild.id)
    user_id = str(user.id)
    try:
        await fully_reset_user_progress(user_id, guild_id)
        await interaction.followup.send(f"✅ 已成功重置用户 {user.mention} 的所有道馆挑战进度、失败记录和身份组领取记录。", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"❌ 重置失败：我没有权限回复此消息。请检查我的权限。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error in /道馆 重置进度 command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ 重置失败: 发生了一个未知错误。", ephemeral=True)

@gym_management_group.command(name="解除处罚", description="解除用户在特定道馆的挑战冷却 (馆主、管理员、开发者)。")
@has_gym_management_permission("解除处罚")
@app_commands.describe(
    user="要解除处罚的用户",
    gym_id="要解除处罚的道馆ID"
)
async def gym_pardon(interaction: discord.Interaction, user: discord.Member, gym_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild_id = str(interaction.guild.id)
    user_id = str(user.id)

    # Check if the gym exists to provide a better error message
    if not await get_single_gym(guild_id, gym_id):
        return await interaction.followup.send(f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。", ephemeral=True)

    try:
        await reset_user_failures_for_gym(user_id, guild_id, gym_id)
        await interaction.followup.send(f"✅ 已成功解除用户 {user.mention} 在道馆 `{gym_id}` 的挑战处罚。", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"❌ 操作失败：我没有权限回复此消息。请检查我的权限。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error in /道馆 解除处罚 command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ 操作失败: 发生了一个未知错误。", ephemeral=True)

# --- Automatic Backup Task ---

async def backup_single_gym(guild_id: str, gym_id: str):
    """Backs up the current state of a single gym."""
    try:
        gym_data = await get_single_gym(guild_id, gym_id)
        if not gym_data:
            logging.warning(f"BACKUP: Attempted to back up non-existent gym '{gym_id}' in guild '{guild_id}'.")
            return

        # Create a structured directory path for backups
        backup_dir = os.path.join(script_dir, 'gym_backups', guild_id, gym_id)
        os.makedirs(backup_dir, exist_ok=True)

        # Prepare the new backup content as a sorted JSON string for consistent comparison
        new_backup_json_string = json.dumps(gym_data, indent=4, ensure_ascii=False, sort_keys=True)

        # Find the most recent backup file for this specific gym to compare against
        existing_backups = sorted(
            [f for f in os.listdir(backup_dir) if f.endswith(".json")],
            reverse=True
        )

        if existing_backups:
            latest_backup_file = os.path.join(backup_dir, existing_backups[0])
            async with aiofiles.open(latest_backup_file, 'r', encoding='utf-8') as f:
                last_backup_json_string = await f.read()

            if new_backup_json_string == last_backup_json_string:
                # No changes, so no backup needed for this trigger.
                return

        # Content has changed or no backup exists, create a new one with a precise timestamp.
        timestamp_str = datetime.datetime.now(BEIJING_TZ).strftime('%Y-%m-%d_%H-%M-%S')
        backup_filepath = os.path.join(backup_dir, f"{timestamp_str}.json")

        async with aiofiles.open(backup_filepath, 'w', encoding='utf-8') as f:
            await f.write(new_backup_json_string)
        logging.info(f"BACKUP: Successfully created on-demand backup for gym {gym_id} in guild {guild_id}.")

    except Exception as e:
        logging.error(f"BACKUP: Failed to process on-demand backup for gym {gym_id} in guild {guild_id}. Reason: {e}", exc_info=True)

@tasks.loop(hours=24)
async def daily_backup_task():
    """A background task that automatically backs up all gyms for all guilds daily."""
    logging.info("BACKUP: Starting daily full backup process...")
    try:
        async with aiosqlite.connect(db_path) as conn:
            cursor = await conn.execute("SELECT DISTINCT guild_id FROM gyms")
            guild_ids = [row[0] for row in await cursor.fetchall()]
        
        for guild_id in guild_ids:
            async with aiosqlite.connect(db_path) as conn:
                cursor = await conn.execute("SELECT DISTINCT gym_id FROM gyms WHERE guild_id = ?", (guild_id,))
                gym_ids = [row[0] for row in await cursor.fetchall()]
            
            for gym_id in gym_ids:
                await backup_single_gym(guild_id, gym_id)
        
        logging.info("BACKUP: Daily full backup process finished.")
    except Exception as e:
        logging.error(f"BACKUP: A critical error occurred during the daily backup task. Reason: {e}", exc_info=True)

@daily_backup_task.before_loop
async def before_daily_backup_task():
    """Ensures the bot is fully ready before the backup loop starts."""
    await bot.wait_until_ready()

@gym_management_group.command(name="停业", description="设置一个道馆的营业状态 (馆主、管理员、开发者)。")
@has_gym_management_permission("停业")
@app_commands.describe(
    gym_id="要操作的道馆ID。",
    status="选择要执行的操作。"
)
@app_commands.choices(status=[
    app_commands.Choice(name="开启", value="enable"),
    app_commands.Choice(name="停业", value="disable"),
])
async def gym_status(interaction: discord.Interaction, gym_id: str, status: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild_id = str(interaction.guild.id)
    
    is_enabled = True if status == "enable" else False
    
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("UPDATE gyms SET is_enabled = ? WHERE guild_id = ? AND gym_id = ?", (is_enabled, guild_id, gym_id))
        await conn.commit()
        
    if cursor.rowcount > 0:
        status_text = "开启" if is_enabled else "停业"
        await interaction.followup.send(f"✅ 道馆 `{gym_id}` 已{status_text}。", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。", ephemeral=True)

@bot.tree.command(name="道馆黑名单", description="管理作弊黑名单 (馆主、管理员、开发者)。")
@has_gym_management_permission("道馆黑名单")
@app_commands.describe(
    action="要执行的操作",
    target="[添加/移除] 操作的目标用户或身份组",
    role_target="[记录] 操作的目标身份组",
    reason="[添加/记录] 操作的原因"
)
@app_commands.choices(action=[
    app_commands.Choice(name="添加 (用户或身份组)", value="add"),
    app_commands.Choice(name="移除 (用户或身份组)", value="remove"),
    app_commands.Choice(name="记录 (身份组成员)", value="record_role"),
    app_commands.Choice(name="查看列表", value="view_list"),
    app_commands.Choice(name="清空 (!!!)", value="clear"),
])
async def gym_blacklist(
    interaction: discord.Interaction,
    action: str,
    target: typing.Optional[typing.Union[discord.Member, discord.Role]] = None,
    role_target: typing.Optional[discord.Role] = None,
    reason: typing.Optional[str] = "无"
):
    guild_id = str(interaction.guild.id)
    added_by = str(interaction.user.id)

    if action == "add":
        if not target:
            return await interaction.response.send_message("❌ `添加` 操作需要一个 `target` (用户或身份组)。", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        target_id = str(target.id)
        target_type = 'user' if isinstance(target, (discord.User, discord.Member)) else 'role'
        
        try:
            await add_to_blacklist(guild_id, target_id, target_type, reason, added_by)
            logging.info(f"BLACKLIST: User '{added_by}' added '{target_id}' ({target_type}) to blacklist in guild '{guild_id}'. Reason: {reason}")
            await interaction.followup.send(f"✅ 已成功将 {target.mention} 添加到黑名单。\n**原因:** {reason}", ephemeral=True)
        except Exception as e:
            logging.error(f"Error in /道馆黑名单 add command: {e}", exc_info=True)
            await interaction.followup.send("❌ 添加到黑名单时发生错误。", ephemeral=True)

    elif action == "remove":
        if not target:
            return await interaction.response.send_message("❌ `移除` 操作需要一个 `target` (用户或身份组)。", ephemeral=True)
            
        await interaction.response.defer(ephemeral=True, thinking=True)
        target_id = str(target.id)
        
        try:
            removed_count = await remove_from_blacklist(guild_id, target_id)
            if removed_count > 0:
                logging.info(f"BLACKLIST: User '{interaction.user.id}' removed '{target_id}' from blacklist in guild '{guild_id}'.")
                await interaction.followup.send(f"✅ 已成功将 {target.mention} 从黑名单中移除。", ephemeral=True)
            else:
                await interaction.followup.send(f"ℹ️ {target.mention} 不在黑名单中。", ephemeral=True)
        except Exception as e:
            logging.error(f"Error in /道馆黑名单 remove command: {e}", exc_info=True)
            await interaction.followup.send("❌ 从黑名单移除时发生错误。", ephemeral=True)

    elif action == "record_role":
        if not role_target:
            return await interaction.response.send_message("❌ `记录` 操作需要一个 `role_target` (身份组)。", ephemeral=True)

        members_in_role = role_target.members
        member_count = len(members_in_role)

        if not members_in_role:
            await interaction.response.send_message(f"ℹ️ 身份组 {role_target.mention} 中没有任何成员。", ephemeral=True)
            return

        # Acknowledge the interaction immediately and inform the user that the task is running in the background.
        await interaction.response.send_message(
            f"✅ **任务已开始**\n正在后台记录身份组 {role_target.mention} 的 {member_count} 名成员。完成后将发送通知。",
            ephemeral=True
        )

        # --- Run the long task in the background ---
        async def background_task():
            chunk_size = 1000
            total_added_count = 0
            try:
                for i in range(0, member_count, chunk_size):
                    chunk = members_in_role[i:i + chunk_size]
                    if not chunk:
                        continue
                    
                    added_count = await add_to_blacklist_bulk(guild_id, chunk, reason, added_by)
                    total_added_count += added_count
                    # Optional: log progress for very long tasks
                    logging.info(f"BLACKLIST_CHUNK: Processed chunk {i//chunk_size + 1}, added {added_count} members.")
                    await asyncio.sleep(1) # Small sleep to yield control and prevent tight loop

                logging.info(f"BLACKLIST: User '{added_by}' bulk-added {total_added_count} members from role '{role_target.id}' in guild '{guild_id}'.")
                # Send a new message upon completion using followup
                await interaction.followup.send(
                    f"✅ **后台记录完成**\n- **身份组:** {role_target.mention}\n- **成功添加:** {total_added_count} 名成员",
                    ephemeral=True
                )
            except Exception as e:
                logging.error(f"Error in background /道馆黑名单 record_role command: {e}", exc_info=True)
                await interaction.followup.send("❌ 批量记录黑名单时发生严重错误。", ephemeral=True)

        # Create a background task so the function can return immediately
        bot.loop.create_task(background_task())

    elif action == "clear":
        view = ConfirmClearView(guild_id=guild_id, original_interaction=interaction)
        await interaction.response.send_message(
            "⚠️ **警告:** 此操作将永久删除本服务器的 **所有** 黑名单记录，且无法撤销。\n请确认你的操作。",
            view=view,
            ephemeral=True
        )

    elif action == "view_list":
        await interaction.response.defer(ephemeral=True, thinking=True)
        blacklist_entries = await get_blacklist(guild_id)

        if not blacklist_entries:
            await interaction.followup.send("✅ 本服务器的黑名单是空的。", ephemeral=True)
            return

        view = BlacklistPaginatorView(interaction, blacklist_entries)
        embed = await view.create_embed()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="道馆封禁", description="管理挑战封禁名单 (馆主、管理员、开发者)。")
@has_gym_management_permission("道馆封禁")
@app_commands.describe(
   action="要执行的操作",
   target="[添加/移除] 操作的目标用户或身份组",
   reason="[添加] 操作的原因"
)
@app_commands.choices(action=[
   app_commands.Choice(name="添加 (用户或身份组)", value="add"),
   app_commands.Choice(name="移除 (用户或身份组)", value="remove"),
   app_commands.Choice(name="查看列表", value="view_list"),
])
async def gym_ban(
   interaction: discord.Interaction,
   action: str,
   target: typing.Optional[typing.Union[discord.Member, discord.Role]] = None,
   reason: typing.Optional[str] = "无"
):
   guild_id = str(interaction.guild.id)
   added_by = str(interaction.user.id)

   if action == "add":
       if not target:
           return await interaction.response.send_message("❌ `添加` 操作需要一个 `target` (用户或身份组)。", ephemeral=True)
       
       await interaction.response.defer(ephemeral=True, thinking=True)
       target_id = str(target.id)
       target_type = 'user' if isinstance(target, (discord.User, discord.Member)) else 'role'
       
       try:
           await add_to_ban_list(guild_id, target_id, target_type, reason, added_by)
           logging.info(f"BAN_LIST: User '{added_by}' added '{target_id}' ({target_type}) to ban list in guild '{guild_id}'. Reason: {reason}")
           await interaction.followup.send(f"✅ 已成功将 {target.mention} 添加到挑战封禁名单。\n**原因:** {reason}", ephemeral=True)
       except Exception as e:
           logging.error(f"Error in /道馆封禁 add command: {e}", exc_info=True)
           await interaction.followup.send("❌ 添加到封禁名单时发生错误。", ephemeral=True)

   elif action == "remove":
       if not target:
           return await interaction.response.send_message("❌ `移除` 操作需要一个 `target` (用户或身份组)。", ephemeral=True)
           
       await interaction.response.defer(ephemeral=True, thinking=True)
       target_id = str(target.id)
       
       try:
           removed_count = await remove_from_ban_list(guild_id, target_id)
           if removed_count > 0:
               logging.info(f"BAN_LIST: User '{interaction.user.id}' removed '{target_id}' from ban list in guild '{guild_id}'.")
               await interaction.followup.send(f"✅ 已成功将 {target.mention} 从挑战封禁名单中移除。", ephemeral=True)
           else:
               await interaction.followup.send(f"ℹ️ {target.mention} 不在挑战封禁名单中。", ephemeral=True)
       except Exception as e:
           logging.error(f"Error in /道馆封禁 remove command: {e}", exc_info=True)
           await interaction.followup.send("❌ 从封禁名单移除时发生错误。", ephemeral=True)

   elif action == "view_list":
       await interaction.response.defer(ephemeral=True, thinking=True)
       ban_list_entries = await get_ban_list(guild_id)

       if not ban_list_entries:
           await interaction.followup.send("✅ 本服务器的挑战封禁名单是空的。", ephemeral=True)
           return

       view = BanListPaginatorView(interaction, ban_list_entries)
       embed = await view.create_embed()
       await interaction.followup.send(embed=embed, view=view, ephemeral=True)

# --- Badge Showcase Command ---

class BadgeView(discord.ui.View):
    def __init__(self, user: discord.User, gyms: list):
        super().__init__(timeout=180)
        self.user = user
        self.gyms = gyms
        self.current_index = 0
        self.update_buttons()

    async def create_embed(self) -> discord.Embed:
        """Creates the embed for the current badge."""
        gym = self.gyms[self.current_index]
        gym_name = gym['name']
        url = gym.get('badge_image_url')

        badge_desc = gym.get('badge_description')

        embed = discord.Embed(
            title=f"{self.user.display_name}的徽章墙",
            color=discord.Color.gold()
        )
        
        # Build the description
        description_text = f"### {gym_name}\n\n"
        if badge_desc:
            description_text += f"{badge_desc}\n\n"
        
        embed.description = description_text
        embed.set_footer(text=f"徽章 {self.current_index + 1}/{len(self.gyms)}")

        if isinstance(url, str) and url:
            embed.set_image(url=url)
        else:
            # If there's no image, add a note to the description
            embed.description += "🖼️ *此道馆未设置徽章图片。*"
            
        return embed

    def update_buttons(self):
        """Disables/Enables buttons based on the current index."""
        if len(self.gyms) <= 1:
            self.children[0].disabled = True
            self.children[1].disabled = True
            return
            
        self.children[0].disabled = self.current_index == 0
        self.children[1].disabled = self.current_index == len(self.gyms) - 1

    async def handle_interaction(self, interaction: discord.Interaction):
        """Central handler for button interactions."""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("你不能操作别人的徽章墙哦。", ephemeral=True)
            return
        
        self.update_buttons()
        await interaction.response.edit_message(embed=await self.create_embed(), view=self)

    @discord.ui.button(label="◀️ 上一个", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_index -= 1
        await self.handle_interaction(interaction)

    @discord.ui.button(label="下一个 ▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_index += 1
        await self.handle_interaction(interaction)


@bot.tree.command(name="我的徽章墙", description="查看你已获得的道馆徽章。")
async def my_badges(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild.id)
    
    user_progress = await get_user_progress(user_id, guild_id)
    if not user_progress:
        return await interaction.followup.send("你还没有通过任何道馆的考核。", ephemeral=True)
        
    completed_gym_ids = list(user_progress.keys())
    all_guild_gyms = await get_guild_gyms(guild_id)
    
    # We now pass all completed gyms, the view will handle URL validation.
    completed_gyms = [gym for gym in all_guild_gyms if gym['id'] in completed_gym_ids]
    
    if not completed_gyms:
        # This case should ideally not be hit if user_progress is not empty, but as a safeguard:
        return await interaction.followup.send("你还没有通过任何道馆的考核。", ephemeral=True)
        
    view = BadgeView(interaction.user, completed_gyms)
    await interaction.followup.send(embed=await view.create_embed(), view=view, ephemeral=True)


bot.tree.add_command(gym_management_group)
# The /my_badges command is already registered via the @bot.tree.command decorator,
# so it does not need to be added to the group.

# --- Main Execution ---
if __name__ == "__main__":
    bot.run(config['BOT_TOKEN'])