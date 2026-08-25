import json

import requests

from .ai_base import AIError, BaseAIClient


class GeminiClient(BaseAIClient):
    URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
    provider_id = "gemini"
    provider_name = "Google Gemini"

    def __init__(self, api_key, model="gemini-3.5-flash-lite", session=None, usage_callback=None, user_agent=None):
        session = session or requests.Session()
        model = (model or "gemini-3.5-flash-lite").strip()
        if model.startswith("models/"):
            model = model[7:]
        super().__init__(api_key, model, session=session, usage_callback=usage_callback)
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
        # schema_name is meaningful to OpenAI but Gemini takes the JSON Schema directly.
        del schema_name

        input_parts = [str(user_text or "").strip()]
        for item in extra_input or []:
            text = str(item.get("content") or "") if isinstance(item, dict) else str(item or "")
            text = text.strip()
            if text:
                input_parts.append(text)
        interaction_input = "\n\n".join(part for part in input_parts if part)

        payload = {
            "model": self.model,
            "input": interaction_input,
            "system_instruction": instructions,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            # The curator is stateless; there is no need for Google to retain
            # an Interaction resource just to generate a list.
            "store": False,
        }
        try:
            response = self.session.post(
                self.URL,
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                # Keep connection failures responsive, while allowing enough
                # time for a structured list response on slower Kodi devices.
                timeout=(15, 180),
            )
        except requests.Timeout:
            raise AIError(
                "Google Gemini took too long to respond. No list changes were saved. "
                "Please try again; the timed-out request may still count towards your Gemini usage."
            )
        except requests.RequestException as exc:
            raise AIError("Could not contact Google Gemini: %s" % exc)
        if response.status_code >= 400:
            raise AIError(self._api_error(response))
        try:
            data = response.json()
        except ValueError:
            raise AIError("Google Gemini returned a response that was not valid JSON.")

        self._report_usage(data, usage_kind)
        text = self._output_text(data)
        if not text:
            raise AIError("Google Gemini returned no usable structured data.")
        try:
            return json.loads(text)
        except (TypeError, ValueError) as exc:
            raise AIError("Google Gemini structured JSON could not be read: %s" % exc)

    def _report_usage(self, data, kind):
        usage = data.get("usage") or {} if isinstance(data, dict) else {}
        if not isinstance(usage, dict):
            return
        self._emit_usage(
            kind,
            input_tokens=usage.get("total_input_tokens"),
            cached_input_tokens=usage.get("total_cached_tokens"),
            output_tokens=usage.get("total_output_tokens"),
            reasoning_tokens=usage.get("total_thought_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

    @staticmethod
    def _output_text(data):
        if not isinstance(data, dict):
            return ""

        status = str(data.get("status") or "").lower()
        if status in ("failed", "cancelled", "budget_exceeded"):
            error = data.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else ""
            raise AIError(message or "Google Gemini interaction failed (%s)." % status)

        chunks = []
        for step in data.get("steps") or []:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            for content in step.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "text" and content.get("text") is not None:
                    chunks.append(str(content.get("text") or ""))
        return "".join(chunks)

    @staticmethod
    def _api_error(response):
        message = "Google Gemini API request failed"
        try:
            body = response.json()
            error = body.get("error", {}) if isinstance(body, dict) else {}
            if isinstance(error, dict):
                message = error.get("message") or message
        except ValueError:
            if response.text:
                message = response.text[:500]
        if response.status_code in (401, 403):
            message = "Google Gemini rejected the API key. Check the key in addon settings."
        elif response.status_code == 429:
            message = "Google Gemini rate or quota limit reached. Try again later or check Gemini API quota/billing."
        return "%s (HTTP %s)" % (message, response.status_code)
