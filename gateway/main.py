import asyncio
import os
import time

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import JSONResponse, Response

app = FastAPI()

# ENV VARS
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
JWT_SECRET = os.getenv("JWT_SECRET", "supersecretkey")
GLOBAL_RATE = int(os.getenv("GLOBAL_RATE", "100"))  # req/sec
# === Prometheus Metrics ===
REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "endpoint", "http_status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "Request latency (s)", ["endpoint"]
)
RATE_LIMITED_COUNT = Counter(
    "http_rate_limited_total",
    "Number of requests rejected due to rate limiting",
    ["endpoint"],
)
# Create global Redis connection instance (async)
redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

# Local token bucket
RATE_WINDOW = 1.0  # 1 second
local_tokens = GLOBAL_RATE
last_refill = time.monotonic()


shutdown_event = asyncio.Event()


@app.on_event("startup")
async def startup_event():
    print("✅ Gateway starting up...")


@app.on_event("shutdown")
async def shutdown_event_handler():
    print("🛑 Gateway shutting down gracefully...")
    await redis_client.close()


async def verify_jwt(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def rate_limiter(api_key: str):
    global local_tokens, last_refill

    now = time.monotonic()
    elapsed = now - last_refill
    refill = elapsed * GLOBAL_RATE
    if refill >= 1:
        local_tokens = min(GLOBAL_RATE, local_tokens + int(refill))
        last_refill = now

    if local_tokens <= 0:
        raise HTTPException(status_code=429, detail="Too many requests (local limit)")
    local_tokens -= 1

    # Use redis_client directly — it's already a connected instance
    key = f"rate:{api_key}:{int(now)}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, 1)

    if count > GLOBAL_RATE:
        raise HTTPException(status_code=429, detail="Too many requests (global limit)")


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        process_time = time.perf_counter() - start_time
        endpoint = request.url.path
        status_code = response.status_code if response else 500

        REQUEST_COUNT.labels(request.method, endpoint, status_code).inc()
        REQUEST_LATENCY.labels(endpoint).observe(process_time)


@app.middleware("http")
async def auth_and_rate_limit(request: Request, call_next):
    if request.url.path.startswith("/health") or request.url.path.startswith(
        "/metrics"
    ):
        return await call_next(request)

    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Missing token"})

    token = auth_header.split(" ", 1)[1]
    payload = await verify_jwt(token)
    api_key = payload.get("sub")
    if not api_key:
        return JSONResponse(
            status_code=401, content={"detail": "Invalid token payload"}
        )

    try:
        await rate_limiter(api_key)
    except HTTPException as e:
        # ✅ Properly return JSON 429 instead of blowing up
        if e.status_code == 429:
            RATE_LIMITED_COUNT.labels(request.url.path).inc()
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})

    # Proxy to backend
    try:
        async with httpx.AsyncClient() as client:
            backend_resp = await client.request(
                request.method,
                f"{BACKEND_URL}{request.url.path}",
                headers=request.headers.raw,
                content=await request.body(),
            )
            return JSONResponse(
                status_code=backend_resp.status_code, content=backend_resp.json()
            )
    except Exception as e:
        # Catch backend errors and still return something nice
        return JSONResponse(status_code=502, content={"detail": str(e)})


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    return {"status": "ok"}
