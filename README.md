# reqwatch

Audit Python `requirements*.txt` files for stale, missing, or unused dependencies.

## About

`reqwatch` reads requirements-style files and classifies each dependency as:

- `missing` — referenced local path does not exist
- `stale` — requirements file has not been modified within 180 days
- `unused` — dependency name does not match any importable Python module or package found in the project tree

It is intended for local development hygiene and lightweight CI checks.

## Known limitations

- `unused` analysis is best-effort: it matches against `*.py` filenames and `src/` package roots, so it may surface false positives for native/namespace packages, configuration-only requirements, and `pyproject.toml` sections. Use `--exclude` to tune noise.
- No HTTP reachability checks in this release. `--skip-http` is accepted for CLI compatibility.

## Installation

```bash
git clone https://github.com/felixhartmannnn/reqwatch.git
cd reqwatch
python -m pip install -e .
```

## Usage

```bash
# Audit a single file
reqwatch requirements.txt

# Audit a project root
reqwatch .

# Markdown output for CI artifacts
reqwatch . --format markdown

# Focus on unused dependencies only
reqwatch . --only unused

# Ignore noisy packages
reqwatch . --exclude 'pytest*' --exclude 'mock*'

# Always exit 0
reqwatch . --exit-zero
```

Exit codes:

- `0` — no issues or `--exit-zero` is set
- `1` — one or more issues found
- `2` — invalid input, such as a missing path

## Project structure

```text
reqwatch/
  .github/workflows/ci.yml
  .gitignore
  LICENSE
  README.md
  pyproject.toml
  reqwatch.py
  tests/
    test_reqwatch.py
```

## Tags

python, requirements, dependencies, audit, cli, local-first, ci
