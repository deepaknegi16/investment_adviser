"""Cache abstraction: Redis when configured, in-process dicts otherwise.

The app ran on five module-level dictionaries. That is the right answer for one
uvicorn process and the wrong one the moment there are several, because each
worker then keeps its own copy and re-fetches everything the others already
have. This session produced direct evidence of why that matters: doubling the
request volume to Yahoo made it start dropping symbols from batches, and N
workers with private caches multiply request volume by N.

A restart also threw everything away, so the first page load after every deploy
paid the full cold-fetch cost.

Interface is deliberately tiny — get / set / lock. Anything richer would be
carrying Redis semantics into call sites that should not care which backend is
running.

SERIALISATION: values include pandas Series, so the Redis backend pickles.
Pickle deserialises arbitrary objects, which is safe here only because the app
is the sole writer to this Redis and it is not exposed outside the compose
network. Do not point REDIS_URL at a shared or public instance.
"""
from __future__ import annotations

import os
import pickle
import threading
import time
from typing import Any, Callable, Optional

_PREFIX = "adviser:v1:"


class _MemoryBackend:
    """Per-process dict with TTLs — the single-worker default."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            expires, value = entry
            if expires < time.time():
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl, value)

    def acquire(self, key: str, ttl: int) -> bool:
        with self._lock:
            entry = self._data.get(key)
            if entry and entry[0] > time.time():
                return False
            self._data[key] = (time.time() + ttl, True)
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def incr(self, key: str, ttl: int) -> int:
        with self._lock:
            entry = self._data.get(key)
            now = time.time()
            if entry and entry[0] > now:
                count = entry[1] + 1
                self._data[key] = (entry[0], count)  # keep the original window
                return count
            self._data[key] = (now + ttl, 1)
            return 1

    @property
    def kind(self) -> str:
        return "memory"


class _RedisBackend:
    """Shared across workers and survives restarts."""

    def __init__(self, client) -> None:
        self._r = client

    def get(self, key: str) -> Optional[Any]:
        try:
            raw = self._r.get(key)
        except Exception:
            return None      # a cache miss is always safe; an exception is not
        if raw is None:
            return None
        try:
            return pickle.loads(raw)
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int) -> None:
        try:
            self._r.setex(key, ttl, pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))
        except Exception:
            pass             # caching is an optimisation, never a dependency

    def acquire(self, key: str, ttl: int) -> bool:
        try:
            return bool(self._r.set(key, b"1", nx=True, ex=ttl))
        except Exception:
            return True      # if the lock store is down, do the work rather than skip it

    def release(self, key: str) -> None:
        try:
            self._r.delete(key)
        except Exception:
            pass

    def incr(self, key: str, ttl: int) -> int:
        try:
            pipe = self._r.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl, nx=True)   # only set the window on first hit
            count, _ = pipe.execute()
            return int(count)
        except Exception:
            return 0         # fail open — never lock a user out on a cache outage

    @property
    def kind(self) -> str:
        return "redis"


_backend = None
_init_lock = threading.Lock()


def backend():
    global _backend
    if _backend is not None:
        return _backend
    with _init_lock:
        if _backend is not None:
            return _backend
        url = os.environ.get("REDIS_URL")
        if url:
            try:
                import redis  # imported lazily so the dependency stays optional

                client = redis.Redis.from_url(url, socket_timeout=2,
                                              socket_connect_timeout=2)
                client.ping()
                _backend = _RedisBackend(client)
            except Exception:
                # Redis configured but unreachable: degrade to memory rather than
                # refuse to serve. The status endpoint reports which is live.
                _backend = _MemoryBackend()
        else:
            _backend = _MemoryBackend()
    return _backend


def get(namespace: str, key: str) -> Optional[Any]:
    return backend().get(f"{_PREFIX}{namespace}:{key}")


def set(namespace: str, key: str, value: Any, ttl: int) -> None:  # noqa: A001
    backend().set(f"{_PREFIX}{namespace}:{key}", value, ttl)


def get_or_set(namespace: str, key: str, ttl: int, producer: Callable[[], Any]) -> Any:
    hit = get(namespace, key)
    if hit is not None:
        return hit
    value = producer()
    if value is not None:
        set(namespace, key, value, ttl)
    return value


def acquire(namespace: str, key: str, ttl: int) -> bool:
    """Best-effort distributed lock, so N workers do one piece of work once."""
    return backend().acquire(f"{_PREFIX}lock:{namespace}:{key}", ttl)


def release(namespace: str, key: str) -> None:
    backend().release(f"{_PREFIX}lock:{namespace}:{key}")


def incr(namespace: str, key: str, ttl: int) -> int:
    """Counter within a fixed window — the primitive behind rate limiting."""
    return backend().incr(f"{_PREFIX}{namespace}:{key}", ttl)


def status() -> dict:
    b = backend()
    return {"backend": b.kind, "redis_url_set": bool(os.environ.get("REDIS_URL")),
            "shared_across_workers": b.kind == "redis"}
