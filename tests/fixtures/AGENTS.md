<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-04 | Updated: 2026-04-04 -->

# fixtures

## Purpose
Shared test fixtures and mock objects used by both unit and integration tests. Provides mock ZFS dataset/snapshot objects and SSH connection stubs.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Package marker |
| `mock_zfs.py` | Mock ZFS objects - `MockZFSDataset`, `MockZFSFilesystem`, `MockZFSSnapshot` with configurable properties and snapshot lists |
| `mock_ssh.py` | Mock SSH connection object for testing remote operations without real SSH |

## For AI Agents

### Working In This Directory
- These are test support modules, not production code
- When adding new ZFS operations to `pyzfs.py`, add corresponding mock methods here
- Mock objects should match the interface of real `pyzfs.py` classes

### Common Patterns
- `MockZFSSnapshot` supports `.name`, `.snapname()`, `.fsname()`, `.getprops()`, `.stream_size()`
- `MockZFSFilesystem` supports `.name`, `.snapshots()`, `.snapshot()`, `.getprops()`, `.ispropval()`
- Configure mock behavior by setting attributes before passing to test functions

<!-- MANUAL: Custom project notes can be added below -->
