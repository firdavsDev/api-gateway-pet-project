import time

from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/api/resource")
async def resource(request: Request):
    # simulate small processing
    ts = time.time()
    return {"ok": True, "ts": ts, "service": "backend"}


@app.get("/health")
async def health():
    return {"status": "ok"}
