import json

import requests

from .ai_base import AIError, BaseAIClient


class AnthropicClient(BaseAIClient):
    URL = "https://api.anthropic.com/v1/messages"
    provider_id = "anthropic"
    provider_name = "Anthropic Claude"

    def __init__(self, api_key, model="claude-sonnet-5", session=None,
                 usage_callback=None, user_agent=None):
        session = session or requests.Session()
        super().__init__(api_key, model or "claude-sonnet-5", session=session,
                         usage_callback=usage_callback)
        self.session.headers.update({"User-Agent": user_agent or "curatr"})

    def _structured_request(self, instructions, user_text, schema, schema_name,
                            usage_kind, extra_input=None):
        del schema_name
        content = [str(user_text or "")]
        for item in extra_input or []:
            if isinstance(item, dict) and item.get("content"):
                content.append(str(item.get("content")))
        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "system": instructions,
            "messages": [{"role": "user", "content": "\n\n".join(content)}],
            "output_config": {
                "format": {"type": "json_schema", "schema": schema}
            },
        }
        try:
            response = self.session.post(
                self.URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
        except requests.RequestException as exc:
            raise AIError("Could not contact Anthropic Claude: %s" % exc)
        if response.status_code >= 400:
            raise AIError(self._api_error(response))
        try:
            data = response.json()
        except ValueError:
            raise AIError("Anthropic Claude returned a response that was not valid JSON.")
        self._report_usage(data, usage_kind)
        if data.get("stop_reason") == "refusal":
            raise AIError("Anthropic Claude declined the recommendation request.")
        text = "".join(
            str(block.get("text") or "") for block in data.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text:
            raise AIError("Anthropic Claude returned no usable structured data.")
        try:
            return json.loads(text)
        except (TypeError, ValueError) as exc:
            raise AIError("Anthropic Claude structured JSON could not be read: %s" % exc)

    def _report_usage(self, data, kind):
        usage = data.get("usage") or {} if isinstance(data, dict) else {}
        input_tokens = self._safe_int(usage.get("input_tokens"))
        cache_read = self._safe_int(usage.get("cache_read_input_tokens"))
        cache_create = self._safe_int(usage.get("cache_creation_input_tokens"))
        output_tokens = self._safe_int(usage.get("output_tokens"))
        self._emit_usage(
            kind,
            input_tokens=input_tokens + cache_read + cache_create,
            cached_input_tokens=cache_read,
            output_tokens=output_tokens,
            total_tokens=input_tokens + cache_read + cache_create + output_tokens,
        )

    @staticmethod
    def _api_error(response):
        message = "Anthropic Claude API request failed"
        try:
            body = response.json()
            error = body.get("error") or {} if isinstance(body, dict) else {}
            if isinstance(error, dict):
                message = error.get("message") or message
        except ValueError:
            if response.text:
                message = response.text[:500]
        if response.status_code in (401, 403):
            message = "Anthropic rejected the API key. Check it in curatr settings."
        elif response.status_code == 429:
            message = "Anthropic rate or usage limit reached. Try again later or check provider limits."
        return "%s (HTTP %s)" % (message, response.status_code)
