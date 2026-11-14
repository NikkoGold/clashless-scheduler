"""Google Gemini (google-genai SDK), the recommended provider: native JSON-schema output."""
from scheduler.parsing.providers import MAX_OUTPUT_TOKENS, import_sdk


def _least_thinking(model: str) -> dict:
    """Gemini 2.x turns thinking off with a zero budget; Gemini 3+ rejects that and takes a level instead."""
    if model.removeprefix("models/").startswith("gemini-2"):
        return {"thinking_budget": 0}
    return {"thinking_level": "minimal"}


class GeminiProvider:
    name = "gemini"
    counts_toward_budget = True

    def __init__(self, api_key: str, model: str, client=None):
        if client is None:
            client = import_sdk("gemini", "google.genai").Client(api_key=api_key)
        self.client = client
        self.model = model
        self.model_version = None

    def complete(self, system: str, user: str, schema: dict) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=user,
            config={
                "system_instruction": system,
                "response_mime_type": "application/json",
                "response_json_schema": schema,
                "temperature": 0,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "thinking_config": _least_thinking(self.model),  # thinking costs output tokens and isn't needed
            },
        )
        self.model_version = getattr(response, "model_version", None)
        return response.text
