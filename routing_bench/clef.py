"""Native Cloudflare Clef joint-schema decision head (no text generation)."""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

# Reviewed release revisions. Override explicitly with --revision for a different release.
CLEF_REVISIONS = {
    "Cloudflare/clef": "cfddda043dd190ef3b01cff449d5ee8cff1e8ec6",
    "Cloudflare/clef-flash": "fb4417d31c1d0f9e7e77f1141b40aaede7491e00",
}


def load_native(path: Path):
    source = path / "joint_schema_model.py"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    name = f"routing_bench_clef_{digest}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, source)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot import Clef native decision code: {source}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # Required by native dataclass definitions.
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name], digest


class ClefRuntime:
    def __init__(self, path, device, dtype, limit, quantization="none"):
        import torch

        if quantization not in {"none", "4bit", "8bit"}:
            raise ValueError("Clef quantization must be none, 4bit or 8bit")
        self.native, source_hash = load_native(Path(path))
        self.limit = limit
        kwargs = {}
        dtype = dtype or (torch.float32 if device == "cpu" else torch.bfloat16)
        if quantization != "none":
            if not str(device).startswith("cuda"):
                raise ValueError("The benchmark's quantized Clef loader requires CUDA")
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=quantization == "4bit", load_in_8bit=quantization == "8bit",
                bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=dtype)
        self.model, self.processor = self.native.load_release_model(path, device=device, dtype=dtype, **kwargs)
        self.info = {"native_source_sha256": source_hash, "quantization": quantization,
                     "dtype": str(dtype), "allow_null": False, "truncation": "error",
                     "inference": "native joint_schema_model.systemone; no generate()"}

    def __call__(self, payload):
        # Upstream encode_record truncates state to fit max_length. Count the COMPLETE
        # record first on CPU, then refuse overflow before any model forward pass.
        encoded = self.native.encode_record(self.processor.tokenizer, payload,
                                           max_length=2**31 - 1, processor=self.processor)
        tokens = len(encoded.input_ids)
        if tokens > self.limit:
            raise ValueError(f"Clef complete decision input ({tokens} tokens) exceeds "
                             f"--max-input-tokens={self.limit}; input was not truncated")
        result = self.native.systemone(self.model, self.processor, payload, max_length=self.limit)
        if result["usage"]["input_tokens"] != tokens:
            raise ValueError("Clef input token count changed during inference; possible truncation")
        result["benchmark_input_limits"] = {"complete_input_tokens": tokens, "max_input_tokens": self.limit,
                                            "truncation": "error"}
        return result
