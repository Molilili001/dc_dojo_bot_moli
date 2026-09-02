# Privacy Policy

Last updated: September 2, 2026

This Privacy Policy explains how Discord Dojo Bot (the "Bot") collects, uses, stores, and deletes data when it is added to a Discord server.

The Bot is a community management and event automation bot for Discord servers. It provides dojo/challenge progression, badge and reward role management, forum post monitoring, member join statistics, feedback collection, todo/event reminders, keyword-based thread commands, and cross-bot moderation synchronization.

## Data We Collect

The Bot may process and store the following Discord data when needed for enabled features:

- Discord user IDs, guild/server IDs, channel IDs, message IDs, thread IDs, role IDs, and bot IDs.
- Member display names or usernames where needed for moderation records, progress history, todo/event records, or audit context.
- Member role information where needed for permission checks, challenge rewards, automatic role assignment/removal, and moderation synchronization.
- Member join events where the member monitoring feature is enabled by server administrators.
- Challenge progress, badge/reward state, leaderboard results, blacklist or ban records, failure/cooldown records, and related history.
- Forum/thread monitoring configuration, notification configuration, feedback configuration, and administrator-defined automation rules.
- Todo/event list entries, reminders, message links, and related metadata created by users or administrators.
- Message content only where required by enabled features, such as configured keyword triggers, todo/event trigger phrases, thread utility commands, and JSON messages used for cross-bot moderation synchronization.
- Logs needed to operate, debug, and secure the Bot.

## Why We Use This Data

The Bot uses data only to provide server features requested or configured by server administrators and users, including:

- Managing dojo/challenge progress, rewards, badges, cooldowns, and leaderboards.
- Checking permissions for administrator, moderator, and authorized user actions.
- Assigning or removing roles for challenge rewards, forum post automation, and moderation actions.
- Monitoring new member joins and sending configured alerts or daily reports.
- Processing feedback submissions and delivering them to configured channels.
- Running todo/event reminders and displaying user-created event lists.
- Detecting configured keyword, exact-match, prefix, contains, or regex triggers for thread and channel automation.
- Parsing JSON messages from configured trusted bots for cross-bot moderation synchronization.
- Maintaining audit records and operational logs needed for reliability and abuse prevention.

The Bot does not sell data, share data with advertisers, build advertising profiles, or use message content for unrelated analytics.

## Dojo Quiz Eligibility

When a member of the configured target Discord server chooses to start a dojo challenge, the Bot sends that member's Discord user ID to the production Dojo website. The website operator compares the ID with existing anti-cheating observations and returns only whether the member is currently eligible or is missing a current observation. The Bot does not send challenge answers, roles, message content, or other Discord profile data in this request, and the website does not return browser, network, hash, or observation-time data.

The Bot owner is responsible for the Bot configuration and this transfer. The Dojo website operator is responsible for the website's observation data, eligibility decision, retention, and verified-deletion process. The website stores no per-request eligibility decision record. If the eligibility service is unavailable or reports no current observation, the challenge does not start and no challenge failure is recorded; the response is private to the member.

## Message Content

The Bot requires Discord's Message Content privileged intent for specific configured features. Message content is processed only when it is necessary for enabled automation or moderation features, including:

- Reading JSON moderation sync messages from configured monitored channels.
- Matching administrator-configured keyword or regex rules for auto-replies, reactions, message deletion, and thread utilities.
- Reading specific todo/event trigger phrases in configured monitored channels.
- Reading thread messages where needed for configured thread utility features.

Message content is not used for advertising, profiling, or unrelated monitoring.

## Member Data

The Bot requires Discord's Server Members privileged intent for features that depend on guild member data, including:

- Counting member join events for configured member monitoring alerts and reports.
- Resolving guild members for challenge progress, moderation checks, and role automation.
- Reading member roles for permission checks and blacklist/ban enforcement.
- Adding or removing roles for rewards, forum post automation, and cross-bot moderation synchronization.

The Bot does not require or use Presence data.

## Data Storage and Retention

Data is stored in the Bot's local database and log files operated by the Bot owner or server operator.

Retention depends on the feature:

- Active configuration and progress records are kept while the Bot remains in use or until they are deleted by an administrator or the Bot owner.
- Member join statistics may be automatically cleaned after the configured retention period used by the Bot.
- Todo/event records may be automatically cleaned or soft-deleted based on the Bot's cleanup settings.
- Logs are retained only as needed for operation, debugging, and security.
- Server administrators may remove feature configuration or request deletion of stored server data.

## Data Sharing

The Bot does not sell or rent data.

Data may be visible inside the Discord server where the Bot operates when a feature intentionally posts messages, embeds, reminders, alerts, moderation records, or reports. Data may also be processed by Discord as part of normal Discord platform operation.

The Bot owner may access stored data and logs only for maintenance, debugging, security, abuse prevention, or responding to deletion requests.

## User and Server Controls

Server administrators can control which features are enabled and where the Bot listens or posts messages. Users and administrators can use the Bot's commands and configuration panels to manage supported data such as challenge progress, todo/event entries, feedback settings, monitoring configuration, and automation rules.

To request deletion of data stored by the Bot and associated with a user, server, or feature, contact the Bot owner through the GitHub repository issues page:

https://github.com/Molilili001/dc_dojo_bot_moli/issues

In the public issue, ask only for a private deletion-contact channel. Do not publish Discord IDs, private tokens, credentials, or other sensitive details. The Bot owner will provide private instructions for identifying the records and verifying control of the affected account or server.

For anti-cheating observations held by the Dojo website, use the verified community-contact process published in the [Dojo website privacy notice](https://dojo.windkingmomfly.site/#privacy). Do not post credentials, browser or network details, or other sensitive data in a public issue.

## Security

The Bot is designed to minimize access to data and use Discord permissions only for configured features. Bot tokens, configuration files containing secrets, databases, and logs should not be published publicly.

Server operators should invite the Bot only with the permissions needed for their server's enabled features.

## Changes to This Policy

This Privacy Policy may be updated when the Bot's features or data handling practices change. The latest version will be available in this repository.
