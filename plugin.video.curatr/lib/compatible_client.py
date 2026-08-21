import json
from urllib.parse import urlparse

import requests

from .ai_base import AIError, BaseAIClient


class CompatibleAIClient(BaseAIClient):
    """OpenAI Chat Completions compatible provider with strict local validation."""

    def __init__(self, api_key, model, base_url, provider_id="compatible",
                 provider_name="OpenAI-compatible", session=None,
                 usage_callback=None, user_agent=None):
        session = session or requests.Session()
        super().__init__(api_key, model, session=session, usage_callback=usage_callback)
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.base_url = self._normalise_base_url(base_url) if str(base_url or "").strip() else ""
        self.session.headers.update({"User-Agent": user_agent or "curatr"})

    @staticmethod
    def _normalise_base_url(value):
        url = str(value or "").strip().rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise AIError("The custom AI endpoint must be a complete HTTP or HTTPS URL.")
        if url.endswith("/chat/completions"):
            return url
        if url.endswith("/v1") or url.endswith("/api/v1"):
            return url + "/chat/completions"
        return url + "/v1/chat/completions"

    def _structured_request(self, instructions, user_text, schema, schema_name,
                            usage_kind, extra_input=None):
        if not self.base_url:
            raise AIError("The custom AI endpoint is not configured.")
        if not self.model:
            raise AIError("The custom AI model ID is not configured.")
        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_text},
        ]
        messages.extend(extra_input or [])
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        try:
            response = self.session.post(
                self.base_url,
                headers={"Authorization": "Bearer " + self.api_key,
                         "Content-Type": "application/json"},
                json=payload,
                timeout=120,
            )
        except requests.RequestException as exc:
            raise AIError("Could not contact %s: %s" % (self.provider_name, exc))
        if response.status_code >= 400:
            raise AIError(self._api_error(response))
        try:
            data = response.json()
        except ValueError:
            raise AIError("%s returned a response that was not valid JSON." % self.provider_name)
        self._report_usage(data, usage_kind)
        text = self._output_text(data)
        if not text:
            raise AIError("%s returned no usable structured data." % self.provider_name)
        try:
            return json.loads(self._strip_json_fence(text))
        except (TypeError, ValueError) as exc:
            raise AIError("%s structured JSON could not be read: %s" % (self.provider_name, exc))

    @staticmethod
    def _strip_json_fence(text):
        value = str(text or "").strip()
        if value.startswith("```") and value.endswith("```"):
            lines = value.splitlines()
            if len(lines) >= 3:
                value = "\n".join(lines[1:-1]).strip()
        return value

    def _output_text(self, data):
        choices = data.get("choices") or [] if isinstance(data, dict) else []
        if not choices or not isinstance(choices[0], dict):
            return ""
        choice = choices[0]
        error = choice.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise AIError(message or "%s could not complete the request." % self.provider_name)
        message = choice.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else ""
        if isinstance(content, list):
            return "".join(str(row.get("text") or "") for row in content if isinstance(row, dict))
        return str(content or "")

    def _report_usage(self, data, kind):
        usage = data.get("usage") or {} if isinstance(data, dict) else {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        self._emit_usage(
            kind,
            input_tokens=usage.get("prompt_tokens"),
            cached_input_tokens=prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else 0,
            output_tokens=usage.get("completion_tokens"),
            reasoning_tokens=completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else 0,
            total_tokens=usage.get("total_tokens"),
        )

    def _api_error(self, response):
        message = "%s API request failed" % self.provider_name
        try:
            body = response.json()
            error = body.get("error") or {} if isinstance(body, dict) else {}
            if isinstance(error, dict):
                message = error.get("message") or message
        except ValueError:
            if response.text:
                message = response.text[:500]
        if response.status_code in (401, 403):
            message = "%s rejected the API key. Check it in curatr settings." % self.provider_name
        elif response.status_code == 429:
            message = "%s rate or usage limit reached. Try again later or check provider limits." % self.provider_name
        return "%s (HTTP %s)" % (message, response.status_code)


class OpenRouterClient(CompatibleAIClient):
    def __init__(self, api_key, model, session=None, usage_callback=None, user_agent=None):
        super().__init__(
            api_key, model or "openai/gpt-5-mini",
            "https://openrouter.ai/api/v1",
            provider_id="openrouter", provider_name="OpenRouter",
            session=session, usage_callback=usage_callback, user_agent=user_agent,
        )
        self.session.headers.update({"X-OpenRouter-Title": "curatr"})
