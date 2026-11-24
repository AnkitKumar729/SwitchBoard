"""A local stand-in for the Gemini HTTP API.

The tests point LiteLLM at this server (LLM_API_BASE), so its real request, error and
response handling runs. Only the model on the other end of the wire is fake.

- The reply repeats every user message it was sent, so memory across turns is visible.
- Token counts grow with the size of the prompt.
- An API key that contains a status code, such as "bad-key-429", gets that HTTP status back.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeProvider:
    def __init__(self) -> None:
        self.requests: list[dict] = []  # one entry per call: the key used, the system prompt and the messages
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api_key = self.headers.get("x-goog-api-key", "")
                contents = body.get("contents", [])
                messages = [(item["role"], item["parts"][0]["text"]) for item in contents]
                system_prompt = body.get("system_instruction", {}).get("parts", [{}])[0].get("text")
                provider.requests.append({"api_key": api_key, "system_prompt": system_prompt, "messages": messages})

                for status in (429, 500, 502, 503):
                    if str(status) in api_key:
                        return self.reply(status, {"error": {"code": status, "message": f"fake HTTP {status}"}})

                answer = "You said: " + " | ".join(text for role, text in messages if role == "user")
                prompt_tokens = len(json.dumps(body)) // 4
                answer_tokens = len(answer) // 4 + 1
                self.reply(
                    200,
                    {
                        "candidates": [
                            {"content": {"role": "model", "parts": [{"text": answer}]}, "finishReason": "STOP"}
                        ],
                        "usageMetadata": {
                            "promptTokenCount": prompt_tokens,
                            "candidatesTokenCount": answer_tokens,
                            "totalTokenCount": prompt_tokens + answer_tokens,
                        },
                    },
                )

            def reply(self, status: int, payload: dict) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def keys_called(self) -> list[str]:
        return [request["api_key"] for request in self.requests]

    def close(self) -> None:
        self.server.shutdown()
