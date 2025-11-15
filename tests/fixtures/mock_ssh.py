"""
Mock SSH objects for unit testing.

Provides mock implementations of SSH connections and operations
without requiring actual SSH servers.
"""

from unittest.mock import Mock, MagicMock


class MockSSHConnection:
    """
    Mock implementation of an SSH connection.

    Simulates SSH connection behavior for testing purposes.
    """

    def __init__(self, user, host, port=22, key=None, compress='lzop'):
        """
        Initialize a mock SSH connection.

        Args:
            user: SSH username
            host: Remote hostname
            port: SSH port (default: 22)
            key: Path to SSH key file
            compress: Compression method
        """
        self.user = user
        self.host = host
        self.port = port
        self.key = key
        self.compress = compress
        self._closed = False
        self._socket = f'/tmp/mock_ssh_{user}_{host}_{port}'

        # Mock stdin/stdout/stderr
        self.stdin = Mock()
        self.stdout = Mock()
        self.stderr = Mock()

    def close(self):
        """Close the SSH connection."""
        self._closed = True

    def is_closed(self):
        """Check if connection is closed."""
        return self._closed

    def exec_command(self, command, timeout=None):
        """
        Mock command execution.

        Args:
            command: Command to execute
            timeout: Command timeout

        Returns:
            Tuple of (stdin, stdout, stderr) mocks
        """
        return (self.stdin, self.stdout, self.stderr)

    def __repr__(self):
        status = 'closed' if self._closed else 'open'
        return f"MockSSHConnection({self.user}@{self.host}:{self.port}, {status})"

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


def create_mock_ssh(user='root', host='example.com', port=22, key=None):
    """
    Create a mock SSH connection.

    Args:
        user: SSH username
        host: Remote hostname
        port: SSH port
        key: Path to SSH key

    Returns:
        MockSSHConnection instance

    Example:
        >>> ssh = create_mock_ssh('backup', 'backup.example.com', 22)
        >>> ssh.user
        'backup'
    """
    return MockSSHConnection(user=user, host=host, port=port, key=key)
