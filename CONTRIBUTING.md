# Contributing to FlashCrucible

This file contains quick, agent-friendly instructions for contributors and automated agents.

Prerequisites

- Python 3.13+
- `uv` installed (project uses `uv` to manage venv & run commands)

Quickstart

```bash
make install         # create venv and install dependencies
uv run tfqa --help   # run CLI entrypoint
```

Local development setup (optional: pre-commit hooks)

```bash
# Install pre-commit framework
pip install pre-commit

# Install the git hooks defined in .pre-commit-config.yaml
pre-commit install

# (Optional) Run hooks on all files to check before committing
pre-commit run --all-files
```

Testing & Quality

```bash
make verify      # everything CI runs: lint, format, types, tests, hermetic tests, schemas
make format      # apply formatting and autofixes
make help        # list the individual targets
```

CI runs `make verify`, so there is one definition of "green".

Agent-specific notes

- Use `TFQA_MODE=ai` for AI-driven automation. This sets `--output json --non-interactive --no-color` semantics.
- For destructive test simulations in CI or tests, prefer mocking (do not touch real devices). Example:

```python
# In tests, mock subprocesses and set env TFQA_MODE=ai
result = subprocess.run(["tfqa", "full-capacity-test", "--device", "/dev/fake", "--yes", "--non-interactive"])
```

PR guidelines

- Create feature branches and open PRs against `master`.
- CI must pass (lint, mypy, pytest, format) before merge.
- Keep changes small and add tests for behavioral changes.
- Run `uv run ruff format .` locally before pushing to catch formatting issues early.

Contact & review

- Add `@<owner>` as reviewer for non-trivial changes.
