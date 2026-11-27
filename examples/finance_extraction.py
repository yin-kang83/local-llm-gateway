"""示例：把本网关接进"确定性抽参"流程（模型只读，不参与计算）。

运行前先起本地模型：

    ollama pull qwen2.5:1.5b
    set LLM_GATEWAY_BASE_URL=http://127.0.0.1:11434/v1
    set LLM_GATEWAY_MODEL=qwen2.5:1.5b

然后：

    python examples/finwiki_adapter.py "甲公司以 1000 万元取得乙公司 30% 股权……"

关键纪律（与 deterministic finance 架构一致）：

1. 模型只负责**读**：把题干抽成候选 JSON；
2. 字段、类型、单位由调用方的契约层校验（这里用 dataclass + Decimal 演示）；
3. 抽不出来 → 直接失败（``json_extraction_error``），**不猜、不兜底**；
4. 任何金额计算都不在本文件出现——那是确定性算子的事。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from local_llm_gateway import Gateway, GatewayConfig, JSONExtractionError

SYSTEM_PROMPT = (
    "你是财务实体抽取器。只输出一行 JSON，不要解释、不要 Markdown 代码块。"
    "金额输出为十进制字符串（单位：万元），未知字段输出 null。"
    '字段：{"initial_cost": "万元字符串或null", "ownership_pct": "百分比字符串或null", '
    '"investee_net_profit": "万元字符串或null", "questions": ["题目要判断的问题"]}'
)

EXAMPLE_QUESTION = (
    "2023 年 1 月 1 日，甲公司支付 1000 万元取得乙公司 30% 股权，"
    "能够施加重大影响，采用权益法核算。2023 年乙公司实现净利润 500 万元。"
    "2023 年末该股权投资可收回金额为 1100 万元。"
    "编制甲公司 2023 年末与长期股权投资相关的会计分录。"
)


@dataclass(frozen=True)
class Extraction:
    """T-box：字段与类型由代码写死，模型无权增删。"""

    initial_cost: Decimal | None
    ownership_pct: Decimal | None
    investee_net_profit: Decimal | None
    questions: list[str]

    def __post_init__(self) -> None:
        if self.ownership_pct is not None and not (0 <= self.ownership_pct <= 100):
            raise ValueError("ownership_pct out of range")


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"金额必须是十进制字符串，收到 {type(value).__name__}")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"无法解析为 Decimal: {value!r}") from None


def extract(question: str, gateway: Gateway | None = None) -> Extraction:
    """抽参并做契约校验；任何一步失败都抛出，不降级。"""
    gateway = gateway or Gateway(GatewayConfig.from_env())
    data, _completion = gateway.complete_json(question, system=SYSTEM_PROMPT)

    if not isinstance(data, dict):
        raise ValueError("模型返回的不是 JSON 对象")

    allowed = {"initial_cost", "ownership_pct", "investee_net_profit", "questions"}
    unexpected = set(data) - allowed
    if unexpected:
        # 契约之外的多余字段：拒绝，而不是默默忽略（防止模型"自由发挥"）
        raise ValueError(f"模型返回了契约之外的字段: {sorted(unexpected)}")

    questions = data.get("questions") or []
    if not isinstance(questions, list) or not all(isinstance(item, str) for item in questions):
        raise ValueError("questions 必须是字符串数组")

    return Extraction(
        initial_cost=_to_decimal(data.get("initial_cost")),
        ownership_pct=_to_decimal(data.get("ownership_pct")),
        investee_net_profit=_to_decimal(data.get("investee_net_profit")),
        questions=questions,
    )


def main(argv: list[str]) -> int:
    question = " ".join(argv[1:]).strip() or EXAMPLE_QUESTION
    try:
        extraction = extract(question)
    except JSONExtractionError as exc:
        # 感知层失败：显式退出，绝不把半截 JSON 交给下游
        print(f"[fail-closed] 抽参失败（{exc.code}）：{exc}", file=sys.stderr)
        return 5
    except Exception as exc:  # 契约校验失败
        print(f"[contract] 契约校验失败：{exc}", file=sys.stderr)
        return 4
    print(json.dumps(extraction.__dict__, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
