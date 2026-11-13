"""Convert Gmail payloads to display data and drafts to MIME messages."""

import base64
from email import policy
from email.message import EmailMessage, Message

from fastapi import HTTPException


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
    payload = data.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    text = "This message has no plain-text version. View it in Gmail."
    pending = [(payload, 0)]
    visited = 0
    while pending:
        part, depth = pending.pop()
        visited += 1
        if depth > 20 or visited > 100:
            raise HTTPException(413, "This message is too complex. Open it in Gmail.")
        part_headers = {h["name"].lower(): h["value"] for h in part.get("headers", [])}
        if part.get("filename") or part_headers.get(
            "content-disposition", ""
        ).lower().startswith("attachment"):
            continue
        if part.get("mimeType") == "text/plain":
            encoded = part.get("body", {}).get("data", "")
            if part.get("body", {}).get("attachmentId") and not encoded:
                text = "This message's text is too large to preview. Open it in Gmail."
                break
            if len(encoded) > 1_400_000:
                raise HTTPException(413, "This message is too large. Open it in Gmail.")
            try:
                raw = base64.b64decode(
                    encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
                )
                content_type = Message(policy=policy.default)
                content_type["Content-Type"] = part_headers.get(
                    "content-type", "text/plain; charset=utf-8"
                )
                try:
                    text = raw.decode(
                        content_type.get_content_charset() or "utf-8", errors="replace"
                    )
                except LookupError:
                    text = raw.decode("utf-8", errors="replace")
            except (ValueError, TypeError):
                raise HTTPException(502, "Gmail returned an invalid message.") from None
            break
        pending.extend((child, depth + 1) for child in reversed(part.get("parts", [])))
    return {
        "id": data["id"],
        "from": headers.get("from", ""),
        "subject": headers.get("subject", "(No subject)"),
        "body": text,
    }


def encode_draft(sender: str, to: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
