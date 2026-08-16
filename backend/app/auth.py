"""JWT authentication for the API.

Single-user app: credentials come from AUTH_USERNAME / AUTH_PASSWORD in
backend/.env (defaults documented in .env.example and README). A successful
login returns an HS256 JWT; every other /api route requires it as a Bearer
token. The signing secret is auto-generated once into backend/jwt_secret.key
(gitignored) unless JWT_SECRET is set.
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
DEFAULT_PASSWORD = "adviser@123"

_bearer = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api/auth")


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
    expected_pw = os.environ.get("AUTH_PASSWORD", DEFAULT_PASSWORD)
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
