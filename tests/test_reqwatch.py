import os
import tempfile
from pathlib import Path

import reqwatch
from reqwatch import (
    DEFAULT_STALE_DAYS,
    Dependency,
    ProjectContext,
    RequirementFile,
    _age_days,
    _collect,
    _detect_unused,
    _extract_name,
    _find_python_modules,
    _find_python_packages,
    _is_excluded,
    _is_requirement_like,
    _markdown_report,
    _parse_requirement_line,
    _plain_report,
    _read_requirement_file,
)

_now = reqwatch._now


def test_extract_name_positive():
    assert _extract_name("requests==2.31.0") == "requests"
    assert _extract_name("rich[all]>=13.7.0") == "rich[all]"
    assert _extract_name("  flask") == "flask"
    assert _extract_name("pyyaml") == "pyyaml"


def test_extract_name_negative():
    assert _extract_name("# comment") is None
    assert _extract_name("") is None
    assert _extract_name("-r other.txt") is None


def test_parse_requirement_line():
    dep = _parse_requirement_line("requests==2.31.0", "reqs.txt", 1)
    assert dep is not None
    assert dep.name == "requests"
    assert dep.spec == "==2.31.0"
    assert dep.source == "reqs.txt"
    assert dep.line == 1


def test_is_requirement_like():
    assert _is_requirement_like("requirements.txt") is True
    assert _is_requirement_like("requirements-dev.txt") is True
    assert _is_requirement_like("README.md") is False
    assert _is_requirement_like("constraints.txt") is True


def test_read_requirement_file(tmp_path: Path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("requests==2.31.0\n# comment\nrich>=13.7.0\n")
    result = _read_requirement_file(str(req_file))
    assert [dep.name for dep in result.declared] == ["requests", "rich"]
    assert result.path == str(req_file)


def test_age_days(tmp_path: Path):
    old_file = tmp_path / "old.txt"
    old_file.write_text("x")
    os.utime(str(old_file), (1000000000, 1000000000))
    age = _age_days(str(old_file))
    assert age is not None
    assert age > 1000


def test_is_excluded():
    assert _is_excluded("pytest", ["pytest"]) is True
    assert _is_excluded("pytest-cov", ["pytest*"]) is True
    assert _is_excluded("rich", ["pytest*"]) is False


def test_collect_detects_missing(tmp_path: Path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("local-package file:///tmp/nonexistent-pkg-12345\n")
    reqs, context = _collect(str(tmp_path))
    assert len(reqs) == 1
    assert [dep.name for dep in reqs[0].missing] == ["local-package"]


def test_collect_detects_unused(tmp_path: Path):
    (tmp_path / "app.py").write_text("print('hello')\n")
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("requests==2.31.0\nrich>=13.7.0\n")
    reqs, context = _collect(str(tmp_path))
    assert len(reqs) == 1
    unused = [dep.name for dep in reqs[0].unused]
    assert "requests" in unused
    assert "rich" in unused


def test_plain_report_includes_issues(tmp_path: Path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("local-package file:///tmp/nonexistent-pkg-12345\n")
    reqs, _ = _collect(str(tmp_path))
    report = _plain_report(reqs, only="all")
    assert "local-package" in report
    assert "missing:" in report


def test_markdown_report_includes_issues(tmp_path: Path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("local-package file:///tmp/nonexistent-pkg-12345\n")
    reqs, _ = _collect(str(tmp_path))
    report = _markdown_report(reqs, only="all")
    assert "# Dependency audit report" in report
    assert "local-package" in report


def test_main_returns_zero_when_no_issues(tmp_path: Path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("rich>=13.7.0\n")
    (tmp_path / "rich.py").write_text("def main(): ...\n")
    old_time = _now() - DEFAULT_STALE_DAYS * 2
    os.utime(str(req_file), (old_time, old_time))
    rc = reqwatch.main([str(tmp_path)])
    assert rc == 0


def test_main_returns_one_when_issues(tmp_path: Path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("local-package file:///tmp/nonexistent-pkg-12345\n")
    rc = reqwatch.main([str(tmp_path)])
    assert rc == 1


def test_main_returns_two_when_path_missing():
    rc = reqwatch.main(["/tmp/reqwatch-missing-12345"])
    assert rc == 2


def test_main_help_returns_zero():
    rc = reqwatch.main(["--help"])
    assert rc == 0
