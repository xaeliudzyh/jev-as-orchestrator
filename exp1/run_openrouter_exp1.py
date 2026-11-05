#!/usr/bin/env python3
"""exp1: skill routing via openrouter."""
import sys
from pathlib import Path

# Support both `python exp1/run_openrouter_exp1.py` and `python -m exp1.run_openrouter_exp1`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routing_bench.backends import openrouter_backend
from routing_bench.cli import openrouter_parser
from routing_bench.experiment import main


if __name__ == "__main__":
    raise SystemExit(main(openrouter_parser("exp1: skill routing", "exp1"), openrouter_backend))
