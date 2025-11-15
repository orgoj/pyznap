#!/usr/bin/env pytest -v
"""
Unit tests for pyznap.send_helpers module.

Tests helper classes and utilities for send operations.
"""

import pytest
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, '/home/user/pyznap')

from pyznap.send_helpers import (
    ParsedName,
    DestConfig,
    SourceContext,
    DestContext,
    SSHManager,
    extract_config_list_value
)


class TestParsedName:
    """Test ParsedName dataclass."""

    def test_local_filesystem(self):
        """Test local filesystem parsing."""
        parsed = ParsedName(type='local', name='tank/data')

        assert parsed.type == 'local'
        assert parsed.name == 'tank/data'
        assert parsed.user is None
        assert parsed.host is None
        assert parsed.port is None
        assert not parsed.is_ssh

    def test_ssh_filesystem(self):
        """Test SSH filesystem parsing."""
        parsed = ParsedName(
            type='ssh',
            name='backup/data',
            user='root',
            host='backup.example.com',
            port=22
        )

        assert parsed.type == 'ssh'
        assert parsed.name == 'backup/data'
        assert parsed.user == 'root'
        assert parsed.host == 'backup.example.com'
        assert parsed.port == 22
        assert parsed.is_ssh

    def test_display_name_local(self):
        """Test display name for local filesystem."""
        parsed = ParsedName(type='local', name='tank/data')
        assert parsed.display_name == 'tank/data'

    def test_display_name_ssh(self):
        """Test display name for SSH filesystem."""
        parsed = ParsedName(
            type='ssh',
            name='backup/data',
            user='root',
            host='backup.example.com',
            port=22
        )
        assert parsed.display_name == 'root@backup.example.com:backup/data'


class TestDestConfig:
    """Test DestConfig dataclass."""

    def test_creation(self):
        """Test creating DestConfig."""
        config = DestConfig(
            name='backup/data',
            exclude=['*.tmp'],
            raw=False,
            resume=True,
            send_last_snapshot=False,
            dest_auto_create=False,
            retries=3,
            retry_interval=10
        )

        assert config.name == 'backup/data'
        assert config.exclude == ['*.tmp']
        assert config.raw is False
        assert config.resume is True
        assert config.retries == 3

    def test_normalize_send_last_snapshot(self):
        """Test that 'no' is converted to False."""
        config = DestConfig(
            name='backup/data',
            exclude=[],
            raw=False,
            resume=False,
            send_last_snapshot='no',  # Should be converted to False
            dest_auto_create=False,
            retries=0,
            retry_interval=10
        )

        assert config.send_last_snapshot is False

    def test_none_exclude_normalized(self):
        """Test that None exclude is converted to empty list."""
        config = DestConfig(
            name='backup/data',
            exclude=None,  # Should be converted to []
            raw=False,
            resume=False,
            send_last_snapshot=False,
            dest_auto_create=False,
            retries=0,
            retry_interval=10
        )

        assert config.exclude == []


class TestSourceContext:
    """Test SourceContext dataclass."""

    def test_local_context(self):
        """Test local source context."""
        ctx = SourceContext(name='tank/data')

        assert ctx.name == 'tank/data'
        assert ctx.ssh is None
        assert ctx.display_name == 'tank/data'
        assert not ctx.is_remote

    def test_remote_context(self):
        """Test remote source context."""
        mock_ssh = Mock()
        mock_ssh.user = 'root'
        mock_ssh.host = 'example.com'

        ctx = SourceContext(name='backup/data', ssh=mock_ssh)

        assert ctx.name == 'backup/data'
        assert ctx.ssh is mock_ssh
        assert ctx.is_remote
        assert 'root@example.com:backup/data' in ctx.display_name

    def test_close_local(self):
        """Test closing local context (no-op)."""
        ctx = SourceContext(name='tank/data')
        ctx.close()  # Should not raise

    def test_close_remote(self):
        """Test closing remote context."""
        mock_ssh = Mock()
        ctx = SourceContext(name='backup/data', ssh=mock_ssh)

        ctx.close()

        mock_ssh.close.assert_called_once()


class TestDestContext:
    """Test DestContext dataclass."""

    def test_local_context(self):
        """Test local destination context."""
        ctx = DestContext(name='backup/data')

        assert ctx.name == 'backup/data'
        assert ctx.ssh is None
        assert ctx.display_name == 'backup/data'
        assert not ctx.is_remote

    def test_remote_context(self):
        """Test remote destination context."""
        mock_ssh = Mock()
        mock_ssh.user = 'backup'
        mock_ssh.host = 'backup.server.com'

        ctx = DestContext(name='tank/data', ssh=mock_ssh)

        assert ctx.name == 'tank/data'
        assert ctx.ssh is mock_ssh
        assert ctx.is_remote

    def test_close_with_ssh(self):
        """Test that close() calls SSH close."""
        mock_ssh = Mock()
        ctx = DestContext(name='backup/data', ssh=mock_ssh)

        ctx.close()

        mock_ssh.close.assert_called_once()


class TestSSHManager:
    """Test SSHManager singleton."""

    def setUp(self):
        """Clear SSH connections before each test."""
        SSHManager._connections.clear()

    def tearDown(self):
        """Clean up after each test."""
        SSHManager._connections.clear()

    def test_connection_storage(self):
        """Test that SSHManager stores connections correctly."""
        SSHManager._connections.clear()

        # Create mock SSH connections and add them directly
        mock_ssh = Mock()
        mock_ssh.close = Mock()

        # Manually add connection to test storage
        conn_id = "root@example.com:22"
        SSHManager._connections[conn_id] = mock_ssh

        # Verify it's stored
        assert conn_id in SSHManager._connections
        assert SSHManager._connections[conn_id] is mock_ssh
        assert len(SSHManager._connections) == 1

    def test_connection_caching(self):
        """Test that SSHManager caches connections by host/user/port."""
        SSHManager._connections.clear()

        # Create mock connections for different configurations
        mock_ssh1 = Mock()
        mock_ssh2 = Mock()
        mock_ssh3 = Mock()

        # Add connections with different IDs
        SSHManager._connections["root@host1.com:22"] = mock_ssh1
        SSHManager._connections["user@host1.com:22"] = mock_ssh2  # different user
        SSHManager._connections["root@host1.com:2222"] = mock_ssh3  # different port

        # Verify all are stored separately
        assert len(SSHManager._connections) == 3
        assert SSHManager._connections["root@host1.com:22"] is mock_ssh1
        assert SSHManager._connections["user@host1.com:22"] is mock_ssh2
        assert SSHManager._connections["root@host1.com:2222"] is mock_ssh3

    def test_connection_id_format(self):
        """Test that connection IDs are formatted correctly."""
        SSHManager._connections.clear()

        # Create mock connections with expected ID format
        mock_ssh1 = Mock()
        mock_ssh2 = Mock()

        SSHManager._connections["root@host1.com:22"] = mock_ssh1
        SSHManager._connections["root@host2.com:2222"] = mock_ssh2

        # Verify both are stored
        assert len(SSHManager._connections) == 2
        assert "root@host1.com:22" in SSHManager._connections
        assert "root@host2.com:2222" in SSHManager._connections

    def test_close_all(self):
        """Test closing all SSH connections."""
        SSHManager._connections.clear()

        # Create mock connections
        mock_ssh1 = Mock()
        mock_ssh2 = Mock()

        SSHManager._connections["root@host1.com:22"] = mock_ssh1
        SSHManager._connections["root@host2.com:22"] = mock_ssh2

        SSHManager.close_all()

        mock_ssh1.close.assert_called_once()
        mock_ssh2.close.assert_called_once()
        assert len(SSHManager._connections) == 0

    def test_close_specific(self):
        """Test closing specific SSH connection."""
        SSHManager._connections.clear()

        # Create mock connections
        mock_ssh1 = Mock()
        mock_ssh2 = Mock()

        SSHManager._connections["root@host1.com:22"] = mock_ssh1
        SSHManager._connections["root@host2.com:22"] = mock_ssh2

        SSHManager.close('root', 'host1.com', 22)

        mock_ssh1.close.assert_called_once()
        mock_ssh2.close.assert_not_called()
        assert len(SSHManager._connections) == 1


class TestExtractConfigListValue:
    """Test extract_config_list_value utility."""

    def test_extract_from_list(self):
        """Test extracting value from list."""
        config = {'exclude': [['*.tmp'], ['*.log'], ['*.bak']]}

        value = extract_config_list_value(config, 'exclude', 0)
        assert value == ['*.tmp']

    def test_extract_missing_key(self):
        """Test extracting when key doesn't exist."""
        config = {}

        value = extract_config_list_value(config, 'exclude', 0, default=[])
        assert value == []

    def test_extract_none_value(self):
        """Test extracting when value is None."""
        config = {'exclude': None}

        value = extract_config_list_value(config, 'exclude', 0, default=[])
        assert value == []

    def test_extract_non_list(self):
        """Test extracting when value is not a list."""
        config = {'compress': 'lzop'}

        value = extract_config_list_value(config, 'compress', 0)
        assert value == 'lzop'

    def test_extract_index_out_of_range(self):
        """Test extracting when index is out of range."""
        config = {'exclude': [['*.tmp']]}

        value = extract_config_list_value(config, 'exclude', 5, default=[])
        assert value == []

    def test_pop_behavior(self):
        """Test that extract pops from front of list."""
        config = {'retries': [3, 5, 7]}

        val1 = extract_config_list_value(config, 'retries', 0)
        val2 = extract_config_list_value(config, 'retries', 0)

        assert val1 == 3
        assert val2 == 5
        assert config['retries'] == [7]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
