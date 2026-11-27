"""Offline tests for the gateway contract, retries, failure modes and evidence.

Run with::

    python -m unittest discover -s tests -v

Every test is offline: a local fake OpenAI-compatible server is used and the
import path points at ``src/`` (the package is not installed).
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import unittest

import _bootstrap  # noqa: F401  (sys.path side effect)
from fake_server import FakeServer

from local_llm_gateway import (
    BackendTimeout,
    BackendUnreachable,
    ConfigError,
    EmptyResponse,
    Gateway,
    GatewayConfig,
    HTTPStatusError,
    JSONExtractionError,
    PromptTooLarge,
    ProtocolError,
    ResponseTooLarge,
)


def _gateway(server: FakeServer, **kwargs) -> Gateway:
    config = GatewayConfig(
        base_url=kwargs.pop("base_url", server.base_url),
        model=kwargs.pop("model", "fake-model"),
        **kwargs,
    )
    return Gateway(config)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class CompletionContractTests(unittest.TestCase):
    def test_plain_completion_contract(self):
        with FakeServer(completion_text='{"ok": true}') as server:
            gateway = _gateway(server)
            completion = gateway.complete("只输出 JSON")

            self.assertEqual(completion.text, '{"ok": true}')
            self.assertEqual(completion.model, "fake-model")
            self.assertEqual(completion.attempts, 1)
            self.assertEqual(len(completion.prompt_sha256), 64)
            self.assertEqual(len(completion.response_sha256), 64)

            request = server.requests[-1]
            self.assertEqual(request["path"], "/v1/chat/completions")
            payload = request["json"]
            self.assertEqual(payload["model"], "fake-model")
            self.assertEqual(payload["temperature"], 0.0)
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["messages"], [{"role": "user", "content": "只输出 JSON"}])
            self.assertNotIn("authorization", request["headers"])

    def test_system_prompt_and_seed_are_sent(self):
        with FakeServer() as server:
            gateway = _gateway(server, seed=42)
            gateway.complete("hi", system="be terse")

            payload = server.requests[-1]["json"]
            self.assertEqual(payload["messages"][0], {"role": "system", "content": "be terse"})
            self.assertEqual(payload["seed"], 42)

    def test_api_key_goes_into_header_only(self):
        with FakeServer() as server:
            gateway = _gateway(server, api_key="sk-test-secret")
            gateway.complete("hi")

            request = server.requests[-1]
            self.assertEqual(request["headers"]["authorization"], "Bearer sk-test-secret")
            self.assertNotIn("sk-test-secret", json.dumps(request["json"]))

    def test_empty_completion_is_an_error(self):
        with FakeServer(completion_text="   ") as server:
            with self.assertRaises(EmptyResponse):
                _gateway(server).complete("hi")

    def test_missing_choices_is_a_protocol_error(self):
        with FakeServer() as server:
            server.queue(body={"choices": []})
            with self.assertRaises(ProtocolError):
                _gateway(server, max_retries=0).complete("hi")

    def test_non_json_body_is_a_protocol_error(self):
        with FakeServer() as server:
            server.queue(body="<html>not json</html>")
            with self.assertRaises(ProtocolError):
                _gateway(server, max_retries=0).complete("hi")

    def test_prompt_too_large_fails_before_any_request(self):
        with FakeServer() as server:
            gateway = _gateway(server, max_prompt_bytes=8)
            with self.assertRaises(PromptTooLarge):
                gateway.complete("x" * 100)
            self.assertEqual(server.requests, [])

    def test_response_too_large_is_rejected(self):
        big = {"id": "x", "choices": [{"message": {"content": "y" * 5000}}]}
        with FakeServer() as server:
            server.queue(body=big)
            with self.assertRaises(ResponseTooLarge):
                _gateway(server, max_retries=0, max_response_bytes=512).complete("hi")


class RetryPolicyTests(unittest.TestCase):
    def test_retryable_500_is_retried_then_succeeds(self):
        with FakeServer() as server:
            server.queue(status=500).queue(status=503)
            completion = _gateway(server, max_retries=2).complete("hi")
            self.assertEqual(completion.attempts, 3)
            self.assertEqual(len(server.requests), 3)

    def test_client_error_is_not_retried(self):
        with FakeServer() as server:
            server.queue(status=400, body={"error": "bad request"})
            with self.assertRaises(HTTPStatusError) as ctx:
                _gateway(server, max_retries=2).complete("hi")
            self.assertEqual(ctx.exception.detail["status"], 400)
            self.assertEqual(len(server.requests), 1)

    def test_retry_budget_exhaustion_raises(self):
        with FakeServer() as server:
            server.queue(status=500).queue(status=500).queue(status=500)
            with self.assertRaises(HTTPStatusError):
                _gateway(server, max_retries=2).complete("hi")
            self.assertEqual(len(server.requests), 3)

    def test_timeout_is_reported(self):
        with FakeServer() as server:
            server.queue(delay=0.5)
            with self.assertRaises(BackendTimeout):
                _gateway(server, timeout_s=0.15, max_retries=0).complete("hi")

    def test_unreachable_backend_is_reported(self):
        config = GatewayConfig(base_url=f"http://127.0.0.1:{_free_port()}/v1", model="m", max_retries=0)
        with self.assertRaises(BackendUnreachable):
            Gateway(config).complete("hi")

    def test_error_messages_do_not_leak_api_key(self):
        with FakeServer() as server:
            server.queue(status=400, body={"error": "nope"})
            gateway = _gateway(server, api_key="sk-super-secret", max_retries=0)
            with self.assertRaises(HTTPStatusError) as ctx:
                gateway.complete("hi")
            self.assertNotIn("sk-super-secret", str(ctx.exception))
            self.assertNotIn("sk-super-secret", json.dumps(ctx.exception.detail))


class JsonContractTests(unittest.TestCase):
    def test_plain_json_is_parsed(self):
        with FakeServer(completion_text='{"amount": "100.00"}') as server:
            data, completion = _gateway(server).complete_json("抽参")
            self.assertEqual(data, {"amount": "100.00"})
            self.assertEqual(completion.attempts, 1)

    def test_fenced_json_is_parsed(self):
        text = '说明如下：\n```json\n{"a": [1, 2]}\n```\n以上。'
        with FakeServer(completion_text=text) as server:
            data, _ = _gateway(server).complete_json("抽参")
            self.assertEqual(data, {"a": [1, 2]})

    def test_non_json_completion_raises(self):
        with FakeServer(completion_text="抱歉，我无法完成。") as server:
            with self.assertRaises(JSONExtractionError):
                _gateway(server).complete_json("抽参")

    def test_prose_wrapped_json_needs_opt_in(self):
        text = '结果是 {"a": 1} 这样'
        with FakeServer(completion_text=text) as server:
            gateway = _gateway(server)
            with self.assertRaises(JSONExtractionError):
                gateway.complete_json("抽参")
            data, _ = gateway.complete_json("抽参", allow_prose=True)
            self.assertEqual(data, {"a": 1})


class CheckTests(unittest.TestCase):
    def test_check_reports_available_model(self):
        with FakeServer(models=["fake-model", "other"]) as server:
            report = _gateway(server).check()
            self.assertTrue(report["ok"])
            self.assertTrue(report["model_available"])
            self.assertIn("fake-model", report["models"])

    def test_check_reports_missing_model(self):
        with FakeServer(models=["other"]) as server:
            report = _gateway(server).check()
            self.assertFalse(report["model_available"])


class EvidenceTests(unittest.TestCase):
    def test_evidence_is_hash_only_and_contains_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "evidence.jsonl")
            with FakeServer(completion_text='{"ok": true}') as server:
                gateway = _gateway(server, api_key="sk-evidence-secret", evidence_path=path)
                gateway.complete("客户名称：某某公司")
                gateway.complete_json("第二问")

            with open(path, "r", encoding="utf-8") as handle:
                lines = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["kind"], "completion")
            self.assertEqual(len(lines[0]["prompt_sha256"]), 64)
            self.assertEqual(len(lines[0]["response_sha256"]), 64)
            self.assertNotIn("response_text", lines[0])
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read()
            self.assertNotIn("sk-evidence-secret", raw)
            self.assertNotIn("某某公司", raw)
            self.assertNotIn("第二问", raw)

    def test_json_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "evidence.jsonl")
            with FakeServer(completion_text="不是 JSON") as server:
                gateway = _gateway(server, evidence_path=path)
                with self.assertRaises(JSONExtractionError):
                    gateway.complete_json("抽参")
            with open(path, "r", encoding="utf-8") as handle:
                lines = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(lines[-1]["kind"], "json_extraction_failure")


class ConfigTests(unittest.TestCase):
    def test_env_config_mapping(self):
        env = {
            "LLM_GATEWAY_BASE_URL": "http://127.0.0.1:11434/v1",
            "LLM_GATEWAY_MODEL": "qwen2.5:1.5b",
            "LLM_GATEWAY_MAX_RETRIES": "1",
            "LLM_GATEWAY_TIMEOUT_S": "12.5",
            "LLM_GATEWAY_SEED": "7",
        }
        config = GatewayConfig.from_env(env)
        self.assertEqual(config.model, "qwen2.5:1.5b")
        self.assertEqual(config.max_retries, 1)
        self.assertEqual(config.timeout_s, 12.5)
        self.assertEqual(config.seed, 7)

    def test_env_config_rejects_bad_int(self):
        with self.assertRaises(ConfigError):
            GatewayConfig.from_env({"LLM_GATEWAY_MAX_RETRIES": "many"})

    def test_invalid_base_url_is_rejected(self):
        with self.assertRaises(ConfigError):
            Gateway(GatewayConfig(base_url="ftp://example.com", model="m"))

    def test_bare_host_gets_v1_suffix(self):
        gateway = Gateway(GatewayConfig(base_url="http://127.0.0.1:11434/", model="m"))
        self.assertEqual(gateway.config.base_url, "http://127.0.0.1:11434/")
        self.assertEqual(gateway.backend.base_url, "http://127.0.0.1:11434/v1")


class BenchmarkTests(unittest.TestCase):
    def test_benchmark_reports_latency_and_tokens(self):
        with FakeServer(usage={"prompt_tokens": 5, "completion_tokens": 10}) as server:
            report = _gateway(server).benchmark(n=2, warmup=0)
            self.assertEqual(report["n"], 2)
            self.assertEqual(report["completion_tokens_total"], 20)
            self.assertIsNotNone(report["tokens_per_second"])
            self.assertGreater(report["latency_s"]["median"], 0)
            self.assertEqual(len(server.requests), 2)

    def test_benchmark_rejects_bad_n(self):
        with FakeServer() as server:
            with self.assertRaises(ConfigError):
                _gateway(server).benchmark(n=0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
