import asyncio
import os
import time

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from jose import JWTError, jwt
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# ======== OTEL Tracing Setup ========
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)

otlp_exporter = OTLPSpanExporter(endpoint="http://tempo:4317", insecure=True)
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(
        otlp_exporter, max_export_batch_size=512, schedule_delay_millis=200
    )
)

# ======== FastAPI app ========
app = FastAPI(title="API Gateway", version="1.0.0")
FastAPIInstrumentor.instrument_app(app)

# ======== ENV VARS ========
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
JWT_SECRET = os.getenv("JWT_SECRET", "supersecretkey")
GLOBAL_RATE = int(os.getenv("GLOBAL_RATE", "10000"))  # req/sec

# ======== Metrics ========
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

# ======== Redis Client ========
redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

# ======== HTTPX Client ========
http_client = httpx.AsyncClient(timeout=5.0)

# ======== Local Token Bucket ========
RATE_WINDOW = 1.0
local_tokens = GLOBAL_RATE
last_refill = time.monotonic()

bucket_lock = asyncio.Lock()

# ======== JWT Cache ========
jwt_cache = {}  # {token: (payload, expire_time)}

shutdown_event = asyncio.Event()


# ======== Startup / Shutdown ========
@app.on_event("startup")
async def startup_event():
    print("✅ Gateway starting up...")


@app.on_event("shutdown")
async def shutdown_event_handler():
    print("🛑 Gateway shutting down gracefully...")
    await redis_client.close()
    await http_client.aclose()


# ======== JWT Verification ========
async def verify_jwt(token: str):
    now = time.time()
    # Check cache first
    cached = jwt_cache.get(token)
    if cached and cached[1] > now:
        return cached[0]

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        # Cache for 60 seconds
        jwt_cache[token] = (payload, now + 60)
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ======== Rate Limiter ========
async def rate_limiter(api_key: str):
    global local_tokens, last_refill
    async with bucket_lock:
        now = time.monotonic()
        elapsed = now - last_refill
        refill = elapsed * GLOBAL_RATE
        if refill >= 1:
            local_tokens = min(GLOBAL_RATE, local_tokens + int(refill))
            last_refill = now

        if local_tokens <= 0:
            raise HTTPException(
                status_code=429, detail="Too many requests (local limit)"
            )
        local_tokens -= 1

    # Redis global rate limit
    key = f"rate:{api_key}:{int(time.time())}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, 1)

    if count > GLOBAL_RATE:
        raise HTTPException(status_code=429, detail="Too many requests (global limit)")


# ======== Metrics Middleware ========
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.perf_counter()
    response: Response = None
    try:
        response = await call_next(request)
        return response
    finally:
        process_time = time.perf_counter() - start_time
        endpoint = request.url.path
        status_code = response.status_code if response else 500
        REQUEST_COUNT.labels(request.method, endpoint, status_code).inc()
        REQUEST_LATENCY.labels(endpoint).observe(process_time)


# ======== Auth & Rate Limit Middleware ========
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
        if e.status_code == 429:
            RATE_LIMITED_COUNT.labels(request.url.path).inc()
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})

    # Proxy to backend
    try:
        backend_resp = await http_client.request(
            request.method,
            f"{BACKEND_URL}{request.url.path}",
            headers=request.headers.raw,
            content=await request.body(),
        )
        # If backend returns non-JSON, fallback
        try:
            content = backend_resp.json()
        except Exception:
            content = backend_resp.text
        return JSONResponse(status_code=backend_resp.status_code, content=content)
    except Exception as e:
        return JSONResponse(status_code=502, content={"detail": str(e)})


# ======== Metrics Endpoint ========
@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ======== Health Endpoint ========
@app.get("/health")
async def health():
    return {"status": "ok"}
