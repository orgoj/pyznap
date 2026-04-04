<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-04 | Updated: 2026-04-04 -->

# pyznap (package)

## Purpose
Core package implementing ZFS snapshot lifecycle: take, clean, send/receive, status, verification, and fix. Contains Python ZFS bindings, SSH connection management, config parsing, and structured output handling.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Package version (`2.2.0`) |
| `main.py` | CLI entry point - argparse with subcommands: snap, send, full, fix, status, verify, validate-config, setup |
| `pyzfs.py` | ZFS Python bindings - `ZFSDataset`, `ZFSFilesystem`, `ZFSSnapshot`, `ZFSVolume` classes; `find()`, `open()`, `receive()`, `STATS` |
| `send.py` | Send/receive logic - `send_snap()`, `send_filesystem()`, `send_filesystem_stepwise()`, `send_config()` |
| `take.py` | Snapshot creation - `take_snap()`, `take_filesystem()`, `take_config()` |
| `clean.py` | Snapshot cleanup - `clean_snap()`, `clean_filesystem()`, `clean_config()` |
| `utils.py` | Config parsing (`read_config()`, `validate_config()`), name parsing (`parse_name()`), helpers |
| `ssh.py` | SSH connection class with ControlMaster, compression, mbuffer/pv detection |
| `process.py` | Subprocess wrappers with ZFS error detection (`DatasetNotFoundError`, `DatasetBusyError`, etc.), dry-run support |
| `output.py` | `OutputHandler` for JSON/JSONL structured output |
| `status.py` | Snapshot status reporting with log/jsonl/html output, destination checking |
| `status_helpers.py` | `SnapshotCategorizer`, `FilesystemOperations`, `FilesystemStatus`, `DestStatus` data classes |
| `send_helpers.py` | `ParsedName`, `DestConfig`, `SourceContext`, `DestContext`, `SSHManager` data classes (target architecture, partially integrated) |
| `verification.py` | Remote backup verification - `verify_remote_snapshots()`, `VerificationReport`, `Status` enum, `SnapshotInfo` |
| `fix.py` | Snapshot renaming tool for converting zfs-auto-snap/zfsnap formats to pyznap naming |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `config/` | Sample configuration file (see `config/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- This is the hot path - changes here affect all functionality
- `pyzfs.py` is the foundation; `send.py` depends heavily on it
- `send.py` and `pyzfs.py` are the most frequently modified files (hot paths)
- Config arrays (dest, compress, exclude, raw_send, resume, etc.) are consumed via `.pop(0)` in `send_config()` - always `copy.deepcopy(conf)` first
- Error codes from send functions: 0=success, 1=failure, 2=transient/SSH error (retriable)
- `process.py` custom errors: `DatasetNotFoundError`, `DatasetExistsError`, `DatasetBusyError`
- `ZFSSnapshot.send()` returns a `Popen` instance; `zfs.receive()` returns a `Popen` instance; they pipe stdout->stdin

### Testing Requirements
- Unit tests in `tests/unit/` cover helpers (status_helpers, send_helpers, verification, utils, config validation)
- Integration tests require root + ZFS kernel module
- Mock objects available in `tests/fixtures/mock_zfs.py` and `tests/fixtures/mock_ssh.py`

### Common Patterns
- Each module follows `*_config()` -> `*_filesystem()` -> individual operation pattern
- SSH connections: `SSH(user, host, port, key)` with auto-cleanup via `close()` or `__del__`
- Dry run: `set_dry_run()` sets global flag checked by `check_output_dry()`
- Stream size caching on `ZFSSnapshot.stream_cache` dict
- `mbuffer` and `pv` used when available for progress/compression pipelining

## Dependencies

### Internal
- All modules depend on `process.py` for subprocess execution and error types
- Most modules depend on `ssh.py` for remote operations
- `utils.py` provides config parsing consumed by all `*_config()` functions
- `status_helpers.py` and `send_helpers.py` are helper layers used by status.py and send.py

### External
- `errorhandler` (main.py only)
- `psutil` (main.py PID check only)
- Standard library: `subprocess`, `configparser`, `logging`, `argparse`, `datetime`

<!-- MANUAL: Custom project notes can be added below -->
