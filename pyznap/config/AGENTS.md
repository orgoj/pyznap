<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-04 | Updated: 2026-04-04 -->

# config

## Purpose
Contains the sample configuration file template installed by `pyznap setup`. Used as default config reference and for initial deployment.

## Key Files

| File | Description |
|------|-------------|
| `pyznap.conf` | Sample INI config with commented-out examples for snapshot, clean, send, SSH, and multi-dest setups |

## For AI Agents

### Working In This Directory
- This file is read via `importlib.resources` in `utils.py:create_config()` and copied to `/etc/pyznap/` during setup
- Config format: INI sections are ZFS dataset names (e.g., `[rpool/data]`), `//` maps to root
- Key options: `frequent`, `hourly`, `daily`, `weekly`, `monthly`, `yearly` (int counts), `snap`, `clean` (yes/no), `dest` (comma-separated SSH/local paths), `compress`, `exclude`, `raw_send`, `resume`, `retries`, `dest_auto_create`
- Values propagate from parent to child sections automatically

### Common Patterns
- Refer to this file when adding new config options - add to `options` list in `utils.py:read_config()` and update parsing logic

<!-- MANUAL: Custom project notes can be added below -->
