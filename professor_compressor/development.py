"""Fail-closed local launcher for the separate development Discord bot."""

from __future__ import annotations

import argparse
import asyncio
import os
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import certifi
from aiohttp import ClientError, ClientSession, ClientTimeout
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_APPLICATION_ID = "1550752400271351839"
PRODUCTION_HOST = "professor-compressor.duckdns.org"
DEV_KEYS = frozenset(
    {
        "DISCORD_TOKEN",
        "DEV_APPLICATION_ID",
        "ALLOWED_GUILD_IDS",
        "PUBLIC_BASE_URL",
        "WEB_HOST",
        "WEB_PORT",
        "OBSERVABILITY_ADMIN_KEY",
        "TOPGG_API_TOKEN",
        "DSC_API_TOKEN",
        "ALERT_WEBHOOK_URL",
    }
)
SYSTEM_KEYS = frozenset(
    {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
)


class DevelopmentError(ValueError):
    """An intentionally safe, actionable startup message."""


def snowflake(value: str, label: str) -> str:
    if not value.isascii() or not value.isdigit() or not 0 < int(value) < 2**64:
        raise DevelopmentError(f"{label} must be a numeric Discord ID.")
    return str(int(value))


def development_environment(path: Path) -> dict[str, str]:
    """Use only the explicit dev file and safe OS settings, not inherited secrets."""
    path = path.resolve()
    if not path.is_file():
        raise DevelopmentError(
            "Dev configuration file not found. Create dev.env first."
        )
    if path == (ROOT / ".env").resolve():
        raise DevelopmentError("The production .env cannot be used for development.")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise DevelopmentError("Dev credentials must be owner-only: chmod 600 dev.env")
    parsed = dotenv_values(path, interpolate=False)
    if set(parsed) - DEV_KEYS:
        raise DevelopmentError(
            "Unsupported dev configuration key; check the dev template."
        )
    values = {key: (value or "").strip() for key, value in parsed.items()}
    token = values.get("DISCORD_TOKEN", "")
    if not token or any(character.isspace() for character in token):
        raise DevelopmentError("DISCORD_TOKEN must contain the dev bot token.")
    expected = snowflake(values.get("DEV_APPLICATION_ID", ""), "DEV_APPLICATION_ID")
    if expected == PRODUCTION_APPLICATION_ID:
        raise DevelopmentError("Refusing to start the production bot in development.")
    guilds = values.get("ALLOWED_GUILD_IDS", "").split(",")
    allowed = [snowflake(value.strip(), "ALLOWED_GUILD_IDS") for value in guilds]
    host = values.get("WEB_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "::1"}:
        raise DevelopmentError("Development WEB_HOST must be a loopback IP address.")
    try:
        port = int(values.get("WEB_PORT", "8080"))
    except ValueError:
        raise DevelopmentError("WEB_PORT must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise DevelopmentError("WEB_PORT must be an integer from 1 to 65535.")
    public = values.get("PUBLIC_BASE_URL", "").rstrip("/")
    try:
        url = urlsplit(public)
        hostname = url.hostname
        public_port = url.port
    except ValueError:
        raise DevelopmentError("PUBLIC_BASE_URL must be a valid dev URL.") from None
    if (
        not hostname
        or hostname.rstrip(".").lower() == PRODUCTION_HOST
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path
        or url.scheme not in {"http", "https"}
        or (url.scheme == "http" and hostname not in {"127.0.0.1", "localhost", "::1"})
        or (hostname in {"127.0.0.1", "localhost", "::1"} and public_port != port)
    ):
        raise DevelopmentError(
            "PUBLIC_BASE_URL must be a dev HTTPS origin or local URL."
        )
    for key in ("TOPGG_API_TOKEN", "DSC_API_TOKEN", "ALERT_WEBHOOK_URL"):
        if values.get(key):
            raise DevelopmentError(
                f"Leave {key} empty in development to isolate integrations."
            )
    admin = values.get("OBSERVABILITY_ADMIN_KEY", "")
    if admin and len(admin) < 32:
        raise DevelopmentError(
            "OBSERVABILITY_ADMIN_KEY must contain at least 32 characters."
        )
    environment = {
        key: value for key, value in os.environ.items() if key in SYSTEM_KEYS
    }
    environment.update(
        DISCORD_TOKEN=token,
        DEV_APPLICATION_ID=expected,
        ALLOWED_GUILD_IDS=",".join(allowed),
        DIAGNOSTIC_TEST_GUILD_IDS=",".join(allowed),
        WEB_HOST=host,
        WEB_PORT=str(port),
        PUBLIC_BASE_URL=public,
        PYTHON_DOTENV_DISABLED="1",
        PYTHONDONTWRITEBYTECODE="1",
        SSL_CERT_FILE=certifi.where(),
        APP_REVISION="development",
        OBSERVABILITY_DB_PATH=str(
            ROOT / "observability-data" / "dev" / "events.sqlite3"
        ),
        OBSERVABILITY_ADMIN_KEY=admin,
        TOPGG_API_TOKEN="",
        DSC_API_TOKEN="",
        ALERT_WEBHOOK_URL="",
    )
    return environment


async def verify_identity(token: str, expected: str) -> None:
    """Check the actual token identity before starting any gateway or web server."""
    async with (
        ClientSession(timeout=ClientTimeout(total=15), trust_env=False) as session,
        session.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": "Bot " + token},
            allow_redirects=False,
            ssl=ssl.create_default_context(cafile=certifi.where()),
        ) as response,
    ):
        if response.status != 200:
            raise DevelopmentError(
                f"Discord identity check returned HTTP {response.status}. "
                "Check the dev Bot Token; no bot was started."
            )
        if response.content_length is not None and response.content_length > 16384:
            raise DevelopmentError(
                "Unexpected Discord identity response; no bot was started."
            )
        identity = await response.json()
        if (
            not isinstance(identity, dict)
            or identity.get("bot") is not True
            or identity.get("id") != expected
            or identity.get("id") == PRODUCTION_APPLICATION_ID
        ):
            raise DevelopmentError(
                "Token does not match the expected dev bot. No bot started."
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / "dev.env")
    parser.add_argument("--check", action="store_true", help="Verify without starting")
    args = parser.parse_args()
    try:
        environment = development_environment(args.env_file)
        asyncio.run(
            verify_identity(
                environment["DISCORD_TOKEN"], environment["DEV_APPLICATION_ID"]
            )
        )
    except DevelopmentError as error:
        print("Dev startup blocked: " + str(error), file=sys.stderr)
        return 1
    except (ValueError, OSError, ClientError, TimeoutError):
        # Validation messages are deliberately authored and never include raw secrets.
        # Network exceptions may contain request details, so do not print their text.
        print(
            "Dev startup blocked. Check the dev file, expected app ID, Bot Token, "
            "and Discord connectivity. No bot was started.",
            file=sys.stderr,
        )
        return 1
    print(
        "Verified dev bot identity; production credentials and integrations excluded."
    )
    if args.check:
        return 0
    print("Starting local dev bot. Keep this terminal and your tunnel running.")
    try:
        return subprocess.run(
            [sys.executable, str(ROOT / "bot.py")],
            cwd=ROOT,
            env=environment,
            check=False,
        ).returncode
    except KeyboardInterrupt:
        return 130
