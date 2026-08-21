import json

import requests

from .ai_base import AIError, BaseAIClient


class OpenAIClient(BaseAIClient):
    URL = "https://api.openai.com/v1/responses"
    provider_id = "openai"
    provider_name = "OpenAI"

    def __init__(self, api_key, model="gpt-5-mini", session=None, usage_callback=None, user_agent=None):
        session = session or requests.Session()
        super().__init__(api_key, model or "gpt-5-mini", session=session, usage_callback=usage_callback)
        self.session.headers.update({"User-Agent": user_agent or "curatr"})

    def _structured_request(
        self,
        instructions,
        user_text,
        schema,
        schema_name,
        usage_kind,
        extra_input=None,
    ):
        payload_input = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_text},
        ]
        if extra_input:
            payload_input.extend(extra_input)
        payload = {
            "model": self.model,
            "store": False,
            "input": payload_input,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        try:
            response = self.session.post(
                self.URL,
                headers={
                    "Authorization": "Bearer " + self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
        except requests.RequestException as exc:
            raise AIError("Could not contact OpenAI: %s" % exc)
        if response.status_code >= 400:
            raise AIError(self._api_error(response))
        try:
            data = response.json()
        except ValueError:
            raise AIError("OpenAI returned a response that was not valid JSON.")
        self._report_usage(data, usage_kind)
        if data.get("status") == "incomplete":
            details = data.get("incomplete_details") or {}
            reason = details.get("reason") if isinstance(details, dict) else details
            raise AIError("OpenAI response was incomplete%s." % (": %s" % reason if reason else ""))
        text = self._output_text(data)
        if not text:
            raise AIError("OpenAI returned no usable structured data.")
        try:
            return json.loads(text)
        except (TypeError, ValueError) as exc:
            raise AIError("OpenAI structured JSON could not be read: %s" % exc)

    def _report_usage(self, data, kind):
        usage = data.get("usage") or {} if isinstance(data, dict) else {}
        if not isinstance(usage, dict):
            return
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        self._emit_usage(
            kind,
            input_tokens=usage.get("input_tokens"),
            cached_input_tokens=input_details.get("cached_tokens") if isinstance(input_details, dict) else 0,
            output_tokens=usage.get("output_tokens"),
            reasoning_tokens=output_details.get("reasoning_tokens") if isinstance(output_details, dict) else 0,
            total_tokens=usage.get("total_tokens"),
        )

    @staticmethod
    def _api_error(response):
        message = "OpenAI API request failed"
        try:
            body = response.json()
            error = body.get("error", {}) if isinstance(body, dict) else {}
            if isinstance(error, dict):
                message = error.get("message") or message
        except ValueError:
            if response.text:
                message = response.text[:500]
        if response.status_code == 401:
            message = "OpenAI rejected the API key. Check the key in addon settings."
        elif response.status_code == 429:
            message = "OpenAI rate or usage limit reached. Try again later or check API billing/limits."
        return "%s (HTTP %s)" % (message, response.status_code)

    @staticmethod
    def _output_text(data):
        direct = data.get("output_text")
        if direct:
            return direct
        parts = []
        refusals = []
        for item in data.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                content_type = content.get("type")
                if content_type == "output_text":
                    parts.append(content.get("text", ""))
                elif content_type == "refusal":
                    refusals.append(content.get("refusal") or "The model declined the request.")
        if refusals and not parts:
            raise AIError("OpenAI could not produce the requested result: %s" % refusals[0])
        return "".join(parts)
