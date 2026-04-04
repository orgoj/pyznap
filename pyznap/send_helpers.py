"""
Helper classes and utilities for pyznap send operations.

This module provides structured data classes and helper functions
to make send operations more maintainable and testable.
"""

# TODO: migrate send.py to use these helpers. This module represents the target
# architecture for send operations. See RALPLAN Fix 2.4 for context.

import logging
from dataclasses import dataclass
from typing import List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class ParsedName:
    """
    Parsed ZFS filesystem or SSH destination name.

    Attributes:
        type: 'local' or 'ssh'
        name: Filesystem name (without ssh prefix)
        user: SSH username (None for local)
        host: SSH hostname (None for local)
        port: SSH port (None for local)
    """

    type: str
    name: str
    user: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None

    @property
    def display_name(self) -> str:
        """Get human-readable display name."""
        if self.type == 'ssh':
            return f'{self.user}@{self.host}:{self.name}'
        return self.name

    @property
    def is_ssh(self) -> bool:
        """Check if this is an SSH destination."""
        return self.type == 'ssh'


@dataclass
class DestConfig:
    """
    Configuration for a single destination.

    Contains all parameters needed to send to one destination.
    """

    name: str
    exclude: List[str]
    raw: bool
    resume: bool
    send_last_snapshot: Union[str, bool]
    dest_auto_create: bool
    retries: int
    retry_interval: int
    dest_key: Optional[str] = None
    compress: Optional[str] = None
    send_exclude_property: Optional[str] = None

    def __post_init__(self):
        """Validate and normalize values."""
        if self.exclude is None:
            self.exclude = []
        if self.send_last_snapshot == 'no':
            self.send_last_snapshot = False


@dataclass
class SourceContext:
    """
    Context for source filesystem operations.

    Manages source filesystem and its SSH connection if remote.
    """

    name: str
    ssh: Optional[object] = None  # SSH object
    display_name: str = ''

    def __post_init__(self):
        """Initialize display name if not provided."""
        if not self.display_name:
            if self.ssh:
                self.display_name = f'{self.ssh.user}@{self.ssh.host}:{self.name}'
            else:
                self.display_name = self.name

    def close(self):
        """Close SSH connection if exists."""
        if self.ssh:
            self.ssh.close()

    @property
    def is_remote(self) -> bool:
        """Check if source is remote."""
        return self.ssh is not None


@dataclass
class DestContext:
    """
    Context for destination filesystem operations.

    Manages destination filesystem and its SSH connection if remote.
    """

    name: str
    ssh: Optional[object] = None  # SSH object
    display_name: str = ''

    def __post_init__(self):
        """Initialize display name if not provided."""
        if not self.display_name:
            if self.ssh:
                self.display_name = f'{self.ssh.user}@{self.ssh.host}:{self.name}'
            else:
                self.display_name = self.name

    def close(self):
        """Close SSH connection if exists."""
        if self.ssh:
            self.ssh.close()

    @property
    def is_remote(self) -> bool:
        """Check if destination is remote."""
        return self.ssh is not None


class SSHManager:
    """
    Manages SSH connections with singleton pattern.

    Ensures that we reuse SSH connections instead of creating
    multiple connections to the same host.
    """

    _connections = {}

    @classmethod
    def get_or_create(cls, user: str, host: str, port: int = 22, key: Optional[str] = None, compress: str = 'lzop'):
        """
        Get existing SSH connection or create new one.

        Args:
            user: SSH username
            host: SSH hostname
            port: SSH port (default: 22)
            key: Path to SSH key file
            compress: Compression method

        Returns:
            SSH connection object
        """
        from paramiko.ssh_exception import SSHException

        from .ssh import SSH

        conn_id = f'{user}@{host}:{port}'

        if conn_id not in cls._connections:
            logger.debug(f'Creating new SSH connection: {conn_id}')
            try:
                cls._connections[conn_id] = SSH(user, host, port=port, key=key, compress=compress)
            except (FileNotFoundError, SSHException) as err:
                logger.error(f'Failed to create SSH connection to {conn_id}: {err}')
                raise

        return cls._connections[conn_id]

    @classmethod
    def close_all(cls):
        """Close all managed SSH connections."""
        for conn_id, ssh in cls._connections.items():
            logger.debug(f'Closing SSH connection: {conn_id}')
            ssh.close()
        cls._connections.clear()

    @classmethod
    def close(cls, user: str, host: str, port: int = 22):
        """Close specific SSH connection."""
        conn_id = f'{user}@{host}:{port}'
        if conn_id in cls._connections:
            logger.debug(f'Closing SSH connection: {conn_id}')
            cls._connections[conn_id].close()
            del cls._connections[conn_id]


def extract_config_list_value(config: dict, key: str, index: int, default=None):
    """
    Extract value from config list at specific index.

    Many config values are lists (one per destination).
    This helper safely extracts the value at the given index.

    Args:
        config: Configuration dict
        key: Key to extract
        index: Index in the list
        default: Default value if not found

    Returns:
        Value at index or default

    Example:
        >>> config = {'exclude': [['*.tmp'], ['*.log']]}
        >>> extract_config_list_value(config, 'exclude', 0)
        ['*.tmp']
    """
    if key not in config or config[key] is None:
        return default

    value_list = config[key]

    if not isinstance(value_list, list):
        return value_list

    if index < len(value_list):
        return value_list.pop(0)  # Pop from front to maintain compatibility

    return default
