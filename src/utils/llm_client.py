"""
Ares v4.0 - 统一 LLM 客户端

支持的 Provider：
  - openai          : 官方 OpenAI（GPT-4o 等）
  - gemini          : Google Gemini 原生 SDK
  - openai_compat   : 任意 OpenAI 兼容端点（one-api / openrouter / 硅基流动等）
                      Gemini 也可通过 base_url 走此模式

环境变量配置（均可在 .env 中设置）：
  ARES_LLM_PROVIDER   : openai | gemini | openai_compat  (默认 openai)
  ARES_LLM_MODEL      : 模型名，默认随 provider 自动选择
  ARES_LLM_BASE_URL   : 自定义 API base URL（openai_compat 必填；openai 可选覆盖）
  ARES_LLM_API_KEY    : 通用 API Key（优先级高于各 provider 专属 key）
  OPENAI_API_KEY      : OpenAI 专属 Key（向后兼容）
  GEMINI_API_KEY      : Gemini 专属 Key
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from src.utils.logger import setup_logger

logger = setup_logger("ares.llm_client")


# ── Provider 默认配置表 ───────────────────────────────────────────────────────

_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
    },
    "gemini": {
        "model": "gemini-3-flash-preview",
        "base_url": "",  # 原生 SDK，不走 HTTP base_url
        "api_key_env": "GEMINI_API_KEY",
    },
    "openai_compat": {
        "model": "gpt-4o",
        "base_url": "",  # 必须由用户在 ARES_LLM_BASE_URL 中指定
        "api_key_env": "ARES_LLM_API_KEY",
    },
}

# Gemini 的 OpenAI 兼容端点（可直接用 openai_compat 模式走 Gemini）
GEMINI_OPENAI_COMPAT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass
class LLMConfig:
    """运行时 LLM 配置，从环境变量解析得到。"""

    provider: str
    model: str
    api_key: str
    base_url: str

    def describe(self) -> str:
        masked_key = self.api_key[:8] + "..." if len(self.api_key) > 8 else "***"
        base = f" @ {self.base_url}" if self.base_url else ""
        return f"[{self.provider}] {self.model}{base} (key={masked_key})"


def load_llm_config() -> LLMConfig:
    """
    从环境变量解析并返回当前 LLM 配置。

    优先级：
      1. ARES_LLM_PROVIDER / ARES_LLM_MODEL / ARES_LLM_BASE_URL（显式覆盖）
      2. Provider 默认值

    注意：调用前确保已执行 load_dotenv(override=True)，否则 shell 环境变量可能覆盖 .env。
    """
    provider = os.environ.get("ARES_LLM_PROVIDER", "openai").lower().strip()
    if provider not in _PROVIDER_DEFAULTS:
        logger.warning(
            f"未知 ARES_LLM_PROVIDER='{provider}'，回退到 openai。"
            f"可用值: {list(_PROVIDER_DEFAULTS.keys())}"
        )
        provider = "openai"

    defaults = _PROVIDER_DEFAULTS[provider]

    model = (
        os.environ.get("ARES_LLM_MODEL", "").strip()
        or defaults["model"]
    )

    base_url = (
        os.environ.get("ARES_LLM_BASE_URL", "").strip()
        or defaults["base_url"]
    )

    # API Key 解析：ARES_LLM_API_KEY > provider 专属 key
    api_key = (
        os.environ.get("ARES_LLM_API_KEY", "").strip()
        or os.environ.get(defaults["api_key_env"], "").strip()
    )

    return LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


# ── OpenAI / OpenAI-Compatible 调用 ──────────────────────────────────────────

def _call_openai_compat(
    prompt: str,
    system_prompt: str,
    config: LLMConfig,
    temperature: float,
    max_tokens: int,
) -> str:
    """通过 OpenAI SDK 调用（支持自定义 base_url 的所有兼容端点）。"""
    from openai import OpenAI

    client_kwargs: dict = {"api_key": config.api_key}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url

    client = OpenAI(**client_kwargs)
    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


# ── Gemini 原生 SDK 调用 (google-genai 新包) ─────────────────────────────────

# 支持 ThinkingConfig 的模型前缀列表（思考模型用流式 + HIGH thinking level）
_THINKING_MODEL_PREFIXES = ("gemini-3", "gemini-2.5")


def _is_thinking_model(model_name: str) -> bool:
    return any(model_name.startswith(p) for p in _THINKING_MODEL_PREFIXES)


def _call_gemini_native(
    prompt: str,
    system_prompt: str,
    config: LLMConfig,
    temperature: float,
    max_tokens: int,
) -> str:
    """
    通过 google-genai 原生 SDK 调用 Gemini（新版 API）。

    - 思考模型（gemini-3-* / gemini-2.5-*）：使用流式 + ThinkingConfig(HIGH)
    - 标准模型（gemini-2.0-* 等）：使用标准非流式调用
    """
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        raise ImportError(
            "google-genai 未安装。请执行: pip install google-genai"
        )

    client = genai.Client(api_key=config.api_key)

    contents = [
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=prompt)],
        )
    ]

    if _is_thinking_model(config.model):
        # 思考模型：流式输出 + HIGH 思考强度
        generate_config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            thinking_config=genai_types.ThinkingConfig(thinking_level="HIGH"),
            max_output_tokens=max_tokens,
        )
        chunks: list[str] = []
        for chunk in client.models.generate_content_stream(
            model=config.model,
            contents=contents,
            config=generate_config,
        ):
            if chunk.text:
                chunks.append(chunk.text)
        return "".join(chunks)
    else:
        # 标准模型：非流式调用
        generate_config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        response = client.models.generate_content(
            model=config.model,
            contents=contents,
            config=generate_config,
        )
        return response.text or ""


# ── 统一调用入口 ──────────────────────────────────────────────────────────────

HALT_PLACEHOLDER = "[LLM 未配置]"


def call_llm(
    prompt: str,
    system_prompt: str,
    config: Optional[LLMConfig] = None,
    temperature: float = 0.2,
    max_tokens: int = 600,
) -> str:
    """
    统一 LLM 调用入口，自动根据 provider 路由。

    Args:
        prompt:        用户侧 Prompt。
        system_prompt: 系统侧 Prompt。
        config:        LLM 配置，默认从环境变量加载。
        temperature:   生成温度。
        max_tokens:    最大输出 Token 数。

    Returns:
        LLM 输出文本；配置缺失时返回占位符。
    """
    if config is None:
        config = load_llm_config()

    if not config.api_key:
        provider_key_hints = {
            "openai": ("OPENAI_API_KEY", "ARES_LLM_API_KEY"),
            "gemini": ("GEMINI_API_KEY", "ARES_LLM_API_KEY"),
            "openai_compat": ("ARES_LLM_API_KEY",),
        }
        hints = provider_key_hints.get(config.provider, ("ARES_LLM_API_KEY",))
        hint_str = " 或 ".join(hints)
        logger.warning(
            f"LLM API Key 未设置（provider={config.provider}）。"
            f"请在 .env 中配置 {hint_str}。"
        )
        return f"[LLM 未配置: 请设置 {hints[0]}]"

    logger.info(f"LLM 调用: {config.describe()}")

    try:
        if config.provider == "gemini":
            return _call_gemini_native(
                prompt, system_prompt, config, temperature, max_tokens
            )
        else:
            # openai 和 openai_compat 均走 OpenAI SDK
            return _call_openai_compat(
                prompt, system_prompt, config, temperature, max_tokens
            )
    except Exception as exc:
        logger.error(f"LLM 调用失败 ({config.describe()}): {exc}")
        raise
