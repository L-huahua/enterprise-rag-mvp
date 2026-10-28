"""集中读取运行配置。

所有密钥只从环境变量 / .env 读取，绝不出现在代码里，也不进 Git。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 工具与运行产物统一放在仓库根目录（Demo 外），Demo 内只保留代码和配置
REPO_ROOT = PROJECT_ROOT.parent
WORKSPACE_DIR = REPO_ROOT / "artifacts" / "workspace"
TOOLS_DIR = REPO_ROOT / "tools"

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Provider:
    """一个 OpenAI 兼容的模型服务商。"""

    name: str
    api_key: str
    base_url: str
    model: str


@dataclass
class Settings:
    providers: list[Provider] = field(default_factory=list)
    max_steps: int = 24
    run_timeout: int = 60
    request_timeout: float = 120.0
    temperature: float = 0.2
    # 计费单价（元 / 百万 token），仅用于交付材料里的费用估算
    price_input_per_m: float = 1.0
    price_output_per_m: float = 2.0

    @property
    def primary(self) -> Provider:
        if not self.providers:
            raise RuntimeError(
                "未配置模型服务：请把 .env.example 复制为 .env 并填入 LLM_API_KEY"
            )
        return self.providers[0]


def _build_provider(prefix: str, name: str) -> Provider | None:
    api_key = os.getenv(f"{prefix}API_KEY", "").strip()
    if not api_key:
        return None
    return Provider(
        name=name,
        api_key=api_key,
        base_url=os.getenv(f"{prefix}BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
        model=os.getenv(f"{prefix}MODEL", "deepseek-chat"),
    )


def load_settings() -> Settings:
    providers: list[Provider] = []
    for prefix, name in (("LLM_", "主模型"), ("LLM_FALLBACK_", "备用模型")):
        provider = _build_provider(prefix, name)
        if provider:
            providers.append(provider)

    return Settings(
        providers=providers,
        max_steps=int(os.getenv("AGENT_MAX_STEPS", "24")),
        run_timeout=int(os.getenv("AGENT_RUN_TIMEOUT", "60")),
        request_timeout=float(os.getenv("LLM_TIMEOUT", "120")),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        price_input_per_m=float(os.getenv("LLM_PRICE_INPUT", "1.0")),
        price_output_per_m=float(os.getenv("LLM_PRICE_OUTPUT", "2.0")),
    )


def find_ffmpeg() -> str | None:
    """优先用项目内置便携版，其次用系统 PATH；找不到返回 None。"""
    local = TOOLS_DIR / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local.exists():
        return str(local)
    return shutil.which("ffmpeg")


def find_ffprobe() -> str | None:
    local = TOOLS_DIR / "ffmpeg" / "bin" / "ffprobe.exe"
    if local.exists():
        return str(local)
    return shutil.which("ffprobe")
