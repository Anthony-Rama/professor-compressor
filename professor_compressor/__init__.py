"""Professor Compressor application package."""


def run() -> None:
    """Load and start the application without import-time side effects."""
    from .app.runtime import run as run_application

    run_application()


__all__ = ["run"]
