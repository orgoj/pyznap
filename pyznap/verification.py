"""
Remote snapshot verification for pyznap.

This module provides functionality to verify that remote backup destinations
are up-to-date and contain all necessary snapshots.

Key features:
- Check time lag between source and destination
- Verify existence of critical snapshots (daily, weekly, monthly)
- Detect missing incremental snapshots
- Intelligent handling of new remotes with partial history
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class Status(Enum):
    """Verification status levels."""

    OK = 'OK'
    WARNING = 'WARNING'
    ERROR = 'ERROR'
    CRITICAL = 'CRITICAL'
    UNKNOWN = 'UNKNOWN'

    def __lt__(self, other):
        """Allow comparison of status levels."""
        order = [Status.OK, Status.WARNING, Status.ERROR, Status.CRITICAL, Status.UNKNOWN]
        return order.index(self) < order.index(other)


class SnapshotInfo:
    """
    Metadata about a ZFS snapshot.

    Attributes:
        name: Full snapshot name (filesystem@snapshot)
        creation_time: When the snapshot was created
        used: Used space in bytes
        referenced: Referenced space in bytes
        snap_type: Type of snapshot (hourly, daily, weekly, monthly, yearly)
    """

    def __init__(self, name: str, creation_time: datetime, used: int, referenced: int, snap_type: str):
        self.name = name
        self.creation_time = creation_time
        self.used = used
        self.referenced = referenced
        self.snap_type = snap_type

    def __repr__(self):
        return f"SnapshotInfo('{self.name}', type={self.snap_type}, time={self.creation_time})"

    def __eq__(self, other):
        if not isinstance(other, SnapshotInfo):
            return False
        return self.name == other.name


class VerificationReport:
    """
    Report from remote snapshot verification.

    Contains status, lag information, missing snapshots, and recommendations.
    """

    def __init__(self):
        self.status = Status.UNKNOWN
        self.lag_seconds = 0.0
        self.missing_snapshots: List[SnapshotInfo] = []
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.recommendations: List[str] = []
        self.info: List[str] = []

    def add_error(self, message: str):
        """Add an error message."""
        self.errors.append(message)

    def add_warning(self, message: str):
        """Add a warning message."""
        self.warnings.append(message)

    def add_recommendation(self, message: str):
        """Add a recommendation."""
        self.recommendations.append(message)

    def add_info(self, message: str):
        """Add an info message."""
        self.info.append(message)

    def to_dict(self) -> Dict:
        """
        Serialize to dictionary for JSON output.

        Returns:
            Dict with all report fields
        """
        return {
            'status': self.status.value,
            'lag_seconds': self.lag_seconds,
            'lag_human': format_duration(self.lag_seconds),
            'missing_snapshots_count': len(self.missing_snapshots),
            'missing_snapshots': [s.name for s in self.missing_snapshots],
            'errors': self.errors,
            'warnings': self.warnings,
            'recommendations': self.recommendations,
            'info': self.info,
        }

    def format_human_readable(self) -> str:
        """
        Format report for human-readable output.

        Returns:
            Formatted string
        """
        lines = []
        lines.append(f'Status: {self.status.value}')
        lines.append(f'Lag: {format_duration(self.lag_seconds)}')

        if self.errors:
            lines.append('\nErrors:')
            for err in self.errors:
                lines.append(f'  ❌ {err}')

        if self.warnings:
            lines.append('\nWarnings:')
            for warn in self.warnings:
                lines.append(f'  ⚠️  {warn}')

        if self.missing_snapshots:
            lines.append(f'\nMissing snapshots: {len(self.missing_snapshots)}')
            for snap in self.missing_snapshots[:5]:  # Show first 5
                lines.append(f'  - {snap.name}')
            if len(self.missing_snapshots) > 5:
                lines.append(f'  ... and {len(self.missing_snapshots) - 5} more')

        if self.recommendations:
            lines.append('\nRecommendations:')
            for rec in self.recommendations:
                lines.append(f'  💡 {rec}')

        if self.info:
            lines.append('\nInfo:')
            for inf in self.info:
                lines.append(f'  ℹ️  {inf}')

        return '\n'.join(lines)


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to human-readable format.

    Args:
        seconds: Duration in seconds

    Returns:
        Human-readable string (e.g., "1.2d", "3.5h", "45m")
    """
    if seconds < 60:
        return f'{seconds:.0f}s'
    elif seconds < 3600:
        return f'{seconds / 60:.0f}m'
    elif seconds < 86400:
        return f'{seconds / 3600:.1f}h'
    else:
        return f'{seconds / 86400:.1f}d'


def get_snapshots_with_metadata(filesystem) -> List[SnapshotInfo]:
    """
    Get list of snapshots with metadata.

    Args:
        filesystem: ZFS filesystem object

    Returns:
        List of SnapshotInfo objects, sorted by creation time (newest first)
    """
    snapshots = []

    try:
        fs_snapshots = filesystem.snapshots()
    except Exception as err:
        logger.error(f'Failed to get snapshots for {filesystem}: {err}')
        return []

    for snap in fs_snapshots:
        try:
            props = snap.getprops()
            creation_time = datetime.fromtimestamp(int(props['creation'][0]))
            used = int(props['used'][0])
            referenced = int(props['referenced'][0])

            # Extract snapshot type from name
            snap_type = extract_snapshot_type(snap.name)

            snapshot_info = SnapshotInfo(
                name=snap.name, creation_time=creation_time, used=used, referenced=referenced, snap_type=snap_type
            )
            snapshots.append(snapshot_info)

        except (KeyError, ValueError, IndexError) as err:
            logger.debug(f'Skipping snapshot {snap.name}: {err}')
            continue

    # Sort by creation time, newest first
    snapshots.sort(key=lambda s: s.creation_time, reverse=True)
    return snapshots


def extract_snapshot_type(full_name: str) -> str:
    """
    Extract snapshot type from full snapshot name.

    Format: pool/dataset@pyznap_<timestamp>_<type>

    Args:
        full_name: Full snapshot name (e.g., 'tank/data@pyznap_2025-01-15_143022_daily')

    Returns:
        Snapshot type (e.g., 'daily', 'weekly') or 'unknown'
    """
    try:
        # Split on @ to get snapshot name
        if '@' not in full_name:
            return 'unknown'

        snapshot_part = full_name.split('@')[1]

        # Split on _ to get parts
        parts = snapshot_part.split('_')

        # pyznap snapshots: pyznap_YYYY-MM-DD_HHMMSS_type
        if len(parts) >= 4 and parts[0] == 'pyznap':
            return parts[-1]  # Last part is the type

        return 'unknown'

    except (IndexError, AttributeError):
        return 'unknown'


def extract_snapshot_name(full_name: str) -> str:
    """
    Extract snapshot name without filesystem prefix.

    Args:
        full_name: Full snapshot name (e.g., 'tank/data@snapshot_name')

    Returns:
        Just the snapshot name part (e.g., 'snapshot_name')
    """
    return full_name.split('@')[-1] if '@' in full_name else full_name


def find_latest_common_snapshot(
    source_snaps: List[SnapshotInfo], dest_snaps: List[SnapshotInfo]
) -> Optional[SnapshotInfo]:
    """
    Find the latest snapshot that exists on both source and destination.

    Args:
        source_snaps: List of source snapshots
        dest_snaps: List of destination snapshots

    Returns:
        Latest common SnapshotInfo or None if no common snapshots
    """
    # Create sets of snapshot names (without filesystem prefix)
    source_names = {extract_snapshot_name(s.name) for s in source_snaps}
    dest_names = {extract_snapshot_name(s.name) for s in dest_snaps}

    # Find common names
    common_names = source_names & dest_names

    if not common_names:
        return None

    # Filter source snapshots to only common ones
    common_snaps = [s for s in source_snaps if extract_snapshot_name(s.name) in common_names]

    if not common_snaps:
        return None

    # Return the one with latest creation time
    return max(common_snaps, key=lambda s: s.creation_time)


def check_missing_incrementals(
    source_snaps: List[SnapshotInfo], dest_snaps: List[SnapshotInfo], latest_common: SnapshotInfo
) -> List[SnapshotInfo]:
    """
    Check for missing snapshots between latest_common and source_latest.

    These missing incremental snapshots could indicate problems with
    the incremental send chain.

    Args:
        source_snaps: List of source snapshots
        dest_snaps: List of destination snapshots
        latest_common: Latest common snapshot

    Returns:
        List of missing SnapshotInfo objects
    """
    dest_names = {extract_snapshot_name(s.name) for s in dest_snaps}

    missing = []
    found_common = False

    # Sort by creation time (oldest first for this check)
    sorted_source = sorted(source_snaps, key=lambda s: s.creation_time)

    for snap in sorted_source:
        # Find the common snapshot
        if snap.name == latest_common.name:
            found_common = True
            continue

        # After finding common, check if snapshots exist on dest
        if found_common:
            snap_name = extract_snapshot_name(snap.name)
            if snap_name not in dest_names:
                missing.append(snap)

    return missing


def count_snapshots_of_type(snapshots: List[SnapshotInfo], snap_type: str) -> int:
    """
    Count how many snapshots of a specific type exist.

    Args:
        snapshots: List of snapshots
        snap_type: Type to count (e.g., 'daily', 'weekly')

    Returns:
        Count of snapshots of that type
    """
    return len([s for s in snapshots if s.snap_type == snap_type])


def verify_remote_snapshots(source_fs, dest_fs, config: Dict) -> VerificationReport:
    """
    Verify that remote destination is up-to-date with source.

    Main verification function that checks:
    - Time lag between source and destination
    - Existence of critical snapshots
    - Integrity of incremental chain
    - Special handling for new remotes

    Args:
        source_fs: Source filesystem object
        dest_fs: Destination filesystem object
        config: Configuration dict with 'verify_thresholds'

    Returns:
        VerificationReport with results and recommendations
    """
    report = VerificationReport()

    # Get thresholds from config
    thresholds = config.get(
        'verify_thresholds',
        {
            'ok': 86400,  # 1 day
            'warning': 172800,  # 2 days
            'critical': 604800,  # 7 days
        },
    )

    # 1. Get snapshots with metadata
    logger.debug(f'Getting snapshots for source {source_fs}')
    source_snaps = get_snapshots_with_metadata(source_fs)

    logger.debug(f'Getting snapshots for destination {dest_fs}')
    dest_snaps = get_snapshots_with_metadata(dest_fs)

    # 2. Check if destination has any snapshots
    if not dest_snaps:
        report.status = Status.WARNING
        report.add_warning('Destination has no snapshots (new remote?)')
        report.add_recommendation('Initialize with: pyznap send')
        return report

    if not source_snaps:
        report.status = Status.ERROR
        report.add_error('Source has no snapshots')
        return report

    # 3. Find latest common snapshot
    latest_common = find_latest_common_snapshot(source_snaps, dest_snaps)

    if not latest_common:
        report.status = Status.CRITICAL
        report.add_error('No common snapshots found between source and destination')
        report.add_recommendation('May need to re-initialize backup with full send')
        return report

    # 4. Calculate time lag
    source_latest = max(source_snaps, key=lambda s: s.creation_time)
    lag = source_latest.creation_time - latest_common.creation_time
    report.lag_seconds = lag.total_seconds()

    logger.debug(f'Latest common snapshot: {latest_common.name}')
    logger.debug(f'Lag: {format_duration(report.lag_seconds)}')

    # 5. Evaluate status based on lag
    if report.lag_seconds < thresholds['ok']:
        report.status = Status.OK
    elif report.lag_seconds < thresholds['warning']:
        report.status = Status.WARNING
        report.add_warning(f'Destination is {format_duration(report.lag_seconds)} behind')
        report.add_recommendation('Consider increasing backup frequency')
    elif report.lag_seconds < thresholds['critical']:
        report.status = Status.ERROR
        report.add_error(f'Destination is {format_duration(report.lag_seconds)} behind')
        report.add_recommendation('Immediate backup required: pyznap send')
    else:
        report.status = Status.CRITICAL
        report.add_error(f'Destination is severely out of date ({format_duration(report.lag_seconds)} behind)')
        report.add_recommendation('URGENT: Run pyznap send immediately')

    # 6. Check for missing incremental snapshots
    missing_incrementals = check_missing_incrementals(source_snaps, dest_snaps, latest_common)

    if missing_incrementals:
        report.missing_snapshots = missing_incrementals
        report.add_warning(f'{len(missing_incrementals)} incremental snapshots missing on destination')

        # Upgrade status if we have warnings but status is OK
        if report.status == Status.OK:
            report.status = Status.WARNING

    # 7. Check snapshot type coverage (daily, weekly, monthly)
    for snap_type in ['daily', 'weekly', 'monthly']:
        source_count = count_snapshots_of_type(source_snaps, snap_type)
        dest_count = count_snapshots_of_type(dest_snaps, snap_type)

        if source_count > 0:
            coverage = dest_count / source_count if source_count > 0 else 0

            if coverage < 0.8:  # Less than 80% coverage
                report.add_warning(
                    f'Low {snap_type} snapshot coverage: {coverage * 100:.0f}% ({dest_count}/{source_count})'
                )

                # Upgrade to WARNING if currently OK
                if report.status == Status.OK:
                    report.status = Status.WARNING

    # 8. Special case: Check if dest is newer than source (new backup destination)
    oldest_dest = min(dest_snaps, key=lambda s: s.creation_time)
    oldest_source = min(source_snaps, key=lambda s: s.creation_time)

    if oldest_dest.creation_time > oldest_source.creation_time:
        report.add_info(f'Remote initialized at {oldest_dest.creation_time.isoformat()}')
        report.add_info('Partial history is expected for new backup destinations')

    return report
