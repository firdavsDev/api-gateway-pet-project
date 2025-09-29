import os

import requests

from locust import HttpUser, between, task

AUTH_URL = os.getenv("AUTH_URL", "http://auth:8001")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8080")


class GatewayUser(HttpUser):
    wait_time = between(0.001, 0.005)  # tweak to increase concurrency

    def on_start(self):
        # obtain token once per user
        r = requests.post(f"{AUTH_URL}/token", data={"client_id": "locust-user"})
        if r.status_code != 200:
            raise Exception("failed to get token")
        j = r.json()
        self.token = j["access_token"]

    @task
    def hit_resource(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        with self.client.get(
            "/api/resource", headers=headers, name="/api/resource", catch_response=True
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
            else:
                resp.success()
