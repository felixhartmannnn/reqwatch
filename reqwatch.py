from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

DEFAULT_WARN_DAYS = 60
DEFAULT_STALE_DAYS = 180
DEFAULT_TIMEOUT = 10
DEFAULT_WORKERS = 8
DEFAULT_OUTPUT = "plain"

REQUIREMENT_LINE_RE = re.compile(
    r"^\s*[A-Za-z0-9_\.\-]+(?:\[[^\]]+\])?\s*([=<>!~\s].+)?$"
)
COMMENT_OR_EMPTY_RE = re.compile(r"^\s*(#.*|$)")
VCS_URI_RE = re.compile(
    r"^(git\+)?(https?|ssh|git)://|^(git\+)?(git@|hg@|bzr@|svn@)", re.IGNORECASE
)


class _HelpArgs:
    help_requested = True


@dataclass
class Dependency:
    name: str
    line: int
    source: str
    spec: str = ""


@dataclass
class RequirementFile:
    path: str
    declared: List[Dependency] = field(default_factory=list)
    missing: List[Dependency] = field(default_factory=list)
    stale: List[Dependency] = field(default_factory=list)
    unused: List[Dependency] = field(default_factory=list)
    vcs_uris: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ProjectContext:
    root: str
    packages: List[str] = field(default_factory=list)
    modules: List[str] = field(default_factory=list)


def _now() -> float:
    import time
    return time.time()


def _age_days(path: str) -> Optional[float]:
    try:
        return (_now() - os.path.getmtime(path)) / 86400
    except OSError:
        return None


def _is_vcs_uri(spec: str) -> bool:
    return bool(VCS_URI_RE.match(spec.strip()))


def _extract_name(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("-"):
        return None
    marker = re.search(r"[^A-Za-z0-9_\.\-\[\]]", stripped)
    if marker:
        name = stripped[: marker.start()]
    else:
        name = stripped
    name = name.strip().lstrip("./")
    if not name:
        return None
    if name in {"", ".", ".."}:
        return None
    if not re.match(r"^[A-Za-z0-9_\.\-\[\]]+$", name):
        return None
    return name


def _parse_requirement_line(line: str, source: str, lineno: int) -> Optional[Dependency]:
    name = _extract_name(line)
    if name is None:
        return None
    spec = line[len(name):].strip()
    return Dependency(name=name, line=lineno, source=source, spec=spec)


def _find_python_packages(root: str) -> List[str]:
    packages: List[str] = []
    src_root = os.path.join(root, "src")
    search_roots = [root, src_root] if os.path.isdir(src_root) else [root]
    for base in search_roots:
        for dirpath, dirnames, _ in os.walk(base):
            rel = os.path.relpath(dirpath, base)
            if rel.startswith("."):
                continue
            for skip in {"__pycache__", ".venv", "venv", "node_modules", "dist", "build"}:
                if skip in dirnames:
                    dirnames.remove(skip)
            pkg = os.path.relpath(dirpath, base).replace(os.sep, ".")
            if pkg in {".", "__init__"}:
                continue
            if os.path.isfile(os.path.join(dirpath, "__init__.py")):
                packages.append(pkg)
    return packages


def _find_python_modules(root: str) -> List[str]:
    modules: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for skip in {"__pycache__", ".venv", "venv", "node_modules", "dist", "build"}:
            if skip in dirnames:
                dirnames.remove(skip)
        for filename in filenames:
            if filename.endswith(".py"):
                if filename == "__init__.py":
                    continue
                modules.append(filename[:-3])
    return modules


def _build_project_context(root: str) -> ProjectContext:
    return ProjectContext(
        root=root,
        packages=_find_python_packages(root),
        modules=_find_python_modules(root),
    )


def _read_requirement_file(path: str) -> RequirementFile:
    file = RequirementFile(path=path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as exc:
        file.error = str(exc)
        return file
    for lineno, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip("\n")
        if COMMENT_OR_EMPTY_RE.match(line):
            continue
        dep = _parse_requirement_line(line, path, lineno)
        if dep is None:
            continue
        file.declared.append(dep)
        if dep.name.startswith("-"):
            continue
        if _is_vcs_uri(dep.spec):
            file.vcs_uris.append(f"{dep.name} -> {dep.spec}")
            continue
        if dep.spec and re.match(r"^(file://|\.\.?/|/)", dep.spec):
            target = os.path.join(os.path.dirname(path), dep.name)
            if not os.path.exists(target):
                file.missing.append(dep)
        age = _age_days(path)
        if age is not None and age > DEFAULT_STALE_DAYS:
            stale_dep = Dependency(
                name=dep.name,
                line=dep.line,
                source=dep.source,
                spec=dep.spec,
            )
            file.stale.append(stale_dep)
    return file


def _is_excluded(name: str, patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _detect_unused(requirement: RequirementFile, context: ProjectContext, exclude_patterns: Sequence[str] = ()) -> List[Dependency]:
    used_names = set()
    for name in context.modules:
        if _is_excluded(name, exclude_patterns):
            continue
        normalized = name.lower()
        used_names.add(normalized)
    for pkg in context.packages:
        if _is_excluded(pkg, exclude_patterns):
            continue
        used_names.add(pkg.lower())
    unused: List[Dependency] = []
    for dep in requirement.declared:
        if _is_excluded(dep.name, exclude_patterns):
            continue
        if dep.name.lower() not in used_names:
            unused.append(dep)
    return unused


def _find_requirement_files(path: str) -> List[str]:
    files: List[str] = []
    if os.path.isfile(path):
        if _is_requirement_like(path):
            files.append(path)
        return files
    for root, _, filenames in os.walk(path):
        for filename in sorted(filenames):
            if _is_requirement_like(filename):
                files.append(os.path.join(root, filename))
    return files


_REQUIREMENT_NAMES = {"requirements", "requirements-dev", "requirements_test", "test-requirements", "dev-requirements"}


def _is_requirement_like(name: str) -> bool:
    base = os.path.basename(name)
    if base.startswith("requirements") and base.endswith(".txt"):
        return True
    if base in {"Pipfile", "pyproject.toml", "poetry.lock"}:
        return True
    if base.startswith("constraints") and base.endswith(".txt"):
        return True
    if base in {"setup.cfg", "setup.py"}:
        return True
    return False


def _collect(path: str) -> Tuple[List[RequirementFile], ProjectContext]:
    requirement_files = [_read_requirement_file(p) for p in _find_requirement_files(path)]
    root = path if os.path.isdir(path) else os.path.dirname(path)
    context = _build_project_context(root)
    for req in requirement_files:
        req.unused = _detect_unused(req, context, exclude_patterns=[])
    return requirement_files, context


def _plain_report(requirement_files: Sequence[RequirementFile], only: str) -> str:
    lines: List[str] = []
    overall_issues = 0
    for req in requirement_files:
        if req.error:
            lines.append(f"{req.path}: error: {req.error}")
            continue
        issues = []
        if only in {"missing", "all"}:
            for dep in req.missing:
                issues.append(f"missing: {dep.name}")
        if only in {"stale", "all"}:
            for dep in req.stale:
                age = _age_days(req.path)
                age_str = f"{age:.1f}d" if age is not None else "unknown age"
                issues.append(f"stale: {dep.name} ({age_str})")
        if only in {"unused", "all"}:
            for dep in req.unused:
                issues.append(f"unused: {dep.name}")
        if issues:
            lines.append(f"{req.path}:")
            lines.extend(f"  - {issue}" for issue in issues)
            overall_issues += len(issues)
    if not lines:
        lines.append("No dependency issues found.")
    lines.append(f"Total issues: {overall_issues}")
    return "\n".join(lines)


def _markdown_report(requirement_files: Sequence[RequirementFile], only: str) -> str:
    lines: List[str] = ["# Dependency audit report", ""]
    overall_issues = 0
    for req in requirement_files:
        if req.error:
            lines.extend([f"## {req.path}", f"**error:** {req.error}", ""])
            continue
        issues = []
        if only in {"missing", "all"}:
            for dep in req.missing:
                issues.append(f"- missing: `{dep.name}`")
        if only in {"stale", "all"}:
            for dep in req.stale:
                age = _age_days(req.path)
                age_str = f"{age:.1f}d" if age is not None else "unknown age"
                issues.append(f"- stale: `{dep.name}` ({age_str})")
        if only in {"unused", "all"}:
            for dep in req.unused:
                issues.append(f"- unused: `{dep.name}`")
        if issues:
            lines.append(f"## {req.path}")
            lines.extend(issues)
            lines.append("")
            overall_issues += len(issues)
    lines.append(f"**Total issues:** {overall_issues}")
    return "\n".join(lines)


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace | _HelpArgs | None:
    parser = argparse.ArgumentParser(
        prog="reqwatch",
        description="Audit requirements-style files for stale, missing, or unused dependencies.",
    )
    parser.add_argument("path", help="requirements file or project root")
    parser.add_argument("--format", choices=["plain", "markdown"], default="plain")
    parser.add_argument("--skip-http", action="store_true", help="placeholder; no HTTP checks in current release")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="request timeout in seconds")
    parser.add_argument("--only", choices=["missing", "stale", "unused", "all"], default="all")
    parser.add_argument("--exclude", action="append", default=[], help="glob pattern to exclude")
    parser.add_argument("--exit-zero", action="store_true", help="always exit 0")
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            return _HelpArgs()
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if isinstance(args, _HelpArgs) or args is None:
        return 0
    if not os.path.exists(args.path):
        print(f"reqwatch: path not found: {args.path}", file=sys.stderr)
        return 2
    try:
        requirement_files, context = _collect(args.path)
    except Exception as exc:
        print(f"reqwatch: failed to collect requirements: {exc}", file=sys.stderr)
        return 2
    if args.format == "markdown":
        report = _markdown_report(requirement_files, args.only)
    else:
        report = _plain_report(requirement_files, args.only)
    print(report)
    issue_count = sum(
        len(req.missing) + len(req.stale) + len(req.unused)
        for req in requirement_files
    )
    if args.exit_zero:
        return 0
    return 1 if issue_count else 0


__all__ = [
    "main",
    "Dependency",
    "RequirementFile",
    "ProjectContext",
    "DEFAULT_STALE_DAYS",
]
