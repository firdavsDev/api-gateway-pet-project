import os
from datetime import datetime, timedelta

from fastapi import FastAPI
from jose import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "supersecretkey")

app = FastAPI()


@app.get("/token")
async def generate_token(sub: str = "demo-client"):
    payload = {"sub": sub, "exp": datetime.utcnow() + timedelta(minutes=5)}
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer"}


@app.get("/health")
async def health():
    return {"status": "ok"}
