from __future__ import annotations

import dataclasses
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests


class HTTPBackend:
    def __init__(self, endpoint: str, token: str | None, timeout: float, retries: int, api_format="systemone"):
        if urlparse(endpoint).scheme not in {"http", "https"}:
            raise ValueError("Endpoint must be an http(s) URL")
        self.endpoint, self.timeout, self.retries = endpoint, timeout, retries
        self.api_format = api_format
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.info = {"transport": "http", "endpoint": endpoint, "api_format": api_format}

    def __call__(self, payload: dict) -> dict:
        body = {"inputs": payload} if self.api_format == "hf-handler" else payload
        for attempt in range(self.retries + 1):
            try:
                response = self.session.post(self.endpoint, json=body, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as error:
                if attempt == self.retries:
                    # Do not serialize request headers, which contain credentials.
                    raise RuntimeError(f"HTTP transport failed: {type(error).__name__}") from None
                time.sleep(min(2 ** attempt, 30))
                continue
            if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < self.retries:
                delay = response.headers.get("Retry-After", "")
                time.sleep(min(float(delay), 30) if delay.replace(".", "", 1).isdigit() else min(2 ** attempt, 30))
                continue
            if not response.ok:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1500]}")
            result = response.json()
            if isinstance(result, list) and len(result) == 1:
                result = result[0]
            if not isinstance(result, dict):
                raise ValueError("Expected a JSON object from decision endpoint")
            return result
        raise AssertionError("unreachable")

    def close(self):
        self.session.close()


class LocalBackend:
    """Use native decision heads, never generated/self-reported probabilities."""

    def __init__(self, model: str, adapter: str, device="auto", dtype="auto", revision=None,
                 cache_dir=None, max_input_tokens=32768):
        import torch

        if max_input_tokens <= 0:
            raise ValueError("--max-input-tokens must be positive")
        if adapter == "auto":
            name = model.lower()
            adapter = next((key for key in ("frida", "decider", "kev") if key in name), "")
            if not adapter:
                raise ValueError("Cannot infer adapter; specify --adapter frida|decider|kev")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        torch_dtype = None if dtype == "auto" else getattr(torch, dtype)
        self.adapter, self.limit, self.resource = adapter, max_input_tokens, None
        resolved = model
        if revision or cache_dir:
            if not Path(model).is_dir():
                from huggingface_hub import snapshot_download
                # Native loaders need complete checkpoint/config/adapter files.
                resolved = snapshot_download(model, revision=revision, cache_dir=cache_dir)
        self.info = {"adapter": adapter, "device": device, "requested_dtype": dtype,
                     "model_path": resolved, "revision": revision,
                     "max_input_tokens": max_input_tokens, "truncation": "refuse_or_expand_without_cutting"}
        try:
            if adapter == "frida":
                from frida_decisions import Judge
                self.model = Judge.from_pretrained(resolved, device=device, dtype=torch_dtype,
                                                  state_cache_mb=0, rows_per_forward=1)
                self.info["native_config"] = dataclasses.asdict(self.model.config)
                self.info["resolved_path"] = str(self.model.folder)
                self.info["dtype"] = str(self.model.dtype)
            elif adapter == "decider":
                from decider.infer import Decider
                self.model = Decider(resolved, device=device, dtype=torch_dtype, use_graphs=False)
                self.info["temperature"] = self.model.T
                self.info["temperature_by_type"] = self.model.T_by_type
            elif adapter == "kev":
                from kev.checkpoint import Checkpoint, LoadOptions
                from kev.serve import Server
                ck = Checkpoint(resolved)
                tok, model_obj = ck.load(device, LoadOptions(dtype=torch_dtype))
                self.resource = Server(ck, tok, model_obj, device, truncate_states=False)
                self.model = self.resource
                self.info["resolved_path"] = str(ck.path)
                self.info["temperature"] = model_obj.head.temperature
            else:
                raise ValueError(f"Unsupported adapter: {adapter}")
        except ImportError as error:
            raise RuntimeError(f"Install the native {adapter} runtime; see README.md. {error}") from error

    def __call__(self, payload: dict) -> dict:
        if self.adapter == "frida":
            # Native FRIDA defaults cut instructions at 96 and options at 256 tokens.
            # Raise those caps to fit the actual input, including full stage-2 skills.
            _, candidates, _ = self.model.compile(payload)
            count = self.model.text.count
            state_size = max(count(c.state) for c in candidates)
            instruction_size = max(count(c.instruction) for c in candidates)
            option_size = max(count(c.text) + 1 for c in candidates)
            if state_size + instruction_size + option_size > self.limit:
                raise ValueError("FRIDA candidate exceeds --max-input-tokens; input was not truncated")
            self.model.state_max = max(1, state_size)
            self.model.config = dataclasses.replace(self.model.config,
                instruction_max_tokens=max(1, instruction_size), option_max_tokens=max(1, option_size),
                max_row_tokens=max(self.model.config.max_row_tokens, state_size + instruction_size + option_size))
            result = self.model(payload)
            result["benchmark_input_limits"] = {"state_tokens": state_size,
                "instruction_max_tokens": instruction_size, "option_max_tokens": option_size,
                "expanded_native_caps": True}
            return result
        if self.adapter == "decider":
            from decider.systemone import render_state, render_question
            tok = self.model.m.tok
            state = render_state(payload["state"])
            if len(tok.encode(state, add_special_tokens=False)) > self.limit:
                raise ValueError("State exceeds --max-input-tokens; input was not truncated")
            # Check the complete question, not only state; SDK's state cap is separate.
            for spec in payload["questions"].values():
                question = render_question(spec)
                text = state + "\n" + question["question"] + "\n" + "\n".join(question["options"])
                if len(tok.encode(text, add_special_tokens=False)) + 256 > self.limit:
                    raise ValueError("Complete decision input exceeds --max-input-tokens")
            return self.model.system_one(payload["state"], payload["questions"],
                max_state_tokens=self.limit, layout="state_first")
        from kev.api import SystemOneRequest, to_record
        from kev.model import admit
        # Kev's admit() rejects context overflow by default.
        request = SystemOneRequest(**payload)
        record, _ = to_record(request)
        encoded = admit(self.model.model, self.model.tok, record, truncate=False)
        if len(encoded["ids"]) > self.limit:
            raise ValueError("Kev input exceeds --max-input-tokens; input was not truncated")
        return self.model.answer(request)

    def close(self):
        if self.resource is not None:
            self.resource.close()


def openrouter_backend(args):
    token = os.environ.get(args.api_key_env)
    if not token:
        raise ValueError(f"Set {args.api_key_env} in the environment or .env")
    return HTTPBackend(args.endpoint, token, args.timeout, args.retries)


def hf_backend(args):
    if args.mode == "api":
        if not args.endpoint:
            raise ValueError("--mode api requires --endpoint (a deployed native decision endpoint)")
        return HTTPBackend(args.endpoint, os.environ.get(args.api_key_env), args.timeout, args.retries, args.api_format)
    return LocalBackend(args.model, args.adapter, args.device, args.dtype,
                        args.revision, args.cache_dir, args.max_input_tokens)
