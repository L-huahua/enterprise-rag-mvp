"""OpenAI 兼容客户端封装：重试 + 主备切换 + 统一返回 assistant message。"""

from __future__ import annotations

import logging
import time
from typing import Any

from openai import OpenAI

from .config import Settings

logger = logging.getLogger(__name__)

# 命中这些关键字视为可重试错误（限流、超时、连接、服务端 5xx）
RETRYABLE_TOKENS = ("429", "500", "502", "503", "504", "timeout", "timed out", "connection")


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        # 累计用量，供交付材料里的费用统计使用
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0
        self.last_usage: tuple[int, int] = (0, 0)
        self.clients = [
            OpenAI(
                api_key=provider.api_key,
                base_url=provider.base_url,
                timeout=settings.request_timeout,
                max_retries=0,  # 重试策略由本类自己控制
            )
            for provider in settings.providers
        ]

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Any:
        """调用模型并返回 assistant message；全部失败时抛 RuntimeError。"""
        if not self.clients:
            raise RuntimeError("没有可用的模型服务商，请检查 .env 中的 LLM_API_KEY")

        last_error: Exception | None = None
        for attempt in range(3):
            for index, client in enumerate(self.clients):
                provider = self.settings.providers[index]
                try:
                    kwargs: dict[str, Any] = {
                        "model": provider.model,
                        "messages": messages,
                        "temperature": self.settings.temperature,
                    }
                    if tools:
                        kwargs["tools"] = tools
                        kwargs["tool_choice"] = "auto"

                    response = client.chat.completions.create(**kwargs)
                    usage = getattr(response, "usage", None)
                    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                    self.last_usage = (prompt_tokens, completion_tokens)
                    self.prompt_tokens += prompt_tokens
                    self.completion_tokens += completion_tokens
                    self.calls += 1
                    return response.choices[0].message
                except Exception as exc:  # noqa: BLE001 网络/限流/服务端错误统一处理
                    last_error = exc
                    logger.warning(
                        "[%s] 第 %d/3 次调用失败：%s", provider.name, attempt + 1, exc
                    )
                    retryable = any(token in str(exc).lower() for token in RETRYABLE_TOKENS)
                    if not retryable:
                        continue  # 不可重试则直接换下一个服务商
            time.sleep(2**attempt)  # 指数退避：1s、2s、4s

        raise RuntimeError(f"模型调用失败：{last_error}")
