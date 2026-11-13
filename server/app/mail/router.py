"""Authenticated Gmail inbox, message, and send endpoints."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth.rate_limit import limit_requests
from app.auth.security import require_origin, require_user
from app.auth.store import AuthStore
from app.config import Settings
from app.mail.schemas import MailDraft
from app.mail.service import GmailService


def create_mail_router(
    settings: Settings,
    store: AuthStore,
    mail_service: Callable[[], GmailService],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/mail/messages")
    def inbox(request: Request, page_token: str = Query("", max_length=2048)):
        user = require_user(request, store, settings)
        limit_requests(store, "mail", user["id"], settings.mail_requests_per_minute)
        return mail_service().inbox(user["id"], page_token)

    @router.get("/api/mail/messages/{message_id}")
    def read_message(request: Request, message_id: str):
        user = require_user(request, store, settings)
        limit_requests(store, "mail", user["id"], settings.mail_requests_per_minute)
        if not message_id.isalnum() or len(message_id) > 128:
            raise HTTPException(400, "Invalid message ID")
        return mail_service().message(user["id"], message_id)

    @router.post("/api/mail/send")
    def send_mail(request: Request, mail: MailDraft):
        require_origin(request, settings)
        user = require_user(request, store, settings)
        limit_requests(store, "send", user["id"], settings.send_requests_per_minute)
        return mail_service().send(
            user["id"], user["email"], str(mail.to), mail.subject, mail.body
        )

    return router
