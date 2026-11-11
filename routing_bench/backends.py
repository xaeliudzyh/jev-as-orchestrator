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
        if self.api_format == "cloudflare" and payload.get("model") not in {"clef", "clef-flash"}:
            raise ValueError("Cloudflare Clef REST API requires --model clef or clef-flash, not a Hugging Face repository ID")
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
            if self.api_format == "cloudflare":
                if result.get("success") is not True:
                    raise RuntimeError(f"Cloudflare API reported failure: {str(result.get('errors', []))[:1000]}")
                native = result.get("result")
                if not isinstance(native, dict) or not isinstance(native.get("answers"), dict):
                    raise ValueError("Cloudflare result must contain a native decisions object with answers")
                # Keep the complete REST envelope for provenance, while exposing the
                # native decision fields expected by the common scorer.
                result = dict(native, cloudflare_response=result)
            return result
        raise AssertionError("unreachable")

    def close(self):
        self.session.close()


class LocalBackend:
    """Use native decision heads, never generated/self-reported probabilities."""

    def __init__(self, model: str, adapter: str, device="auto", dtype="auto", revision=None,
                 cache_dir=None, max_input_tokens=32768, jeva_cache="none",
                 jeva_option_chunk_size=1, jeva_option_batch_tokens=4096, clef_quantization="none"):
        import torch

        if max_input_tokens <= 0:
            raise ValueError("--max-input-tokens must be positive")
        if adapter == "auto":
            name = model.lower()
            adapter = next((key for key in ("frida", "decider", "kev", "jeva", "clef") if key in name), "")
            if not adapter:
                raise ValueError("Cannot infer adapter; specify --adapter frida|decider|kev|jeva|clef")
        if adapter == "jeva" and (jeva_option_chunk_size <= 0 or jeva_option_batch_tokens <= 0):
            raise ValueError("Jeva option chunk size and batch token budget must be positive")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        torch_dtype = None if dtype == "auto" else getattr(torch, dtype)
        self.adapter, self.limit, self.resource = adapter, max_input_tokens, None
        resolved = model
        if adapter == "clef" and not Path(model).is_dir() and revision is None:
            from .clef import CLEF_REVISIONS
            revision = CLEF_REVISIONS.get(model)
        if revision or cache_dir or adapter == "clef":
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
            elif adapter == "jeva":
                from jeva import Jeva
                self.model = Jeva.from_pretrained(resolved, device=device, dtype=torch_dtype,
                    cache=jeva_cache, truncation="error", option_chunk_size=jeva_option_chunk_size,
                    option_batch_tokens=jeva_option_batch_tokens)
                # Retain the checkpoint's native length limits; the CLI may only lower them.
                self.model.max_state_length = min(self.model.max_state_length, self.limit)
                self.model.max_option_length = min(self.model.max_option_length, self.limit)
                self.info.update(native_config=dataclasses.asdict(self.model.config),
                    resolved_path=str(self.model.checkpoint_dir),
                    dtype=self.model.config.model.torch_dtype,
                    cache=jeva_cache, option_chunk_size=jeva_option_chunk_size,
                    option_batch_tokens=jeva_option_batch_tokens, allow_null=False,
                    max_state_length=self.model.max_state_length,
                    max_option_length=self.model.max_option_length,
                    truncation="error")
            elif adapter == "clef":
                from .clef import ClefRuntime
                self.model = ClefRuntime(resolved, device, torch_dtype, self.limit, clef_quantization)
                self.info.update(self.model.info)
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
        if self.adapter == "clef":
            return self.model(payload)
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
            original_config, original_state_max = self.model.config, self.model.state_max
            request_config = dataclasses.replace(original_config,
                instruction_max_tokens=max(1, instruction_size), option_max_tokens=max(1, option_size),
                max_row_tokens=max(original_config.max_row_tokens, state_size + instruction_size + option_size))
            # Caps belong to this request, including when inference raises. Otherwise a long
            # stage-2 option permanently changes packing and memory use of later stage-1 calls.
            try:
                self.model.state_max = max(1, state_size)
                self.model.config = request_config
                result = self.model(payload)
            finally:
                self.model.config = original_config
                self.model.state_max = original_state_max
            result["benchmark_input_limits"] = {"state_tokens": state_size,
                "instruction_max_tokens": instruction_size, "option_max_tokens": option_size,
                "max_row_tokens": request_config.max_row_tokens,
                "max_options_per_row": request_config.max_options_per_row,
                "expanded_native_caps": True}
            return result
        if self.adapter == "jeva":
            from jeva import Choice
            questions = {}
            for name, spec in payload["questions"].items():
                if spec["type"] != "choice":
                    raise ValueError("The benchmark's Jeva adapter supports choice questions only")
                questions[name] = Choice(instructions=spec["instructions"],
                    criteria=spec["criteria"], allow_null=False)
            result = self.model.system_one(state=payload["state"], questions=questions).to_dict()
            # Keep every native diagnostic; add the common answer type for rank_response.
            for answer in result["answers"].values():
                answer["type"] = answer["kind"]
            result["model"] = payload["model"]
            result["truncated"] = result["usage"]["truncated_tokens"] > 0
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
        if self.adapter == "jeva":
            self.model.clear_cache()
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
        token = os.environ.get(args.api_key_env)
        if args.api_format == "cloudflare" and not token:
            raise ValueError(f"Set {args.api_key_env} in the environment or .env")
        return HTTPBackend(args.endpoint, token, args.timeout, args.retries, args.api_format)
    return LocalBackend(args.model, args.adapter, args.device, args.dtype,
                        args.revision, args.cache_dir, args.max_input_tokens,
                        args.jeva_cache, args.jeva_option_chunk_size, args.jeva_option_batch_tokens,
                        getattr(args, "clef_quantization", "none"))
