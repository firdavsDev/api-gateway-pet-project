import asyncio
import os
import time
from typing import Dict

import httpx
import jwt
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

# Config from env
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
JWT_SECRET = os.getenv("JWT_SECRET", "supersecret123")
GLOBAL_RATE = int(os.getenv("GLOBAL_RATE", "200"))  # global RPS per api_key (demo)
LOCAL_RATE = int(os.getenv("LOCAL_RATE", "50"))  # tokens/sec per process

app = FastAPI(title="Demo API Gateway")

redis = None
http_client = httpx.AsyncClient(
    timeout=10.0, limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
)


# --- local token bucket (per API key in this process) ---
class LocalTokenBucket:
    def __init__(self, rate: int, capacity: int = None):
        self.rate = rate
        self.capacity = capacity or rate
        self.tokens = self.capacity
        self.last = time.monotonic()
        self.lock = asyncio.Lock()

    async def consume(self, n: int = 1) -> bool:
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last
            refill = elapsed * self.rate
            if refill > 0:
                self.tokens = min(self.capacity, self.tokens + refill)
                self.last = now
            if self.tokens >= n:
                self.tokens -= n
                return True
            return False


# store per-api_key
local_buckets: Dict[str, LocalTokenBucket] = {}


async def get_local_bucket(api_key: str) -> LocalTokenBucket:
    b = local_buckets.get(api_key)
    if not b:
        b = LocalTokenBucket(rate=LOCAL_RATE, capacity=LOCAL_RATE * 2)
        local_buckets[api_key] = b
    return b


# --- global simple fixed-window limiter using Redis INCR ---
async def global_allow(api_key: str) -> bool:
    # fixed-window per second (demo). Key: rate:{api_key}:{epoch_second}
    now_s = int(time.time())
    key = f"rate:{api_key}:{now_s}"
    # atomic increment with expiration
    val = await redis.incr(key)
    if val == 1:
        await redis.expire(key, 2)
    if val > GLOBAL_RATE:
        return False
    return True


# --- JWT validation (HS256) ---
def validate_jwt(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        # Expect payload contains sub or api_key
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )


@app.on_event("startup")
async def startup():
    global redis
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)


@app.on_event("shutdown")
async def shutdown():
    await http_client.aclose()
    if redis:
        await redis.close()


@app.middleware("http")
async def auth_and_rate_middleware(request: Request, call_next):
    # Bypass for health or non-proxied endpoints
    if request.url.path in ("/health", "/openapi.json", "/docs"):
        return await call_next(request)

    auth = request.headers.get("authorization")
    if not auth or not auth.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Missing auth token"})
    token = auth.split(" ", 1)[1].strip()
    try:
        payload = validate_jwt(token)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})

    api_key = payload.get("sub") or payload.get("api_key") or "unknown"

    # Local bucket
    bucket = await get_local_bucket(api_key)
    allowed_local = await bucket.consume(1)
    if not allowed_local:
        return JSONResponse(
            status_code=429, content={"detail": "local rate limit exceeded"}
        )

    # Global limiter
    allowed_global = await global_allow(api_key)
    if not allowed_global:
        return JSONResponse(
            status_code=429, content={"detail": "global rate limit exceeded"}
        )

    # attach payload to request state for handlers
    request.state.jwt = payload
    return await call_next(request)


# --- proxy endpoint to backend ---
@app.get("/api/resource")
async def proxy_resource(request: Request):
    # forward headers (except host) and keep connection pooled
    headers = {}
    for k, v in request.headers.items():
        if k.lower() != "host":
            headers[k] = v
    try:
        resp = await http_client.get(f"{BACKEND_URL}/api/resource", headers=headers)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="backend unreachable")
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/health")
async def health():
    return {"status": "ok"}
