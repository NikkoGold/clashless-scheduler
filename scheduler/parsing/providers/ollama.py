"""Ollama, a local model server: its chat API with `format` set to the schema. Local, so not counted in the daily limit."""
import httpx

from scheduler.parsing.providers import MAX_OUTPUT_TOKENS

TIMEOUT = 120  # local models on a laptop can be slow


class OllamaProvider:
    name = "ollama"
    counts_toward_budget = False

    def __init__(self, url: str, model: str, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.model = model
        self.client = client or httpx.Client(timeout=TIMEOUT)
        self.model_version = None

    def complete(self, system: str, user: str, schema: dict) -> str:
        response = self.client.post(f"{self.url}/api/chat", json={
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0, "num_predict": MAX_OUTPUT_TOKENS},
        })
        response.raise_for_status()
        data = response.json()
        self.model_version = data.get("model")
        return data["message"]["content"]
