"""Telethon client factory."""
from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import load_config

_config = load_config()


def proxy_tuple(
    host: str,
    port: int,
    proxy_type: str,
    username: str | None,
    password: str | None,
) -> tuple:
    """Convert proxy fields to Telethon proxy tuple."""
    import socks
    ptype = socks.SOCKS5 if proxy_type.lower() == "socks5" else socks.HTTP
    return (ptype, host, port, True, username, password)


def make_client(session_string: str, proxy: tuple | None = None) -> TelegramClient:
    """Create a Telethon client from a StringSession."""
    return TelegramClient(
        StringSession(session_string),
        _config.tg_api_id,
        _config.tg_api_hash,
        proxy=proxy,
    )
