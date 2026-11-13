"""Browser identity, session, and pending OAuth records."""

from dataclasses import dataclass
from typing import TypedDict


class User(TypedDict):
    id: str
    email: str
    name: str


@dataclass(frozen=True)
class PendingLogin:
    verifier: str
    nonce: str
    expires_at: int


@dataclass(frozen=True)
class Session:
    user: User
    expires_at: int
    generation: str = ""
