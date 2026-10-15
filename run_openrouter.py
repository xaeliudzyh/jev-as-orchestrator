#!/usr/bin/env python3
"""Runner 1: native Jev decisions through OpenRouter."""
from routing_bench.backends import openrouter_backend
from routing_bench.experiment import common_parser, main


if __name__ == "__main__":
    parser = common_parser("Evaluate Jev skill routing through OpenRouter Decisions API")
    parser.add_argument("--endpoint", default="https://openrouter.ai/api/alpha/decisions")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    raise SystemExit(main(parser, openrouter_backend))
