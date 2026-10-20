#!/usr/bin/env python3
"""Runner 2: native open decision models, locally or on an HF endpoint."""
from routing_bench.backends import hf_backend
from routing_bench.experiment import common_parser, main


if __name__ == "__main__":
    parser = common_parser("Evaluate Hugging Face decision models locally or through an endpoint")
    parser.add_argument("--mode", choices=("local", "api"), default="local")
    parser.add_argument("--adapter", choices=("auto", "frida", "decider", "kev", "jeva"), default="auto")
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, cuda:0, mps")
    parser.add_argument("--dtype", choices=("auto", "float32", "float16", "bfloat16"), default="auto")
    parser.add_argument("--revision", help="HF revision/commit to download for local inference")
    parser.add_argument("--cache-dir", help="Local Hugging Face checkpoint cache")
    parser.add_argument("--max-input-tokens", type=int, default=32768)
    parser.add_argument("--jeva-cache", choices=("none", "set", "hybrid"), default="none",
                        help="Jeva option-memory reuse; disabled by default for uncached timings")
    parser.add_argument("--jeva-option-chunk-size", type=int, default=1,
                        help="Maximum Jeva options encoded per batch (1 conserves VRAM)")
    parser.add_argument("--jeva-option-batch-tokens", type=int, default=4096,
                        help="Jeva padded-token batch budget; a single longer option is still encoded whole")
    parser.add_argument("--endpoint", help="Full URL of a deployed native decision endpoint")
    parser.add_argument("--api-format", choices=("hf-handler", "systemone"), default="hf-handler")
    parser.add_argument("--api-key-env", default="HF_TOKEN")
    raise SystemExit(main(parser, hf_backend))
