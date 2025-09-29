import time

from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/api/resource")
async def get_resource():
    return {"message": "Hello from backend"}


@app.get("/health")
async def health():
    return {"status": "ok"}
