"""Social sign-in providers (OAuth 2.0 authorization-code flow with PKCE).

Each provider turns an authorization code into a verified profile: a stable `subject` (the
provider's user id), an email, and whether the provider has *verified* that email.

`email_verified` matters: accounts are only matched by email when the provider vouches for it.
Microsoft's `email` claim is not verified (anyone can put any address on a Microsoft account),
so Microsoft sign-ins never auto-link by email; they need an invite or an explicit link.
"""
import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

HTTP_TIMEOUT = 10.0


class OAuthError(RuntimeError):
    """Something went wrong talking to the provider; the message is safe to show the user."""


@dataclass
class Profile:
    subject: str
    email: str | None
    email_verified: bool
    name: str


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class OAuthProvider:
    name: str
    label: str
    authorize_url: str
    token_url: str
    scopes: list[str]

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret

    def authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str:
        params = {
            "client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": " ".join(self.scopes), "state": state,
            "code_challenge": code_challenge, "code_challenge_method": "S256",
            **self.extra_authorize_params(),
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    def extra_authorize_params(self) -> dict:
        return {}

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> dict:
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "client_id": self.client_id, "client_secret": self.client_secret, "code_verifier": verifier}
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                r = await client.post(self.token_url, data=data, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise OAuthError(f"Couldn't reach {self.label}. Please try again.") from e
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or "access_token" not in body:
            raise OAuthError(f"{self.label} sign-in failed ({body.get('error', r.status_code)}). Please try again.")
        return body

    async def _get(self, url: str, access_token: str) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                r = await client.get(url, headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
        except httpx.HTTPError as e:
            raise OAuthError(f"Couldn't reach {self.label}. Please try again.") from e
        if r.status_code != 200:
            raise OAuthError(f"Couldn't read your {self.label} profile.")
        return r

    async def profile(self, tokens: dict) -> Profile:
        raise NotImplementedError


class GoogleProvider(OAuthProvider):
    name, label = "google", "Google"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    userinfo_url = "https://openidconnect.googleapis.com/v1/userinfo"
    scopes = ["openid", "email", "profile"]

    def extra_authorize_params(self) -> dict:
        return {"prompt": "select_account"}

    async def profile(self, tokens: dict) -> Profile:
        info = (await self._get(self.userinfo_url, tokens["access_token"])).json()
        return Profile(subject=str(info["sub"]), email=(info.get("email") or "").lower() or None,
                       email_verified=bool(info.get("email_verified")), name=info.get("name") or "")


class MicrosoftProvider(OAuthProvider):
    name, label = "microsoft", "Microsoft"
    scopes = ["openid", "email", "profile", "User.Read"]
    userinfo_url = "https://graph.microsoft.com/oidc/userinfo"

    def __init__(self, client_id: str, client_secret: str, tenant: str = "common") -> None:
        super().__init__(client_id, client_secret)
        base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
        self.authorize_url, self.token_url = f"{base}/authorize", f"{base}/token"

    def extra_authorize_params(self) -> dict:
        return {"prompt": "select_account"}

    async def profile(self, tokens: dict) -> Profile:
        info = (await self._get(self.userinfo_url, tokens["access_token"])).json()
        email = (info.get("email") or info.get("preferred_username") or "").lower() or None
        # Microsoft doesn't verify this address, so it's never used to match existing accounts.
        return Profile(subject=str(info["sub"]), email=email, email_verified=False, name=info.get("name") or "")


class GitHubProvider(OAuthProvider):
    name, label = "github", "GitHub"
    authorize_url = "https://github.com/login/oauth/authorize"
    token_url = "https://github.com/login/oauth/access_token"
    scopes = ["read:user", "user:email"]

    async def profile(self, tokens: dict) -> Profile:
        user = (await self._get("https://api.github.com/user", tokens["access_token"])).json()
        emails = (await self._get("https://api.github.com/user/emails", tokens["access_token"])).json()
        primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
        return Profile(subject=str(user["id"]), email=primary["email"].lower() if primary else None,
                       email_verified=primary is not None, name=user.get("name") or user.get("login") or "")


def configured_providers() -> dict[str, OAuthProvider]:
    s = get_settings()
    out: dict[str, OAuthProvider] = {}
    if s.google_client_id and s.google_client_secret:
        out["google"] = GoogleProvider(s.google_client_id, s.google_client_secret)
    if s.microsoft_client_id and s.microsoft_client_secret:
        out["microsoft"] = MicrosoftProvider(s.microsoft_client_id, s.microsoft_client_secret, s.microsoft_tenant)
    if s.github_client_id and s.github_client_secret:
        out["github"] = GitHubProvider(s.github_client_id, s.github_client_secret)
    return out


def get_oauth_providers() -> dict[str, OAuthProvider]:
    """FastAPI dependency (overridden in tests with simulated providers)."""
    return configured_providers()


def redirect_uri(provider: str) -> str:
    return f"{get_settings().public_url.rstrip('/')}/api/auth/oauth/{provider}/callback"
