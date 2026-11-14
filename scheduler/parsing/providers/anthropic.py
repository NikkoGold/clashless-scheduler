"""Anthropic Claude: structured output through one forced tool call."""
import json

from scheduler.parsing.providers import MAX_OUTPUT_TOKENS, import_sdk

TOOL = "record_constraints"


class AnthropicProvider:
    name = "anthropic"
    counts_toward_budget = True

    def __init__(self, api_key: str, model: str, client=None):
        if client is None:
            client = import_sdk("anthropic", "anthropic").Anthropic(api_key=api_key)
        self.client = client
        self.model = model
        self.model_version = None

    def complete(self, system: str, user: str, schema: dict) -> str:
        response = self.client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[{"name": TOOL, "description": "Record the constraints read from the user's text.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": TOOL},
            temperature=0,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        self.model_version = getattr(response, "model", None)
        for block in response.content:
            if block.type == "tool_use":
                return json.dumps(block.input)
        raise ValueError("the reply had no tool call")
