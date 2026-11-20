import logging


def setup_logging() -> None:
    """Show the app's own INFO logs (uvicorn and arq only configure their own loggers)."""
    root = logging.getLogger("prepforge")
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s: %(message)s", "%H:%M:%S"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        root.propagate = False
