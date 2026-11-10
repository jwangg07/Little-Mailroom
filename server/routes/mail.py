"""Authenticated Gmail inbox, message, and send endpoints."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

from auth_store import AuthStore
from config import Settings
from gmail_service import GmailService
from security import require_origin, require_user


class SendMail(BaseModel):
    to: EmailStr
    subject: str = Field(max_length=998)
    body: str = Field(min_length=1, max_length=100_000)

    @field_validator("subject", "to")
    @classmethod
    def no_header_injection(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Mail headers must be a single line")
        return value


def create_mail_router(
    settings: Settings,
    store: AuthStore,
    mail_service: Callable[[], GmailService],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/mail/messages")
    def inbox(request: Request, page_token: str = Query("", max_length=2048)):
        user = require_user(request, store)
        return mail_service().inbox(user["id"], page_token)

    @router.get("/api/mail/messages/{message_id}")
    def read_message(request: Request, message_id: str):
        user = require_user(request, store)
        if not message_id.isalnum() or len(message_id) > 128:
            raise HTTPException(400, "Invalid message ID")
        return mail_service().message(user["id"], message_id)

    @router.post("/api/mail/send")
    def send_mail(request: Request, mail: SendMail):
        user = require_user(request, store)
        require_origin(request, settings)
        return mail_service().send(
            user["id"], user["email"], str(mail.to), mail.subject, mail.body
        )

    return router
