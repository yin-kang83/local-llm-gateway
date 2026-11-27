"""CLI behaviour tests: exit codes and safe error output (offline)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

import _bootstrap  # noqa: F401
from fake_server import FakeServer

from local_llm_gateway.cli import main


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class CliTests(unittest.TestCase):
    def test_version(self):
        code, out, _ = _run_cli(["version", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["version"], "0.1.1")

    def test_check_success_is_zero_and_json(self):
        with FakeServer(models=["fake-model"]) as server:
            code, out, _ = _run_cli(
                ["check", "--base-url", server.base_url, "--model", "fake-model", "--json"]
            )
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertTrue(payload["model_available"])

    def test_check_missing_model_exits_4(self):
        with FakeServer(models=["other"]) as server:
            code, _, _ = _run_cli(["check", "--base-url", server.base_url, "--model", "fake-model"])
            self.assertEqual(code, 4)

    def test_run_expect_json_success(self):
        with FakeServer(completion_text='{"amount": "100.00"}') as server:
            code, out, _ = _run_cli(
                [
                    "run",
                    "--base-url",
                    server.base_url,
                    "--model",
                    "fake-model",
                    "--prompt",
                    "抽参",
                    "--expect-json",
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["json"], {"amount": "100.00"})

    def test_run_invalid_json_exits_5(self):
        with FakeServer(completion_text="抱歉，不知道。") as server:
            code, out, _ = _run_cli(
                [
                    "run",
                    "--base-url",
                    server.base_url,
                    "--model",
                    "fake-model",
                    "--prompt",
                    "抽参",
                    "--expect-json",
                    "--json",
                ]
            )
            self.assertEqual(code, 5)
            self.assertEqual(json.loads(out)["error"], "json_extraction_error")

    def test_unreachable_backend_exits_3(self):
        code, _, err = _run_cli(
            ["check", "--base-url", "http://127.0.0.1:9/v1", "--model", "m"]
        )
        self.assertEqual(code, 3)
        self.assertIn("backend_unreachable", err)

    def test_bad_config_exits_2(self):
        code, _, _ = _run_cli(["check", "--base-url", "ftp://example.com", "--model", "m"])
        self.assertEqual(code, 2)

    def test_prompt_file_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "prompt.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("只输出 JSON")
            with FakeServer(completion_text='{"ok": true}') as server:
                code, out, _ = _run_cli(
                    [
                        "run",
                        "--base-url",
                        server.base_url,
                        "--model",
                        "fake-model",
                        "--prompt-file",
                        path,
                        "--json",
                    ]
                )
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["text"], '{"ok": true}')
                self.assertEqual(server.requests[-1]["json"]["messages"][-1]["content"], "只输出 JSON")

    def test_no_subcommand_prints_help_and_exits_2(self):
        code, out, _ = _run_cli([])
        self.assertEqual(code, 2)
        self.assertIn("usage", out.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
