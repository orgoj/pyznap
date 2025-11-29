# pyznap Development Guide

## Quick Start

```bash
# Setup development environment
make install-dev

# Run linting
make lint

# Run unit tests (no root required)
make test-unit

# Run ALL tests (unit + integration with root)
make test
```

## Project Structure

```
pyznap/
├── pyznap/           # Main package
│   ├── main.py       # CLI entry point
│   ├── send.py       # ZFS send/receive logic
│   ├── take.py       # Snapshot creation
│   ├── clean.py      # Snapshot cleanup
│   ├── pyzfs.py      # ZFS Python bindings
│   ├── ssh.py        # SSH connection handling
│   └── utils.py      # Config parsing, helpers
├── tests/            # Test suite
└── setup.py          # Package configuration
```

## Testing

**IMPORTANT: Always use make targets for running tests!**

### Running Tests

```bash
# Setup test environment (install packages, SSH keys)
make test-setup

# ALL tests (unit + integration)
make test

# Unit tests only (no root required)
make test-unit

# All root tests (integration + SSH)
make test-root

# Integration tests only (root + ZFS)
make test-integration

# SSH tests only (root + ZFS + SSH)
make test-ssh
```

### Test Types

- **Unit tests** (`tests/unit/`): No ZFS/root required
- **Integration tests** (`test_functions.py`, `test_pyznap.py`): Require root + ZFS
- **SSH tests** (`test_functions_ssh.py`, `test_pyznap_ssh.py`): Require root + ZFS + SSH to root@127.0.0.1

### Test Requirements

Integration tests require:
- Root access via pkexec or sudo (handled by Makefile)
- ZFS module loaded (tests create temporary file-backed pools)
- `faketime`, `pv`, `mbuffer` packages (installed by `make test-setup`)

## Code Style

**🚨 CRITICAL - MANDATORY ORDER (NEVER VIOLATE):**

```
1. make format    ← FIRST!
2. make lint      ← SECOND!
3. make test-unit ← THIRD!
4. git commit     ← LAST!
```

**NEVER run tests before lint. NEVER run lint before format. VIOLATION = IMMEDIATE STOP.**

- **Formatter**: ruff format
- **Linter**: ruff check
- **Pre-commit**: Runs ruff automatically on commit

## Key Modules

### send.py
- `send_snap()`: Send single snapshot
- `send_filesystem()`: Send filesystem with `-I` (all intermediates)
- `send_filesystem_stepwise()`: Send one-by-one with `-i` (for broken chains)
- `send_config()`: Process config and send all filesystems

### pyzfs.py
- `ZFSSnapshot.send()`: Build `zfs send` command
- `receive()`: Build `zfs receive` command
- `open()`: Open ZFS dataset

### utils.py
- `read_config()`: Parse INI config file
- `parse_name()`: Parse `ssh:port:user@host:pool/data` format

## Common Patterns

### Adding New Config Option

1. Add to `options` list in `utils.py:read_config()`
2. Add parsing logic based on type (boolean, list, string)
3. Add validation in `validate_config()` if needed
4. Pass through to relevant function in send.py/take.py/clean.py

### Adding New CLI Flag

1. Add `parser.add_argument()` in `main.py`
2. Pass to settings dict or config
3. Handle in relevant module
