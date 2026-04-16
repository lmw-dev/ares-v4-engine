---
tags:
  - area/football-matrix
  - type/guideline
  - ares/tactical-standard
status: evergreen
version: 1.0
creation_date: 2026-04-12
last_modified_date: 2026-04-12
parent: "[[体系 - Ares Football 深度分析与 RAG 对冲系统 v3.5]]"
---

# 规范 - Ares 战术 5x5 矩阵标准化取值规范 v1.0

> [!tip] 核心目标
本规范定义了 v3.5 Tactical RAG 的底层“度量衡”。所有从专家源抓取的情报必须强制转化为以下标准化标签，以确保战术碰撞引擎的机器可读性。

## 1. 维度一：进攻发起 (Build-up & Press Resistance)
* **P1 (Press-Resistant)**：极强抗压。能从后场通过短传拆解高位逼抢。
* **P3 (Mixed)**：具备出球能力但依赖核心。核心被封锁后易失误。
* **P5 (Long-ball Only)**：无抗压能力。面对逼抢仅能长传大脚解围。

## 2. 维度二：空间利用 (Space Exploitation)
* **W (Wide-Heavy)**：侧重边路进攻。依赖边后卫插上或翼卫下拉宽度。
* **C (Central-Tunnel)**：侧重中路渗透。通过肋部小组配合撕开防线。
* **H (Hybrid)**：全能空间利用。具备动态切换进攻轴心的能力。

## 3. 维度三：转换速率 (Transition Speed)
* **F (Fast/Vertical)**：极致转换。抢断后 3-5 秒内完成垂直打击。
* **S (Slow/Possession)**：控球优先。转换阶段倾向于回传重新组织。
* **M (Reactive)**：根据场景调整。领先降速，落后提速。

## 4. 维度四：防守高度 (Defensive Line Height)
* **H (High-Press)**：高位防线。防线推至中圈，风险在于身后大空档。
* **M (Mid-Block)**：中场绞杀。保持紧凑，不轻易前压。
* **L (Deep-Block/Low)**：蹲坑铁桶阵。禁区内堆砌人数，放弃控球。

## 5. 维度五：定位球表现 (Set-piece Efficiency)
* **A (Advantage)**：具备绝对高空优势或顶级罚球手。
* **V (Vulnerable)**：定位球防守存在结构性缺陷（如区域漏人）。
* **N (Neutral)**：无明显倾向。

## 6. 维护规则：战术衰减 (Tactical Decay)
- **时效性红线**：若 RAG 库中关于某球队的 S 级分析超过 **21 天** 未更新，该球队的战术标签必须在分析模板中标记为 `Unknown`，并触发定向补扫。
- **权重标记**：在 `7_Team_Archives` 的 YAML 区块中，必须注明每个标签的来源等级 (S/A/B)。

## 7. 关联工具
- **逻辑引擎**：[[体系 - Ares Football 深度分析与 RAG 对冲系统 v3.5]]