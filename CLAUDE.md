# pyznap Development Guide

## Quick Start

```bash
# Setup development environment
source .venv/bin/activate
pip install -e .[dev]

# Run linting
ruff check pyznap/
ruff format --check pyznap/

# Run unit tests (no ZFS required)
pytest tests/ -m "not slow"
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

### Test Types

- **Unit tests** (`tests/conftest.py` fixtures): Run without ZFS, use mocks
- **Integration tests** (`test_pyznap.py`, `test_functions.py`): Require root + ZFS pools + `faketime`

### Running Tests

```bash
# Unit tests only (fast, no ZFS needed)
pytest tests/ -m "not slow" -v

# All tests (requires root + ZFS + faketime)
sudo pytest tests/ -v

# Specific test file
pytest tests/test_functions.py -v
```

### Test Requirements

Integration tests require:
- Root access (ZFS commands need root)
- `faketime` program installed
- Available disk space for temporary ZFS pools (100MB each)

## Code Style

- **Formatter**: ruff format
- **Linter**: ruff check
- **Pre-commit**: Runs ruff automatically on commit

```bash
# Format code
ruff format pyznap/ tests/

# Check linting
ruff check pyznap/ tests/

# Fix auto-fixable issues
ruff check --fix pyznap/ tests/
```

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
