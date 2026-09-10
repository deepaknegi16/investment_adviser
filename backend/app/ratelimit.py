"""Request rate limiting, worker-shared when Redis is configured.

The gap this closes is specific: `/api/auth/login` had no throttling of any
kind, and the dashboard is now bound to the LAN so anyone on the Wi-Fi could
grind the password at whatever rate they liked. A single-user app with one
hand-set password is exactly the case where an unlimited login endpoint matters.

Two tiers, because they defend different things:

  login    5 attempts / 15 min per IP, counted only on FAILURE. Successful
           logins are free, so the owner is never locked out of their own
           dashboard by using it normally.
  api      240 requests / min per IP. Generous — the SPA polls the watchlist
           every 60 s and a page load fans out to several endpoints — so this
           is a runaway guard, not a quota.

Counting lives in cache.incr, so with Redis the limit is enforced across all
workers; without it, per process. The failure mode is deliberately open: if the
counter store is unreachable the request is allowed, because locking the owner
out of their own portfolio is a worse outcome than briefly not rate limiting.

nginx also rate limits at the edge (see nginx.conf). This layer exists because
the app must not depend on being deployed behind that proxy to be safe.
"""
from __future__ import annotations

import os
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from . import cache

LOGIN_MAX = int(os.environ.get("RATE_LIMIT_LOGIN_MAX", "5"))
LOGIN_WINDOW = int(os.environ.get("RATE_LIMIT_LOGIN_WINDOW", "900"))     # 15 min
API_MAX = int(os.environ.get("RATE_LIMIT_API_MAX", "240"))
API_WINDOW = int(os.environ.get("RATE_LIMIT_API_WINDOW", "60"))

LOGIN_PATH = "/api/auth/login"
EXEMPT = {"/api/health", "/api/ready"}


def client_ip(request: Request) -> str:
    """Real client IP, trusting X-Forwarded-For only from our own proxy.

    Behind nginx every request arrives from the proxy, so limiting on the socket
    address would put the whole world in one bucket. The header is only honoured
    when TRUST_PROXY is set, so a direct-to-uvicorn deployment cannot be spoofed
    by a client that simply sends the header itself.
    """
    if os.environ.get("TRUST_PROXY"):
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _too_many(retry_after: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": detail},
        headers={"Retry-After": str(retry_after)},
    )


async def middleware(request: Request, call_next: Callable):
    path = request.url.path
    if not path.startswith("/api") or path in EXEMPT:
        return await call_next(request)

    ip = client_ip(request)

    # --- general API ceiling ---
    count = cache.incr("rl:api", ip, API_WINDOW)
    if count > API_MAX:
        return _too_many(API_WINDOW,
                         f"Too many requests — limit is {API_MAX} per "
                         f"{API_WINDOW}s. Slow down and retry.")

    # --- login: check the failure counter BEFORE attempting ---
    if path == LOGIN_PATH and request.method == "POST":
        fails = cache.get("rl:login", ip) or 0
        if fails >= LOGIN_MAX:
            return _too_many(LOGIN_WINDOW,
                             f"Too many failed sign-ins. Try again in "
                             f"{LOGIN_WINDOW // 60} minutes.")
        response = await call_next(request)
        if response.status_code == 401:
            # Only failures count, so normal use never throttles the owner.
            cache.set("rl:login", ip, int(fails) + 1, LOGIN_WINDOW)
        elif response.status_code < 400:
            cache.set("rl:login", ip, 0, 1)   # clear on success
        return response

    return await call_next(request)


def status() -> dict:
    return {
        "login": {"max_failures": LOGIN_MAX, "window_seconds": LOGIN_WINDOW},
        "api": {"max_requests": API_MAX, "window_seconds": API_WINDOW},
        "shared_across_workers": cache.status()["shared_across_workers"],
        "trust_proxy_header": bool(os.environ.get("TRUST_PROXY")),
    }
