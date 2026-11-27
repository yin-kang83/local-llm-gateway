"""Command line interface.

Examples
--------
::

    llm-gateway check --base-url http://127.0.0.1:11434/v1 --model qwen2.5:1.5b
    llm-gateway run --prompt "只输出 JSON: {\"ok\": true}" --expect-json
    llm-gateway bench --n 5

Exit codes are stable: 0 ok, 2 config, 3 backend, 4 protocol, 5 JSON contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from . import __version__
from .errors import ConfigError, GatewayError
from .gateway import Gateway, GatewayConfig


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", help="OpenAI-compatible base URL, e.g. http://127.0.0.1:11434/v1")
    common.add_argument("--model", help="model id served by the backend")
    common.add_argument(
        "--api-key",
        help="API key for cloud-compatible backends; prefer LLM_GATEWAY_API_KEY (never logged)",
    )
    common.add_argument("--timeout", type=float, help="per-request timeout in seconds")
    common.add_argument("--max-retries", type=int, help="bounded retries for timeout/5xx/429")
    common.add_argument("--evidence", help="append-only JSONL evidence log path (hashes only)")
    common.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    parser = argparse.ArgumentParser(
        prog="llm-gateway",
        description="Local-first OpenAI-compatible LLM gateway (fail-closed, auditable).",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "check", parents=[common], help="probe the backend and verify the configured model exists"
    )

    run_parser = subparsers.add_parser(
        "run", parents=[common], help="send one prompt and print the completion"
    )
    prompt_group = run_parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="prompt text")
    prompt_group.add_argument("--prompt-file", help="path to a UTF-8 prompt file")
    run_parser.add_argument("--system", help="optional system prompt")
    run_parser.add_argument(
        "--expect-json", action="store_true", help="require a JSON object/array, else exit 5"
    )
    run_parser.add_argument(
        "--allow-prose", action="store_true", help="allow one prose-wrapped JSON block"
    )

    bench_parser = subparsers.add_parser(
        "bench", parents=[common], help="measure latency / throughput on this machine"
    )
    bench_parser.add_argument("--n", type=int, default=3, help="measured calls (default 3)")
    bench_parser.add_argument("--warmup", type=int, default=1, help="unmeasured warm-up calls")
    bench_parser.add_argument("--prompt", help="override the probe prompt")

    subparsers.add_parser("version", parents=[common], help="print version as JSON")
    return parser


def _config_from_args(args: argparse.Namespace) -> GatewayConfig:
    config = GatewayConfig.from_env()
    overrides: dict = {}
    if args.base_url:
        overrides["base_url"] = args.base_url
    if args.model:
        overrides["model"] = args.model
    if args.api_key:
        overrides["api_key"] = args.api_key
    if args.timeout is not None:
        overrides["timeout_s"] = args.timeout
    if args.max_retries is not None:
        overrides["max_retries"] = args.max_retries
    if args.evidence:
        overrides["evidence_path"] = args.evidence
    return replace(config, **overrides)


def _emit(payload: dict, as_json: bool, human_lines: list[str] | None = None) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for line in human_lines or []:
            print(line)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2

    try:
        if args.command == "version":
            _emit({"version": __version__}, args.json, [f"local-llm-gateway {__version__}"])
            return 0

        gateway = Gateway(_config_from_args(args))

        if args.command == "check":
            report = gateway.check()
            human = [
                f"backend : {report['base_url']}",
                f"model   : {report['model']} "
                f"({'available' if report['model_available'] else 'NOT ADVERTISED'})",
                f"models  : {', '.join(report['models'])}",
                f"latency : {report['latency_s']}s",
            ]
            _emit(report, args.json, human)
            return 0 if report["model_available"] else 4

        if args.command == "run":
            if args.prompt_file:
                try:
                    with open(args.prompt_file, "r", encoding="utf-8") as handle:
                        prompt = handle.read()
                except OSError as exc:
                    raise ConfigError(f"cannot read prompt file: {exc.strerror or 'error'}") from None
            else:
                prompt = args.prompt
            if args.expect_json:
                data, completion = gateway.complete_json(
                    prompt, system=args.system, allow_prose=args.allow_prose
                )
                payload = {"json": data, "meta": completion.meta()}
                _emit(payload, args.json, [json.dumps(data, ensure_ascii=False, indent=2)])
            else:
                completion = gateway.complete(prompt, system=args.system)
                payload = {"text": completion.text, "meta": completion.meta()}
                if args.json:
                    _emit(payload, True)
                else:
                    print(completion.text)
                    print(
                        f"[model={completion.model} latency={completion.latency_s:.3f}s "
                        f"attempts={completion.attempts}]",
                        file=sys.stderr,
                    )
            return 0

        if args.command == "bench":
            report = gateway.benchmark(n=args.n, warmup=args.warmup, prompt=args.prompt)
            latency = report["latency_s"]
            human = [
                f"model   : {report['model']} @ {report['host']}",
                f"latency : min={latency['min']}s median={latency['median']}s max={latency['max']}s",
                f"tokens/s: {report['tokens_per_second']}",
                f"cpu     : {report['hardware']['cpu_count']} cores / {report['hardware']['system']}",
            ]
            _emit(report, args.json, human)
            return 0

        parser.print_help()
        return 2

    except GatewayError as exc:
        if getattr(args, "json", False):
            print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"[{exc.code}] {exc}", file=sys.stderr)
            if exc.detail:
                safe_detail = {
                    key: value
                    for key, value in exc.detail.items()
                    if key not in {"body_excerpt", "reason"} or isinstance(value, (int, str))
                }
                print(f"detail: {json.dumps(safe_detail, ensure_ascii=False)}", file=sys.stderr)
        return exc.exit_code
