<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-04 | Updated: 2026-04-04 -->

# tests

## Purpose
Test suite for pyznap. Divided into unit tests (no root required) and integration/SSH tests (require root, ZFS kernel module, and optionally SSH). Tests use file-backed temporary ZFS pools.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Package marker |
| `conftest.py` | Shared pytest fixtures |
| `test_utils.py` | Tests for `utils.py` (config parsing, name parsing, bytes_fmt) |
| `test_functions.py` | Integration tests for core functions (root + ZFS required) |
| `test_functions_ssh.py` | Integration tests for SSH send/receive (root + ZFS + SSH required) |
| `test_pyznap.py` | Integration CLI-level tests (root + ZFS required) |
| `test_pyznap_ssh.py` | Integration CLI-level SSH tests (root + ZFS + SSH required) |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `unit/` | Unit tests requiring no root or ZFS (see `unit/AGENTS.md`) |
| `fixtures/` | Test fixtures and mock objects (see `fixtures/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- Run unit tests: `make test-unit` or `pytest tests/unit/ -v`
- Run integration tests: `make test-integration` (requires root via pkexec/sudo)
- Run SSH tests: `make test-ssh` (requires root + SSH to root@127.0.0.1)
- Integration tests create temporary file-backed ZFS pools, cleaned up after each test
- Tests use `faketime` to simulate time progression for snapshot scheduling
- Required packages for integration tests: `faketime`, `pv`, `mbuffer`

### Testing Requirements
- Always run `make format` and `make lint` before running tests
- Pre-commit hook runs ruff automatically on commit
- Use `@pytest.mark.slow` for tests requiring ZFS/root

### Common Patterns
- Integration tests follow pattern: create pool -> take snapshots -> verify -> cleanup
- SSH tests require `pytest-dependency` for ordered execution

<!-- MANUAL: Custom project notes can be added below -->
