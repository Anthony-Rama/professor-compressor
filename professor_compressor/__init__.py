"""Professor Compressor application package."""

import os


def run() -> None:
    """Load and start the application without import-time side effects."""
    # aiohttp builds its verified TLS context when it is first imported, so
    # the certifi bundle must be selected before the runtime (which imports
    # discord.py and aiohttp) is loaded.
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    from .app.runtime import run as run_application

    run_application()


__all__ = ["run"]
