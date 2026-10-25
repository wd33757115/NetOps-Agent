"""Bounded usage/finish metadata; never retain model reasoning or answer content."""

import json


class Usage:
    def __init__(self):
        self.buffer = b""
        self.tokens = None
        self.finish_reason = None

    def feed(self, chunk):
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if not line.startswith(b"data:") or len(line) > 128000:
                continue
            try:
                payload = json.loads(line[5:])
                for choice in payload.get("choices") or []:
                    reason = choice.get("finish_reason")
                    if reason:
                        self.finish_reason = (
                            reason
                            if reason
                            in {"stop", "tool_calls", "length", "content_filter", "function_call"}
                            else "other"
                        )
                usage = payload.get("usage")
                if isinstance(usage, dict):
                    values = {
                        key: usage[key]
                        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                        if type(usage.get(key)) is int and usage[key] >= 0
                    }
                    if values:
                        self.tokens = values
            except (ValueError, TypeError, AttributeError):
                pass
        if len(self.buffer) > 128000:
            self.buffer = b""
