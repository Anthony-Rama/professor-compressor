"""Checks for normal bot-message delivery, not interaction responses."""

import discord

from .config import MIB


def channel_delivery_problem(channel, permissions) -> str | None:
    if channel is None or not hasattr(channel, "send"):
        return "Use a text channel or thread where the bot can send files."
    missing = []
    for flag, label in (
        ("view_channel", "View Channel"),
        ("attach_files", "Attach Files"),
    ):
        if not getattr(permissions, flag, False):
            missing.append(label)
    in_thread = isinstance(channel, discord.Thread)
    send_flag = "send_messages_in_threads" if in_thread else "send_messages"
    if not getattr(permissions, send_flag, False):
        missing.append("Send Messages in Threads" if in_thread else "Send Messages")
    if missing:
        return (
            "The bot needs these permissions here: "
            + ", ".join(missing)
            + ". Ask a server admin to update the channel permissions."
        )
    if in_thread and channel.archived:
        return "Unarchive this thread before starting compression."
    if (
        in_thread
        and channel.is_private()
        and channel.me is None
        and not permissions.manage_threads
    ):
        return "Add the bot to this private thread before starting compression."
    return None


def bot_upload_limit(guild: discord.Guild, base_limit: int = 20 * MIB) -> int:
    # interaction.filesize_limit may include the invoking user's Nitro allowance.
    # A channel.send upload instead uses the guild allowance available to bots.
    # discord.py 2.7.1 still has a 10 MiB base; Discord's reference now states
    # 20 MiB. Keep a maintainer override for future platform changes.
    return max(base_limit, guild.filesize_limit)
