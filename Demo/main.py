"""命令行入口。

用法：
    python main.py --selfcheck          # 环境自检（不调用模型）
    python main.py --selfcheck --api    # 额外验证模型连通性
    python main.py "任务描述"            # 让 Coding Agent 自主开发并验证
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from agent.config import WORKSPACE_DIR, load_settings
from agent.llm import LLMClient
from agent.loop import CodingAgent
from agent.selfcheck import run_selfcheck


def main() -> int:
    parser = argparse.ArgumentParser(description="Coding Agent 入口")
    parser.add_argument("task", nargs="?", help="要交给 Agent 完成的任务描述")
    parser.add_argument("--selfcheck", action="store_true", help="只做环境自检")
    parser.add_argument("--api", action="store_true", help="自检时额外验证模型连通")
    parser.add_argument("--max-steps", type=int, default=None, help="覆盖最大步数")
    parser.add_argument("--verbose", action="store_true", help="打印完整日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.selfcheck:
        return run_selfcheck(api_check=args.api)

    if not args.task:
        parser.print_help()
        return 2

    settings = load_settings()
    if not settings.providers:
        print("未配置模型：请把 .env.example 复制为 .env 并填入 LLM_API_KEY")
        return 2

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    agent = CodingAgent(LLMClient(settings), settings)
    report = agent.run(args.task, max_steps=args.max_steps)

    print("\n" + "=" * 60)
    print(f"任务：{report.task}")
    print(f"步数：{len(report.steps)}  完成：{report.finished}  校验：{report.verified}")
    print(f"校验信息：{report.verify_message}")
    print(f"产物：{report.artifacts or '（无）'}")
    if report.summary:
        print(f"说明：{report.summary}")
    if report.run_dir:
        meta_path = Path(report.run_dir) / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            print(
                f"耗时：{meta.get('duration_s')}s  "
                f"token：{meta.get('prompt_tokens')} in / {meta.get('completion_tokens')} out  "
                f"费用估算：¥{meta.get('cost_yuan')}"
            )
        print(f"日志目录：{report.run_dir}")
    print("=" * 60)
    return 0 if report.finished else 1


if __name__ == "__main__":
    sys.exit(main())
