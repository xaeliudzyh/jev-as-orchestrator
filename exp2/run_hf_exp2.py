#!/usr/bin/env python3
"""exp2: subagent routing via hf."""
import sys
from pathlib import Path

# Support both `python exp2/run_hf_exp2.py` and `python -m exp2.run_hf_exp2`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routing_bench.backends import hf_backend
from routing_bench.cli import hf_parser
from routing_bench.experiment import main


if __name__ == "__main__":
    raise SystemExit(main(hf_parser("exp2: subagent routing", "exp2"), hf_backend))
