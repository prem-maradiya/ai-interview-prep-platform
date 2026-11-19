"""Social sign-in: every flow, with simulated providers (no real Google/Microsoft/GitHub calls)."""
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.oauth import TX_COOKIE
from app.main import app
from app.services import oauth as oauth_mod
from app.services.oauth import GitHubProvider, GoogleProvider, MicrosoftProvider, OAuthProvider, Profile, get_oauth_providers

pytestmark = pytest.mark.asyncio

# Profiles the fake identity provider returns, keyed by the authorization "code".
PROFILES = {
    "g-alice": Profile("google-111", "alice@acme.io", True, "Alice Google"),
    "g-alice-renamed": Profile("google-111", "alice.new@gmail.com", True, "Alice"),  # same person, new email
    "g-stranger": Profile("google-999", "stranger@acme.io", True, "Stranger"),
    "g-founder": Profile("google-222", "founder@newco.io", True, "Founder"),
    "g-unverified": Profile("google-333", "nobody@newco.io", False, "Unverified"),
    "ms-claims-alice": Profile("ms-444", "alice@acme.io", False, "Not Alice"),  # Microsoft email is unverified
    "ms-bob": Profile("ms-555", "bob@acme.io", False, "Bob MS"),
    "gh-carol": Profile("gh-666", "carol@acme.io", True, "Carol"),
}


class FakeProvider(OAuthProvider):
    def __init__(self, name: str, label: str) -> None:
        super().__init__("client-id", "client-secret")
        self.name, self.label = name, label
        self.authorize_url, self.token_url, self.scopes = f"https://{name}.idp.test/authorize", "", ["openid"]

    async def exchange(self, code, verifier, redirect_uri):
        assert len(verifier) > 40 and redirect_uri.endswith(f"/api/auth/oauth/{self.name}/callback")
        return {"access_token": code}

    async def profile(self, tokens):
        return PROFILES[tokens["access_token"]]


@pytest.fixture(autouse=True)
def fake_providers():
    fakes = {"google": FakeProvider("google", "Google"), "microsoft": FakeProvider("microsoft", "Microsoft"),
             "github": FakeProvider("github", "GitHub")}
    app.dependency_overrides[get_oauth_providers] = lambda: fakes
    yield
    app.dependency_overrides.pop(get_oauth_providers, None)


async def sign_in(client, provider, code, *, tamper_state=False, **params):
    """Run start -> (provider) -> callback. Returns the fragment the app receives, as a dict."""
    start = await client.get(f"/api/auth/oauth/{provider}/start", params=params)
    assert start.status_code == 302, start.text
    loc = urlparse(start.headers["location"])
    if loc.netloc.endswith("idp.test"):
        q = parse_qs(loc.query)
        assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0]
        state = q["state"][0] + ("x" if tamper_state else "")
        done = await client.get(f"/api/auth/oauth/{provider}/callback", params={"code": code, "state": state})
    else:
        done = start  # start itself redirected back to the app with an error
    assert done.status_code == 302 and "/oauth/complete#" in done.headers["location"]
    frag = parse_qs(urlparse(done.headers["location"]).fragment)
    return {k: unquote(v[0]) for k, v in frag.items()}


def auth(frag):
    return {"Authorization": f"Bearer {frag['access_token']}"}


async def invite(client, owner, **body):
    return (await client.post("/api/org/invites", json={"role": "candidate", **body}, headers=owner)).json()["code"]


# ---------- sign in ----------
async def test_providers_listed_and_unknown_disabled(client):
    names = [p["name"] for p in (await client.get("/api/auth/providers")).json()]
    assert names == ["google", "microsoft", "github"]
    assert (await client.get("/api/auth/oauth/facebook/start", params={"org_slug": "acme"})).status_code == 404


async def test_login_links_by_verified_email_then_by_identity(client, reg):
    await reg(client, "acme", "alice@acme.io")
    frag = await sign_in(client, "google", "g-alice", mode="login", org_slug="acme")
    me = (await client.get("/api/auth/me", headers=auth(frag))).json()
    assert me["email"] == "alice@acme.io" and me["role"] == "owner"
    # Later the Google account's email changes: the stable subject still finds the same user.
    frag2 = await sign_in(client, "google", "g-alice-renamed", mode="login", org_slug="acme")
    assert (await client.get("/api/auth/me", headers=auth(frag2))).json()["id"] == me["id"]
    ids = (await client.get("/api/auth/identities", headers=auth(frag2))).json()
    assert [i["provider"] for i in ids["identities"]] == ["google"] and ids["has_password"]


async def test_login_rejects_strangers_and_unverified_email_takeover(client, reg):
    await reg(client, "acme", "alice@acme.io")
    assert "no account" in (await sign_in(client, "google", "g-stranger", mode="login", org_slug="acme"))["error"].lower()
    # A Microsoft account claiming alice@acme.io must NOT get into Alice's account (email not verified).
    frag = await sign_in(client, "microsoft", "ms-claims-alice", mode="login", org_slug="acme")
    assert "error" in frag and "access_token" not in frag
    assert "No organization" in (await sign_in(client, "google", "g-alice", mode="login", org_slug="nope"))["error"]
    assert "short name" in (await sign_in(client, "google", "g-alice", mode="login", org_slug="BAD SLUG"))["error"]


async def test_csrf_state_and_cookie_are_required(client, reg):
    await reg(client, "acme", "alice@acme.io")
    assert "could not be verified" in (await sign_in(client, "google", "g-alice", tamper_state=True,
                                                     mode="login", org_slug="acme"))["error"]
    # A callback without the browser's sign-in cookie (e.g. a link sent by an attacker) is rejected.
    start = await client.get("/api/auth/oauth/google/start", params={"mode": "login", "org_slug": "acme"})
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        r = await other.get("/api/auth/oauth/google/callback", params={"code": "g-alice", "state": state})
    assert "expired" in unquote(urlparse(r.headers["location"]).fragment)
    # The provider reporting a cancelled sign-in.
    r = await client.get("/api/auth/oauth/google/callback", params={"error": "access_denied"})
    assert "cancelled" in unquote(urlparse(r.headers["location"]).fragment)
    # The cookie is HttpOnly, scoped to the OAuth path, and SameSite=Lax.
    raw = start.headers["set-cookie"].lower()
    assert TX_COOKIE in raw and "httponly" in raw and "path=/api/auth/oauth" in raw and "samesite=lax" in raw


async def test_deactivated_member_cannot_sign_in(client, reg):
    owner = await reg(client, "acme", "owner@acme.io")
    code = await invite(client, owner)
    frag = await sign_in(client, "github", "gh-carol", mode="invite", invite=code)
    carol = (await client.get("/api/auth/me", headers=auth(frag))).json()
    await client.patch(f"/api/org/members/{carol['id']}", json={"is_active": False}, headers=owner)
    assert "deactivated" in (await sign_in(client, "github", "gh-carol", mode="login", org_slug="acme"))["error"]


# ---------- new organization ----------
async def test_create_org_with_google_then_add_password(client):
    frag = await sign_in(client, "google", "g-founder", mode="signup", org_slug="newco", org_name="NewCo Bootcamp")
    me = (await client.get("/api/auth/me", headers=auth(frag))).json()
    assert me["role"] == "owner" and me["tenant"]["slug"] == "newco" and me["full_name"] == "Founder"
    # Social-only account: password login doesn't work until a password is set.
    login = {"org_slug": "newco", "email": "founder@newco.io", "password": "password123"}
    assert (await client.post("/api/auth/login", json=login)).status_code == 401
    assert not (await client.get("/api/auth/identities", headers=auth(frag))).json()["has_password"]
    assert (await client.post("/api/auth/password", json={"new_password": "password123"}, headers=auth(frag))).status_code == 204
    assert (await client.post("/api/auth/login", json=login)).status_code == 200
    # Now a current password is required to change it.
    assert (await client.post("/api/auth/password", json={"new_password": "another-pass"}, headers=auth(frag))).status_code == 400
    # Slug taken; unverified email can't create an org.
    assert "taken" in (await sign_in(client, "google", "g-alice", mode="signup", org_slug="newco", org_name="Dup"))["error"]
    assert "verified email" in (await sign_in(client, "google", "g-unverified", mode="signup",
                                              org_slug="other", org_name="Other"))["error"]


# ---------- invites ----------
async def test_join_with_invite_rules(client, reg):
    owner = await reg(client, "acme", "owner@acme.io")
    code = await invite(client, owner)
    frag = await sign_in(client, "github", "gh-carol", mode="invite", invite=code)
    me = (await client.get("/api/auth/me", headers=auth(frag))).json()
    assert me["email"] == "carol@acme.io" and me["role"] == "candidate" and me["full_name"] == "Carol"
    assert "invalid or has expired" in (await sign_in(client, "google", "g-stranger", mode="invite", invite=code))["error"]

    locked = await invite(client, owner, email="dave@acme.io")
    assert "different email" in (await sign_in(client, "google", "g-stranger", mode="invite", invite=locked))["error"]
    # Microsoft emails aren't verified, so they can't satisfy an email-locked invite.
    locked_bob = await invite(client, owner, email="bob@acme.io")
    assert "different email" in (await sign_in(client, "microsoft", "ms-bob", mode="invite", invite=locked_bob))["error"]
    # ...but an open invite works with Microsoft.
    frag = await sign_in(client, "microsoft", "ms-bob", mode="invite", invite=await invite(client, owner))
    assert (await client.get("/api/auth/me", headers=auth(frag))).json()["email"] == "bob@acme.io"


async def test_same_google_account_in_two_orgs(client, reg):
    a = await reg(client, "alpha", "owner@alpha.io")
    b = await reg(client, "beta", "owner@beta.io")
    f1 = await sign_in(client, "github", "gh-carol", mode="invite", invite=await invite(client, a))
    f2 = await sign_in(client, "github", "gh-carol", mode="invite", invite=await invite(client, b))
    t1 = (await client.get("/api/auth/me", headers=auth(f1))).json()["tenant"]["slug"]
    t2 = (await client.get("/api/auth/me", headers=auth(f2))).json()["tenant"]["slug"]
    assert {t1, t2} == {"alpha", "beta"}
    # Signing in picks the user of the org you name.
    f3 = await sign_in(client, "github", "gh-carol", mode="login", org_slug="beta")
    assert (await client.get("/api/auth/me", headers=auth(f3))).json()["tenant"]["slug"] == "beta"


# ---------- linking from account settings ----------
async def test_link_and_unlink(client, reg):
    owner = await reg(client, "acme", "owner@acme.io")
    ticket = (await client.post("/api/auth/oauth/link-ticket", headers=owner)).json()["ticket"]
    frag = await sign_in(client, "microsoft", "ms-bob", mode="link", ticket=ticket)
    assert frag.get("linked") == "microsoft"
    # Now that Microsoft login is explicitly linked, it signs the owner in (no email matching involved).
    login = await sign_in(client, "microsoft", "ms-bob", mode="login", org_slug="acme")
    assert (await client.get("/api/auth/me", headers=auth(login))).json()["email"] == "owner@acme.io"
    assert "expired" in (await sign_in(client, "google", "g-alice", mode="link", ticket="garbage"))["error"]

    # Another member can't link a login that's already someone else's.
    code = await invite(client, owner)
    carol = await sign_in(client, "github", "gh-carol", mode="invite", invite=code)
    t2 = (await client.post("/api/auth/oauth/link-ticket", headers=auth(carol))).json()["ticket"]
    assert "another member" in (await sign_in(client, "microsoft", "ms-bob", mode="link", ticket=t2))["error"]

    # Carol is social-only with one login: unlinking it would lock her out.
    ident = (await client.get("/api/auth/identities", headers=auth(carol))).json()["identities"][0]
    assert (await client.delete(f"/api/auth/identities/{ident['id']}", headers=auth(carol))).status_code == 409
    await client.post("/api/auth/password", json={"new_password": "password123"}, headers=auth(carol))
    assert (await client.delete(f"/api/auth/identities/{ident['id']}", headers=auth(carol))).status_code == 204
    assert (await client.delete(f"/api/auth/identities/{ident['id']}", headers=owner)).status_code == 404


# ---------- real provider adapters (HTTP mocked) ----------
async def test_real_provider_urls_and_profiles(monkeypatch):
    g = GoogleProvider("gid", "gsecret")
    url = urlparse(g.authorization_url("st4te", "chal", "http://localhost:3080/api/auth/oauth/google/callback"))
    q = parse_qs(url.query)
    assert url.netloc == "accounts.google.com" and q["scope"] == ["openid email profile"]
    assert q["state"] == ["st4te"] and q["code_challenge"] == ["chal"] and q["client_id"] == ["gid"]
    assert "gsecret" not in url.geturl()  # the secret never goes to the browser

    class Resp:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    async def fake_get(self, url, token):
        return Resp({
            "https://api.github.com/user": {"id": 42, "login": "octo", "name": None},
            "https://api.github.com/user/emails": [
                {"email": "old@x.io", "primary": False, "verified": True},
                {"email": "Octo@X.io", "primary": True, "verified": True}],
            "https://graph.microsoft.com/oidc/userinfo": {"sub": "m1", "email": "m@x.io", "name": "M"},
            "https://openidconnect.googleapis.com/v1/userinfo": {"sub": "g1", "email": "G@x.io", "email_verified": True},
        }[url])

    monkeypatch.setattr(oauth_mod.OAuthProvider, "_get", fake_get)
    gh = await GitHubProvider("a", "b").profile({"access_token": "t"})
    assert (gh.subject, gh.email, gh.email_verified, gh.name) == ("42", "octo@x.io", True, "octo")
    ms = await MicrosoftProvider("a", "b").profile({"access_token": "t"})
    assert ms.email == "m@x.io" and ms.email_verified is False
    go = await g.profile({"access_token": "t"})
    assert go.email == "g@x.io" and go.email_verified
    assert "login.microsoftonline.com/common/" in MicrosoftProvider("a", "b").authorize_url
