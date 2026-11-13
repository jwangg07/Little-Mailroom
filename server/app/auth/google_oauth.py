"""Google OAuth flow construction, token exchange, and identity verification."""

import logging
from collections.abc import Callable
from functools import partial

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException

from app.auth.models import PendingLogin, User
from app.config import Settings

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",
]
logger = logging.getLogger(__name__)


class GoogleSignInError(Exception):
    """Google sign-in failed; provider details stay out of the HTTP response."""


class GoogleOAuth:
    def __init__(self, settings: Settings, get_secret: Callable[[], str]):
        self.settings = settings
        self.get_secret = get_secret

    def make_flow(self, verifier: str) -> Flow:
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.get_secret(),
                    "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=SCOPES,
            redirect_uri=self.settings.google_redirect_uri,
            code_verifier=verifier,
        )

    def authorization_url(
        self, state: str, verifier: str, nonce: str, reconnect: bool
    ) -> str:
        flow = self.make_flow(verifier)
        url, _ = flow.authorization_url(
            state=state,
            nonce=nonce,
            access_type="offline",
            prompt="consent select_account" if reconnect else "select_account",
        )
        return url

    def exchange(self, code: str, attempt: PendingLogin) -> tuple[User, Credentials]:
        stage = "flow_setup"
        try:
            flow = self.make_flow(attempt.verifier)
            stage = "token_exchange"
            flow.fetch_token(code=code, timeout=15)
            stage = "id_token_verification"
            claims = id_token.verify_oauth2_token(
                flow.credentials.id_token,
                partial(GoogleRequest(), timeout=15),
                self.settings.google_client_id,
            )
            stage = "identity_claims"
            if claims.get("nonce") != attempt.nonce or not claims.get("email_verified"):
                raise ValueError("Unverified identity")
            user = {
                "id": claims["sub"],
                "email": claims["email"],
                "name": claims.get("name", claims["email"]),
            }
            return user, flow.credentials
        except (
            GoogleAuthError,
            OAuth2Error,
            RequestException,
            ValueError,
            KeyError,
            Warning,
        ) as exc:
            # Exception messages, tracebacks and callback URLs can contain tokens.
            logger.warning(
                "Google sign-in failed stage=%s exception_type=%s",
                stage,
                type(exc).__name__,
            )
            raise GoogleSignInError from None
