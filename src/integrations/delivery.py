"""
Ares v4.0 - 审计报告落盘模块

唯一职责：
  生成结构化 Markdown 审计战报，并将其作为独立文件保存到
  ARES_VAULT_PATH/03_Match_Audits/ 目录。

消息投递（Discord / OpenClaw）完全解耦，由外部 cron job 负责消费该目录。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from src.engine.entropy import EntropyResult
from src.engine.simulator import SimulationReport
from src.integrations.market import EVResult
from src.utils.logger import setup_logger

logger = setup_logger("ares.delivery")


def build_audit_report(
    entropy: EntropyResult,
    simulation: SimulationReport,
    ev: Optional[EVResult] = None,
) -> str:
    """
    将审计结果序列化为结构化 Markdown 战报字符串。

    Args:
        entropy:    熵值计算结果。
        simulation: 压力测试报告。
        ev:         EV 分析结果（可选）。

    Returns:
        完整的 Markdown 正文字符串。
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = "⚠️ " if entropy.is_critical else "✅ "

    lines: list[str] = [
        f"# {prefix}Ares 审计报告 - {entropy.team_name}",
        f"> {ts}  |  Ares v4.0 动态战术压力引擎",
        "",
        "## 熵值摘要",
        f"- **S_dynamic**: `{entropy.s_dynamic:.3f}` / 阈值 `{entropy.threshold:.3f}`",
        f"- **状态**: `{entropy.status}`",
        f"- **整体韧性评分**: `{simulation.overall_resilience_score:.3f}`",
    ]

    if entropy.risk_flags:
        lines.append("- **风险标记**: " + " | ".join(entropy.risk_flags))

    if simulation.halt_triggered:
        lines.append("\n> 🛑 **停机**: RAG 库逆境样本不足，部分场景无法评估。")

    lines.append("\n## 压力场景结果")
    for result in simulation.scenario_results:
        if result.is_halted:
            body = "[Unknown: Insufficient Resilience Data]"
        else:
            analysis = result.llm_analysis[:400] + (
                "..." if len(result.llm_analysis) > 400 else ""
            )
            body = f"成功率预估: **{result.success_rate_estimate:.0%}**\n{analysis}"
        lines.append(f"\n### {result.scenario_name}\n{body}")

    if ev:
        lines += [
            "\n## 市场解耦分析",
            f"- 市场隐含: `{ev.market_implied_prob:.1%}` | 模型估算: `{ev.model_win_prob:.1%}`",
            f"- EV 标记: **{ev.ev_tag}** | {ev.decision}",
        ]

    return "\n".join(lines)


def save_audit_report(
    vault_path_str: str,
    team_name: str,
    report_content: str,
) -> Path:
    """
    将审计战报落盘到 ``ARES_VAULT_PATH/03_Match_Audits/``。

    文件名格式: ``YYYYMMDD_HHMMSS-{team_name}-Audit.md``

    Args:
        vault_path_str: Obsidian Vault 根目录路径字符串。
        team_name:      球队名称，用于构造文件名（空格转下划线）。
        report_content: Markdown 战报正文。

    Returns:
        写入成功的 Path 对象。

    Raises:
        OSError: 目录创建或文件写入失败时抛出。
    """
    audits_dir = Path(vault_path_str).expanduser().resolve() / "03_Match_Audits"
    audits_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = team_name.replace(" ", "_").replace("/", "-")
    filepath = audits_dir / f"{timestamp}-{safe_name}-Audit.md"

    filepath.write_text(report_content, encoding="utf-8")
    logger.info(f"审计战报已落盘: {filepath}")
    return filepath
