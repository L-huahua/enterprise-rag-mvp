"""环境自检：不调用模型也能确认本地链路是否就绪。"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from .config import load_settings, find_ffmpeg, find_ffprobe, PROJECT_ROOT, WORKSPACE_DIR

REQUIRED_PACKAGES = ("openai", "dotenv")
OPTIONAL_PACKAGES = ("cv2", "flask")


def _check_module(name: str) -> tuple[bool, str]:
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 缺包/装坏都归为不可用
        return False, f"不可用（{exc.__class__.__name__}）"
    version = getattr(module, "__version__", "")
    return True, version or "ok"


def _check_ffmpeg() -> tuple[bool, str]:
    path = find_ffmpeg()
    if not path:
        return False, "未找到 ffmpeg（项目 tools/ffmpeg 或系统 PATH 均无）"
    return True, path


def _check_ffprobe() -> tuple[bool, str]:
    path = find_ffprobe()
    if not path:
        return False, "未找到 ffprobe"
    return True, path


def _check_env_key() -> tuple[bool, str]:
    settings = load_settings()
    if not settings.providers:
        return False, "未配置 LLM_API_KEY（.env 缺失或为空）"
    provider = settings.primary
    return True, f"{provider.name} {provider.model} @ {provider.base_url}"


def _check_api() -> tuple[bool, str]:
    """实际发一次最小请求，验证 Key、网络、模型名三者都通。"""
    from .llm import LLMClient

    try:
        client = LLMClient(load_settings())
        message = client.chat([{"role": "user", "content": "只回复：pong"}])
    except Exception as exc:  # noqa: BLE001 自检失败要报告而不是崩溃
        return False, f"调用失败：{exc}"
    return True, (message.content or "").strip()[:40]


def run_selfcheck(api_check: bool = False) -> int:
    rows: list[tuple[str, bool, str]] = []

    py_ok = sys.version_info >= (3, 10)
    rows.append(("Python", py_ok, sys.version.split()[0]))

    for name in REQUIRED_PACKAGES:
        ok, detail = _check_module(name)
        rows.append((name, ok, detail))

    for name in OPTIONAL_PACKAGES:
        ok, detail = _check_module(name)
        rows.append((f"{name}（可选）", ok, detail))

    rows.append(("ffmpeg", *_check_ffmpeg()))
    rows.append(("ffprobe", *_check_ffprobe()))
    rows.append((".env 配置", *_check_env_key()))
    if api_check:
        rows.append(("模型连通", *_check_api()))

    print("\n环境自检")
    print("-" * 60)
    for name, ok, detail in rows:
        mark = "OK  " if ok else "FAIL"
        print(f"[{mark}] {name:<16} {detail}")
    print("-" * 60)
    print(f"项目根目录：{PROJECT_ROOT}")
    print(f"workspace ：{WORKSPACE_DIR}")

    required_failed = [
        name for name, ok, _ in rows if not ok and "可选" not in name
    ]
    if required_failed:
        print(f"未通过项：{', '.join(required_failed)}")
        return 1
    print("全部必需项通过。")
    return 0
