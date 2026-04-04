<!-- Generated: 2026-04-04 | Updated: 2026-04-04 -->

# pyznap

## Purpose
ZFS snapshot management tool written in Python. Handles automated snapshot creation (take), cleanup (clean), remote replication via send/receive, snapshot status reporting, and backup verification. Designed for cron/systemd scheduled backup workflows with support for local and SSH-based remote ZFS pools.

## Key Files

| File | Description |
|------|-------------|
| `pyproject.toml` | Package config, dependencies, ruff/pytest settings, entry point `pyznap.main:main` |
| `Makefile` | Build/test/lint/format/release targets. Use `make test-unit` (no root), `make test` (all), `make lint` |
| `setup.cfg` | Legacy pytest aliases |
| `CLAUDE.md` | AI agent instructions for this project |
| `.pre-commit-config.yaml` | Pre-commit hooks (ruff format + lint) |
| `README.md` | User documentation |
| `CHANGELOG.md` | Release history |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `pyznap/` | Main package source code (see `pyznap/AGENTS.md`) |
| `tests/` | Test suite - unit, integration, SSH tests (see `tests/AGENTS.md`) |
| `scripts/` | Build/test helper scripts (see `scripts/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- **Mandatory order**: `make format` -> `make lint` -> `make test-unit` -> `git commit`
- Never run lint before format. Never run tests before lint.
- Formatter: ruff format (single quotes, 120 char lines)
- Linter: ruff check (E, W, F, I, B, C4, UP rules)
- Python >=3.8 required

### Testing Requirements
- Unit tests: `make test-unit` (no root needed)
- Integration tests: `make test-integration` (root + ZFS kernel module)
- SSH tests: `make test-ssh` (root + ZFS + SSH to root@127.0.0.1)
- All tests: `make test`

### Design Decisions
- **Force receive (`-F`) is BY DESIGN**: Pyznap creates exact backup mirrors. Force receive ensures destination matches source exactly. This is core backup semantics, not a bug.
- **Dest disk space is dest machine's responsibility**: Pyznap does not monitor destination pool capacity. Operators should use Prometheus/node_exporter or similar on the backup target.

### Common Patterns
- Config format: INI files parsed by ConfigParser (see `pyznap/config/pyznap.conf` sample)
- SSH destinations: `ssh:port:user@host:pool/dataset`
- Snapshot naming: `pyznap_YYYY-MM-DD_HH:MM:SS_<type>` where type is frequent/hourly/daily/weekly/monthly/yearly
- Snapshot types tuple: `('frequent', 'hourly', 'daily', 'weekly', 'monthly', 'yearly')`
- Config values for arrays (dest, compress, exclude, etc.) are comma-separated and pop'd per destination

## Dependencies

### External
- `errorhandler` - Error handling in main loop
- `psutil` - PID file checking
- `paramiko` (dev) - SSH transport
- `pytest`, `pytest-dependency` (dev) - Testing
- `ruff`, `pre-commit` (dev) - Code quality

<!-- MANUAL: Custom project notes can be added below -->
