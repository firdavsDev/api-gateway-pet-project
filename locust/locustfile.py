import os

import httpx
import requests

from locust import HttpUser, between, task

GATEWAY_URL = os.getenv("LOCUST_HOST", "http://gateway:8080")
AUTH_URL = os.getenv("AUTH_URL", "http://auth:8001/token")
TOKEN = None


class GatewayUser(HttpUser):
    wait_time = between(0.01, 0.1)  # 10-100ms between requests

    def on_start(self):
        global TOKEN
        resp = httpx.get(AUTH_URL)
        TOKEN = resp.json()["access_token"]

    @task
    def hit_resource(self):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        self.client.get(f"{GATEWAY_URL}/api/resource", headers=headers)
