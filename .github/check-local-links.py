#!/usr/bin/env python3
"""Check local Markdown links and anchors that should resolve in this repository."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from source_boundaries import SOURCE_PREFIXES, is_research_repository_snapshot


ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".history", "node_modules"}
SKIP_PREFIXES = SOURCE_PREFIXES
LINK_PATTERNS = [
    # 现有条目标签含一层方括号，如entries[0..4]；不把它截成未闭合标签。
    re.compile(r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(([^)]+)\)"),
    re.compile(r"\b(?:href|src)=[\"']([^\"']+)[\"']"),
]
EXTERNAL_PREFIXES = (
    "http://",
    "https://",
    "wss://",
    "ws://",
    "mailto:",
    "tel:",
    "data:",
)


def github_slug(title: str) -> str:
    """Approximate GitHub Markdown heading anchors for local validation."""
    title = re.sub(r"<[^>]+>", "", title.strip().lower())
    title = re.sub(r"[`*_~]", "", title)
    title = re.sub(r"[^\w\u4e00-\u9fff\- ]+", "", title)
    title = re.sub(r"\s+", "-", title).strip("-")
    return title


def strip_fenced_code(text: str) -> str:
    """Remove fenced code blocks before scanning inline links."""
    lines: list[str] = []
    in_fence = False
    fence_marker = ""

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            lines.append("")
            continue
        lines.append("" if in_fence else line)

    return "\n".join(lines)


def markdown_anchors(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    anchors = set(re.findall(r"<a\s+id=[\"']([^\"']+)[\"']", text))
    used: dict[str, int] = {}
    in_fence = False
    fence_marker = ""

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if not heading:
            continue
        slug = github_slug(heading.group(2))
        if not slug:
            continue
        count = used.get(slug, 0)
        used[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")

    return anchors


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in SKIP_PARTS for part in rel.parts):
        return True
    if is_research_repository_snapshot(rel):
        return True
    return any(rel == prefix or prefix in rel.parents for prefix in SKIP_PREFIXES)


def gitlink_roots() -> tuple[Path, ...]:
    """读取已跟踪Gitlink目录，不初始化其工作树。"""
    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--stage", "-z"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if completed.returncode != 0:
        return ()

    roots: set[Path] = set()
    for entry in completed.stdout.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", 1)
        except ValueError:
            continue
        if metadata.split(b" ", 1)[0] == b"160000":
            roots.add((ROOT / os.fsdecode(raw_path)).resolve())
    return tuple(sorted(roots))


def is_uninitialized_gitlink_target(destination: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        if os.path.lexists(root / ".git"):
            continue
        try:
            destination.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def normalize_target(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if not raw or raw.startswith(EXTERNAL_PREFIXES):
        return "", ""
    target = raw.split()[0] if " " in raw and not raw.startswith("<") else raw
    target = target.strip("<>")
    if "#" in target:
        path_part, anchor = target.split("#", 1)
    else:
        path_part, anchor = target, ""
    return urllib.parse.unquote(path_part), urllib.parse.unquote(anchor)


def main() -> int:
    missing_links: list[tuple[Path, str, Path]] = []
    missing_anchors: list[tuple[Path, str, Path, str]] = []
    anchor_cache: dict[Path, set[str]] = {}
    checked_files = 0
    uninitialized_gitlinks = gitlink_roots()
    skipped_gitlink_targets = 0

    for markdown_file in sorted(ROOT.rglob("*.md")):
        if should_skip(markdown_file):
            continue
        checked_files += 1
        text = markdown_file.read_text(encoding="utf-8", errors="ignore")
        scan_text = strip_fenced_code(text)

        for pattern in LINK_PATTERNS:
            for match in pattern.finditer(scan_text):
                raw = match.group(1)
                target, anchor = normalize_target(raw)
                if target.startswith(("/", "\\")):
                    continue

                destination = (markdown_file.parent / target).resolve() if target else markdown_file.resolve()
                try:
                    destination.relative_to(ROOT)
                except ValueError:
                    missing_links.append((markdown_file.relative_to(ROOT), raw, destination))
                    continue

                if not destination.exists():
                    if is_uninitialized_gitlink_target(destination, uninitialized_gitlinks):
                        skipped_gitlink_targets += 1
                        continue
                    missing_links.append((markdown_file.relative_to(ROOT), raw, markdown_file.parent / target))
                    continue
                if anchor and destination.suffix.lower() == ".md":
                    if destination not in anchor_cache:
                        anchor_cache[destination] = markdown_anchors(destination)
                    if anchor not in anchor_cache[destination]:
                        missing_anchors.append((markdown_file.relative_to(ROOT), raw, destination.relative_to(ROOT), anchor))

    if skipped_gitlink_targets:
        print(f"SKIPPED_UNINITIALIZED_GITLINK_TARGETS={skipped_gitlink_targets}")

    if missing_links or missing_anchors:
        print("MISSING_LINKS")
        for source, raw, resolved in missing_links:
            try:
                display_path = resolved.relative_to(ROOT)
            except ValueError:
                display_path = resolved
            print(f"{source} -> {raw} => {display_path}")
        print("MISSING_ANCHORS")
        for source, raw, destination, anchor in missing_anchors:
            print(f"{source} -> {raw} => {destination}#{anchor}")
        print(f"TOTAL_LINKS={len(missing_links)}")
        print(f"TOTAL_ANCHORS={len(missing_anchors)}")
        return 1

    print(f"OK local links and anchors checked: {checked_files} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
