"""Copy to the root of an HF model repo together with routing_bench/."""
import os
import threading

from routing_bench.backends import LocalBackend


class EndpointHandler:
    def __init__(self, path=""):
        self.backend = LocalBackend(path, adapter=os.environ.get("DECISION_ADAPTER", "frida"),
            device=os.environ.get("DECISION_DEVICE", "auto"),
            dtype=os.environ.get("DECISION_DTYPE", "auto"),
            max_input_tokens=int(os.environ.get("DECISION_MAX_INPUT_TOKENS", "32768")))
        self.lock = threading.Lock()

    def __call__(self, data):
        payload = data["inputs"]
        if not isinstance(payload, dict) or "state" not in payload or "questions" not in payload:
            raise ValueError("inputs must contain a native state/questions request")
        # Some native loaders mutate per-request buffers/configuration.
        with self.lock:
            result = self.backend(payload)
        result["benchmark_backend"] = self.backend.info
        return result
