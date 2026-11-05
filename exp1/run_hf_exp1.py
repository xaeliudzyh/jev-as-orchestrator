#!/usr/bin/env python3
"""exp1: skill routing via hf."""
import sys
from pathlib import Path

# Support both `python exp1/run_hf_exp1.py` and `python -m exp1.run_hf_exp1`.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routing_bench.backends import hf_backend
from routing_bench.cli import hf_parser
from routing_bench.experiment import main


if __name__ == "__main__":
    raise SystemExit(main(hf_parser("exp1: skill routing", "exp1"), hf_backend))
