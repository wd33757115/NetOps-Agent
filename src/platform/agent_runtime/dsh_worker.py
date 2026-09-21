"""Credential-minimal SDK subprocess. NetOps never implements the Agent loop here."""

import json
import sys
import threading

from deepseek_harness import DeepSeekHarness


def main():
    config = json.loads(sys.stdin.readline())
    harness = DeepSeekHarness(**config["harness"])
    timer = threading.Timer(config["timeout"], harness.close)
    timer.start()
    try:
        result = harness.run(config["prompt"], session_id=config["session_id"])
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
