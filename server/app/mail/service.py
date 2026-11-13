"""User actions: load an inbox, open a message, and send a draft."""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from app.mail.formatting import encode_draft, mail_message, mail_summary
from app.mail.gmail_client import GmailClient


class GmailService:
    def __init__(self, client: GmailClient):
        self.client = client

    def inbox(self, user_id: str, page_token: str = "") -> dict:
        page = self.client.request(
            user_id,
            "GET",
            "/messages",
            params={
                "labelIds": "INBOX",
                "maxResults": 20,
                "pageToken": page_token,
            },
        )

        def summary(item):
            message = self.client.request(
                user_id,
                "GET",
                "/messages/" + quote(item["id"], safe=""),
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
            )
            return mail_summary(message)

        with ThreadPoolExecutor(max_workers=5) as pool:
            messages = list(pool.map(summary, page.get("messages", [])[:20]))
        return {"messages": messages, "nextPageToken": page.get("nextPageToken")}

    def message(self, user_id: str, message_id: str) -> dict:
        data = self.client.request(
            user_id,
            "GET",
            "/messages/" + quote(message_id, safe=""),
            params={"format": "full"},
        )
        return mail_message(data)

    def send(self, user_id: str, sender: str, to: str, subject: str, body: str) -> dict:
        data = self.client.request(
            user_id,
            "POST",
            "/messages/send",
            json={"raw": encode_draft(sender, to, subject, body)},
        )
        return {"id": data["id"], "threadId": data.get("threadId")}
