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

<!-- bv-agent-instructions-v2 -->

---

## Beads Workflow Integration

This project uses [beads_rust](https://github.com/Dicklesworthstone/beads_rust) (`br`) for issue tracking and [beads_viewer](https://github.com/Dicklesworthstone/beads_viewer) (`bv`) for graph-aware triage. Issues are stored in `.beads/` and tracked in git.

### Using bv as an AI sidecar

bv is a graph-aware triage engine for Beads projects (.beads/beads.jsonl). Instead of parsing JSONL or hallucinating graph traversal, use robot flags for deterministic, dependency-aware outputs with precomputed metrics (PageRank, betweenness, critical path, cycles, HITS, eigenvector, k-core).

**Scope boundary:** bv handles *what to work on* (triage, priority, planning). `br` handles creating, modifying, and closing beads.

**CRITICAL: Use ONLY --robot-* flags. Bare bv launches an interactive TUI that blocks your session.**

#### The Workflow: Start With Triage

**`bv --robot-triage` is your single entry point.** It returns everything you need in one call:
- `quick_ref`: at-a-glance counts + top 3 picks
- `recommendations`: ranked actionable items with scores, reasons, unblock info
- `quick_wins`: low-effort high-impact items
- `blockers_to_clear`: items that unblock the most downstream work
- `project_health`: status/type/priority distributions, graph metrics
- `commands`: copy-paste shell commands for next steps

```bash
bv --robot-triage        # THE MEGA-COMMAND: start here
bv --robot-next          # Minimal: just the single top pick + claim command

# Token-optimized output (TOON) for lower LLM context usage:
bv --robot-triage --format toon
```

#### Other bv Commands

| Command | Returns |
|---------|---------|
| `--robot-plan` | Parallel execution tracks with unblocks lists |
| `--robot-priority` | Priority misalignment detection with confidence |
| `--robot-insights` | Full metrics: PageRank, betweenness, HITS, eigenvector, critical path, cycles, k-core |
| `--robot-alerts` | Stale issues, blocking cascades, priority mismatches |
| `--robot-suggest` | Hygiene: duplicates, missing deps, label suggestions, cycle breaks |
| `--robot-diff --diff-since <ref>` | Changes since ref: new/closed/modified issues |
| `--robot-graph [--graph-format=json\|dot\|mermaid]` | Dependency graph export |

#### Scoping & Filtering

```bash
bv --robot-plan --label backend              # Scope to label's subgraph
bv --robot-insights --as-of HEAD~30          # Historical point-in-time
bv --recipe actionable --robot-plan          # Pre-filter: ready to work (no blockers)
bv --recipe high-impact --robot-triage       # Pre-filter: top PageRank scores
```

### br Commands for Issue Management

```bash
br ready              # Show issues ready to work (no blockers)
br list --status=open # All open issues
br show <id>          # Full issue details with dependencies
br create --title="..." --type=task --priority=2
br update <id> --status=in_progress
br close <id> --reason="Completed"
br close <id1> <id2>  # Close multiple issues at once
br sync --flush-only  # Export DB to JSONL
```

### Workflow Pattern

1. **Triage**: Run `bv --robot-triage` to find the highest-impact actionable work
2. **Claim**: Use `br update <id> --status=in_progress`
3. **Work**: Implement the task
4. **Complete**: Use `br close <id>`
5. **Sync**: Always run `br sync --flush-only` at session end

### Key Concepts

- **Dependencies**: Issues can block other issues. `br ready` shows only unblocked work.
- **Priority**: P0=critical, P1=high, P2=medium, P3=low, P4=backlog (use numbers 0-4, not words)
- **Types**: task, bug, feature, epic, chore, docs, question
- **Blocking**: `br dep add <issue> <depends-on>` to add dependencies

### Session Protocol

```bash
git status              # Check what changed
git add <files>         # Stage code changes
br sync --flush-only    # Export beads changes to JSONL
git commit -m "..."     # Commit everything
git push                # Push to remote
```

<!-- end-bv-agent-instructions -->
