---
tags:
  - area/football-matrix
  - type/moc
  - ares/archives
status: active
creation_date: 2026-04-12
last_modified_date: 2026-04-12
parent: "[[领域 - 竞彩架构与数据分析]]"
aliases:
  - 7_Team_Archives/五大联赛球队索引
---

# MOC - 五大联赛球队索引

> [!tip] 资产定位
本笔记是 `7_Team_Archives` 的顶层入口，用于快速定位受审计球队的战术内核档案。所有档案必须符合 [[规范 - Ares 战术 5x5 矩阵标准化取值规范 v1.0]]。

## 🏴󠁧󠁢󠁥󠁮󠁧󠁿 英超 (Premier League)
- [[曼城 (Man City)]]
- [[阿森纳 (Arsenal)]]
- [[利物浦 (Liverpool)]]
- ...

## 🇪🇸 西甲 (La Liga)
- [[皇马 (Real Madrid)]]
- [[巴萨 (Barcelona)]]
- [[赫罗纳 (Girona)]]
- ...

## 🇮🇹 意甲 (Serie A)
- [[国际米兰 (Inter)]]
- [[AC米兰 (AC Milan)]]
- [[尤文图斯 (Juventus)]]
- ...

## 🇩🇪 德甲 (Bundesliga)
- [[勒沃库森 (Leverkusen)]]
- [[拜仁 (Bayern Munich)]]
- ...

## 🇫🇷 法甲 (Ligue 1)
- [[巴黎圣日耳曼 (PSG)]]
- ...

---
## 🛠️ 档案维护规范
1. **强制字段**：每份球队档案必须包含 `tactical_logic` YAML 字段。
2. **时效性**：检测到 `last_modified` 超过 21 天时，需触发补扫指令。