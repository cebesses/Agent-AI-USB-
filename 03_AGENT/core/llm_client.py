"""
Agent IA USB LLM Client
Connects to the local llama.cpp server via its OpenAI-compatible API.
One method does the work: chat() (with optional streaming and tool calls).
"""
import json
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


class LLMClient:
    """Client for the local llama.cpp server."""

    def __init__(self, server_url: str = "http://127.0.0.1:8080"):
        self.server_url = server_url.rstrip("/")

    def is_server_running(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.server_url}/health", method='GET')
            with urllib.request.urlopen(req, timeout=3) as resp:
                return json.loads(resp.read().decode()).get("status") == "ok"
        except Exception:
            return False

    def chat(self, messages: List[Dict[str, str]],
             temperature: float = 0.7,
             max_tokens: int = 2048,
             top_p: float = 0.9,
             stop: List[str] = None,
             tools: List[Dict] = None,
             tool_choice: str = "auto",
             top_k: Optional[int] = None,
             min_p: Optional[float] = None,
             presence_penalty: Optional[float] = None,
             enable_thinking: bool = False,
             timeout: int = 900,
             stream: bool = False,
             on_token=None) -> Dict[str, Any]:
        """Send a chat completion request. Long timeout: CPU-only prompt processing is slow.
        With stream=True the reply is streamed and `on_token(text)` is called for each piece
        of visible text; the return value is the same shape either way (choices/message/timings)."""
        payload = {
            "model": "local-model",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
        for key, value in (("top_k", top_k), ("min_p", min_p), ("presence_penalty", presence_penalty)):
            if value is not None:
                payload[key] = value

        if stop:
            payload["stop"] = stop

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            f"{self.server_url}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method='POST'
        )

        try:
            if stream:
                return self._stream_chat(req, timeout, on_token)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors='replace')
            return {"error": f"HTTP {e.code}: {body[:500]}"}
        except Exception as e:
            return {"error": str(e)}

    def _stream_chat(self, req, timeout, on_token):
        """Consume an SSE stream and rebuild one non-streaming-shaped response."""
        content = []
        tool_acc = {}           # index -> {id, type, function:{name, arguments}}
        finish_reason = None
        timings = None
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buffer = ""
            while True:
                chunk = resp.read(1024)
                if not chunk:
                    break
                buffer += chunk.decode('utf-8', errors='replace')
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if not line.startswith('data: '):
                        continue
                    payload = line[6:]
                    if payload == '[DONE]':
                        break
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("timings"):
                        timings = obj["timings"]
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        content.append(piece)
                        if on_token:
                            try:
                                on_token(piece)
                            except Exception:
                                pass
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tool_acc.setdefault(idx, {"id": None, "type": "function",
                                                          "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
        message = {"role": "assistant", "content": "".join(content)}
        if tool_acc:
            message["tool_calls"] = [tool_acc[i] for i in sorted(tool_acc)]
        result = {"choices": [{"message": message, "finish_reason": finish_reason}]}
        if timings:
            result["timings"] = timings
        return result
