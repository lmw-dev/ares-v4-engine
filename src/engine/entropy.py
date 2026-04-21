"""
Ares v4.0 - 动态战术稳定性熵值 (S_dynamic) 计算引擎

算法核心：
  S_dynamic = S_base + Σ(节点缺失惩罚) + Σ(战术维度风险修正)

阈值熔断：S_dynamic > threshold → status = CRITICAL_WARNING
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

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
    xg: float = 0.0
    passes_attacking_third: int = 0


@dataclass
class EntropyResult:
    """熵值计算结果。"""

    team_name: str
    s_base: float
    s_dynamic: float
    threshold: float
    status: str
    penalty_breakdown: list[dict[str, str | float]] = field(default_factory=list)
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

# S_dynamic 最终安全边界
S_DYNAMIC_MIN = 0.10
S_DYNAMIC_MAX = 0.90

# 对攻转化效率阈值及修正项
EFFICIENCY_LETHAL_THRESHOLD = 0.08
EFFICIENCY_WASTEFUL_THRESHOLD = 0.03
EFFICIENCY_WASTEFUL_PASSES_THRESHOLD = 50
EFFICIENCY_LETHAL_MODIFIER = -0.15
EFFICIENCY_WASTEFUL_MODIFIER = 0.10

EfficiencyBand = Literal["LETHAL", "WASTEFUL", "NORMAL"]


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


def _clip(value: float, lower: float, upper: float) -> float:
    """将数值限制在 [lower, upper] 区间。"""
    if lower > upper:
        raise ValueError(f"invalid clip bounds: lower={lower}, upper={upper}")
    return max(lower, min(value, upper))


def _calculate_efficiency_modifier(
    xg: float,
    passes_attacking_third: int,
) -> tuple[float, float, EfficiencyBand]:
    """
    计算对攻转化效率修正项。

    efficiency_ratio = xG / (passes_attacking_third + 1.0)
    """
    safe_xg = max(float(xg), 0.0)
    safe_passes = max(int(passes_attacking_third), 0)
    efficiency_ratio = safe_xg / (safe_passes + 1.0)

    if efficiency_ratio > EFFICIENCY_LETHAL_THRESHOLD:
        modifier = EFFICIENCY_LETHAL_MODIFIER
        band: EfficiencyBand = "LETHAL"
        logger.info(
            "效率因子[Lethal]: ratio=%.4f (> %.2f), 修正 %.2f",
            efficiency_ratio,
            EFFICIENCY_LETHAL_THRESHOLD,
            modifier,
        )
    elif (
        efficiency_ratio < EFFICIENCY_WASTEFUL_THRESHOLD
        and safe_passes > EFFICIENCY_WASTEFUL_PASSES_THRESHOLD
    ):
        modifier = EFFICIENCY_WASTEFUL_MODIFIER
        band = "WASTEFUL"
        logger.warning(
            "效率因子[Wasteful]: ratio=%.4f (< %.2f) 且 passes=%d (> %d), 修正 +%.2f",
            efficiency_ratio,
            EFFICIENCY_WASTEFUL_THRESHOLD,
            safe_passes,
            EFFICIENCY_WASTEFUL_PASSES_THRESHOLD,
            modifier,
        )
    else:
        modifier = 0.0
        band = "NORMAL"
        logger.info(
            "效率因子[Normal]: ratio=%.4f, 修正 %.2f",
            efficiency_ratio,
            modifier,
        )

    return modifier, efficiency_ratio, band


def calculate_s_dynamic(
    s_base: float,
    fear_factor_modifier: float,
    injury_modifier: float,
    xg: float,
    passes_attacking_third: int,
    clip_lower: float = S_DYNAMIC_MIN,
    clip_upper: float = S_DYNAMIC_MAX,
) -> tuple[float, float, float, EfficiencyBand]:
    """
    计算最终动态熵值。

    新公式:
        S_dynamic = S_base + Fear_Factor_Modifier + Injury_Modifier + Efficiency_Modifier

    其中:
        efficiency_ratio = xG / (passes_attacking_third + 1.0)
    """
    efficiency_modifier, efficiency_ratio, efficiency_band = _calculate_efficiency_modifier(
        xg=xg,
        passes_attacking_third=passes_attacking_third,
    )

    raw_s_dynamic = (
        float(s_base)
        + float(fear_factor_modifier)
        + float(injury_modifier)
        + efficiency_modifier
    )
    clipped_s_dynamic = _clip(raw_s_dynamic, clip_lower, clip_upper)

    logger.info(
        "S_dynamic 分解: S_base=%.3f, Fear=%.3f, Injury=%.3f, Efficiency=%.3f -> raw=%.3f",
        s_base,
        fear_factor_modifier,
        injury_modifier,
        efficiency_modifier,
        raw_s_dynamic,
    )

    if clipped_s_dynamic != raw_s_dynamic:
        logger.warning(
            "S_dynamic 安全截断触发: raw=%.3f, clipped=%.3f, bounds=[%.2f, %.2f]",
            raw_s_dynamic,
            clipped_s_dynamic,
            clip_lower,
            clip_upper,
        )
    else:
        logger.info("S_dynamic 无需截断: %.3f", clipped_s_dynamic)

    return clipped_s_dynamic, efficiency_modifier, efficiency_ratio, efficiency_band


def compute_entropy(
    inp: EntropyInput,
    key_node_absence_penalty: float = KEY_NODE_ABSENCE_PENALTY,
    key_node_locked_penalty: float = KEY_NODE_LOCKED_PENALTY,
) -> EntropyResult:
    """
    计算动态战术稳定性熵值 S_dynamic。

    Args:
        inp:                       熵值计算输入参数。
        key_node_absence_penalty:  每个缺阵关键节点的惩罚值。
        key_node_locked_penalty:   每个被战术锁死节点的惩罚值。

    Returns:
        EntropyResult 对象。
    """
    s_base = inp.tactical_entropy_base
    fear_factor_modifier = 0.0
    injury_modifier = 0.0

    penalty_breakdown: list[dict[str, str | float]] = []
    risk_flags: list[str] = []

    # 1. 缺阵惩罚：关键节点在 absent_players 列表中
    absent_key_nodes = [p for p in inp.absent_players if p in inp.key_node_dependency]
    for player in absent_key_nodes:
        penalty = key_node_absence_penalty
        injury_modifier += penalty
        penalty_breakdown.append({"player": player, "type": "absent", "penalty": penalty})
        risk_flags.append(f"🔴 {player} 缺阵(+{penalty:.2f})")
        logger.warning("关键节点 [%s] 缺阵，Injury_Modifier +%.2f", player, penalty)

    # 2. 被锁死惩罚：关键节点在 locked_players 列表中
    locked_key_nodes = [p for p in inp.locked_players if p in inp.key_node_dependency]
    for player in locked_key_nodes:
        penalty = key_node_locked_penalty
        injury_modifier += penalty
        penalty_breakdown.append({"player": player, "type": "locked", "penalty": penalty})
        risk_flags.append(f"🟠 {player} 被锁死(+{penalty:.2f})")
        logger.warning("关键节点 [%s] 被战术封锁，Injury_Modifier +%.2f", player, penalty)

    # 3. 战术维度风险修正
    tactical_risk, tac_flags = _calculate_tactical_risk(inp.tactical_logic)
    fear_factor_modifier += tactical_risk
    risk_flags.extend(tac_flags)
    logger.info("战术风险汇总: Fear_Factor_Modifier %+0.2f", tactical_risk)

    # 4. 比分压力与比赛语境修正（可选 match_context）
    score_status = inp.match_context.get("score_status", "")
    match_time = int(inp.match_context.get("time", 0))
    if score_status == "Trailing" and match_time > 60:
        fear_factor_modifier += 0.10
        risk_flags.append("🔵 落后+60min(+0.10)")
        logger.info("比分压力情景：落后且超过60分钟，Fear_Factor_Modifier +0.10")

    stakes = inp.match_context.get("stakes", "")
    team_status = inp.match_context.get("team_status", "")

    if stakes == "relegation_battle":
        fear_factor_modifier -= 0.10
        risk_flags.append("🔶 默契/惧败 [DRAW_BIAS] (-0.10)")
        logger.info("语境情景：保级生死战，Fear_Factor_Modifier -0.10")

    if team_status == "relegated_no_pressure":
        fear_factor_modifier += 0.20
        risk_flags.append("🎴 薛定谔防线 [WILDCARD] (+0.20)")
        logger.info("语境情景：已降级无压力，Fear_Factor_Modifier +0.20")

    s_dynamic, efficiency_modifier, efficiency_ratio, efficiency_band = calculate_s_dynamic(
        s_base=s_base,
        fear_factor_modifier=fear_factor_modifier,
        injury_modifier=injury_modifier,
        xg=inp.xg,
        passes_attacking_third=inp.passes_attacking_third,
    )

    if efficiency_band == "LETHAL":
        risk_flags.append(
            f"🗡️ 对攻转化效率[Lethal] ({efficiency_ratio:.4f}, {efficiency_modifier:+.2f})"
        )
    elif efficiency_band == "WASTEFUL":
        risk_flags.append(
            f"🧱 对攻转化效率[Wasteful] ({efficiency_ratio:.4f}, {efficiency_modifier:+.2f})"
        )

    penalty_breakdown.append(
        {
            "type": "efficiency_modifier",
            "penalty": efficiency_modifier,
            "efficiency_ratio": round(efficiency_ratio, 4),
        }
    )

    # 5. 阈值熔断
    status = "CRITICAL_WARNING" if s_dynamic > inp.system_fragility_threshold else "STABLE"

    result = EntropyResult(
        team_name=inp.team_name,
        s_base=s_base,
        s_dynamic=round(s_dynamic, 4),
        threshold=inp.system_fragility_threshold,
        status=status,
        penalty_breakdown=penalty_breakdown,
        risk_flags=risk_flags,
    )

    if result.is_critical:
        logger.error(
            "⚠️ [%s] 体系崩坏预警！S_dynamic=%.3f > 阈值=%.3f",
            inp.team_name,
            result.s_dynamic,
            result.threshold,
        )
    else:
        logger.info(
            "[%s] 熵值计算完成: S=%.3f (%s)",
            inp.team_name,
            result.s_dynamic,
            status,
        )

    return result
