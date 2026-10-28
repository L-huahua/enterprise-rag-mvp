"""Agent 可调用的工具集合。

所有文件操作都被限制在 workspace 目录内，避免 Agent 写到项目外的文件。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .config import WORKSPACE_DIR

MAX_OUTPUT = 4000
MAX_READ_CHARS = 12000
MAX_READ_LINES = 400


def _safe_path(raw: str) -> Path:
    """把相对路径解析到 workspace 内，越界直接报错。"""
    root = WORKSPACE_DIR.resolve()
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"路径越界：{raw} 不在 workspace 内")
    return target


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    """长输出保留头尾：报错堆栈通常在尾部，不能简单截掉后半段。"""
    if len(text) <= limit:
        return text
    head = text[: limit // 3]
    tail = text[-(limit * 2 // 3):]
    return f"{head}\n...（省略 {len(text) - limit} 字符）...\n{tail}"


def list_files(path: str = ".") -> str:
    target = _safe_path(path)
    if not target.exists():
        return f"目录不存在：{path}"
    entries = []
    for item in sorted(target.rglob("*")):
        if item.is_file():
            size = item.stat().st_size
            entries.append(f"{item.relative_to(WORKSPACE_DIR.resolve()).as_posix()}  ({size} B)")
    return "\n".join(entries) if entries else "（空目录）"


def read_file(path: str, start_line: int = 1, max_lines: int = MAX_READ_LINES) -> str:
    """按行分页读取文件，返回内容前先给出行号范围与续读提示。

    单次最多返回 MAX_READ_CHARS 字符，避免长文件被截断后模型只能靠猜。
    """
    target = _safe_path(path)
    if not target.is_file():
        return f"文件不存在：{path}"

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    if total == 0:
        return f"{path}：空文件"

    start = max(1, min(int(start_line), total))
    selected: list[str] = []
    used = 0
    index = start - 1
    while index < total and len(selected) < max_lines:
        line = lines[index]
        cost = len(line) + 1
        if selected and used + cost > MAX_READ_CHARS:
            break
        selected.append(line if cost <= MAX_READ_CHARS else line[:MAX_READ_CHARS])
        used += min(cost, MAX_READ_CHARS)
        index += 1

    end = start + len(selected) - 1
    header = f"{path}：共 {total} 行，本次返回第 {start}-{end} 行"
    if end < total:
        header += f"（未读完，继续读取请设 start_line={end + 1}）"
    return f"{header}\n---\n" + "\n".join(selected)


def write_file(path: str, content: str) -> str:
    target = _safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"已写入 {path}（{len(content)} 字符，{content.count(chr(10)) + 1} 行）"


def run_python(path: str, args: list[str] | None = None, timeout: int = 60) -> str:
    """用当前 venv 的 Python 执行 workspace 内的脚本，返回退出码与输出。"""
    target = _safe_path(path)
    if not target.is_file():
        return f"文件不存在：{path}，请先 write_file 创建"

    command = [sys.executable, str(target), *(args or [])]
    try:
        result = subprocess.run(
            command,
            cwd=str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(timeout, 300),
        )
    except subprocess.TimeoutExpired:
        return f"执行超时（>{timeout}s）：{path}\n【处理建议】缩小任务规模或加日志定位卡点"

    output = [
        f"exit_code={result.returncode}",
        f"--- stdout ---\n{_truncate(result.stdout.strip()) or '（空）'}",
        f"--- stderr ---\n{_truncate(result.stderr.strip()) or '（空）'}",
    ]
    return "\n".join(output)


def check_python(path: str) -> str:
    """只做语法编译检查，比整体运行更快定位低级错误。"""
    target = _safe_path(path)
    if not target.is_file():
        return f"文件不存在：{path}"
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0:
        return f"{path} 语法检查通过"
    return f"{path} 语法错误：\n{_truncate(result.stderr.strip())}"


def finish(summary: str) -> str:
    """声明任务完成。真正是否结束由 loop 的产物校验决定。"""
    return summary


TOOLS = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "run_python": run_python,
    "check_python": check_python,
    "finish": finish,
}


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出 workspace 内的文件和大小，用于确认已有哪些产物",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "子目录，默认根目录"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "按行读取 workspace 内文件，长文件自动分页；返回内容首行会标注总行数与本次行号范围，未读完时按提示设置 start_line 继续读",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对路径"},
                    "start_line": {"type": "integer", "description": "起始行号，从 1 开始，默认 1"},
                    "max_lines": {"type": "integer", "description": "本次最多读取行数，默认 400"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖写入文件，自动创建父目录。内容必须是完整文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对路径，如 video_agent.py"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "用项目 venv 的 Python 执行脚本，返回 exit_code、stdout、stderr",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要执行的脚本相对路径"},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "命令行参数",
                    },
                    "timeout": {"type": "integer", "description": "超时秒数，默认 60"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_python",
            "description": "对脚本做语法检查，不执行逻辑",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "所有功能已验证可用后调用，提交完成说明",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string", "description": "完成情况说明"}},
                "required": ["summary"],
            },
        },
    },
]


def dispatch(name: str, raw_arguments: str) -> tuple[str, bool]:
    """执行工具调用，返回 (结果文本, 是否调用 finish)。"""
    if name not in TOOLS:
        return f"未知工具：{name}", False

    try:
        arguments = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError as exc:
        return f"参数不是合法 JSON：{exc}。请重新组织参数后重试", False

    try:
        result = TOOLS[name](**arguments)
    except TypeError as exc:
        return f"参数与工具签名不匹配：{exc}", False
    except Exception as exc:  # noqa: BLE001 工具异常必须回喂给模型，而不是让循环崩掉
        return f"工具执行异常：{exc}", False

    return str(result), name == "finish"
