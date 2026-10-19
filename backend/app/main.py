"""ASGI entrypoint: `uvicorn app.main:app --workers 1`. Fails at import if config is unsafe."""

from app.factory import create_app

app = create_app()
