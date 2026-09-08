"""JWT authentication for the API.

Single-user app: credentials come from AUTH_USERNAME / AUTH_PASSWORD in
backend/.env. A successful login returns an HS256 JWT; every other /api route
requires it as a Bearer token. The signing secret is auto-generated once into
backend/jwt_secret.key (gitignored) unless JWT_SECRET is set.

There is deliberately **no fallback password**. An earlier version fell back to
a hardcoded default when AUTH_PASSWORD was unset, which meant a missing or
renamed .env downgraded the app from "locked" to "open with a password printed
in the README" — and it did so silently, while still serving traffic. Now the
app refuses to start instead. A dashboard that won't boot is a visible problem;
one quietly serving on a published password is not.
"""
from __future__ import annotations

import datetime as dt
import os
import secrets
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

SECRET_PATH = Path(__file__).resolve().parent.parent / "jwt_secret.key"
TOKEN_TTL_HOURS = 24

DEFAULT_USERNAME = "deepak"

# The password that shipped in .env.example and the README. It is refused by
# name: anyone who copies the example file verbatim gets a hard failure rather
# than a working login whose password is public.
PUBLISHED_EXAMPLE_PASSWORD = "adviser@123"

# Long enough to be worth having; short enough to type on a phone. Below this
# the app still starts but says so loudly on every boot.
ADVISORY_MIN_LENGTH = 12

_bearer = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api/auth")


class AuthNotConfigured(RuntimeError):
    """Raised at startup when AUTH_PASSWORD is missing or is the public default."""


_SETUP_HELP = (
    "\n"
    "  Set a password in backend/.env:\n"
    "\n"
    "      AUTH_USERNAME=deepak\n"
    "      AUTH_PASSWORD=<your password>\n"
    "\n"
    "  Generate a strong one:\n"
    "      python3 -c \"import secrets,string; a=''.join(c for c in "
    "string.ascii_letters+string.digits if c not in 'O0Il1'); "
    "print('-'.join(''.join(secrets.choice(a) for _ in range(5)) for _ in range(3)))\"\n"
)


def auth_password() -> str:
    """The configured password, or raise with an actionable message."""
    pw = os.environ.get("AUTH_PASSWORD", "")
    if not pw:
        raise AuthNotConfigured(
            "AUTH_PASSWORD is not set — refusing to start rather than fall back "
            "to a default password." + _SETUP_HELP
        )
    if secrets.compare_digest(pw, PUBLISHED_EXAMPLE_PASSWORD):
        raise AuthNotConfigured(
            "AUTH_PASSWORD is still the example password from .env.example, "
            "which is published in this repo. Refusing to start." + _SETUP_HELP
        )
    return pw


def verify_configured() -> None:
    """Startup gate. Raises AuthNotConfigured; warns on a weak-but-valid password."""
    pw = auth_password()
    if len(pw) < ADVISORY_MIN_LENGTH:
        print(
            f"WARNING: AUTH_PASSWORD is only {len(pw)} characters. The dashboard "
            f"is reachable from your network — consider at least "
            f"{ADVISORY_MIN_LENGTH}.",
            flush=True,
        )


def _secret() -> str:
    env = os.environ.get("JWT_SECRET")
    if env:
        return env
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text().strip()
    generated = secrets.token_hex(32)
    SECRET_PATH.write_text(generated)
    return generated


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody):
    expected_user = os.environ.get("AUTH_USERNAME", DEFAULT_USERNAME)
    try:
        expected_pw = auth_password()
    except AuthNotConfigured as e:
        # Unreachable when started through main.py, which gates on startup.
        raise HTTPException(503, str(e).splitlines()[0]) from e
    user_ok = secrets.compare_digest(body.username.strip(), expected_user)
    pw_ok = secrets.compare_digest(body.password, expected_pw)
    if not (user_ok and pw_ok):
        raise HTTPException(401, "Invalid username or password.")
    token = jwt.encode(
        {
            "sub": body.username.strip(),
            "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=TOKEN_TTL_HOURS),
        },
        _secret(),
        algorithm="HS256",
    )
    return {"token": token, "expires_in": TOKEN_TTL_HOURS * 3600}


def require_auth(
    cred: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    """FastAPI dependency: validates the Bearer JWT, returns the username."""
    if cred is None:
        raise HTTPException(401, "Not authenticated.")
    try:
        payload = jwt.decode(cred.credentials, _secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired — please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid authentication token.")
    return payload["sub"]
