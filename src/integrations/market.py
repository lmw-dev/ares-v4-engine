"""
Ares v4.0 - 赔率对冲与 EV（超额价值）剪刀差计算模块

核心逻辑：
  市场情绪（热度/隐含概率）与 v4.0 压力测试结果（鲁棒性）之间的非对称偏离
  → 标记为 EV+（市场低估了真实风险）或 EV-（市场过度定价了强队）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.utils.logger import setup_logger

logger = setup_logger("ares.market")


# ── 数据模型 ─────────────────────────────────────────────────────────────────

@dataclass
class OddsInput:
    """赔率输入数据。"""

    team_name: str
    home_odds: float
    draw_odds: float
    away_odds: float
    is_home: bool = True
    strength_gap_index: float = 0.0

    @property
    def target_odds(self) -> float:
        """目标队伍（home/away）对应的胜赔。"""
        return self.home_odds if self.is_home else self.away_odds

    @property
    def market_implied_prob(self) -> float:
        """赔率隐含的胜率（去除庄家优势后近似值）。"""
        total_overround = (
            1 / self.home_odds + 1 / self.draw_odds + 1 / self.away_odds
        )
        raw_prob = 1 / self.target_odds
        return round(raw_prob / total_overround, 4)

    @property
    def overround(self) -> float:
        """庄家总超额（Hold 率），反映市场定价效率。"""
        return round(
            1 / self.home_odds + 1 / self.draw_odds + 1 / self.away_odds, 4
        )


@dataclass
class EVResult:
    """EV 分析结果。"""

    team_name: str
    market_implied_prob: float
    model_win_prob: float
    ev_score: float
    ev_tag: str
    decision: str
    market_odds: float
    expected_value: float

    def summary(self) -> str:
        return (
            f"[{self.team_name}] "
            f"市场隐含={self.market_implied_prob:.1%} | "
            f"模型估算={self.model_win_prob:.1%} | "
            f"EV差={self.ev_score:+.3f} → {self.ev_tag} | "
            f"决策: {self.decision}"
        )


# ── 核心计算 ─────────────────────────────────────────────────────────────────

def resilience_to_win_prob(
    resilience_score: float,
    s_dynamic: float,
    base_win_rate: float = 0.5,
) -> float:
    """
    将压力测试结果（韧性评分 + 熵值）转换为模型估算胜率。

    逻辑：
      - 韧性评分高（接近1.0）→ 胜率向上修正
      - 熵值高（接近1.0）→ 胜率向下修正
      - 基础胜率作为锚点（默认 0.5，可由外部市场数据提供）

    Args:
        resilience_score: 压力测试整体韧性评分 [0, 1]
        s_dynamic:        动态熵值 [0, 1]
        base_win_rate:    基础胜率锚点（建议使用市场隐含概率）

    Returns:
        模型估算胜率 [0.05, 0.95]
    """
    resilience_adj = (resilience_score - 0.5) * 0.3
    entropy_adj = -(s_dynamic - 0.35) * 0.4

    model_prob = base_win_rate + resilience_adj + entropy_adj
    return round(max(0.05, min(0.95, model_prob)), 4)


def compute_ev(
    odds_input: OddsInput,
    resilience_score: float,
    s_dynamic: float,
    ev_threshold_positive: float = 0.05,
    ev_threshold_negative: float = -0.05,
) -> EVResult:
    """
    计算 EV（期望价值）剪刀差，输出博弈决策建议。

    Args:
        odds_input:              赔率输入对象。
        resilience_score:        压力测试整体韧性评分。
        s_dynamic:               动态熵值。
        ev_threshold_positive:   EV+ 判定阈值（模型概率 - 市场概率 > 此值）。
        ev_threshold_negative:   EV- 判定阈值（模型概率 - 市场概率 < 此值）。

    Returns:
        EVResult 对象。
    """
    market_prob = odds_input.market_implied_prob
    model_prob = resilience_to_win_prob(
        resilience_score=resilience_score,
        s_dynamic=s_dynamic,
        base_win_rate=market_prob,
    )

    ev_score = round(model_prob - market_prob, 4)
    expected_value = round(model_prob * odds_input.target_odds - 1, 4)

    # 护城河校验：如果绝对实力差距压倒一切，则无视市场表现强制认定为正路
    if odds_input.strength_gap_index > 1.5:
        ev_tag = "TRUE_FAVORITE"
        decision = "✅ 正路 - 实力绝对碾压，无视市场资金挤压"
    elif ev_score > ev_threshold_positive and expected_value > 0:
        ev_tag = "EV+"
        decision = "✅ 可博 - 市场低估真实鲁棒性，存在超额价值"
    elif ev_score < ev_threshold_negative or s_dynamic > 0.7:
        ev_tag = "EV-"
        decision = "🚫 回避 - 市场高估强队，真实风险被熵值揭示"
    else:
        ev_tag = "NEUTRAL"
        decision = "⏸ 观望 - 无明显非对称偏离"

    result = EVResult(
        team_name=odds_input.team_name,
        market_implied_prob=market_prob,
        model_win_prob=model_prob,
        ev_score=ev_score,
        ev_tag=ev_tag,
        decision=decision,
        market_odds=odds_input.target_odds,
        expected_value=expected_value,
    )

    logger.info(result.summary())
    return result


def compute_hedge_ratio(
    stake_a: float,
    odds_a: float,
    odds_b: float,
) -> dict[str, float]:
    """
    计算对冲注额比（两结果对冲锁定利润）。

    Args:
        stake_a:  主注注额。
        odds_a:   主注赔率。
        odds_b:   对冲结果赔率。

    Returns:
        包含对冲注额、锁定利润的字典。
    """
    stake_b = round((stake_a * odds_a) / odds_b, 2)
    guaranteed_profit = round(stake_a * odds_a - stake_a - stake_b, 2)

    result = {
        "stake_a": stake_a,
        "odds_a": odds_a,
        "stake_b": stake_b,
        "odds_b": odds_b,
        "guaranteed_profit": guaranteed_profit,
        "roi": round(guaranteed_profit / (stake_a + stake_b) * 100, 2),
    }

    logger.info(
        f"对冲计算: 主注={stake_a}@{odds_a} | 对冲={stake_b}@{odds_b} | "
        f"锁定利润={guaranteed_profit} | ROI={result['roi']}%"
    )
    return result
