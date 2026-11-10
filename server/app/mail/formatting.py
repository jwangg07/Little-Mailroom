"""Convert Gmail payloads to display data and drafts to MIME messages."""

import base64
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser


def mail_summary(data: dict) -> dict:
    headers = {
        header["name"].lower(): header["value"]
        for header in data.get("payload", {}).get("headers", [])
    }
    return {
        "id": data["id"],
        "threadId": data.get("threadId"),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", "(No subject)"),
        "date": headers.get("date", ""),
        "snippet": data.get("snippet", ""),
        "unread": "UNREAD" in data.get("labelIds", []),
    }


def mail_message(data: dict) -> dict:
    raw = data["raw"]
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    )
    body = message.get_body(preferencelist=("plain",))
    text = (
        body.get_content()
        if body
        else "This message has no plain-text version. View it in Gmail."
    )
    return {
        "id": data["id"],
        "from": str(message.get("From", "")),
        "subject": str(message.get("Subject", "(No subject)")),
        "body": text,
    }


def encode_draft(sender: str, to: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
