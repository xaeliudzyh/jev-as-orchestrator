import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from exp2.run_exp2 import parser_exp2, prepare_args_exp2
from routing_bench.backends import HTTPBackend, hf_backend
from routing_bench.experiment import rank_response


class CloudflareTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"model": "clef-flash", "state": {"query": "route this request"},
            "questions": {"subagent": {"type": "choice", "instructions": "ROUTE",
                                       "criteria": {"a": "A", "b": "B"}}}}
        self.native = {"model": "clef-flash", "answers": {"subagent": {"type": "choice", "choice": "b",
            "probabilities": {"a": 0.2, "b": 0.8}, "confidence": 0.7}}, "usage": {"input_tokens": 100}}
        self.envelope = {"result": self.native, "success": True, "errors": [], "messages": []}
        self.backend = HTTPBackend("https://example.test/clef-flash", "secret", 120, 0, "cloudflare")
        self.addCleanup(self.backend.close)

    def call(self, envelope):
        response = Mock(ok=True)
        response.json.return_value = envelope
        with patch.object(self.backend.session, "post", return_value=response) as post:
            result = self.backend(self.payload)
        self.assertEqual(post.call_args.kwargs["json"], self.payload)
        return result

    def test_unwrap_preserves_full_envelope_probabilities_and_confidence(self):
        original = copy.deepcopy(self.envelope)
        result = self.call(self.envelope)
        ranking, mass = rank_response(result, ["a", "b"], "subagent")
        self.assertEqual(ranking[0], {"subagent": "b", "probability": 0.8})
        self.assertEqual(mass, 1)
        self.assertEqual(result["answers"]["subagent"]["confidence"], 0.7)
        self.assertEqual(result["cloudflare_response"], original)
        self.assertEqual(self.envelope, original)

    def test_failure_in_successful_http_response_is_not_a_prediction(self):
        with self.assertRaisesRegex(RuntimeError, "Cloudflare API reported failure"):
            self.call({"result": None, "success": False, "errors": [{"code": 1000, "message": "failed"}]})

    def test_malformed_success_envelope_rejected(self):
        for result in (None, [], {}, {"answers": []}):
            with self.subTest(result=result), self.assertRaisesRegex(ValueError, "native decisions"):
                self.call({"result": result, "success": True})

    def test_native_truncation_flag_is_preserved_for_common_scorer(self):
        self.native["truncated"] = True
        result = self.call(self.envelope)
        with self.assertRaisesRegex(ValueError, "truncated"):
            rank_response(result, ["a", "b"], "subagent")

    def test_hugging_face_model_id_rejected_before_network(self):
        self.payload["model"] = "Cloudflare/clef-flash"
        with patch.object(self.backend.session, "post") as post:
            with self.assertRaisesRegex(ValueError, "requires --model clef"):
                self.backend(self.payload)
        post.assert_not_called()

    def test_http_errors_use_existing_retry_path(self):
        self.backend.retries = 1
        busy = Mock(status_code=429, headers={"Retry-After": "0"}, ok=False)
        ok = Mock(ok=True)
        ok.json.return_value = self.envelope
        with patch.object(self.backend.session, "post", side_effect=[busy, ok]) as post, patch("time.sleep"):
            result = self.backend(self.payload)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result["answers"], self.native["answers"])

    def test_cloudflare_cli_transport_and_key_defaults(self):
        args = parser_exp2().parse_args(["--model", "clef-flash", "--backend", "hf", "--mode", "api",
            "--api-format", "cloudflare", "--endpoint", "https://example.test/clef-flash"])
        prepare_args_exp2(args)
        self.assertEqual(args.api_key_env, "CLOUDFLARE_API_TOKEN")
        args.api_key_env = "CUSTOM_TOKEN"
        prepare_args_exp2(args)
        self.assertEqual(args.api_key_env, "CUSTOM_TOKEN")
        args.model = "Cloudflare/clef-flash"
        with self.assertRaisesRegex(ValueError, "requires --model clef"):
            prepare_args_exp2(args)
        args.model = "clef-flash"
        args.backend = "openrouter"
        with self.assertRaisesRegex(ValueError, "requires --backend hf --mode api"):
            prepare_args_exp2(args)

    def test_missing_cloudflare_key_fails_before_sending_requests(self):
        args = SimpleNamespace(mode="api", endpoint="https://example.test/clef", api_key_env="CLOUDFLARE_API_TOKEN",
                               timeout=120, retries=3, api_format="cloudflare")
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "Set CLOUDFLARE_API_TOKEN"):
            hf_backend(args)


if __name__ == "__main__":
    unittest.main()
