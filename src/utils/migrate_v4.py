"""
Ares v4.0 team archive batch migration script.

功能：
1. 从 `.env` 读取 `ARES_VAULT_PATH`
2. 递归扫描 `02_Team_Archives/**/*.md`
3. 使用 `ruamel.yaml` 更新 frontmatter 到 v4.0
4. 仅修改 YAML frontmatter，Markdown 正文完全不变
5. 输出每个被更新文件的摘要与总计

用法：
    python src/utils/migrate_v4.py
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
import os
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
TEAM_ARCHIVES_SUBDIR = "02_Team_Archives"


def build_yaml() -> YAML:
    """Create a ruamel.yaml instance configured for round-trip preservation."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def split_frontmatter(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Split markdown text into frontmatter and body.

    Returns:
        (frontmatter_without_delimiters, body_after_second_delimiter)
        If no valid frontmatter is found, returns (None, None).
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, None

    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            frontmatter = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            return frontmatter, body
    return None, None


def dump_frontmatter(yaml: YAML, data: CommentedMap) -> str:
    """Serialize frontmatter back to text without touching the markdown body."""
    stream = StringIO()
    yaml.dump(data, stream)
    return f"---\n{stream.getvalue()}---\n"


def repair_common_frontmatter_issues(frontmatter_text: str) -> str:
    """
    Repair common malformed frontmatter patterns before ruamel parsing.

    Current repairs:
    - Bare tag items under `tags:` missing `-`, e.g.
        tags:
          - foo
        league/bar
          - baz
      becomes:
        tags:
          - foo
          - league/bar
          - baz
    """
    repaired_lines: list[str] = []
    in_tags_block = False

    for raw_line in frontmatter_text.splitlines():
        stripped = raw_line.strip()

        if stripped == "tags:":
            in_tags_block = True
            repaired_lines.append(raw_line)
            continue

        if in_tags_block:
            # next top-level mapping key starts; tags block ends
            if stripped and not raw_line.startswith((" ", "\t", "-")) and ":" in stripped:
                in_tags_block = False
                repaired_lines.append(raw_line)
                continue

            # malformed bare tag line under tags block
            if stripped and ":" not in stripped and not stripped.startswith("-"):
                repaired_lines.append(f"  - {stripped}")
                continue

        repaired_lines.append(raw_line)

    return "\n".join(repaired_lines) + ("\n" if frontmatter_text.endswith("\n") else "")


def parse_frontmatter(yaml: YAML, frontmatter_text: str, md_path: Path) -> Optional[CommentedMap]:
    """
    Parse frontmatter with a repair fallback.

    Returns:
        Parsed CommentedMap, or None if parsing still fails after repair.
    """
    try:
        data = yaml.load(frontmatter_text)
    except YAMLError:
        repaired = repair_common_frontmatter_issues(frontmatter_text)
        try:
            data = yaml.load(repaired)
            print(f"Repaired malformed YAML in {md_path.name}")
        except YAMLError as exc:
            print(f"Skipping {md_path.name}: invalid YAML could not be repaired ({exc})")
            return None

    if not isinstance(data, CommentedMap):
        print(f"Skipping {md_path.name}: frontmatter is not a mapping")
        return None

    return data


def default_tactical_logic() -> CommentedMap:
    """Default v4.0 tactical logic placeholder."""
    logic = CommentedMap()
    logic["P"] = "Unknown"
    logic["Space"] = "Unknown"
    logic["F"] = "Unknown"
    logic["H"] = "Unknown"
    logic["Set_Piece"] = "Unknown"
    return logic


def ensure_v4_defaults(data: CommentedMap) -> None:
    """Inject required v4.0 fields when missing."""
    data["version"] = 4.0

    if "tactical_entropy_base" not in data:
        data["tactical_entropy_base"] = 0.50

    if "system_fragility_threshold" not in data:
        data["system_fragility_threshold"] = 0.70

    if "key_node_dependency" not in data:
        data["key_node_dependency"] = CommentedSeq()

    if "tactical_logic" not in data:
        data["tactical_logic"] = default_tactical_logic()


def migrate_file(md_path: Path, yaml: YAML) -> bool:
    """
    Migrate one markdown file to v4.0 schema.

    Returns:
        True if the file was updated, otherwise False.
    """
    text = md_path.read_text(encoding="utf-8")
    frontmatter_text, body = split_frontmatter(text)
    if frontmatter_text is None or body is None:
        return False

    data = parse_frontmatter(yaml, frontmatter_text, md_path)
    if data is None:
        return False

    old_version = data.get("version")
    old_version_normalized = str(old_version).strip()
    if old_version_normalized == "4.0":
        return False

    print(f"Updating {md_path.stem} from v{old_version} to v4.0")
    ensure_v4_defaults(data)

    updated_text = dump_frontmatter(yaml, data) + body
    md_path.write_text(updated_text, encoding="utf-8")
    return True


def load_vault_archives_dir() -> Path:
    """Load ARES_VAULT_PATH from .env and return the team archives directory."""
    load_dotenv(REPO_ROOT / ".env", override=True)

    vault_path = os.environ.get("ARES_VAULT_PATH", "").strip()
    if not vault_path:
        raise RuntimeError("ARES_VAULT_PATH is not set in .env")

    archives_dir = Path(vault_path).expanduser().resolve() / TEAM_ARCHIVES_SUBDIR
    if not archives_dir.exists():
        raise FileNotFoundError(f"Team archives directory not found: {archives_dir}")

    return archives_dir


def maybe_report_bonus_target() -> None:
    """
    Bonus note: heartbeat dedup target was requested, but the file may not exist locally.
    We only report its presence here to avoid silently implying the patch was applied.
    """
    heartbeat_path = REPO_ROOT / "src" / "integrations" / "heartbeat.py"
    if heartbeat_path.exists():
        print(f"Bonus target detected: {heartbeat_path}")
    else:
        print("Bonus target not found: src/integrations/heartbeat.py (dedup fix not applied).")


def main() -> None:
    yaml = build_yaml()
    archives_dir = load_vault_archives_dir()

    updated_count = 0
    total_md_files = 0

    for md_path in sorted(archives_dir.rglob("*.md")):
        total_md_files += 1
        if migrate_file(md_path, yaml):
            updated_count += 1

    print(f"Scanned {total_md_files} markdown files under {archives_dir}")
    print(f"Total files updated: {updated_count}")
    maybe_report_bonus_target()


if __name__ == "__main__":
    main()
