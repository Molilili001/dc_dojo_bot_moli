import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import sqlite3
import typing
import datetime
import aiohttp

# --- Configuration Loading ---
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.json')
db_path = os.path.join(script_dir, 'progress.db')

with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

# --- Database Management ---
def setup_database():
    """Creates the database and tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # User progress table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id TEXT,
            guild_id TEXT,
            gym_id TEXT,
            completed BOOLEAN DEFAULT TRUE,
            PRIMARY KEY (user_id, guild_id, gym_id)
        )
    ''')
    # Server configuration table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS server_configs (
            guild_id TEXT PRIMARY KEY,
            challenge_channel_id TEXT,
            master_role_id TEXT
        )
    ''')
    # Gym master (gym owner) permissions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gym_masters (
            guild_id TEXT NOT NULL,
            target_id TEXT NOT NULL, -- User ID or Role ID
            target_type TEXT NOT NULL, -- 'user' or 'role'
            permission TEXT NOT NULL, -- 'all' or specific command name like 'gym_create'
            PRIMARY KEY (guild_id, target_id, permission)
        )
    ''')
    # Server-specific gyms table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gyms (
            gym_id TEXT,
            guild_id TEXT,
            name TEXT,
            description TEXT,
            tutorial TEXT, -- Stored as JSON string
            questions TEXT, -- Stored as JSON string
            PRIMARY KEY (guild_id, gym_id)
        )
    ''')
    # Table to track user failures and cooldowns
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS challenge_failures (
            user_id TEXT,
            guild_id TEXT,
            gym_id TEXT,
            failure_count INTEGER DEFAULT 0,
            banned_until TEXT, -- ISO 8601 format timestamp
            PRIMARY KEY (user_id, guild_id, gym_id)
        )
    ''')
    conn.commit()
    conn.close()

# --- Gym Data Functions ---
def get_guild_gyms(guild_id: str) -> list:
    """Gets all gyms for a specific guild."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT gym_id, name, description, tutorial, questions FROM gyms WHERE guild_id = ?", (guild_id,))
    rows = cursor.fetchall()
    conn.close()
    gyms_list = []
    for row in rows:
        gyms_list.append({
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "tutorial": json.loads(row[3]),
            "questions": json.loads(row[4])
        })
    return gyms_list

def get_single_gym(guild_id: str, gym_id: str) -> dict:
    """Gets a single gym's data for a guild."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT gym_id, name, description, tutorial, questions FROM gyms WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "tutorial": json.loads(row[3]),
        "questions": json.loads(row[4])
    }

def create_gym(guild_id: str, gym_data: dict):
    """Creates a new gym. Raises sqlite3.IntegrityError if gym_id already exists."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO gyms (guild_id, gym_id, name, description, tutorial, questions)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            guild_id, gym_data['id'], gym_data['name'], gym_data['description'],
            json.dumps(gym_data['tutorial']), json.dumps(gym_data['questions'])
        ))
        conn.commit()
    finally:
        conn.close()

def update_gym(guild_id: str, gym_id: str, gym_data: dict) -> bool:
    """Updates an existing gym. Returns True if a row was updated, False otherwise."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE gyms SET name = ?, description = ?, tutorial = ?, questions = ?
        WHERE guild_id = ? AND gym_id = ?
    ''', (
        gym_data['name'], gym_data['description'], json.dumps(gym_data['tutorial']),
        json.dumps(gym_data['questions']), guild_id, gym_id
    ))
    updated_rows = cursor.rowcount
    conn.commit()
    conn.close()
    return updated_rows > 0

def delete_gym(guild_id: str, gym_id: str, cursor: sqlite3.Cursor = None):
    """Deletes a gym for a guild. Uses provided cursor if available."""
    conn = None
    # If no cursor is provided, create a new connection for standalone use.
    if cursor is None:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

    cursor.execute("DELETE FROM gyms WHERE guild_id = ? AND gym_id = ?", (guild_id, gym_id))

    # If a new connection was created, commit and close it.
    if conn:
        conn.commit()
        conn.close()

# --- User Progress Functions ---
def get_user_progress(user_id: str, guild_id: str) -> dict:
    """Gets a user's completed gyms for a specific guild."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT gym_id FROM user_progress WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: True for row in rows}

# --- Failure Tracking Functions ---
def get_user_failure_status(user_id: str, guild_id: str, gym_id: str) -> tuple:
    """Gets a user's failure count and ban status for a specific gym."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT failure_count, banned_until FROM challenge_failures WHERE user_id = ? AND guild_id = ? AND gym_id = ?",
        (user_id, guild_id, gym_id)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        # Parse the timestamp string back into a datetime object
        banned_until_dt = datetime.datetime.fromisoformat(row[1]) if row[1] else None
        return row[0], banned_until_dt
    return 0, None

def increment_user_failure(user_id: str, guild_id: str, gym_id: str) -> datetime.timedelta:
    """Increments failure count, calculates and sets ban duration. Returns the duration."""
    current_failures, _ = get_user_failure_status(user_id, guild_id, gym_id)
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

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO challenge_failures (user_id, guild_id, gym_id, failure_count, banned_until)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, guild_id, gym_id) DO UPDATE SET
        failure_count = excluded.failure_count,
        banned_until = excluded.banned_until
    ''', (user_id, guild_id, gym_id, new_failure_count, banned_until_iso))
    conn.commit()
    conn.close()
    return ban_duration

def reset_user_failures_for_gym(user_id: str, guild_id: str, gym_id: str):
    """Resets a user's failure count for a specific gym upon success."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ? AND gym_id = ?",
        (user_id, guild_id, gym_id)
    )
    conn.commit()
    conn.close()

def set_gym_completed(user_id: str, guild_id: str, gym_id: str):
    """Marks a gym as completed for a user in a specific guild."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO user_progress (user_id, guild_id, gym_id) VALUES (?, ?, ?)", (user_id, guild_id, gym_id))
    conn.commit()
    conn.close()

def reset_user_progress(user_id: str, guild_id: str):
    """Resets all progress for a user in a specific guild."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_progress WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    conn.commit()
    conn.close()

# --- Server Config Functions ---
def set_server_config(guild_id: str, channel_id: str, role_id: str):
    """Saves or updates a server's configuration in the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO server_configs (guild_id, challenge_channel_id, master_role_id)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
        challenge_channel_id = excluded.challenge_channel_id,
        master_role_id = excluded.master_role_id
    ''', (guild_id, channel_id, role_id))
    conn.commit()
    conn.close()

def get_server_config(guild_id: str) -> tuple:
    """Gets a server's configuration from the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT master_role_id FROM server_configs WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return row if row else (None,)

# --- Permission Functions ---
def add_gym_master(guild_id: str, target_id: str, target_type: str, permission: str):
    """Adds a gym master permission to the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO gym_masters (guild_id, target_id, target_type, permission)
        VALUES (?, ?, ?, ?)
    ''', (guild_id, target_id, target_type, permission))
    conn.commit()
    conn.close()

def remove_gym_master(guild_id: str, target_id: str, permission: str):
    """Removes a gym master permission from the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM gym_masters WHERE guild_id = ? AND target_id = ? AND permission = ?",
        (guild_id, target_id, permission)
    )
    conn.commit()
    conn.close()

def check_gym_master_permission(guild_id: str, user: discord.Member, permission: str) -> bool:
    """Checks if a user has a specific gym master permission."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check for user-specific permission (for the command or for 'all')
    cursor.execute(
        "SELECT 1 FROM gym_masters WHERE guild_id = ? AND target_id = ? AND target_type = 'user' AND (permission = ? OR permission = 'all')",
        (guild_id, str(user.id), permission)
    )
    if cursor.fetchone():
        conn.close()
        return True

    # Check for role-specific permission (for the command or for 'all')
    role_ids = [str(role.id) for role in user.roles]
    if role_ids:
        placeholders = ','.join('?' for _ in role_ids)
        query = f"""
            SELECT 1 FROM gym_masters
            WHERE guild_id = ? AND target_type = 'role' AND target_id IN ({placeholders})
            AND (permission = ? OR permission = 'all')
        """
        params = [guild_id] + role_ids + [permission]
        cursor.execute(query, params)
        if cursor.fetchone():
            conn.close()
            return True

    conn.close()
    return False

# --- State Management ---
active_challenges = {}

class ChallengeSession:
    """Represents a user's current challenge session."""
    def __init__(self, user_id: int, guild_id: int, gym_id: str):
        self.user_id = user_id
        self.guild_id = guild_id
        self.gym_id = gym_id
        self.gym_info = get_single_gym(str(guild_id), gym_id)
        self.current_question_index = 0

    def get_current_question(self):
        """Returns the current question dictionary."""
        if self.gym_info and self.current_question_index < len(self.gym_info['questions']):
            return self.gym_info['questions'][self.current_question_index]
        return None

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Views ---
class GymSelect(discord.ui.Select):
    def __init__(self, guild_id: str, user_progress: dict):
        guild_gyms = get_guild_gyms(guild_id)
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
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)
        gym_id = self.values[0]

        if gym_id == "no_gyms":
            await interaction.response.edit_message(content="本服务器还没有创建任何道馆哦。", view=None)
            return

        user_progress = get_user_progress(user_id, guild_id)
        if user_progress.get(gym_id, False):
            await interaction.response.edit_message(content="你已经完成过这个道馆的挑战了！", view=None)
            return

        # Check for ban/cooldown status
        _, banned_until = get_user_failure_status(user_id, guild_id, gym_id)
        if banned_until and banned_until > datetime.datetime.now(datetime.timezone.utc):
            # Calculate remaining time
            remaining_time = banned_until - datetime.datetime.now(datetime.timezone.utc)
            # Format the timedelta into a more readable string
            hours, remainder = divmod(int(remaining_time.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            time_str = f"{hours}小时 {minutes}分钟" if hours > 0 else f"{minutes}分钟 {seconds}秒"
            await interaction.response.edit_message(content=f"❌ **挑战冷却中** ❌\n\n由于多次挑战失败，你暂时无法挑战该道馆。\n请在 **{time_str}** 后再试。", view=None)
            return
        
        if user_id in active_challenges:
            del active_challenges[user_id]

        session = ChallengeSession(user_id, interaction.guild.id, gym_id)
        if not session.gym_info:
            await interaction.response.edit_message(content="错误：找不到该道馆的数据。可能已被删除。", view=None)
            return
            
        active_challenges[user_id] = session
        
        tutorial_text = "\n".join(session.gym_info['tutorial'])
        embed = discord.Embed(title=f"欢迎来到 {session.gym_info['name']}", description=tutorial_text, color=discord.Color.blue())
        
        view = discord.ui.View()
        view.add_item(StartChallengeButton(gym_id))
        await interaction.response.edit_message(content=None, embed=embed, view=view)

class GymSelectView(discord.ui.View):
    def __init__(self, guild_id: str, user_progress: dict):
        super().__init__(timeout=180)
        self.add_item(GymSelect(guild_id, user_progress))

class MainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="挑战道馆", style=discord.ButtonStyle.success, custom_id="open_gym_list")
    async def open_gym_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)
        user_gym_progress = get_user_progress(user_id, guild_id)
        
        try:
            await interaction.response.send_message(
                "请从下面的列表中选择你要挑战的道馆。",
                view=GymSelectView(guild_id, user_gym_progress),
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
            await interaction.response.edit_message(content="挑战已取消。", view=None, embed=None)
        else:
            await interaction.response.edit_message(content="没有正在进行的挑战或已超时。", view=None, embed=None)

async def display_question(interaction: discord.Interaction, session: ChallengeSession):
    question = session.get_current_question()
    if not question:
        # On success, reset any failure records for this specific gym
        reset_user_failures_for_gym(str(session.user_id), str(session.guild_id), session.gym_id)
        set_gym_completed(str(session.user_id), str(session.guild_id), session.gym_id)
        del active_challenges[str(session.user_id)]
        embed = discord.Embed(title=f"🎉 恭喜你，挑战成功！", description=f"你已经成功通过 **{session.gym_info['name']}** 的考核！你的失败记录已被清零。", color=discord.Color.green())
        await interaction.response.edit_message(embed=embed, view=None)
        await check_and_award_master_role(interaction.user)
        return

    q_num = session.current_question_index + 1
    total_q = len(session.gym_info['questions'])
    embed = discord.Embed(title=f"问题 {q_num}/{total_q}: {session.gym_info['name']}", description=question['text'], color=discord.Color.orange())
    view = discord.ui.View(timeout=180)
    if question['type'] == 'multiple_choice':
        for option in question['options']:
            view.add_item(QuestionAnswerButton(option, question['correct_answer']))
    elif question['type'] == 'fill_in_blank':
        view.add_item(FillInBlankButton())
    view.add_item(CancelChallengeButton())
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)

class QuestionAnswerButton(discord.ui.Button):
    def __init__(self, label: str, correct_answer: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.correct_answer = correct_answer

    async def callback(self, interaction: discord.Interaction):
        session = active_challenges.get(str(interaction.user.id))
        if not session:
            await interaction.response.edit_message(content="挑战已超时，请重新开始。", view=None, embed=None)
            return
        if self.label == self.correct_answer:
            session.current_question_index += 1
            await display_question(interaction, session)
        else:
            ban_duration = increment_user_failure(str(interaction.user.id), str(session.guild_id), session.gym_id)
            del active_challenges[str(interaction.user.id)]

            if ban_duration.total_seconds() > 0:
                hours, remainder = divmod(int(ban_duration.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                time_str = f"{hours}小时" if hours > 0 else f"{minutes}分钟"
                message = f"回答错误，本次挑战失败！\n你已被禁止挑战该道馆 **{time_str}**。"
            else:
                message = "回答错误，本次挑战失败！请重新开始。"
            
            await interaction.response.edit_message(content=message, view=None, embed=None)

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
        correct_answer = self.question['correct_answer']
        if user_answer.lower() == correct_answer.lower():
            session.current_question_index += 1
            await display_question(interaction, session)
        else:
            ban_duration = increment_user_failure(str(interaction.user.id), str(session.guild_id), session.gym_id)
            del active_challenges[str(interaction.user.id)]

            if ban_duration.total_seconds() > 0:
                hours, remainder = divmod(int(ban_duration.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                time_str = f"{hours}小时" if hours > 0 else f"{minutes}分钟"
                message = f"回答错误，本次挑战失败！\n你已被禁止挑战该道馆 **{time_str}**。"
            else:
                message = "回答错误，本次挑战失败！请重新开始。"

            await interaction.response.edit_message(content=message, view=None, embed=None)

async def check_and_award_master_role(member: discord.Member):
    guild_id = str(member.guild.id)
    user_id = str(member.id)
    user_progress = get_user_progress(user_id, guild_id)
    guild_gyms = get_guild_gyms(guild_id)

    if not user_progress or not guild_gyms:
        return

    all_gym_ids = {gym['id'] for gym in guild_gyms}
    completed_gym_ids = set(user_progress.keys())

    if all_gym_ids.issubset(completed_gym_ids):
        config_row = get_server_config(guild_id)
        if not config_row: return
        master_role_id = int(config_row[0])
        master_role = member.guild.get_role(master_role_id)
        if master_role and master_role not in member.roles:
            try:
                await member.add_roles(master_role)
                await member.send(f"恭喜你！你已在 **{member.guild.name}** 服务器完成了所有道馆挑战，并获得了“徽章大师”身份组！")
            except Exception as e:
                print(f"Failed to assign role in {member.guild.name}: {e}")

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s) globally.")
    except Exception as e:
        print(f"Error syncing commands globally: {e}")
    print('Bot is ready to accept commands.')
    bot.add_view(MainView())

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """A global error handler for all slash commands."""
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ 你没有执行此指令所需的权限。", ephemeral=True)
    else:
        # For other errors, you might want to log them and send a generic message.
        print(f"Unhandled error in command {interaction.command.name if interaction.command else 'unknown'}: {error}")
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
        if check_gym_master_permission(str(interaction.guild.id), interaction.user, command_name):
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
@app_commands.describe(master_role="用户完成所有道馆后将获得的身份组。")
async def gym_summon(interaction: discord.Interaction, master_role: discord.Role):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild_id = str(interaction.guild.id)
    channel_id = str(interaction.channel.id)
    role_id = str(master_role.id)
    try:
        set_server_config(guild_id, channel_id, role_id)
        embed = discord.Embed(title="道馆挑战中心", description="欢迎来到道馆挑战中心！在这里，你可以通过挑战不同的道馆来学习和证明你的能力。\n\n完成所有道馆挑战后，你将获得特殊身份组，并获得提前离开缓冲区的资格（仅限首次）。\n\n点击下方的按钮，开始你的挑战吧！", color=discord.Color.gold())
        await interaction.channel.send(embed=embed, view=MainView())
        await interaction.followup.send(f"✅ 道馆系统已成功设置！\n- **挑战频道**: {interaction.channel.mention}\n- **大师身份组**: {master_role.mention}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 设置失败: {e}", ephemeral=True)

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

        if q['type'] not in ['multiple_choice', 'fill_in_blank']:
            return f"问题 {q_num} 的 `type` 无效，必须是 'multiple_choice' 或 'fill_in_blank'。"
            
        if q['type'] == 'multiple_choice':
            if 'options' not in q or not isinstance(q['options'], list) or len(q['options']) < 2:
                return f"问题 {q_num} (选择题) 必须包含一个至少有2个选项的 `options` 列表。"
            if q['correct_answer'] not in q['options']:
                return f"问题 {q_num} (选择题) 的 `correct_answer` 必须是 `options` 列表中的一个。"
            # Validate button label length
            for opt in q['options']:
                if len(str(opt)) > BUTTON_LABEL_LIMIT:
                    return f"问题 {q_num} 的选项 '{str(opt)[:20]}...' 长度超出了Discord按钮 {BUTTON_LABEL_LIMIT} 字符的限制。"

    return "" # All good

@gym_management_group.command(name="建造", description="通过JSON创建一个新道馆 (馆主、管理员、开发者)。")
@has_gym_management_permission("建造")
@app_commands.describe(json_data="包含道馆完整信息的JSON字符串。")
async def gym_create(interaction: discord.Interaction, json_data: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = json.loads(json_data)
        
        # Deep validation of the JSON data
        validation_error = validate_gym_json(data)
        if validation_error:
            return await interaction.followup.send(f"❌ JSON数据验证失败：{validation_error}", ephemeral=True)
        
        create_gym(str(interaction.guild.id), data)
        await interaction.followup.send(f"✅ 成功创建了道馆: **{data['name']}**", ephemeral=True)
    except json.JSONDecodeError:
        await interaction.followup.send("❌ 无效的JSON格式。请检查您的输入。", ephemeral=True)
    except sqlite3.IntegrityError:
        gym_id = data.get('id', '未知')
        await interaction.followup.send(f"❌ 操作失败：道馆ID `{gym_id}` 已存在。如需修改，请使用 `/道馆 更新` 指令。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 操作失败: {e}", ephemeral=True)

@gym_management_group.command(name="更新", description="用新的JSON数据覆盖一个现有道馆 (馆主、管理员、开发者)。")
@has_gym_management_permission("更新")
@app_commands.describe(gym_id="要更新的道馆ID", json_data="新的道馆JSON数据。")
async def gym_update(interaction: discord.Interaction, gym_id: str, json_data: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = json.loads(json_data)
        
        # Ensure the ID in the JSON matches the provided gym_id
        if 'id' not in data or data['id'] != gym_id:
             return await interaction.followup.send(f"❌ JSON数据中的`id`必须是`{gym_id}`。", ephemeral=True)

        # Deep validation of the JSON data
        validation_error = validate_gym_json(data)
        if validation_error:
            return await interaction.followup.send(f"❌ JSON数据验证失败：{validation_error}", ephemeral=True)

        was_updated = update_gym(str(interaction.guild.id), gym_id, data)
        
        if was_updated:
            await interaction.followup.send(f"✅ 成功更新了道馆: **{data['name']}**", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。如需创建，请使用 `/道馆 建造` 指令。", ephemeral=True)
    except json.JSONDecodeError:
        await interaction.followup.send("❌ 无效的JSON格式。请检查您的输入。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 操作失败: {e}", ephemeral=True)

@gym_management_group.command(name="删除", description="删除一个道馆 (仅限管理员或开发者)。")
@is_admin_or_owner()
@app_commands.describe(gym_id="要删除的道馆ID。")
async def gym_delete(interaction: discord.Interaction, gym_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # Delete progress entries for the gym
        cursor.execute("DELETE FROM user_progress WHERE guild_id = ? AND gym_id = ?", (str(interaction.guild.id), gym_id))
        # Delete the gym itself using the same transaction
        delete_gym(str(interaction.guild.id), gym_id, cursor=cursor)
        conn.commit() # Commit both deletions at once
        await interaction.followup.send(f"✅ 如果道馆 `{gym_id}` 存在，它及其所有相关进度已被删除。", ephemeral=True)
    except Exception as e:
        if conn:
            conn.rollback() # Rollback changes on error
        await interaction.followup.send(f"❌ 操作失败: {e}", ephemeral=True)
    finally:
        if conn:
            conn.close() # Ensure connection is always closed

@gym_management_group.command(name="后门", description="获取一个现有道馆的JSON数据 (馆主、管理员、开发者)。")
@has_gym_management_permission("后门")
@app_commands.describe(gym_id="要获取JSON的道馆ID。")
async def gym_get_json(interaction: discord.Interaction, gym_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    gym_data = get_single_gym(str(interaction.guild.id), gym_id)
    if not gym_data:
        return await interaction.followup.send("❌ 在本服务器找不到指定ID的道馆。", ephemeral=True)
    
    json_string = json.dumps(gym_data, indent=4, ensure_ascii=False)
    # Use a file for long JSON strings
    if len(json_string) > 1900:
        filepath = 'gym_export.json'
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
    guild_gyms = get_guild_gyms(guild_id)

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
            add_gym_master(guild_id, target_id, target_type, permission)
            await interaction.followup.send(f"✅ 已将 `{permission}` 权限授予 {target.mention}。", ephemeral=True)
        elif action == "remove":
            remove_gym_master(guild_id, target_id, permission)
            await interaction.followup.send(f"✅ 已从 {target.mention} 移除 `{permission}` 权限。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 操作失败: {e}", ephemeral=True)

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
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM challenge_failures WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        conn.commit()
        conn.close()
        
        reset_user_progress(user_id, guild_id)
        await interaction.followup.send(f"✅ 已成功重置用户 {user.mention} 的所有道馆挑战进度和失败记录。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 重置失败: {e}", ephemeral=True)

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
    if not get_single_gym(guild_id, gym_id):
        return await interaction.followup.send(f"❌ 操作失败：找不到ID为 `{gym_id}` 的道馆。", ephemeral=True)

    try:
        reset_user_failures_for_gym(user_id, guild_id, gym_id)
        await interaction.followup.send(f"✅ 已成功解除用户 {user.mention} 在道馆 `{gym_id}` 的挑战处罚。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 操作失败: {e}", ephemeral=True)

bot.tree.add_command(gym_management_group)

# --- Main Execution ---
if __name__ == "__main__":
    setup_database()
    bot.run(config['BOT_TOKEN'])