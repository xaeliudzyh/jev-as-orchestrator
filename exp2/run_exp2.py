#!/usr/bin/env python3
"""Unified exp2 runner: shared first-stage evaluation, optional full-text reranking."""
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routing_bench.backends import hf_backend, openrouter_backend
from routing_bench.cli import hf_parser
from routing_bench.experiment import ROOT, main as experiment_main

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"


def parser_exp2(default_backend="openrouter"):
    parser = hf_parser("exp2: subagent routing, two-stage by default", "exp2",
                       default_strategy="2-step", default_prompt=ROOT / "exp2/prompts/routing_scenarios_ru_exp2.txt")
    parser.add_argument("--backend", choices=("openrouter", "hf"), default=default_backend,
                        help="openrouter (default) or Hugging Face local/native API")
    parser.set_defaults(endpoint=None, api_key_env=None, export_stage1=True)
    return parser


def prepare_args_exp2(args):
    if args.api_format == "cloudflare" and (args.backend != "hf" or args.mode != "api"):
        raise ValueError("--api-format cloudflare requires --backend hf --mode api")
    if args.api_format == "cloudflare" and args.model not in {"clef", "clef-flash"}:
        raise ValueError("Cloudflare Clef REST API requires --model clef or clef-flash")
    if args.backend == "openrouter":
        args.endpoint = args.endpoint or OPENROUTER_ENDPOINT
        args.api_key_env = args.api_key_env or "OPENROUTER_API_KEY"
    else:
        args.api_key_env = args.api_key_env or ("CLOUDFLARE_API_TOKEN" if args.api_format == "cloudflare" else "HF_TOKEN")
        if args.mode == "api" and not args.endpoint:
            raise ValueError("--backend hf --mode api requires an explicit --endpoint")


def backend_exp2(args):
    return openrouter_backend(args) if args.backend == "openrouter" else hf_backend(args)


def main(default_backend="openrouter"):
    return experiment_main(parser_exp2(default_backend), backend_exp2, prepare_args_exp2)


if __name__ == "__main__":
    raise SystemExit(main())
