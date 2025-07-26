import discord
from discord.ext import commands
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
from collections import defaultdict

# --- Configuration Loading ---
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.json')
db_path = os.path.join(script_dir, 'progress.db')
log_path = os.path.join(script_dir, 'bot.log')

# --- Logging Setup ---
# The log file is no longer cleared on startup to preserve history.
# Configures a logger that rotates the log file at midnight every day and keeps the last 7 days of logs.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        TimedRotatingFileHandler(log_path, when='midnight', interval=1, backupCount=7, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

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
            CREATE TABLE IF NOT EXISTS server_configs (
                guild_id TEXT PRIMARY KEY,
                challenge_channel_id TEXT,
                master_role_id TEXT, -- Role to ADD on completion
                role_to_remove_on_completion_id TEXT -- Role to REMOVE on completion
            )
        ''')
        # Safely add the new column if it doesn't exist for existing databases
        try:
            await conn.execute("ALTER TABLE server_configs ADD COLUMN role_to_remove_on_completion_id TEXT;")
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
                PRIMARY KEY (guild_id, gym_id)
            )
        ''')
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

        # --- Create Indexes for Performance ---
        # These indexes significantly speed up common queries in a large server.
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_progress_user_guild ON user_progress (user_id, guild_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_challenge_failures_user_guild ON challenge_failures (user_id, guild_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_gyms_guild ON gyms (guild_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_gym_masters_guild_target ON gym_masters (guild_id, target_id);")

        await conn.commit()

# --- Gym Data Functions ---
async def get_guild_gyms(guild_id: str) -> list:
    """Gets all gyms for a specific guild."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT gym_id, name, description, tutorial, questions, questions_to_ask, allowed_mistakes FROM gyms WHERE guild_id = ?", (guild_id,)) as cursor:
            rows = await cursor.fetchall()
    
    gyms_list = []
    for row in rows:
        gym_data = {
            "id": row["gym_id"],
            "name": row["name"],
            "description": row["description"],
            "tutorial": json.loads(row["tutorial"]),
            "questions": json.loads(row["questions"])
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
        async with conn.execute("SELECT gym_id, name, description, tutorial, questions, questions_to_ask, allowed_mistakes FROM gyms WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id)) as cursor:
            row = await cursor.fetchone()

    if not row:
        return None
    gym_data = {
        "id": row["gym_id"],
        "name": row["name"],
        "description": row["description"],
        "tutorial": json.loads(row["tutorial"]),
        "questions": json.loads(row["questions"])
    }
    if row["questions_to_ask"]:
        gym_data["questions_to_ask"] = row["questions_to_ask"]
    if row["allowed_mistakes"] is not None:
        gym_data["allowed_mistakes"] = row["allowed_mistakes"]
    return gym_data

async def create_gym(guild_id: str, gym_data: dict, conn: aiosqlite.Connection):
    """Creates a new gym using the provided connection."""
    await conn.execute('''
        INSERT INTO gyms (guild_id, gym_id, name, description, tutorial, questions, questions_to_ask, allowed_mistakes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        guild_id, gym_data['id'], gym_data['name'], gym_data['description'],
        json.dumps(gym_data['tutorial']), json.dumps(gym_data['questions']),
        gym_data.get('questions_to_ask'), gym_data.get('allowed_mistakes')
    ))

async def update_gym(guild_id: str, gym_id: str, gym_data: dict, conn: aiosqlite.Connection) -> int:
    """Updates an existing gym. Returns rowcount."""
    cursor = await conn.execute('''
        UPDATE gyms SET name = ?, description = ?, tutorial = ?, questions = ?, questions_to_ask = ?, allowed_mistakes = ?
        WHERE guild_id = ? AND gym_id = ?
    ''', (
        gym_data['name'], gym_data['description'], json.dumps(gym_data['tutorial']),
        json.dumps(gym_data['questions']), gym_data.get('questions_to_ask'), gym_data.get('allowed_mistakes'),
        guild_id, gym_id
    ))
    return cursor.rowcount

async def delete_gym(guild_id: str, gym_id: str, conn: aiosqlite.Connection):
    """Deletes a gym for a guild using the provided connection."""
    await conn.execute("DELETE FROM gyms WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id))

# --- Gym Audit Log Functions ---
async def log_gym_action(guild_id: str, gym_id: str, user_id: str, action: str, conn: aiosqlite.Connection):
    """Logs a gym management action using the provided connection."""
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
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

async def reset_user_progress(user_id: str, guild_id: str):
    """Resets all progress for a user in a specific guild."""
    async with user_db_locks[user_id]:
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("DELETE FROM user_progress WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
            await conn.commit()

# --- Server Config Functions ---
async def set_server_config(guild_id: str, channel_id: str, role_to_add_id: str = None, role_to_remove_id: str = None):
    """Saves or updates a server's configuration in the database."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute('''
            INSERT INTO server_configs (guild_id, challenge_channel_id, master_role_id, role_to_remove_on_completion_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
            challenge_channel_id = excluded.challenge_channel_id,
            master_role_id = excluded.master_role_id,
            role_to_remove_on_completion_id = excluded.role_to_remove_on_completion_id
        ''', (guild_id, channel_id, role_to_add_id, role_to_remove_id))
        await conn.commit()

async def get_server_config(guild_id: str) -> tuple:
    """Gets a server's configuration from the database. Returns (role_to_add_id, role_to_remove_id)."""
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute("SELECT master_role_id, role_to_remove_on_completion_id FROM server_configs WHERE guild_id = ?", (guild_id,)) as cursor:
            row = await cursor.fetchone()
    return row if row else (None, None)

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

# --- State Management ---
active_challenges = {}

class ChallengeSession:
    """Represents a user's current challenge session."""
    def __init__(self, user_id: int, guild_id: int, gym_id: str, gym_info: dict):
        self.user_id = user_id
        self.guild_id = guild_id
        self.gym_id = gym_id
        self.gym_info = gym_info
        self.current_question_index = 0
        self.mistakes_made = 0
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
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Views ---
class GymSelect(discord.ui.Select):
    def __init__(self, guild_gyms: list, user_progress: dict):
        options = []
        if not guild_gyms:
            options.append(discord.SelectOption(label="本服务器暂无道馆", description="请管理员使用 /道馆 建造 来创建道馆。", value="no_gyms", emoji="🤷"))
        else:
            for gym in guild_gyms:
                gym_id = gym['id']
                completed = user_progress.get(gym_id, False)
                status_emoji = "✅" if completed else "❌"
                label = f"{status_emoji} {gym['name']}"
                description = "已通关" if completed else "未通关"
                options.append(discord.SelectOption(label=label, description=description, value=gym_id))
        
        super().__init__(placeholder="请选择一个道馆进行挑战...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)
        gym_id = self.values[0]

        if gym_id == "no_gyms":
            await interaction.edit_original_response(content="本服务器还没有创建任何道馆哦。", view=None)
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

        gym_info = await get_single_gym(guild_id, gym_id)
        if not gym_info:
            await interaction.edit_original_response(content="错误：找不到该道馆的数据。可能已被删除。", view=None)
            return
            
        session = ChallengeSession(user_id, interaction.guild.id, gym_id, gym_info)
        active_challenges[user_id] = session
        logging.info(f"CHALLENGE: Session created for user '{user_id}' in gym '{gym_id}'.")
        
        tutorial_text = "\n".join(session.gym_info['tutorial'])
        embed = discord.Embed(title=f"欢迎来到 {session.gym_info['name']}", description=tutorial_text, color=discord.Color.blue())
        
        view = discord.ui.View()
        view.add_item(StartChallengeButton(gym_id))
        await interaction.edit_original_response(content=None, embed=embed, view=view)

class GymSelectView(discord.ui.View):
    def __init__(self, guild_gyms: list, user_progress: dict):
        super().__init__(timeout=180)
        self.add_item(GymSelect(guild_gyms, user_progress))

class MainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="挑战道馆", style=discord.ButtonStyle.success, custom_id="open_gym_list")
    async def open_gym_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)
        
        user_gym_progress = await get_user_progress(user_id, guild_id)
        guild_gyms = await get_guild_gyms(guild_id)
        
        try:
            await interaction.followup.send(
                "请从下面的列表中选择你要挑战的道馆。",
                view=GymSelectView(guild_gyms, user_gym_progress),
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
            await check_and_manage_completion_roles(interaction.user)
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
    else:
        # --- Display Next Question ---
        question = session.get_current_question()
        q_num = session.current_question_index + 1
        total_q = len(session.questions_for_session)
        embed = discord.Embed(title=f"问题 {q_num}/{total_q}: {session.gym_info['name']}", description=question['text'], color=discord.Color.orange())
        
        if question['type'] == 'multiple_choice':
            for option in question['options']:
                view.add_item(QuestionAnswerButton(option, question['correct_answer']))
        elif question['type'] == 'true_false':
            view.add_item(QuestionAnswerButton('正确', question['correct_answer']))
            view.add_item(QuestionAnswerButton('错误', question['correct_answer']))
        elif question['type'] == 'fill_in_blank':
            view.add_item(FillInBlankButton())
        view.add_item(CancelChallengeButton())

    # --- Part 2: Send or Edit the Message ---
    final_view = None if is_final_result else view
    
    if from_modal:
        # The interaction comes from a modal submission. We must edit the original message.
        if interaction.message:
            await interaction.message.edit(embed=embed, view=final_view)
    elif interaction.response.is_done():
        # The interaction has already been responded to (e.g., deferred).
        await interaction.edit_original_response(embed=embed, view=final_view)
    else:
        # This is a direct response to a component interaction (e.g., a button click).
        await interaction.response.edit_message(embed=embed, view=final_view)

class QuestionAnswerButton(discord.ui.Button):
    def __init__(self, label: str, correct_answer: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.correct_answer = correct_answer

    async def callback(self, interaction: discord.Interaction):
        session = active_challenges.get(str(interaction.user.id))
        if not session:
            await interaction.response.edit_message(content="挑战已超时，请重新开始。", view=None, embed=None)
            return
        if self.label != self.correct_answer:
            session.mistakes_made += 1
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
        session = active_challenges.get(str(interaction.user.id))
        if not session:
            await interaction.response.edit_message(content="挑战已超时，请重新开始。", view=None, embed=None)
            return
        user_answer = self.answer_input.value.strip()
        correct_answer_field = self.question['correct_answer']
        is_correct = False

        # 检查 correct_answer_field 是列表还是字符串
        if isinstance(correct_answer_field, list):
            # 如果是列表，检查用户答案是否在列表中（忽略大小写）
            if any(user_answer.lower() == str(ans).lower() for ans in correct_answer_field):
                is_correct = True
        else:
            # 保持对旧格式（字符串）的兼容
            if user_answer.lower() == str(correct_answer_field).lower():
                is_correct = True
        
        if not is_correct:
            session.mistakes_made += 1
            logging.info(f"CHALLENGE: User '{interaction.user.id}' answered incorrectly. Mistakes: {session.mistakes_made}/{session.allowed_mistakes}")

        session.current_question_index += 1
        # Acknowledge the modal submission, then display the next question.
        await interaction.response.defer()
        await display_question(interaction, session, from_modal=True)

async def check_and_manage_completion_roles(member: discord.Member):
    """Checks if a user has completed all gyms and manages roles accordingly."""
    guild_id = str(member.guild.id)
    user_id = str(member.id)
    user_progress = await get_user_progress(user_id, guild_id)
    guild_gyms = await get_guild_gyms(guild_id)

    if not user_progress or not guild_gyms:
        return

    all_gym_ids = {gym['id'] for gym in guild_gyms}
    completed_gym_ids = set(user_progress.keys())

    # Proceed only if the user has completed all available gyms
    if all_gym_ids.issubset(completed_gym_ids):
        role_to_add_id, role_to_remove_id = await get_server_config(guild_id)
        messages = []

        # --- Role to Add ---
        if role_to_add_id:
            role_to_add = member.guild.get_role(int(role_to_add_id))
            if role_to_add and role_to_add not in member.roles:
                try:
                    await member.add_roles(role_to_add)
                    messages.append(f"✅ **获得了身份组**: {role_to_add.mention}")
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
            header = f"🎉 恭喜你！你已在 **{member.guild.name}** 服务器完成了所有道馆挑战！"
            full_message = header + "\n\n" + "\n".join(messages)
            try:
                await member.send(full_message)
            except discord.Forbidden:
                logging.warning(f"Cannot send DM to {member.name} (ID: {member.id}). They may have DMs disabled.")

# --- Bot Events ---
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
    role_to_add="[可选] 用户完成所有道馆后将获得的身份组。",
    role_to_remove="[可选] 用户完成所有道馆后将被移除的身份组。"
)
async def gym_summon(interaction: discord.Interaction, role_to_add: typing.Optional[discord.Role] = None, role_to_remove: typing.Optional[discord.Role] = None):
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    guild_id = str(interaction.guild.id)
    channel_id = str(interaction.channel.id)
    role_add_id = str(role_to_add.id) if role_to_add else None
    role_remove_id = str(role_to_remove.id) if role_to_remove else None

    try:
        await set_server_config(guild_id, channel_id, role_add_id, role_remove_id)
        
        embed = discord.Embed(
            title="道馆挑战中心",
            description="欢迎来到道馆挑战中心！在这里，你可以通过挑战不同的道馆来学习和证明你的能力。\n\n"
                        "完成所有道馆挑战后，可能会有特殊的身份组奖励或变动。\n\n"
                        "点击下方的按钮，开始你的挑战吧！",
            color=discord.Color.gold()
        )
        await interaction.channel.send(embed=embed, view=MainView())
        
        # Build confirmation message
        confirm_messages = [f"✅ 道馆系统已成功设置于 {interaction.channel.mention}！"]
        if role_to_add:
            confirm_messages.append(f"- **通关奖励身份组**: {role_to_add.mention}")
        if role_to_remove:
            confirm_messages.append(f"- **通关移除身份组**: {role_to_remove.mention}")
        
        await interaction.followup.send("\n".join(confirm_messages), ephemeral=True)

    except discord.Forbidden:
        await interaction.followup.send(f"❌ 设置失败：我没有权限在此频道发送消息或管理身份组。请检查我的权限。", ephemeral=True)
    except Exception as e:
        logging.error(f"Error in /道馆 召唤 command: {e}", exc_info=True)
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
            # Validate button label length
            for opt in q['options']:
                if len(str(opt)) > BUTTON_LABEL_LIMIT:
                    return f"问题 {q_num} 的选项 '{str(opt)[:20]}...' 长度超出了Discord按钮 {BUTTON_LABEL_LIMIT} 字符的限制。"

        if q['type'] == 'true_false':
            if q['correct_answer'] not in ['正确', '错误']:
                return f"问题 {q_num} (判断题) 的 `correct_answer` 必须是 '正确' 或 '错误'。"

    return "" # All good

@gym_management_group.command(name="建造", description="通过JSON创建一个新道馆 (馆主、管理员、开发者)。")
@has_gym_management_permission("建造")
@app_commands.describe(json_data="包含道馆完整信息的JSON字符串。")
async def gym_create(interaction: discord.Interaction, json_data: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = json.loads(json_data)
    except json.JSONDecodeError:
        return await interaction.followup.send("❌ 无效的JSON格式。请检查您的输入。", ephemeral=True)

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

@gym_management_group.command(name="更新", description="用新的JSON数据覆盖一个现有道馆 (馆主、管理员、开发者)。")
@has_gym_management_permission("更新")
@app_commands.describe(gym_id="要更新的道馆ID", json_data="新的道馆JSON数据。")
async def gym_update(interaction: discord.Interaction, gym_id: str, json_data: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = json.loads(json_data)
    except json.JSONDecodeError:
        return await interaction.followup.send("❌ 无效的JSON格式。请检查您的输入。", ephemeral=True)

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
        logging.error(f"Error in /道馆 删除 command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ 操作失败: 发生了一个未知错误。", ephemeral=True)

@gym_management_group.command(name="删除", description="删除一个道馆 (仅限管理员或开发者)。")
@is_admin_or_owner()
@app_commands.describe(gym_id="要删除的道馆ID。")
async def gym_delete(interaction: discord.Interaction, gym_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    guild_id = str(interaction.guild.id)
    # First, check if the gym exists.
    if not await get_single_gym(guild_id, gym_id):
        return await interaction.followup.send(f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。", ephemeral=True)

    try:
        async with aiosqlite.connect(db_path) as conn:
            await log_gym_action(guild_id, gym_id, str(interaction.user.id), 'delete', conn)
            await conn.execute("DELETE FROM user_progress WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id))
            await delete_gym(guild_id, gym_id, conn)
            await conn.commit()
        logging.info(f"ADMIN: User '{interaction.user.id}' deleted gym '{gym_id}' from guild '{guild_id}'.")
        await interaction.followup.send(f"✅ 道馆 `{gym_id}` 及其所有相关进度已被成功删除。", ephemeral=True)
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
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(json_string)
            await interaction.followup.send("道馆数据过长，已作为文件发送。", file=discord.File(filepath), ephemeral=True)
        finally:
            # Ensure the temporary file is always removed
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
    
    description = ""
    for gym in guild_gyms:
        description += f"**名称:** {gym['name']}\n**ID:** `{gym['id']}`\n\n"
    
    embed.description = description
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
        app_commands.Choice(name="建造 (/道馆 建造)", value="建造"),
        app_commands.Choice(name="更新 (/道馆 更新)", value="更新"),
        app_commands.Choice(name="后门 (/道馆 后门)", value="后门"),
        app_commands.Choice(name="列表 (/道馆 列表)", value="列表"),
        app_commands.Choice(name="重置进度 (/道馆 重置进度)", value="重置进度"),
        app_commands.Choice(name="解除处罚 (/道馆 解除处罚)", value="解除处罚")
    ]
)
async def set_gym_master(interaction: discord.Interaction, action: str, target: typing.Union[discord.Member, discord.Role], permission: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
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
        # Also reset failure records
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
            await conn.commit()
        
        await reset_user_progress(user_id, guild_id)
        await interaction.followup.send(f"✅ 已成功重置用户 {user.mention} 的所有道馆挑战进度和失败记录。", ephemeral=True)
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

bot.tree.add_command(gym_management_group)

# --- Main Execution ---
if __name__ == "__main__":
    bot.run(config['BOT_TOKEN'])