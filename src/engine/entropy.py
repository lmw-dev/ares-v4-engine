"""
Ares v4.0 - 动态战术稳定性熵值 (S_dynamic) 计算引擎

算法核心：
  S_dynamic = S_base + Σ(节点缺失惩罚) + Σ(战术维度风险修正)

阈值熔断：S_dynamic > threshold → status = CRITICAL_WARNING
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.utils.logger import setup_logger

logger = setup_logger("ares.entropy")


@dataclass
class EntropyInput:
    """熵值计算的输入参数集合。"""

    team_name: str
    tactical_entropy_base: float
    system_fragility_threshold: float
    key_node_dependency: list[str]
    tactical_logic: dict[str, str]
    absent_players: list[str] = field(default_factory=list)
    locked_players: list[str] = field(default_factory=list)
    match_context: dict[str, str] = field(default_factory=dict)


@dataclass
class EntropyResult:
    """熵值计算结果。"""

    team_name: str
    s_base: float
    s_dynamic: float
    threshold: float
    status: str
    penalty_breakdown: list[dict[str, float]] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)

    @property
    def is_critical(self) -> bool:
        return self.status == "CRITICAL_WARNING"

    def summary(self) -> str:
        lines = [
            f"球队: {self.team_name}",
            f"S_base={self.s_base:.3f} → S_dynamic={self.s_dynamic:.3f} (阈值={self.threshold:.3f})",
            f"状态: {self.status}",
        ]
        if self.risk_flags:
            lines.append("风险标记: " + " | ".join(self.risk_flags))
        return "\n".join(lines)


# ── 战术维度风险系数表 ────────────────────────────────────────────────────────
# 根据 5x5 矩阵标准化取值规范，各维度标签对应的额外风险修正值
TACTICAL_RISK_MAP: dict[str, dict[str, float]] = {
    "P": {
        "P1": 0.0,    # 极强抗压，无惩罚
        "P3": 0.05,   # 依赖核心，轻微惩罚
        "P5": 0.15,   # 无抗压能力，重惩罚
    },
    "Space": {
        "H": 0.0,     # 全能空间，无惩罚
        "W": 0.03,    # 边路依赖，轻微惩罚
        "C": 0.03,    # 中路依赖，轻微惩罚
    },
    "F": {
        "F": 0.03,    # 极致转换，高位防线背后风险
        "M": 0.0,
        "S": 0.0,     # 控球优先，稳定
    },
    "H": {
        "H": 0.08,    # 高位防线，身后空档大
        "M": 0.0,
        "L": -0.02,   # 蹲坑阵型，熵值略降
    },
    "Set_Piece": {
        "A": -0.02,   # 定位球优势，熵值略降
        "N": 0.0,
        "V": 0.05,    # 定位球防守结构性缺陷，惩罚
    },
}

# 核心节点缺阵/被锁死的基础惩罚值（每个节点）
KEY_NODE_ABSENCE_PENALTY = 0.4
KEY_NODE_LOCKED_PENALTY = 0.25


def _calculate_tactical_risk(tactical_logic: dict[str, str]) -> tuple[float, list[str]]:
    """根据战术维度标签计算总体风险修正值。"""
    total_risk = 0.0
    flags: list[str] = []

    for dim, label in tactical_logic.items():
        dim_map = TACTICAL_RISK_MAP.get(dim, {})
        risk = dim_map.get(label, 0.0)
        total_risk += risk
        if risk > 0:
            flags.append(f"{dim}={label}(+{risk:.2f})")
        elif risk < 0:
            flags.append(f"{dim}={label}({risk:.2f})")

    return total_risk, flags


def compute_entropy(
    inp: EntropyInput,
    key_node_absence_penalty: float = KEY_NODE_ABSENCE_PENALTY,
    key_node_locked_penalty: float = KEY_NODE_LOCKED_PENALTY,
) -> EntropyResult:
    """
    计算动态战术稳定性熵值 S_dynamic。

    公式:
        S_dynamic = S_base
                  + Σ(缺阵节点 × absence_penalty)
                  + Σ(被锁节点 × locked_penalty)
                  + Σ(战术维度风险修正)

    Args:
        inp:                       熵值计算输入参数。
        key_node_absence_penalty:  每个缺阵关键节点的惩罚值。
        key_node_locked_penalty:   每个被战术锁死节点的惩罚值。

    Returns:
        EntropyResult 对象。
    """
    s = inp.tactical_entropy_base
    penalty_breakdown: list[dict[str, float]] = []
    risk_flags: list[str] = []

    # 1. 缺阵惩罚：关键节点在 absent_players 列表中
    absent_key_nodes = [
        p for p in inp.absent_players if p in inp.key_node_dependency
    ]
    for player in absent_key_nodes:
        penalty = key_node_absence_penalty
        s += penalty
        penalty_breakdown.append({"player": player, "type": "absent", "penalty": penalty})
        risk_flags.append(f"🔴 {player} 缺阵(+{penalty:.2f})")
        logger.warning(f"关键节点 [{player}] 缺阵，熵值 +{penalty:.2f}")

    # 2. 被锁死惩罚：关键节点在 locked_players 列表中
    locked_key_nodes = [
        p for p in inp.locked_players if p in inp.key_node_dependency
    ]
    for player in locked_key_nodes:
        penalty = key_node_locked_penalty
        s += penalty
        penalty_breakdown.append({"player": player, "type": "locked", "penalty": penalty})
        risk_flags.append(f"🟠 {player} 被锁死(+{penalty:.2f})")
        logger.warning(f"关键节点 [{player}] 被战术封锁，熵值 +{penalty:.2f}")

    # 3. 战术维度风险修正
    tactical_risk, tac_flags = _calculate_tactical_risk(inp.tactical_logic)
    s += tactical_risk
    risk_flags.extend(tac_flags)

    # 4. 比分压力修正（可选 match_context）
    score_status = inp.match_context.get("score_status", "")
    match_time = int(inp.match_context.get("time", 0))
    if score_status == "Trailing" and match_time > 60:
        s += 0.1
        risk_flags.append("🔵 落后+60min(+0.10)")
        logger.info("比分压力情景：落后且超过60分钟，熵值 +0.10")

    # 5. 阈值熔断
    status = (
        "CRITICAL_WARNING"
        if s > inp.system_fragility_threshold
        else "STABLE"
    )

    result = EntropyResult(
        team_name=inp.team_name,
        s_base=inp.tactical_entropy_base,
        s_dynamic=round(s, 4),
        threshold=inp.system_fragility_threshold,
        status=status,
        penalty_breakdown=penalty_breakdown,
        risk_flags=risk_flags,
    )

    if result.is_critical:
        logger.error(
            f"⚠️ [{inp.team_name}] 体系崩坏预警！"
            f"S_dynamic={result.s_dynamic:.3f} > 阈值={result.threshold:.3f}"
        )
    else:
        logger.info(
            f"[{inp.team_name}] 熵值计算完成: S={result.s_dynamic:.3f} ({status})"
        )

    return result
