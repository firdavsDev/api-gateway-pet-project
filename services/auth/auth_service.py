import os
import time

import jwt
from fastapi import FastAPI

JWT_SECRET = os.getenv("JWT_SECRET", "supersecret123")
app = FastAPI()


@app.post("/token")
async def get_token(client_id: str = "test-client", ttl: int = 300):
    now = int(time.time())
    payload = {
        "sub": client_id,
        "iat": now,
        "exp": now + ttl,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return {"access_token": token, "expires_in": ttl}


@app.get("/health")
async def health():
    return {"status": "ok"}
