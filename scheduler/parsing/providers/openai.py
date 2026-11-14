"""OpenAI: JSON-schema response format."""
from scheduler.parsing.providers import MAX_OUTPUT_TOKENS, import_sdk


class OpenAIProvider:
    name = "openai"
    counts_toward_budget = True

    def __init__(self, api_key: str, model: str, client=None):
        if client is None:
            client = import_sdk("openai", "openai").OpenAI(api_key=api_key)
        self.client = client
        self.model = model
        self.model_version = None

    def complete(self, system: str, user: str, schema: dict) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_schema", "json_schema": {"name": "constraints", "schema": schema}},
            temperature=0,
            max_completion_tokens=MAX_OUTPUT_TOKENS,
        )
        self.model_version = getattr(response, "model", None)
        return response.choices[0].message.content
