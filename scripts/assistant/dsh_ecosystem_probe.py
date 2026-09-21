"""Pinned DSH + official MCP SDK probe; live calls are explicitly opt-in and bounded."""

import argparse
import asyncio
import hmac
import json
import os
import pathlib
import secrets
import socket
import threading
import time
import urllib.request
import uuid

import uvicorn
from deepseek_harness import DeepSeekHarness
from mcp.server import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--live", action="store_true")
parser.add_argument("--fragment", type=pathlib.Path)
parser.add_argument("--scenario", choices=["complete", "cancel", "timeout"], default="complete")
args = parser.parse_args()
if args.live and (args.fragment is None or args.scenario != "complete"):
    parser.error("Live checks require one bounded fragment and the complete scenario")
fragment = {"text": "synthetic-only", "revision": "fixture"}
if args.fragment:
    if args.fragment.stat().st_size > 12000:
        parser.error("Fragment file exceeds the local input budget")
    fragment = json.loads(args.fragment.read_text(encoding="utf8"))
    if not isinstance(fragment.get("text"), str) or len(fragment["text"]) > 1200:
        parser.error("At most 1200 source characters are permitted")
root = pathlib.Path(__file__).resolve().parents[2] / ".runtime/assistant-dsh-lab"
home = root / ("probe-" + uuid.uuid4().hex)
home.mkdir(parents=True)
workspace = home / "workspace"
workspace.mkdir()
keep = {
    k: v
    for k, v in os.environ.items()
    if k.upper()
    in {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATH",
        "TEMP",
        "TMP",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "LLM_MODEL",
    }
}
os.environ.clear()
os.environ.update(keep)
run_token = secrets.token_urlsafe(32)
os.environ["NETOPS_DSH_RUN_TOKEN"] = run_token
revoked = threading.Event()
requests, invoked = [], []
mcp = MCPServer("netops", log_level="ERROR")


def abandon():
    revoked.set()
    harness.close()


class RunGuard(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if revoked.is_set() or not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + run_token
        ):
            return JSONResponse({"error": "invalid run credential"}, status_code=401)
        return await call_next(request)


@mcp.tool()
def read_sample(offset: int = 0) -> dict:
    """Read a small synthetic sample. Does not access files or devices."""
    invoked.append(offset)
    if offset != 0:
        return {"error": "Only the selected fragment is available"}
    return fragment


async def completion(request):
    body = await request.json()
    tools = [t["function"]["name"] for t in body.get("tools", [])]
    requests.append({"tools": tools, "size": len(json.dumps(body))})
    assert tools == ["mcp__netops__read_sample"], tools
    if len(requests) > 3 or len(json.dumps(body)) > 16000:
        return JSONResponse({"error": "model request budget exceeded"}, status_code=429)
    if args.scenario != "complete":
        if args.scenario == "cancel":
            threading.Timer(0.1, abandon).start()
        await asyncio.sleep(3)
    if args.live:
        if not os.environ.get("DEEPSEEK_API_KEY"):
            return JSONResponse({"error": "API key not configured"}, status_code=503)
        url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        if not url.startswith("https://"):
            return JSONResponse({"error": "Live endpoint must use HTTPS"}, status_code=400)
        body["max_tokens"] = min(body.get("max_tokens", 300), 300)

        def forward():
            request = urllib.request.Request(
                url + "/chat/completions",
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"],
                },
            )
            return urllib.request.urlopen(request, timeout=30)

        upstream = await asyncio.to_thread(forward)

        def chunks():
            with upstream:
                yield from upstream

        return StreamingResponse(chunks(), media_type="text/event-stream")
    delta = {"role": "assistant"}
    if len(requests) == 1:
        delta["tool_calls"] = [
            {
                "index": 0,
                "id": "probe-read",
                "type": "function",
                "function": {"name": tools[0], "arguments": '{"offset":0}'},
            }
        ]
        finish = "tool_calls"
    else:
        delta["content"] = "Synthetic sample read successfully."
        finish = "stop"

    async def events():
        base = {
            "id": "probe",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "deepseek-v4-flash",
        }
        for d, f in [(delta, None), ({}, finish)]:
            yield (
                "data: "
                + json.dumps({**base, "choices": [{"index": 0, "delta": d, "finish_reason": f}]})
                + "\n\n"
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


app = mcp.streamable_http_app(json_response=True, stateless_http=True)
app.routes.append(Route("/chat/completions", completion, methods=["POST"]))
app.add_middleware(RunGuard)
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
config = uvicorn.Config(app, log_level="error")
server = uvicorn.Server(config)
thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
thread.start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.02)
try:
    urllib.request.urlopen(f"http://127.0.0.1:{port}/mcp", timeout=2)
    raise AssertionError("Unauthenticated MCP access must fail")
except urllib.error.HTTPError as error:
    assert error.code == 401
patch = home / "netops.patch.yml"
patch.write_text(
    """- id: persistent-bash
  disabled: true
- id: persistent-pwsh
  disabled: true
- id: terminal-bash
  disabled: true
- id: terminal-pwsh
  disabled: true
- id: llm-retry
  disabled: true
- id: llm-deepseek
  config:
    protocol: chat-completions
    thinking: disabled
    streamIdleTimeoutMs: 10000
- insert:
    - id: netops-mcp
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        transport: streamable-http
        serverName: netops
        url: http://127.0.0.1:"""
    + str(port)
    + """/mcp
        headers:
          Authorization: !!js "'Bearer ' + process.env.NETOPS_DSH_RUN_TOKEN"
        toolCallTimeoutMs: 3000
        failOnStartupError: true
        reconnect:
          enabled: false
""",
    encoding="utf8",
)
harness = DeepSeekHarness(
    dsh_home=str(home),
    profile="sdk-minimal",
    patches=(str(patch),),
    cwd=str(workspace),
    api_key=run_token,
    base_url=f"http://127.0.0.1:{port}",
    model=os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
    max_tokens=300,
    request_timeout_seconds=0.3 if args.scenario == "timeout" else 45,
    initialize_timeout_seconds=15,
)
started = time.monotonic()
deadline_timer = None
try:
    harness.start()
    runtime_process = harness.client._proc  # Test-only process reclamation evidence.
    # In pinned rc1 request_timeout_seconds does not bound the notification wait
    # of a whole Session.run. NetOps therefore owns a separate whole-turn deadline.
    if args.scenario == "timeout":
        deadline_timer = threading.Timer(0.5, abandon)
        deadline_timer.start()
    result = harness.run(
        fragment.get("question", "Use read_sample, then say it was read."), session_id="probe"
    )
    report = {
        "finish_reason": result.finish_reason,
        "final_response": result.final_response,
        "requests": requests,
        "invoked": invoked,
        "event_types": sorted({e.get("type") for e in result.events}),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "external_model_calls": len(requests) if args.live else 0,
        "source_characters": len(fragment["text"]) if args.fragment else 0,
    }
    if result.finish_reason != "completed" or not invoked:
        raise AssertionError("The SDK did not complete a tool-backed answer")
    if args.scenario != "complete":
        raise AssertionError("The cancelled/timed-out turn unexpectedly completed")
except Exception as exc:
    report = {
        "scenario": args.scenario,
        "error_type": type(exc).__name__,
        "requests": requests,
        "external_model_calls": len(requests) if args.live else 0,
    }
    if args.scenario == "complete":
        report["failed"] = True
    elif not isinstance(exc, (TimeoutError,)) and type(exc).__name__ != "TransportClosedError":
        report["failed"] = True
finally:
    if deadline_timer is not None:
        deadline_timer.cancel()
    abandon()
    server.should_exit = True
    thread.join(5)
    sock.close()
report["runtime_reaped"] = (
    runtime_process.poll() is not None if "runtime_process" in globals() else None
)
report["home"] = str(home)
report["unauthenticated_status"] = 401
report["whole_turn_deadline_enforced"] = args.scenario == "timeout"
name = "live" if args.live else args.scenario
(root / f"{name}-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
)
print(json.dumps(report))
if report.get("failed") or report["runtime_reaped"] is not True:
    raise SystemExit(1)
