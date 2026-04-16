"""
Ares v4.0 - 结果投递模块

支持两种交付渠道：
  1. Discord Webhook：发送格式化审计报告到 #football-command 频道
  2. Obsidian 写回：在球队档案的 ## ⚔️ 压力测试记录 节点下插入最新模拟结果
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from src.engine.entropy import EntropyResult
from src.engine.simulator import SimulationReport
from src.integrations.market import EVResult
from src.utils.logger import setup_logger

logger = setup_logger("ares.delivery")

PRESSURE_TEST_SECTION = "## ⚔️ 压力测试记录"


# ── Discord 投递 ──────────────────────────────────────────────────────────────

def _build_discord_embed(
    entropy: EntropyResult,
    simulation: SimulationReport,
    ev: Optional[EVResult] = None,
) -> dict:
    """构建 Discord Embed 消息体。"""
    is_critical = entropy.is_critical
    color = 0xFF0000 if is_critical else 0x00FF88
    prefix = "⚠️ " if is_critical else "✅ "

    title = f"{prefix}Ares 审计报告 - {entropy.team_name}"
    description_parts = [
        f"**S_dynamic**: `{entropy.s_dynamic:.3f}` / 阈值 `{entropy.threshold:.3f}`",
        f"**状态**: `{entropy.status}`",
        f"**整体韧性评分**: `{simulation.overall_resilience_score:.3f}`",
    ]
    if entropy.risk_flags:
        description_parts.append("**风险标记**:\n" + "\n".join(f"  {f}" for f in entropy.risk_flags))

    if simulation.halt_triggered:
        description_parts.append("🛑 **停机**: RAG 库逆境样本不足，部分场景无法评估。")

    fields = []
    for result in simulation.scenario_results:
        if result.is_halted:
            value = "[Unknown: Insufficient Resilience Data]"
        else:
            value = (
                f"成功率预估: **{result.success_rate_estimate:.0%}**\n"
                + result.llm_analysis[:300]
                + ("..." if len(result.llm_analysis) > 300 else "")
            )
        fields.append({
            "name": result.scenario_name,
            "value": value,
            "inline": False,
        })

    if ev:
        fields.append({
            "name": "📊 市场解耦分析",
            "value": (
                f"市场隐含: `{ev.market_implied_prob:.1%}` | "
                f"模型估算: `{ev.model_win_prob:.1%}`\n"
                f"EV 标记: **{ev.ev_tag}** | {ev.decision}"
            ),
            "inline": False,
        })

    return {
        "embeds": [{
            "title": title,
            "description": "\n".join(description_parts),
            "color": color,
            "fields": fields,
            "footer": {
                "text": f"Ares v4.0 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            },
        }]
    }


def send_discord(
    entropy: EntropyResult,
    simulation: SimulationReport,
    ev: Optional[EVResult] = None,
    webhook_url: Optional[str] = None,
) -> bool:
    """
    发送审计结果到 Discord Webhook。

    Args:
        entropy:     熵值计算结果。
        simulation:  压力测试报告。
        ev:          EV 分析结果（可选）。
        webhook_url: Discord Webhook URL，默认从环境变量读取。

    Returns:
        True 表示发送成功，False 表示失败或未配置。
    """
    url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        logger.warning("DISCORD_WEBHOOK_URL 未配置，跳过 Discord 投递。")
        return False

    payload = _build_discord_embed(entropy, simulation, ev)

    try:
        resp = requests.post(
            url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"Discord 投递成功: {entropy.team_name}")
        return True
    except requests.RequestException as exc:
        logger.error(f"Discord 投递失败: {exc}")
        return False


# ── Obsidian 写回 ─────────────────────────────────────────────────────────────

def _build_obsidian_entry(
    entropy: EntropyResult,
    simulation: SimulationReport,
    ev: Optional[EVResult] = None,
) -> str:
    """构建写入 Obsidian 档案的 Markdown 片段。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    critical_prefix = "⚠️ " if entropy.is_critical else ""
    lines = [
        f"\n### {critical_prefix}{ts} - 压力审计结果",
        f"- **S_dynamic**: `{entropy.s_dynamic:.3f}` | **状态**: `{entropy.status}`",
        f"- **韧性评分**: `{simulation.overall_resilience_score:.3f}`",
    ]

    if entropy.risk_flags:
        lines.append("- **风险标记**: " + " | ".join(entropy.risk_flags))

    for result in simulation.scenario_results:
        status_str = "🛑 HALT" if result.is_halted else f"{result.success_rate_estimate:.0%}"
        lines.append(f"- **{result.scenario_name}**: {status_str}")

    if ev:
        lines.append(
            f"- **市场解耦**: {ev.ev_tag} | 市场={ev.market_implied_prob:.1%} vs 模型={ev.model_win_prob:.1%}"
        )

    if simulation.halt_triggered:
        lines.append("> 🛑 部分场景因 RAG 库样本不足触发停机，结论不完整。")

    return "\n".join(lines)


def write_back_to_obsidian(
    file_path: Path,
    entropy: EntropyResult,
    simulation: SimulationReport,
    ev: Optional[EVResult] = None,
) -> bool:
    """
    将审计结果写回对应的 Obsidian Markdown 档案。

    在 `## ⚔️ 压力测试记录` 节点下插入最新模拟结果。
    若该节点不存在，则自动在文件末尾追加。

    Args:
        file_path:   目标 Markdown 文件路径。
        entropy:     熵值计算结果。
        simulation:  压力测试报告。
        ev:          EV 分析结果（可选）。

    Returns:
        True 表示写入成功，False 表示失败。
    """
    if not file_path.exists():
        logger.error(f"Obsidian 档案不存在: {file_path}")
        return False

    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error(f"读取档案失败: {exc}")
        return False

    new_entry = _build_obsidian_entry(entropy, simulation, ev)

    if PRESSURE_TEST_SECTION in content:
        # 在节点标题后插入新条目
        insert_pattern = re.compile(
            rf"({re.escape(PRESSURE_TEST_SECTION)})", re.MULTILINE
        )
        updated_content = insert_pattern.sub(
            rf"\1\n{new_entry}",
            content,
            count=1,
        )
    else:
        # 节点不存在，追加到文件末尾
        updated_content = content.rstrip() + f"\n\n{PRESSURE_TEST_SECTION}\n{new_entry}\n"

    # 如果熵值超阈，在文件标题前添加 ⚠️ 前缀
    if entropy.is_critical:
        updated_content = re.sub(
            r"^(# )(?!⚠️ )",
            r"\1⚠️ ",
            updated_content,
            count=1,
            flags=re.MULTILINE,
        )

    try:
        file_path.write_text(updated_content, encoding="utf-8")
        logger.info(f"Obsidian 写回成功: {file_path.name}")
        return True
    except OSError as exc:
        logger.error(f"Obsidian 写回失败: {exc}")
        return False


# ── 统一交付入口 ──────────────────────────────────────────────────────────────

def deliver_results(
    entropy: EntropyResult,
    simulation: SimulationReport,
    ev: Optional[EVResult] = None,
    obsidian_file: Optional[Path] = None,
    discord_enabled: bool = True,
    obsidian_writeback: bool = True,
) -> dict[str, bool]:
    """
    统一交付入口：同时处理 Discord 和 Obsidian 两个渠道。

    Returns:
        {"discord": bool, "obsidian": bool}
    """
    results = {"discord": False, "obsidian": False}

    if discord_enabled:
        results["discord"] = send_discord(entropy, simulation, ev)

    if obsidian_writeback and obsidian_file:
        results["obsidian"] = write_back_to_obsidian(obsidian_file, entropy, simulation, ev)
    elif obsidian_writeback and not obsidian_file:
        logger.warning("obsidian_writeback=True 但未提供 obsidian_file，跳过写回。")

    return results
