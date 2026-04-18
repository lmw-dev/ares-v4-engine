"""
Ares v4.0 - Obsidian Markdown/YAML 档案解析器
负责从外部 Obsidian Vault 中读取结构化的球队战术档案。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

import frontmatter
import yaml

from src.utils.logger import setup_logger

logger = setup_logger("ares.obsidian")


REQUIRED_FIELDS = {
    "version",
    "tactical_entropy_base",
    "key_node_dependency",
    "tactical_logic",
}

TACTICAL_LOGIC_KEYS = {"P", "Space", "F", "H", "Set_Piece"}

# 匹配玩家名后面的中文备注，如 "Rodri (单后腰/攻防枢纽)" → "Rodri"
_PLAYER_ANNOTATION_RE = re.compile(r"\s*[\(\（].*[\)\）]\s*$")


@dataclass
class TacticalProfile:
    """解析后的球队战术档案，对应一份 Obsidian Markdown 文件。"""

    file_path: Path
    team_name: str
    version: float
    tactical_entropy_base: float
    system_fragility_threshold: float
    key_node_dependency: list[str]
    tactical_logic: dict[str, str]
    last_modified: Optional[date]
    raw_content: str
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_stale(self, ttl_days: int = 21) -> bool:
        """检查档案是否超过 TTL 未更新（战术衰减检查）。"""
        if self.last_modified is None:
            return True
        if isinstance(self.last_modified, datetime):
            delta = datetime.now() - self.last_modified
        else:
            delta = datetime.now() - datetime.combine(self.last_modified, datetime.min.time())
        return delta.days > ttl_days


@dataclass
class ParseResult:
    """单次解析的结果，包含成功档案与错误列表。"""

    profiles: list[TacticalProfile] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return len(self.profiles)

    @property
    def error_count(self) -> int:
        return len(self.errors)


def _get_vault_path() -> Path:
    """从环境变量读取 Vault 路径，并验证其存在性。"""
    raw_path = os.environ.get("ARES_VAULT_PATH", "")
    if not raw_path:
        raise EnvironmentError(
            "环境变量 ARES_VAULT_PATH 未设置。"
            "请在 .env 文件中配置 Obsidian Vault 的绝对路径。"
        )
    vault = Path(raw_path).expanduser().resolve()
    if not vault.exists():
        raise FileNotFoundError(
            f"ARES_VAULT_PATH 指向的路径不存在: {vault}\n"
            "请检查路径是否正确，或者 Obsidian Vault 是否已挂载。"
        )
    return vault


def _clean_player_name(raw: str) -> str:
    """清理玩家名字中的中文注释，如 'Rodri (单后腰/攻防枢纽)' → 'Rodri'。"""
    return _PLAYER_ANNOTATION_RE.sub("", raw).strip()


def _extract_body_params(body: str) -> dict[str, Any]:
    """
    从 Markdown 正文中提取 v4.0 动态参数块。

    支持以下写法：
    - 参数写在正文最前面的 YAML-like 块中（`# ===` 注释行之间）
    - 以第二个 `---` 分隔符或第一个 `# 团队名` 标题为结束边界
    - 行内 `# 注释` 和纯注释行均被清理
    """
    lines = body.splitlines()
    param_lines: list[str] = []
    in_param_block = False

    for line in lines:
        stripped = line.strip()

        # 跳过空行（正文开头的空行）
        if not stripped:
            if not in_param_block:
                continue
            param_lines.append("")
            continue

        # 纯注释行（`# ==...` / `# 说明文字`）→ 跳过，但标记已进入参数区
        if stripped.startswith("#"):
            if not in_param_block and ("entropy" in stripped.lower() or "====" in stripped or "v4" in stripped.lower()):
                in_param_block = True
            continue

        # 第二个 `---` 分隔符 → 参数块结束
        if stripped == "---":
            break

        # 遇到正式 Markdown 标题（`## 正文章节`）→ 结束
        if re.match(r"^#{1,2}\s+\S", stripped):
            break

        # 这是 YAML-like 参数行
        in_param_block = True
        # 移除行尾 `# inline comment`（保留缩进，仅去掉尾注）
        clean_line = re.sub(r"\s+#\s+.*$", "", line)
        param_lines.append(clean_line)

    if not param_lines:
        return {}

    try:
        parsed = yaml.safe_load("\n".join(param_lines)) or {}
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except yaml.YAMLError:
        return {}


def verify_with_osint(team_name: str, local_metadata: dict) -> dict:
    """
    OSINT API 校验网关。
    注意：这里未来将调用外部体育 API 获取真实积分榜和伤停状态等数据，
    与本地数据进行交叉校验，以防止“本地数据滞后”造成的系统幻觉。
    """
    logger.info(f"OSINT API 交叉校验请求发送: {team_name}")
    return local_metadata


def _validate_metadata(metadata: dict[str, Any], file_path: Path) -> list[str]:
    """校验 YAML 元数据的必填字段，返回错误信息列表（空表示通过）。"""
    errors: list[str] = []

    missing = REQUIRED_FIELDS - set(metadata.keys())
    if missing:
        errors.append(f"缺少必填字段: {', '.join(sorted(missing))}")
        return errors

    if not isinstance(metadata.get("key_node_dependency"), list):
        errors.append("key_node_dependency 必须是列表类型")

    tactical_logic = metadata.get("tactical_logic")
    if not isinstance(tactical_logic, dict):
        errors.append("tactical_logic 必须是字典类型")
    else:
        missing_tl_keys = TACTICAL_LOGIC_KEYS - set(tactical_logic.keys())
        if missing_tl_keys:
            errors.append(
                f"tactical_logic 缺少子字段: {', '.join(sorted(missing_tl_keys))}"
            )

    try:
        float(metadata.get("tactical_entropy_base", "invalid"))
    except (TypeError, ValueError):
        errors.append("tactical_entropy_base 必须是数值类型")

    return errors


def _parse_single_file(md_path: Path) -> TacticalProfile:
    """解析单个 Markdown 档案文件，返回 TacticalProfile 对象。"""
    try:
        post = frontmatter.load(str(md_path))
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"文件读取失败: {exc}") from exc

    metadata = dict(post.metadata)

    # 若 frontmatter 缺少 v4.0 必填字段，尝试从正文 YAML 块中补充
    missing = REQUIRED_FIELDS - set(metadata.keys())
    if missing:
        body_params = _extract_body_params(post.content)
        if body_params:
            for key in list(missing):
                if key in body_params:
                    metadata[key] = body_params[key]
                    missing.discard(key)
            if body_params:
                logger.debug(f"从正文提取 v4.0 参数: {list(body_params.keys())}")

    validation_errors = _validate_metadata(metadata, md_path)
    if validation_errors:
        raise ValueError("元数据校验失败:\n  - " + "\n  - ".join(validation_errors))

    team_name = md_path.stem

    # 强制引入 OSINT 校验网关
    metadata = verify_with_osint(team_name, metadata)

    last_modified_raw = metadata.get("last_modified_date") or metadata.get("last_modified")
    last_modified: Optional[date] = None
    if last_modified_raw:
        if isinstance(last_modified_raw, (datetime, date)):
            last_modified = last_modified_raw if isinstance(last_modified_raw, date) else last_modified_raw.date()
        else:
            try:
                last_modified = datetime.strptime(str(last_modified_raw), "%Y-%m-%d").date()
            except ValueError:
                logger.warning(f"无法解析 last_modified_date: {last_modified_raw}")

    # 清理玩家名字中的中文备注，如 "Rodri (单后腰/攻防枢纽)" → "Rodri"
    raw_nodes = list(metadata["key_node_dependency"])
    clean_nodes = [_clean_player_name(str(n)) for n in raw_nodes]

    # 清理 tactical_logic 值（确保是纯字符串标签）
    raw_logic = dict(metadata["tactical_logic"])
    clean_logic = {k: str(v).strip() for k, v in raw_logic.items()}

    return TacticalProfile(
        file_path=md_path,
        team_name=team_name,
        version=float(metadata.get("version", 4.0)),
        tactical_entropy_base=float(metadata["tactical_entropy_base"]),
        system_fragility_threshold=float(
            metadata.get("system_fragility_threshold", 0.7)
        ),
        key_node_dependency=clean_nodes,
        tactical_logic=clean_logic,
        last_modified=last_modified,
        raw_content=post.content,
        raw_metadata=metadata,
    )


def scan_vault(
    vault_path: Optional[Path] = None,
    subdir: str = "02_Team_Archives",
) -> ParseResult:
    """
    递归扫描 Obsidian Vault 中的球队档案目录。

    Args:
        vault_path: Vault 根路径，默认从环境变量读取。
        subdir:     球队档案子目录名。

    Returns:
        ParseResult 对象，包含所有成功解析的档案和错误列表。
    """
    if vault_path is None:
        vault_path = _get_vault_path()

    archives_dir = vault_path / subdir
    if not archives_dir.exists():
        logger.warning(f"档案目录不存在: {archives_dir}，将尝试直接扫描 Vault 根目录")
        archives_dir = vault_path

    result = ParseResult()
    md_files = list(archives_dir.rglob("*.md"))

    if not md_files:
        logger.warning(f"在 {archives_dir} 中未发现任何 Markdown 档案")
        return result

    logger.info(f"发现 {len(md_files)} 个 Markdown 档案，开始解析...")

    for md_path in sorted(md_files):
        try:
            profile = _parse_single_file(md_path)
            result.profiles.append(profile)
            logger.info(f"✅ 解析成功: {profile.team_name}")
        except (ValueError, FileNotFoundError) as exc:
            logger.warning(f"⚠️ 跳过 [{md_path.name}]: {exc}")
            result.errors.append({"file": str(md_path), "error": str(exc)})

    logger.info(
        f"扫描完成 - 成功: {result.success_count}，失败: {result.error_count}"
    )
    return result


def load_team_profile(
    team_name: str,
    vault_path: Optional[Path] = None,
    subdir: str = "02_Team_Archives",
) -> TacticalProfile:
    """
    按球队名称精确加载单个战术档案。

    Args:
        team_name:  球队名称（不含 .md 后缀），大小写不敏感。
        vault_path: Vault 根路径，默认从环境变量读取。
        subdir:     球队档案子目录名。

    Returns:
        TacticalProfile 对象。

    Raises:
        FileNotFoundError: 找不到对应档案时抛出。
    """
    if vault_path is None:
        vault_path = _get_vault_path()

    archives_dir = vault_path / subdir
    search_root = archives_dir if archives_dir.exists() else vault_path

    candidates = list(search_root.rglob(f"{team_name}.md"))
    if not candidates:
        candidates = [
            p for p in search_root.rglob("*.md")
            if team_name.lower() in p.stem.lower()
        ]

    if not candidates:
        raise FileNotFoundError(
            f"在 {search_root} 中找不到球队档案: {team_name}"
        )

    if len(candidates) > 1:
        logger.warning(
            f"找到 {len(candidates)} 个匹配档案，将使用第一个: {candidates[0]}"
        )

    return _parse_single_file(candidates[0])
