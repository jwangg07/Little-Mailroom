"""Uvicorn entry point: run from server with main:app."""

from app.application import create_app

app = create_app()
