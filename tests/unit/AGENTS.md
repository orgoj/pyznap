<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-04 | Updated: 2026-04-04 -->

# unit tests

## Purpose
Unit tests that run without root access or ZFS. Test individual functions and helper classes using mocks and fixtures.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Package marker |
| `test_utils.py` | Tests for `utils.py` - config parsing, `parse_name()`, `bytes_fmt()`, `check_recv()` |
| `test_config_validation.py` | Tests for `utils.py:validate_config()` - error detection, array alignment, type checking |
| `test_send_helpers.py` | Tests for `send_helpers.py` - `ParsedName`, `DestConfig`, `SourceContext`, `DestContext`, `SSHManager`, `extract_config_list_value()` |
| `test_status_helpers.py` | Tests for `status_helpers.py` - `SnapshotCategorizer`, `FilesystemOperations`, `FilesystemStatus`, `DestStatus`, `check_snapshot_counts()` |
| `test_verification.py` | Tests for `verification.py` - `verify_remote_snapshots()`, `SnapshotInfo`, `VerificationReport`, `format_duration()`, `extract_snapshot_type()` |

## For AI Agents

### Working In This Directory
- Run: `pytest tests/unit/ -v` or `make test-unit`
- No root, no ZFS, no SSH needed
- When adding new helper functions to the main package, add corresponding unit tests here
- Mock objects available from `tests/fixtures/` (mock_zfs.py, mock_ssh.py)

### Testing Requirements
- Tests should be self-contained and not depend on external state
- Use `unittest.mock` for mocking subprocess calls and ZFS objects
- Follow naming convention: `test_<function_name>_<scenario>()`

### Common Patterns
- Import fixtures: `from tests.fixtures.mock_zfs import ...` or `from tests.fixtures.mock_ssh import ...`
- Test config validation with constructed config dicts
- Test snapshot categorization with mock snapshot objects

<!-- MANUAL: Custom project notes can be added below -->
