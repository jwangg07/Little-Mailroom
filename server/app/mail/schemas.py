"""Validation for mail HTTP requests."""

from pydantic import BaseModel, EmailStr, Field, field_validator


class MailDraft(BaseModel):
    to: EmailStr
    subject: str = Field(max_length=998)
    body: str = Field(min_length=1, max_length=100_000)

    @field_validator("subject", "to")
    @classmethod
    def no_header_injection(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Mail headers must be a single line")
        return value
