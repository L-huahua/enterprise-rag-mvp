"""运行追踪：把每次执行落盘为可复核的证据。

产出结构（artifacts/runs/<run_id>/）：
    task.md        需求原文
    events.jsonl   结构化事件流（一次 LLM 调用 / 一次工具调用一行）
    transcript.md  人类可读执行记录
    meta.json      汇总：耗时、token、费用、产物、校验结果
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from .config import REPO_ROOT, Settings

RUNS_DIR = REPO_ROOT / "artifacts" / "runs"


class RunTracer:
    """记录一次 Agent 运行的完整轨迹，供交付材料直接引用。"""

    def __init__(self, settings: Settings, root: Path = RUNS_DIR):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_id = stamp
        self.dir = root / stamp
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.transcript_path = self.dir / "transcript.md"
        self.meta_path = self.dir / "meta.json"

        self.price_in = settings.price_input_per_m
        self.price_out = settings.price_output_per_m
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.llm_calls = 0
        self.t0 = time.perf_counter()
        self.task = ""
        self.model = ""
        self.max_steps = 0

    # ---------- 基础设施 ----------
    def _event(self, payload: dict) -> None:
        payload.setdefault("ts", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        payload.setdefault("elapsed_ms", int((time.perf_counter() - self.t0) * 1000))
        with self.events_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _line(self, text: str) -> None:
        with self.transcript_path.open("a", encoding="utf-8") as fp:
            fp.write(text + "\n")

    def start(self, task: str, model: str, max_steps: int) -> None:
        self.task, self.model, self.max_steps = task, model, max_steps
        (self.dir / "task.md").write_text(f"# 任务\n\n{task}\n", encoding="utf-8")
        self.transcript_path.write_text(
            f"# 执行记录 {self.run_id}\n\n- 任务：{task}\n- 模型：{model}\n- 步数上限：{max_steps}\n\n",
            encoding="utf-8",
        )
        self._event({"type": "run_start", "task": task, "model": model, "max_steps": max_steps})

    # ---------- 事件记录 ----------
    def log_llm(
        self,
        step: int,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: int,
        ok: bool = True,
        error: str = "",
    ) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        if ok:
            self.llm_calls += 1
        self._event(
            {
                "type": "llm_call",
                "step": step,
                "ok": ok,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "duration_ms": duration_ms,
                "error": error,
            }
        )
        status = "OK" if ok else "FAIL"
        self._line(
            f"### Step {step} · LLM {status} · {duration_ms} ms · tokens {prompt_tokens}/{completion_tokens}"
            + (f"\n\n```\n{error[:500]}\n```" if error else "")
        )

    def log_tool(
        self,
        step: int,
        tool: str,
        arguments: str,
        result: str,
        duration_ms: int,
        ok: bool = True,
    ) -> None:
        self._event(
            {
                "type": "tool_call",
                "step": step,
                "tool": tool,
                "arguments": arguments,
                "result": result[:2000],
                "duration_ms": duration_ms,
                "ok": ok,
            }
        )
        self._line(
            f"**工具** `{tool}` · {duration_ms} ms\n\n"
            f"参数：`{arguments[:300]}`\n\n"
            f"结果（前 600 字）：\n\n```\n{result[:600]}\n```\n"
        )

    def log_verify(self, step: int, ok: bool, message: str) -> None:
        self._event({"type": "verify", "step": step, "ok": ok, "message": message})
        self._line(f"**产物校验** {'通过' if ok else '未通过'}：{message}\n")

    def log_note(self, step: int, content: str) -> None:
        self._event({"type": "assistant_note", "step": step, "content": content[:1000]})
        self._line(f"**模型说明**：{content[:500]}\n")

    # ---------- 收尾 ----------
    def cost_yuan(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * self.price_in
            + self.completion_tokens / 1_000_000 * self.price_out
        )

    def finish(self, **fields) -> Path:
        duration = round(time.perf_counter() - self.t0, 1)
        meta = {
            "run_id": self.run_id,
            "task": self.task,
            "model": self.model,
            "duration_s": duration,
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "price_input_per_m": self.price_in,
            "price_output_per_m": self.price_out,
            "cost_yuan": round(self.cost_yuan(), 4),
            **fields,
        }
        self.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._event({"type": "run_end", "duration_s": duration, "cost_yuan": meta["cost_yuan"]})
        self._line(
            f"\n## 汇总\n\n- 耗时：{duration}s\n- LLM 调用：{self.llm_calls} 次\n"
            f"- token：{self.prompt_tokens} in / {self.completion_tokens} out\n"
            f"- 估算费用：¥{meta['cost_yuan']}\n- 日志目录：`artifacts/runs/{self.run_id}/`\n"
        )
        return self.dir
