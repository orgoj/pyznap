#!/usr/bin/env pytest -v
"""
Unit tests for pyznap.verification module.

Tests all verification functionality including:
- Snapshot metadata extraction
- Common snapshot finding
- Remote verification logic
- Special cases (new remote, empty remote, no common snapshots)
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock

import sys
sys.path.insert(0, '/home/user/pyznap')

from pyznap.verification import (
    Status,
    SnapshotInfo,
    VerificationReport,
    extract_snapshot_type,
    extract_snapshot_name,
    get_snapshots_with_metadata,
    find_latest_common_snapshot,
    check_missing_incrementals,
    count_snapshots_of_type,
    verify_remote_snapshots,
    format_duration
)
from tests.fixtures.mock_zfs import (
    MockZFSFilesystem,
    create_mock_snapshot,
    create_mock_filesystem_with_snapshots
)


class TestStatus:
    """Test Status enum."""

    def test_status_comparison(self):
        """Test that status levels can be compared."""
        assert Status.OK < Status.WARNING
        assert Status.WARNING < Status.ERROR
        assert Status.ERROR < Status.CRITICAL


class TestSnapshotInfo:
    """Test SnapshotInfo class."""

    def test_snapshot_info_creation(self):
        """Test creating a SnapshotInfo."""
        now = datetime.now()
        snap = SnapshotInfo(
            name='tank/data@pyznap_2025-01-15_120000_daily',
            creation_time=now,
            used=1000000,
            referenced=5000000,
            snap_type='daily'
        )

        assert snap.name == 'tank/data@pyznap_2025-01-15_120000_daily'
        assert snap.creation_time == now
        assert snap.used == 1000000
        assert snap.referenced == 5000000
        assert snap.snap_type == 'daily'

    def test_snapshot_info_equality(self):
        """Test snapshot equality comparison."""
        now = datetime.now()
        snap1 = SnapshotInfo('tank/data@snap1', now, 1000, 5000, 'daily')
        snap2 = SnapshotInfo('tank/data@snap1', now, 1000, 5000, 'daily')
        snap3 = SnapshotInfo('tank/data@snap2', now, 1000, 5000, 'daily')

        assert snap1 == snap2
        assert snap1 != snap3


class TestVerificationReport:
    """Test VerificationReport class."""

    def test_report_creation(self):
        """Test creating an empty report."""
        report = VerificationReport()

        assert report.status == Status.UNKNOWN
        assert report.lag_seconds == 0.0
        assert len(report.missing_snapshots) == 0
        assert len(report.errors) == 0
        assert len(report.warnings) == 0
        assert len(report.recommendations) == 0

    def test_adding_messages(self):
        """Test adding messages to report."""
        report = VerificationReport()

        report.add_error("Test error")
        report.add_warning("Test warning")
        report.add_recommendation("Test recommendation")
        report.add_info("Test info")

        assert len(report.errors) == 1
        assert len(report.warnings) == 1
        assert len(report.recommendations) == 1
        assert len(report.info) == 1

    def test_to_dict(self):
        """Test serialization to dict."""
        report = VerificationReport()
        report.status = Status.WARNING
        report.lag_seconds = 3600.0
        report.add_warning("Test warning")

        result = report.to_dict()

        assert result['status'] == 'WARNING'
        assert result['lag_seconds'] == 3600.0
        assert result['lag_human'] == '1.0h'
        assert len(result['warnings']) == 1


class TestFormatDuration:
    """Test duration formatting."""

    def test_format_seconds(self):
        """Test formatting seconds."""
        assert format_duration(45) == '45s'

    def test_format_minutes(self):
        """Test formatting minutes."""
        assert format_duration(180) == '3m'

    def test_format_hours(self):
        """Test formatting hours."""
        assert format_duration(7200) == '2.0h'

    def test_format_days(self):
        """Test formatting days."""
        assert format_duration(172800) == '2.0d'


class TestExtractSnapshotType:
    """Test snapshot type extraction."""

    def test_pyznap_daily(self):
        """Test extracting daily snapshot type."""
        name = 'tank/data@pyznap_2025-01-15_120000_daily'
        assert extract_snapshot_type(name) == 'daily'

    def test_pyznap_hourly(self):
        """Test extracting hourly snapshot type."""
        name = 'tank/data@pyznap_2025-01-15_120000_hourly'
        assert extract_snapshot_type(name) == 'hourly'

    def test_pyznap_weekly(self):
        """Test extracting weekly snapshot type."""
        name = 'tank/data@pyznap_2025-01-15_120000_weekly'
        assert extract_snapshot_type(name) == 'weekly'

    def test_non_pyznap_snapshot(self):
        """Test non-pyznap snapshot."""
        name = 'tank/data@manual_snapshot'
        assert extract_snapshot_type(name) == 'unknown'

    def test_invalid_format(self):
        """Test invalid snapshot name."""
        assert extract_snapshot_type('invalid') == 'unknown'


class TestExtractSnapshotName:
    """Test snapshot name extraction."""

    def test_extract_name(self):
        """Test extracting snapshot name."""
        full = 'tank/data@pyznap_2025-01-15_120000_daily'
        assert extract_snapshot_name(full) == 'pyznap_2025-01-15_120000_daily'

    def test_no_at_sign(self):
        """Test name without @ sign."""
        assert extract_snapshot_name('justname') == 'justname'


class TestGetSnapshotsWithMetadata:
    """Test getting snapshots with metadata."""

    def test_get_snapshots(self):
        """Test getting snapshots from filesystem."""
        # Create mock filesystem with snapshots
        snap1 = create_mock_snapshot('tank/data', hours_ago=1, snap_type='hourly')
        snap2 = create_mock_snapshot('tank/data', hours_ago=25, snap_type='daily')

        filesystem = MockZFSFilesystem('tank/data', snapshots=[snap1, snap2])

        # Get snapshots with metadata
        snapshots = get_snapshots_with_metadata(filesystem)

        assert len(snapshots) == 2
        assert all(isinstance(s, SnapshotInfo) for s in snapshots)

        # Should be sorted newest first
        assert snapshots[0].snap_type == 'hourly'
        assert snapshots[1].snap_type == 'daily'

    def test_empty_filesystem(self):
        """Test filesystem with no snapshots."""
        filesystem = MockZFSFilesystem('tank/data', snapshots=[])

        snapshots = get_snapshots_with_metadata(filesystem)

        assert len(snapshots) == 0


class TestFindLatestCommonSnapshot:
    """Test finding latest common snapshot."""

    def test_find_common_snapshot(self):
        """Test finding latest common snapshot."""
        now = datetime.now()

        # Source has 3 snapshots
        source_snaps = [
            SnapshotInfo('tank/data@snap1', now - timedelta(hours=1), 1000, 5000, 'hourly'),
            SnapshotInfo('tank/data@snap2', now - timedelta(hours=25), 1000, 5000, 'daily'),
            SnapshotInfo('tank/data@snap3', now - timedelta(hours=49), 1000, 5000, 'daily'),
        ]

        # Dest has snap2 and snap3 (missing snap1)
        dest_snaps = [
            SnapshotInfo('backup/data@snap2', now - timedelta(hours=25), 1000, 5000, 'daily'),
            SnapshotInfo('backup/data@snap3', now - timedelta(hours=49), 1000, 5000, 'daily'),
        ]

        common = find_latest_common_snapshot(source_snaps, dest_snaps)

        assert common is not None
        assert common.name == 'tank/data@snap2'  # Latest common

    def test_no_common_snapshots(self):
        """Test when there are no common snapshots."""
        now = datetime.now()

        source_snaps = [
            SnapshotInfo('tank/data@snap1', now, 1000, 5000, 'hourly'),
        ]

        dest_snaps = [
            SnapshotInfo('backup/data@snap2', now, 1000, 5000, 'hourly'),
        ]

        common = find_latest_common_snapshot(source_snaps, dest_snaps)

        assert common is None


class TestCheckMissingIncrementals:
    """Test checking for missing incremental snapshots."""

    def test_find_missing_incrementals(self):
        """Test finding missing incremental snapshots."""
        now = datetime.now()

        # Source has snap1, snap2, snap3, snap4
        source_snaps = [
            SnapshotInfo('tank/data@snap4', now - timedelta(hours=1), 1000, 5000, 'hourly'),
            SnapshotInfo('tank/data@snap3', now - timedelta(hours=25), 1000, 5000, 'daily'),
            SnapshotInfo('tank/data@snap2', now - timedelta(hours=49), 1000, 5000, 'daily'),
            SnapshotInfo('tank/data@snap1', now - timedelta(hours=73), 1000, 5000, 'daily'),
        ]

        # Dest has snap1 and snap4 (missing snap2 and snap3)
        dest_snaps = [
            SnapshotInfo('backup/data@snap4', now - timedelta(hours=1), 1000, 5000, 'hourly'),
            SnapshotInfo('backup/data@snap1', now - timedelta(hours=73), 1000, 5000, 'daily'),
        ]

        # snap1 is the latest common
        latest_common = source_snaps[3]

        missing = check_missing_incrementals(source_snaps, dest_snaps, latest_common)

        assert len(missing) == 2
        # snap2 and snap3 should be missing
        assert any(s.name == 'tank/data@snap2' for s in missing)
        assert any(s.name == 'tank/data@snap3' for s in missing)

    def test_no_missing_incrementals(self):
        """Test when all incrementals are present."""
        now = datetime.now()

        source_snaps = [
            SnapshotInfo('tank/data@snap2', now - timedelta(hours=1), 1000, 5000, 'hourly'),
            SnapshotInfo('tank/data@snap1', now - timedelta(hours=25), 1000, 5000, 'daily'),
        ]

        dest_snaps = [
            SnapshotInfo('backup/data@snap2', now - timedelta(hours=1), 1000, 5000, 'hourly'),
            SnapshotInfo('backup/data@snap1', now - timedelta(hours=25), 1000, 5000, 'daily'),
        ]

        latest_common = source_snaps[1]

        missing = check_missing_incrementals(source_snaps, dest_snaps, latest_common)

        assert len(missing) == 0


class TestCountSnapshotsOfType:
    """Test counting snapshots by type."""

    def test_count_daily_snapshots(self):
        """Test counting daily snapshots."""
        now = datetime.now()

        snapshots = [
            SnapshotInfo('tank/data@snap1', now, 1000, 5000, 'hourly'),
            SnapshotInfo('tank/data@snap2', now, 1000, 5000, 'daily'),
            SnapshotInfo('tank/data@snap3', now, 1000, 5000, 'daily'),
            SnapshotInfo('tank/data@snap4', now, 1000, 5000, 'weekly'),
        ]

        assert count_snapshots_of_type(snapshots, 'daily') == 2
        assert count_snapshots_of_type(snapshots, 'hourly') == 1
        assert count_snapshots_of_type(snapshots, 'weekly') == 1
        assert count_snapshots_of_type(snapshots, 'monthly') == 0


class TestVerifyRemoteSnapshots:
    """Test main verification function."""

    def test_up_to_date_remote(self):
        """Test when remote is up-to-date."""
        # Create source with recent snapshots
        source_fs = create_mock_filesystem_with_snapshots('tank/data', [
            (1, 'hourly'),
            (25, 'daily'),
            (49, 'daily')
        ])

        # Create dest with same snapshots
        dest_fs = create_mock_filesystem_with_snapshots('backup/data', [
            (1, 'hourly'),
            (25, 'daily'),
            (49, 'daily')
        ])

        config = {
            'verify_thresholds': {
                'ok': 86400,        # 1 day
                'warning': 172800,  # 2 days
                'critical': 604800  # 7 days
            }
        }

        report = verify_remote_snapshots(source_fs, dest_fs, config)

        assert report.status == Status.OK
        assert report.lag_seconds < 3600  # Less than 1 hour

    def test_lagging_remote_warning(self):
        """Test when remote is lagging (WARNING level)."""
        # Source has recent snapshot
        source_fs = create_mock_filesystem_with_snapshots('tank/data', [
            (1, 'hourly'),
            (25, 'daily'),
        ])

        # Dest missing the recent snapshot
        dest_fs = create_mock_filesystem_with_snapshots('backup/data', [
            (25, 'daily'),
        ])

        config = {
            'verify_thresholds': {
                'ok': 3600,         # 1 hour
                'warning': 86400,   # 1 day
                'critical': 604800  # 7 days
            }
        }

        report = verify_remote_snapshots(source_fs, dest_fs, config)

        assert report.status == Status.WARNING
        assert report.lag_seconds > 3600

    def test_lagging_remote_critical(self):
        """Test when remote is severely lagging (CRITICAL)."""
        # Source has recent snapshot
        source_fs = create_mock_filesystem_with_snapshots('tank/data', [
            (1, 'hourly'),
            (169, 'weekly'),  # 1 week old
        ])

        # Dest only has very old snapshot
        dest_fs = create_mock_filesystem_with_snapshots('backup/data', [
            (169, 'weekly'),
        ])

        config = {
            'verify_thresholds': {
                'ok': 86400,        # 1 day
                'warning': 172800,  # 2 days
                'critical': 604800  # 7 days
            }
        }

        report = verify_remote_snapshots(source_fs, dest_fs, config)

        assert report.status in [Status.ERROR, Status.CRITICAL]
        assert report.lag_seconds > 86400  # More than 1 day

    def test_empty_remote(self):
        """Test when remote has no snapshots."""
        source_fs = create_mock_filesystem_with_snapshots('tank/data', [
            (1, 'hourly'),
        ])

        dest_fs = MockZFSFilesystem('backup/data', snapshots=[])

        config = {}

        report = verify_remote_snapshots(source_fs, dest_fs, config)

        assert report.status == Status.WARNING
        assert "no snapshots" in report.warnings[0].lower()

    def test_no_common_snapshots(self):
        """Test when there are no common snapshots."""
        source_fs = create_mock_filesystem_with_snapshots('tank/data', [
            (1, 'hourly'),
        ])

        # Different snapshot names
        now = datetime.now()
        snap = create_mock_snapshot('backup/data', hours_ago=1, snap_type='hourly')
        # Change the name to make it different
        snap._snapshot_name = 'different_snapshot'
        snap.name = f"backup/data@different_snapshot"

        dest_fs = MockZFSFilesystem('backup/data', snapshots=[snap])

        config = {}

        report = verify_remote_snapshots(source_fs, dest_fs, config)

        assert report.status == Status.CRITICAL
        assert "no common" in report.errors[0].lower()

    def test_missing_incrementals_warning(self):
        """Test warning for missing incremental snapshots."""
        # Source has 4 snapshots
        source_fs = create_mock_filesystem_with_snapshots('tank/data', [
            (1, 'hourly'),
            (25, 'daily'),
            (49, 'daily'),
            (73, 'daily'),
        ])

        # Dest missing middle snapshots
        source_snaps = source_fs.snapshots()
        dest_snaps = [source_snaps[0], source_snaps[3]]  # Only first and last

        # Create new mock snapshots for dest with different filesystem name
        dest_snap_list = []
        for s in dest_snaps:
            props = s.getprops()
            new_snap = create_mock_snapshot(
                'backup/data',
                hours_ago=0,
                snap_type=s._snapshot_name.split('_')[-1]
            )
            # Keep the same snapshot name
            new_snap._snapshot_name = s._snapshot_name
            new_snap.name = f"backup/data@{s._snapshot_name}"
            new_snap._creation_time = s._creation_time
            new_snap._timestamp = s._timestamp
            dest_snap_list.append(new_snap)

        dest_fs = MockZFSFilesystem('backup/data', snapshots=dest_snap_list)

        config = {
            'verify_thresholds': {
                'ok': 86400,
                'warning': 172800,
                'critical': 604800
            }
        }

        report = verify_remote_snapshots(source_fs, dest_fs, config)

        # Should have warnings about missing incrementals
        assert len(report.missing_snapshots) > 0
        assert any('incremental' in w.lower() for w in report.warnings)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
