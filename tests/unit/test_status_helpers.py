#!/usr/bin/env pytest -v
"""
Unit tests for pyznap.status_helpers module.

Tests helper classes and utilities for status operations.
"""

from unittest.mock import Mock

import pytest

from pyznap.status_helpers import (
    DestStatus,
    FilesystemOperations,
    FilesystemStatus,
    SnapshotCategorizer,
    bytes_fmt,
    check_snapshot_counts,
    determine_operations,
    extract_snapshot_info,
    should_skip_filesystem,
)
from tests.fixtures.mock_zfs import create_mock_snapshot


class TestFilesystemOperations:
    """Test FilesystemOperations class."""

    def test_all_enabled(self):
        """Test when all operations are enabled."""
        ops = FilesystemOperations(snap=True, clean=True, send=True, excluded=False)

        assert ops.snap is True
        assert ops.clean is True
        assert ops.send is True
        assert ops.excluded is False
        assert ops.any_enabled() is True

    def test_all_disabled(self):
        """Test when all operations are disabled."""
        ops = FilesystemOperations(snap=False, clean=False, send=False, excluded=True)

        assert ops.snap is False
        assert ops.clean is False
        assert ops.send is False
        assert ops.excluded is True
        assert ops.any_enabled() is False

    def test_partial_enabled(self):
        """Test when some operations are enabled."""
        ops = FilesystemOperations(snap=True, clean=False, send=True, excluded=False)

        assert ops.snap is True
        assert ops.clean is False
        assert ops.send is True
        assert ops.any_enabled() is True

    def test_repr(self):
        """Test string representation."""
        ops = FilesystemOperations(snap=True, clean=True)
        repr_str = repr(ops)

        assert 'FilesystemOperations' in repr_str
        assert 'snap=True' in repr_str
        assert 'clean=True' in repr_str


class TestDestStatus:
    """Test DestStatus class."""

    def test_creation(self):
        """Test creating DestStatus."""
        dest = DestStatus(dest_type='ssh', host='backup.example.com', name='backup/data')

        assert dest.type == 'ssh'
        assert dest.host == 'backup.example.com'
        assert dest.name == 'backup/data'
        assert dest.snapshot_count == 0
        assert len(dest.common_snapshots) == 0

    def test_to_dict_basic(self):
        """Test serialization to dict."""
        dest = DestStatus(dest_type='local', host=None, name='backup/data')
        dest.snapshot_count = 10
        dest.common_snapshots = ['snap1', 'snap2', 'snap3']
        dest.first_snapshot = 'snap1'
        dest.last_snapshot = 'snap3'

        result = dest.to_dict(prefix='dest-0-')

        assert result['dest-0-type'] == 'local'
        assert result['dest-0-host'] is None
        assert result['dest-0-name'] == 'backup/data'
        assert result['dest-0-snapshot-count'] == 10
        assert result['dest-0-snapshot-count-common'] == 3
        assert result['dest-0-snapshot-common-first'] == 'snap1'
        assert result['dest-0-snapshot-common-last'] == 'snap3'
        assert result['dest-0-snapshot-dest-first'] == 'snap1'
        assert result['dest-0-snapshot-dest-last'] == 'snap3'

    def test_to_dict_no_snapshots(self):
        """Test serialization when no snapshots."""
        dest = DestStatus(dest_type='local', host=None, name='backup/data')

        result = dest.to_dict(prefix='')

        assert result['type'] == 'local'
        assert result['snapshot-count'] == 0
        assert result['snapshot-count-common'] == 0
        assert 'snapshot-common-first' not in result  # Not present when no snapshots


class TestFilesystemStatus:
    """Test FilesystemStatus class."""

    def test_creation(self):
        """Test creating FilesystemStatus."""
        status = FilesystemStatus(hostname='myhost', name='tank/data', conf_name='tank/data')

        assert status.hostname == 'myhost'
        assert status.name == 'tank/data'
        assert status.conf_name == 'tank/data'
        assert status.excluded is False
        assert isinstance(status.operations, FilesystemOperations)
        assert len(status.destinations) == 0

    def test_to_dict_basic(self):
        """Test serialization to dict."""
        status = FilesystemStatus(hostname='myhost', name='tank/data', conf_name='tank/data')
        status.operations = FilesystemOperations(snap=True, clean=True, send=False)
        status.has_snapshots = True
        status.all_snapshot_count = 10
        status.pyznap_snapshot_count = 8
        status.non_pyznap_snapshot_count = 2

        result = status.to_dict()

        assert result['hostname'] == 'myhost'
        assert result['name'] == 'tank/data'
        assert result['do-snap'] is True
        assert result['do-clean'] is True
        assert result['do-send'] is False
        assert result['snapshot-have'] is True
        assert result['snapshot-count-all'] == 10
        assert result['snapshot-count-pyznap'] == 8
        assert result['snapshot-count-nopyznap'] == 2

    def test_should_warn_with_missing(self):
        """Test that should_warn returns True when missing snapshots."""
        status = FilesystemStatus('myhost', 'tank/data', 'tank/data')
        status.missing_snapshots = True

        assert status.should_warn() is True

    def test_should_warn_without_missing(self):
        """Test that should_warn returns False when not missing."""
        status = FilesystemStatus('myhost', 'tank/data', 'tank/data')
        status.missing_snapshots = False

        assert status.should_warn() is False


class TestSnapshotCategorizer:
    """Test SnapshotCategorizer class."""

    def test_categorize_pyznap_snapshots(self):
        """Test categorizing pyznap snapshots."""
        snap1 = create_mock_snapshot('tank/data', hours_ago=1, snap_type='hourly')
        snap2 = create_mock_snapshot('tank/data', hours_ago=25, snap_type='daily')
        snap3 = create_mock_snapshot('tank/data', hours_ago=169, snap_type='weekly')

        fs_snapshots = [snap1, snap2, snap3]

        categorized = SnapshotCategorizer.categorize(fs_snapshots)

        assert len(categorized['hourly']) == 1
        assert len(categorized['daily']) == 1
        assert len(categorized['weekly']) == 1
        assert len(categorized['monthly']) == 0

    def test_categorize_ignores_non_pyznap(self):
        """Test that non-pyznap snapshots are ignored."""
        snap1 = create_mock_snapshot('tank/data', hours_ago=1, snap_type='daily')

        # Create a non-pyznap snapshot
        snap2 = Mock()
        snap2.name = 'tank/data@manual_snapshot'

        fs_snapshots = [snap1, snap2]

        categorized = SnapshotCategorizer.categorize(fs_snapshots)

        # Only pyznap snapshot should be categorized
        assert len(categorized['daily']) == 1
        assert sum(len(snaps) for snaps in categorized.values()) == 1

    def test_categorize_empty(self):
        """Test categorizing empty list."""
        categorized = SnapshotCategorizer.categorize([])

        assert all(len(snaps) == 0 for snaps in categorized.values())

    def test_count_by_type(self):
        """Test counting snapshots by type."""
        categorized = {
            'hourly': [Mock(), Mock()],
            'daily': [Mock(), Mock(), Mock()],
            'weekly': [Mock()],
            'monthly': [],
            'yearly': [],
            'frequent': [],
        }

        counts = SnapshotCategorizer.count_by_type(categorized)

        assert counts['hourly'] == 2
        assert counts['daily'] == 3
        assert counts['weekly'] == 1
        assert counts['monthly'] == 0


class TestDetermineOperations:
    """Test determine_operations function."""

    def test_all_enabled_main_fs(self):
        """Test when all operations enabled for main filesystem."""
        filesystem = Mock()
        conf = {
            'snap': True,
            'clean': True,
            'dest': ['backup/data'],
            'snap_exclude_property': None,
            'send_exclude_property': None,
        }

        ops = determine_operations(filesystem, conf, main_fs=True)

        assert ops.snap is True
        assert ops.clean is True
        assert ops.send is True
        assert ops.excluded is False

    def test_excluded_by_snap_property(self):
        """Test exclusion by snap property."""
        filesystem = Mock()
        filesystem.name = 'tank/data'
        filesystem.ispropval.return_value = True  # Property is 'false'

        conf = {
            'snap': True,
            'clean': True,
            'dest': [],
            'snap_exclude_property': 'com.sun:auto-snapshot',
            'send_exclude_property': None,
        }

        ops = determine_operations(filesystem, conf, main_fs=False)

        assert ops.snap is False  # Disabled by property
        assert ops.clean is False  # Also disabled
        assert ops.send is False

    def test_main_fs_ignores_exclude_property(self):
        """Test that main filesystem ignores exclude property."""
        filesystem = Mock()
        filesystem.name = 'tank/data'
        filesystem.ispropval.return_value = True

        conf = {
            'snap': True,
            'clean': True,
            'dest': [],
            'snap_exclude_property': 'com.sun:auto-snapshot',
            'send_exclude_property': None,
        }

        ops = determine_operations(filesystem, conf, main_fs=True)

        assert ops.snap is True  # NOT excluded because main_fs=True
        assert ops.clean is True

    def test_excluded_by_send_property(self):
        """Test exclusion by send property."""
        filesystem = Mock()
        filesystem.name = 'tank/data'
        filesystem.ispropval.return_value = True

        conf = {
            'snap': False,
            'clean': False,
            'dest': ['backup/data'],
            'snap_exclude_property': None,
            'send_exclude_property': 'pyznap:exclude',
        }

        ops = determine_operations(filesystem, conf, main_fs=False)

        assert ops.send is False  # Disabled by property


class TestShouldSkipFilesystem:
    """Test should_skip_filesystem function."""

    def test_no_filters(self):
        """Test when no filters provided."""
        assert should_skip_filesystem('tank/data', None) is False
        assert should_skip_filesystem('tank/data', []) is False

    def test_matches_pattern(self):
        """Test when filesystem matches pattern."""
        assert should_skip_filesystem('tank/temp', ['tank/temp']) is True
        assert should_skip_filesystem('tank/data/tmp', ['*/tmp']) is True

    def test_no_match(self):
        """Test when filesystem doesn't match any pattern."""
        assert should_skip_filesystem('tank/data', ['tank/temp']) is False


class TestExtractSnapshotInfo:
    """Test extract_snapshot_info function."""

    def test_extract_valid_snapshot(self):
        """Test extracting info from valid snapshot."""
        snap = Mock()
        snap.getprops.return_value = {
            'creation': ('1705329600', 'default'),  # 2024-01-15 12:00:00 UTC
            'referenced': ('1000000', 'default'),
            'logicalreferenced': ('1500000', 'default'),
        }

        info = extract_snapshot_info(snap)

        assert 'timestamp' in info
        assert info['referenced'] == 1000000
        assert info['logicalreferenced'] == 1500000

    def test_extract_missing_props(self):
        """Test extracting when properties are missing."""
        snap = Mock()
        snap.getprops.return_value = {}

        info = extract_snapshot_info(snap)

        assert info['timestamp'] is None
        assert info['referenced'] == 0
        assert info['logicalreferenced'] == 0


class TestCheckSnapshotCounts:
    """Test check_snapshot_counts function."""

    def test_all_match(self):
        """Test when all counts match policy."""
        categorized = {
            'hourly': [Mock(), Mock()],
            'daily': [Mock()],
            'weekly': [],
            'monthly': [],
            'yearly': [],
            'frequent': [],
        }
        policy = {'hourly': 2, 'daily': 1, 'weekly': 0}

        has_missing, has_extra = check_snapshot_counts(categorized, policy)

        assert has_missing is False
        assert has_extra is False

    def test_missing_snapshots(self):
        """Test when snapshots are missing."""
        categorized = {
            'hourly': [Mock()],  # Only 1, expected 3
            'daily': [],
            'weekly': [],
            'monthly': [],
            'yearly': [],
            'frequent': [],
        }
        policy = {'hourly': 3, 'daily': 2}

        has_missing, has_extra = check_snapshot_counts(categorized, policy)

        assert has_missing is True

    def test_extra_snapshots(self):
        """Test when extra snapshots exist."""
        categorized = {
            'hourly': [Mock(), Mock(), Mock(), Mock()],  # 4, expected 2
            'daily': [],
            'weekly': [],
            'monthly': [],
            'yearly': [],
            'frequent': [],
        }
        policy = {'hourly': 2}

        has_missing, has_extra = check_snapshot_counts(categorized, policy)

        assert has_extra is True


class TestBytesFmt:
    """Test bytes_fmt function."""

    def test_bytes(self):
        """Test formatting bytes."""
        assert bytes_fmt(512) == '512.0'

    def test_kilobytes(self):
        """Test formatting kilobytes."""
        assert bytes_fmt(1536) == '1.5K'  # 1.5 KB

    def test_megabytes(self):
        """Test formatting megabytes."""
        assert bytes_fmt(1572864) == '1.5M'  # 1.5 MB

    def test_gigabytes(self):
        """Test formatting gigabytes."""
        assert bytes_fmt(1610612736) == '1.5G'  # 1.5 GB

    def test_terabytes(self):
        """Test formatting terabytes."""
        assert bytes_fmt(1649267441664) == '1.5T'  # 1.5 TB


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
