<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-04 | Updated: 2026-04-04 -->

# scripts

## Purpose
Helper scripts for test environment setup and development workflows.

## Key Files

| File | Description |
|------|-------------|
| `test-setup.sh` | Sets up test environment: installs required packages (`faketime`, `pv`, `mbuffer`), configures SSH keys for root@127.0.0.1 loopback testing |

## For AI Agents

### Working In This Directory
- Run `make test-setup` to execute test-setup.sh
- Required for integration and SSH test environments
- Modifies system state (installs packages, configures SSH authorized_keys)

<!-- MANUAL: Custom project notes can be added below -->
