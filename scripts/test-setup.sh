#!/bin/bash
set -euo pipefail

# Test setup script for pyznap integration tests
# Tests use temporary file-backed zpools, so no ZFS dataset setup needed

# Re-exec as root if needed (single password prompt)
if [[ $EUID -ne 0 ]]; then
    if command -v pkexec &>/dev/null; then
        exec pkexec "$0" "$@"
    elif command -v sudo &>/dev/null; then
        exec sudo "$0" "$@"
    else
        echo "ERROR: Neither pkexec nor sudo available" >&2
        exit 1
    fi
fi

# Now running as root
PACKAGES=(faketime pv mbuffer)

# 1. Install missing packages
MISSING=()
for pkg in "${PACKAGES[@]}"; do
    command -v "$pkg" &>/dev/null || MISSING+=("$pkg")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "Installing: ${MISSING[*]}"
    apt-get install -y "${MISSING[@]}"
else
    echo "Packages: OK"
fi

# 2. SSH setup (needed for SSH tests)
# Root needs to SSH to root@127.0.0.1
if ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@127.0.0.1 exit 2>/dev/null; then
    echo "SSH root@127.0.0.1: OK"
else
    echo "Setting up SSH for root@127.0.0.1..."

    # Find existing root key or create ed25519
    if [[ -f /root/.ssh/id_ed25519.pub ]]; then
        ROOT_KEY_FILE=/root/.ssh/id_ed25519.pub
    elif [[ -f /root/.ssh/id_rsa.pub ]]; then
        ROOT_KEY_FILE=/root/.ssh/id_rsa.pub
    else
        [[ -d /root/.ssh ]] || install -d -m 700 /root/.ssh
        ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N "" -q
        ROOT_KEY_FILE=/root/.ssh/id_ed25519.pub
    fi

    ROOT_KEY=$(cat "$ROOT_KEY_FILE")

    # Check if key already present
    if [[ -f /root/.ssh/authorized_keys ]] && grep -qF "$ROOT_KEY" /root/.ssh/authorized_keys; then
        echo "Key already in authorized_keys"
    else
        # Create dir if needed, add key
        [[ -d /root/.ssh ]] || install -d -m 700 /root/.ssh
        echo "$ROOT_KEY" >> /root/.ssh/authorized_keys
        chmod 600 /root/.ssh/authorized_keys
        echo "Added key to root authorized_keys"
    fi

    # Verify
    if ! ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@127.0.0.1 exit 2>/dev/null; then
        echo "ERROR: SSH setup failed" >&2
        exit 1
    fi
    echo "SSH root@127.0.0.1: OK"
fi

echo "Setup complete. Tests use temporary file-backed zpools (100MB each)."
