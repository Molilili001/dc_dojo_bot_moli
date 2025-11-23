# Discord Bot Introduction Outline

## 1. Introduction
   - Brief overview of the bot's purpose (Gym challenge system, community management, utilities).
   - Target audience (Server members, Challengers, Administrators, Developers).

## 2. Core Feature: Gym Challenge System
   - **Concept:** Explain the "Gym" metaphor (challenges, badges, progression).
   - **For Challengers:**
     - **Starting a Challenge:** How to use challenge panels (Standard vs. Ultimate).
     - **The Challenge Process:** Answering questions, time limits, mistake allowances.
     - **Progress Tracking:** 
       - `/我的进度` (My Progress): Viewing completion status and failure records.
       - `/我的徽章墙` (My Badge Wall): showcasing earned badges.
     - **Ultimate Gym:** The "Endgame" challenge with zero tolerance and leaderboards.
     - **Leaderboard:** `/排行榜` (Leaderboard) to see top times.
     - **Graduation:** Collecting the final role reward upon completing all gyms.
   - **For Gym Masters (Admins):**
     - **Gym Management:**
       - `/道馆 建造` (Create Gym): Uploading JSON configuration.
       - `/道馆 更新` (Update Gym): Modifying existing gyms.
       - `/道馆 列表` (List Gyms): Viewing all gyms.
       - `/道馆 停业` (Toggle Gym): Enabling/Disabling gyms.
       - `/道馆 删除` (Delete Gym).
       - `/道馆 后门` (Export Gym): Getting JSON data.
     - **Panel Management:**
       - `/召唤面板` (Summon Panel): Creating interactive start buttons for gyms.
       - `/更新面板` (Update Panel): Modifying panel settings (roles, blacklist, etc.).
       - `/召唤徽章墙` (Summon Badge Wall).
       - `/毕业面板` (Summon Graduation Panel).
       - `/召唤排行榜` (Summon Leaderboard).
       - `/道馆 列表面板` (List Panels).

## 3. Community Management & Moderation
   - **Anti-Cheat & Moderation:**
     - **Blacklist System:**
       - `/道馆黑名单` (Gym Blacklist): Add/Remove/List users or roles.
       - Preventing blacklisted users from challenging or claiming rewards.
     - **Ban System:**
       - `/道馆封禁` (Gym Ban): Managing specific challenge bans.
       - Automatic temporary bans for repeated failures.
     - **Cross-Bot Synchronization:**
       - Automatic syncing of punishments and role removals across multiple bots.
       - `/联动同步` (Sync Management): Status, force sync, queue management.
   - **Forum Monitoring:**
     - Automatic handling of new forum posts (auto-reply, auto-role, mentions).
     - `/帖子监控面板` (Forum Monitor Panel): Configuration UI.
     - `/补发帖子消息` (Resend Messages): Manual trigger for missed posts.
   - **Feedback System:**
     - `/召唤反馈面板` (Summon Feedback Panel): Anonymous and Named feedback collection.
     - `/反馈设置频道` (Set Feedback Channel).
     - `/反馈白名单` (Feedback Whitelist): Restricting feedback to specific roles.
   - **General Admin Tools:**
     - `/设置馆主` (Set Gym Master): Granting bot permissions to users/roles.
     - `/say`: Bot speaks in a channel (with reply/attachment support).
     - `/admin_重置进度` (Reset Progress): Manually clearing user data.
     - `/admin_解除处罚` (Pardon User): Removing temporary bans.

## 4. Utility Features
   - **"Huiding" (Back to Top):**
     - `/回顶`, `／回顶`, `回顶`: Quickly get a link to the first message of a channel/thread.
     - `/huiding_toggle`: Enable/Disable this feature.
     - `/huiding_status`: Check status.
   - **Todo List:**
     - `/todo`: Manage personal or server-wide task lists.

## 5. Developer Tools (Owner Only)
   - `/状态` (System Status): View server resource usage and bot stats.
   - `/重载` (Reload): Hot-reload cogs/extensions.
   - `/调试` (Debug): Cache clearing, active challenge viewing, command syncing.
   - `/公告` (Announcement): Broadcast messages to all servers.

## 6. Configuration & Permissions
   - Explanation of the permission system (Gym Master, Admin, Owner).
   - Overview of JSON configuration files (brief mention for context).
