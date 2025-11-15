"""
Pytest configuration and shared fixtures for pyznap tests.

This module provides reusable fixtures for testing pyznap components,
including mocked ZFS filesystems, SSH connections, and snapshots.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock
from collections import OrderedDict


@pytest.fixture
def mock_snapshot():
    """
    Creates a mock ZFS snapshot.

    Returns:
        Mock snapshot with name, creation time, and properties
    """
    def _create_snapshot(name, hours_ago=0, snap_type='daily', used=1000000, referenced=5000000):
        creation_time = datetime.now() - timedelta(hours=hours_ago)
        timestamp = int(creation_time.timestamp())

        snapshot = Mock()
        snapshot.name = name

        # Mock getprops() to return creation time and sizes
        props = {
            'creation': (str(timestamp), 'default'),
            'used': (str(used), 'default'),
            'referenced': (str(referenced), 'default'),
            'logicalreferenced': (str(referenced), 'default')
        }
        snapshot.getprops.return_value = props

        return snapshot

    return _create_snapshot


@pytest.fixture
def mock_filesystem():
    """
    Creates a mock ZFS filesystem.

    Returns:
        Mock filesystem with snapshots and properties
    """
    def _create_filesystem(name, snapshots_list=None):
        filesystem = Mock()
        filesystem.name = name

        # Mock snapshots
        if snapshots_list is None:
            snapshots_list = []
        filesystem.snapshots.return_value = snapshots_list

        # Mock properties
        props = OrderedDict()
        props['used'] = ('1000000', 'default')
        props['available'] = ('5000000', 'default')
        props['referenced'] = ('900000', 'default')
        props['compressratio'] = ('1.50x', 'default')
        filesystem.getprops.return_value = props

        # Mock ispropval
        filesystem.ispropval.return_value = False

        return filesystem

    return _create_filesystem


@pytest.fixture
def mock_ssh_connection():
    """
    Creates a mock SSH connection.

    Returns:
        Mock SSH object with user, host, port attributes
    """
    def _create_ssh(user='root', host='example.com', port=22):
        ssh = Mock()
        ssh.user = user
        ssh.host = host
        ssh.port = port
        ssh.close = Mock()
        return ssh

    return _create_ssh


@pytest.fixture
def mock_pyznap_snapshots(mock_snapshot):
    """
    Creates a list of mock pyznap snapshots for testing.

    Returns:
        List of mock snapshots with various types
    """
    snapshots = [
        mock_snapshot('tank/data@pyznap_2025-01-15_120000_hourly', hours_ago=1),
        mock_snapshot('tank/data@pyznap_2025-01-15_110000_hourly', hours_ago=2),
        mock_snapshot('tank/data@pyznap_2025-01-14_120000_daily', hours_ago=25),
        mock_snapshot('tank/data@pyznap_2025-01-13_120000_daily', hours_ago=49),
        mock_snapshot('tank/data@pyznap_2025-01-07_120000_weekly', hours_ago=193),
        mock_snapshot('tank/data@pyznap_2024-12-15_120000_monthly', hours_ago=745),
    ]
    return snapshots


@pytest.fixture
def sample_config():
    """
    Creates a sample pyznap configuration for testing.

    Returns:
        Dict with sample configuration
    """
    return {
        'name': 'tank/data',
        'frequent': 4,
        'hourly': 24,
        'daily': 7,
        'weekly': 4,
        'monthly': 6,
        'yearly': 1,
        'snap': True,
        'clean': True,
        'dest': ['backup/data'],
        'exclude': [[]],
        'raw_send': [False],
        'resume': [False],
        'retries': [3],
        'retry_interval': [10],
        'dest_keys': [None],
        'compress': ['lzop'],
        'snap_exclude_property': None,
        'send_exclude_property': None,
        'dest_auto_create': [False],
        'send_last_snapshot': [False]
    }


@pytest.fixture
def sample_ssh_config(sample_config):
    """
    Creates a sample configuration with SSH destinations.

    Returns:
        Dict with SSH configuration
    """
    config = sample_config.copy()
    config['name'] = 'tank/data'
    config['dest'] = ['ssh:22:root@backup.example.com:backup/data']
    config['dest_keys'] = ['/root/.ssh/id_rsa']
    return config
