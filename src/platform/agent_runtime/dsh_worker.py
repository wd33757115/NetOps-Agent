"""Credential-minimal SDK subprocess. NetOps never implements the Agent loop here."""

import json
import sys
import threading
from contextlib import nullcontext

from deepseek_harness import DeepSeekHarness


def main():
    config = json.loads(sys.stdin.readline())
    harness = DeepSeekHarness(**config["harness"])
    timer = threading.Timer(config["timeout"], harness.close)
    timer.start()
    try:
        trace_path = config.get("trace_path")
        with open(trace_path, "a", encoding="utf-8") if trace_path else nullcontext() as trace:

            def record(notification):
                if trace is not None:
                    trace.write(
                        json.dumps(
                            {"method": notification.method, "payload": notification.payload},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    trace.flush()

            result = harness.run(
                config["prompt"], session_id=config["session_id"], on_notification=record
            )
        print(
            json.dumps({"finish_reason": result.finish_reason, "text": result.final_response}),
            flush=True,
        )
    except Exception:
        print(json.dumps({"finish_reason": "error", "text": ""}), flush=True)
    finally:
        timer.cancel()
        harness.close()


if __name__ == "__main__":
    main()
