"""Coding Agent 主循环：用四道护栏保证自主执行不失控。

护栏 1：单次命令超时（run_python 内部限制），避免卡死
护栏 2：总步数上限，避免无限自我修正烧掉时间
护栏 3：失败输出原样回喂模型，形成"执行→观察→修正"闭环
护栏 4：调用 finish 前强制产物校验，不允许谎报完成
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import WORKSPACE_DIR, Settings
from .llm import LLMClient
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, dispatch
from .tracing import RunTracer


@dataclass
class StepRecord:
    index: int
    tool: str
    arguments: str
    result: str


@dataclass
class RunReport:
    task: str
    steps: list[StepRecord] = field(default_factory=list)
    finished: bool = False
    verified: bool = False
    verify_message: str = ""
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    run_dir: str = ""


def _message_to_dict(message) -> dict:
    """把 SDK 返回的 message 对象转成可回传的 dict（带 tool_calls 时才带该字段）。"""
    data: dict = {"role": "assistant", "content": message.content or ""}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        data["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in tool_calls
        ]
    return data


def default_verifier(workspace: Path = WORKSPACE_DIR) -> tuple[bool, str]:
    """产物校验：至少有一个 .py 文件，且全部能通过语法编译。"""
    py_files = sorted(workspace.rglob("*.py"))
    if not py_files:
        return False, "workspace 中还没有任何 .py 文件，说明代码尚未落地"

    broken = []
    for path in py_files:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            broken.append(f"{path.name}: {result.stderr.strip()[:300]}")

    if broken:
        return False, "以下文件语法不通过：\n" + "\n".join(broken)
    return True, f"{len(py_files)} 个 Python 文件语法校验通过"


class CodingAgent:
    def __init__(self, llm: LLMClient, settings: Settings, workspace: Path = WORKSPACE_DIR):
        self.llm = llm
        self.settings = settings
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        task: str,
        max_steps: int | None = None,
        verifier=None,
    ) -> RunReport:
        limit = max_steps or self.settings.max_steps
        verify = verifier or (lambda: default_verifier(self.workspace))

        tracer = RunTracer(self.settings)
        tracer.start(task, self.settings.primary.model, limit)

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        report = RunReport(task=task)

        for step in range(1, limit + 1):
            # 步数预算告警：接近上限时停止探索，避免"做完了却没来得及收尾"
            if limit - step == 2:
                messages.append(
                    {
                        "role": "user",
                        "content": "只剩 2 步预算：请立即停止探索和额外验证，直接调用 finish 汇报已完成的结果。",
                    }
                )

            llm_start = time.perf_counter()
            try:
                message = self.llm.chat(messages, TOOL_SCHEMAS)
            except RuntimeError as exc:
                tracer.log_llm(
                    step,
                    0,
                    0,
                    int((time.perf_counter() - llm_start) * 1000),
                    ok=False,
                    error=str(exc),
                )
                report.run_dir = str(
                    tracer.finish(finished=False, verified=False, error=str(exc))
                )
                raise
            tracer.log_llm(
                step,
                *self.llm.last_usage,
                int((time.perf_counter() - llm_start) * 1000),
            )
            assistant = _message_to_dict(message)
            messages.append(assistant)

            # 模型只说不做：明确推它去落地，而不是就此结束
            if not assistant.get("tool_calls"):
                report.steps.append(StepRecord(step, "-", "", (message.content or "")[:300]))
                tracer.log_note(step, message.content or "")
                messages.append(
                    {
                        "role": "user",
                        "content": "只给计划不够：请立刻调用工具写文件并运行验证，完成后再调用 finish。",
                    }
                )
                continue

            finished_now = False
            for call in assistant["tool_calls"]:
                name = call["function"]["name"]
                arguments = call["function"]["arguments"]
                tool_start = time.perf_counter()
                result, is_finish = dispatch(name, arguments)
                tracer.log_tool(
                    step,
                    name,
                    arguments,
                    result,
                    int((time.perf_counter() - tool_start) * 1000),
                )

                if is_finish:
                    ok, verify_message = verify()
                    tracer.log_verify(step, ok, verify_message)
                    report.verified = ok
                    report.verify_message = verify_message
                    if ok:
                        report.finished = True
                        report.summary = result
                        finished_now = True
                        content = result
                    else:
                        content = (
                            f"产物校验未通过：{verify_message}\n"
                            "不要谎报完成，请修复后重新运行验证，再调用 finish。"
                        )
                else:
                    content = result

                print(f"[{step}/{limit}] {name}({arguments[:120]}) -> {content[:160]!r}", flush=True)
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": content[:8000]}
                )
                report.steps.append(StepRecord(step, name, arguments[:500], content[:800]))

            if finished_now:
                break

        report.artifacts = sorted(
            item.relative_to(self.workspace).as_posix()
            for item in self.workspace.rglob("*")
            if item.is_file()
        )
        report.run_dir = str(
            tracer.finish(
                finished=report.finished,
                verified=report.verified,
                verify_message=report.verify_message,
                steps=len(report.steps),
                artifacts=report.artifacts,
                summary=report.summary[:1000],
            )
        )
        return report
