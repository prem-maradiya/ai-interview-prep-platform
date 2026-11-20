from app.core.observability import configure_logging


def setup_logging() -> None:
    """Show the app's own logs (uvicorn and arq only configure their own loggers). See LOG_FORMAT."""
    configure_logging()
