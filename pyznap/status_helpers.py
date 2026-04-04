"""
Helper classes and utilities for pyznap status operations.

This module provides structured data classes and helper functions
to make status operations more maintainable and testable.
"""

# TODO: migrate status.py to use these helpers. This module represents the target
# architecture for status operations. See RALPLAN Fix 2.4 for context.

import logging
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Snapshot types recognized by pyznap
SNAPSHOT_TYPES = ['frequent', 'hourly', 'daily', 'weekly', 'monthly', 'yearly']


class FilesystemOperations:
    """
    Encapsulates which operations should run on a filesystem.

    Attributes:
        snap: Whether to take snapshots
        clean: Whether to clean old snapshots
        send: Whether to send to destinations
        excluded: Whether filesystem is excluded from all operations
    """

    def __init__(self, snap: bool = False, clean: bool = False, send: bool = False, excluded: bool = False):
        self.snap = snap
        self.clean = clean
        self.send = send
        self.excluded = excluded

    def any_enabled(self) -> bool:
        """Check if any operation is enabled."""
        return self.snap or self.clean or self.send

    def __repr__(self):
        return f'FilesystemOperations(snap={self.snap}, clean={self.clean}, send={self.send}, excluded={self.excluded})'


class DestStatus:
    """
    Status information for a single destination.

    Attributes:
        type: Destination type ('local' or 'ssh')
        host: Hostname (None for local)
        name: Destination filesystem name
        snapshot_count: Number of snapshots on destination
        common_snapshots: List of common snapshot names
        first_snapshot: First snapshot name (oldest)
        last_snapshot: Last snapshot name (newest)
    """

    def __init__(self, dest_type: str, host: Optional[str], name: str):
        self.type = dest_type
        self.host = host
        self.name = name
        self.snapshot_count = 0
        self.common_snapshots: List[str] = []
        self.first_snapshot: Optional[str] = None
        self.last_snapshot: Optional[str] = None

    def to_dict(self, prefix: str = '') -> Dict[str, any]:
        """
        Serialize to dictionary with optional prefix.

        Args:
            prefix: Prefix for dictionary keys (e.g., 'dest-0-')

        Returns:
            Dictionary with status information
        """
        result = {}
        result[f'{prefix}type'] = self.type
        result[f'{prefix}host'] = self.host
        result[f'{prefix}name'] = self.name
        result[f'{prefix}snapshot-count'] = self.snapshot_count
        result[f'{prefix}snapshot-count-common'] = len(self.common_snapshots)

        if self.common_snapshots:
            result[f'{prefix}snapshot-common-first'] = self.common_snapshots[0]
            result[f'{prefix}snapshot-common-last'] = self.common_snapshots[-1]

        if self.first_snapshot:
            result[f'{prefix}snapshot-dest-first'] = self.first_snapshot
        if self.last_snapshot:
            result[f'{prefix}snapshot-dest-last'] = self.last_snapshot

        return result


class FilesystemStatus:
    """
    Complete status information for a filesystem.

    Encapsulates all status data for a single filesystem including
    snapshots, operations, and destination status.
    """

    def __init__(self, hostname: str, name: str, conf_name: str):
        self.hostname = hostname
        self.name = name
        self.conf_name = conf_name
        self.excluded = False
        self.operations = FilesystemOperations()
        self.snapshots: Dict[str, List] = {t: [] for t in SNAPSHOT_TYPES}
        self.destinations: List[DestStatus] = []
        self.filesystem_properties: Dict[str, any] = {}

        # Snapshot info
        self.has_snapshots = False
        self.missing_snapshots = False
        self.extra_snapshots = False

        # Snapshot counts
        self.all_snapshot_count = 0
        self.pyznap_snapshot_count = 0
        self.non_pyznap_snapshot_count = 0

        # First/last snapshot info
        self.first_snapshot_info: Optional[Dict] = None
        self.last_snapshot_info: Optional[Dict] = None

        # Config properties
        self.snap_exclude_property: Optional[str] = None
        self.send_exclude_property: Optional[str] = None

    def to_dict(self) -> OrderedDict:
        """
        Serialize to OrderedDict for output.

        Returns:
            OrderedDict with all status information
        """
        status = OrderedDict()
        status['hostname'] = self.hostname
        status['name'] = self.name
        status['conf'] = self.conf_name
        status['excluded'] = self.excluded
        status['do-snap'] = self.operations.snap
        status['do-clean'] = self.operations.clean
        status['do-send'] = self.operations.send
        status['conf-snap_exclude_property'] = self.snap_exclude_property
        status['conf-send_exclude_property'] = self.send_exclude_property

        # Snapshot status
        status['snapshot-have'] = self.has_snapshots
        status['snapshot-missing'] = self.missing_snapshots
        status['snapshot-extra'] = self.extra_snapshots
        status['snapshot-count-all'] = self.all_snapshot_count
        status['snapshot-count-pyznap'] = self.pyznap_snapshot_count
        status['snapshot-count-nopyznap'] = self.non_pyznap_snapshot_count

        # Snapshot types
        for stype in SNAPSHOT_TYPES:
            count = len(self.snapshots.get(stype, []))
            expected = 0  # This should come from config
            status[f'snapshot-types-{stype}'] = f'{count}/{expected}'

        # Destinations
        status['dest'] = [d.name for d in self.destinations]
        for i, dest in enumerate(self.destinations):
            prefix = f'dest-{i}-'
            status.update(dest.to_dict(prefix))

        # First/last snapshot info
        if self.first_snapshot_info:
            for key, value in self.first_snapshot_info.items():
                status[f'snapshot-info-first-{key}'] = value
        if self.last_snapshot_info:
            for key, value in self.last_snapshot_info.items():
                status[f'snapshot-info-last-{key}'] = value

        # Filesystem properties
        for key, value in self.filesystem_properties.items():
            status[f'zfs-{key}'] = value

        return status

    def should_warn(self) -> bool:
        """
        Determine if this filesystem should trigger a warning.

        Returns:
            True if missing snapshots or other warning conditions
        """
        return self.missing_snapshots

    def has_missing_snapshots(self) -> bool:
        """Check if filesystem has missing snapshots according to policy."""
        return self.missing_snapshots


class SnapshotCategorizer:
    """
    Categorizes snapshots by type (frequent, hourly, daily, etc.).

    Provides methods to sort and categorize ZFS snapshots according
    to pyznap naming conventions.
    """

    @staticmethod
    def categorize(fs_snapshots, prefixes=('pyznap',)) -> Dict[str, List]:
        """
        Categorize snapshots by type.

        Args:
            fs_snapshots: List of ZFS snapshot objects
            prefixes: Tuple of snapshot name prefixes to include (default: pyznap only)

        Returns:
            Dict mapping snapshot types to lists of snapshots

        Example:
            >>> snapshots = filesystem.snapshots()
            >>> categorized = SnapshotCategorizer.categorize(snapshots)
            >>> daily_snaps = categorized['daily']
        """
        snapshots = {t: [] for t in SNAPSHOT_TYPES}

        for snap in fs_snapshots:
            # Ignore snapshots not matching accepted prefixes
            snap_name = snap.name.split('@')[1] if '@' in snap.name else ''
            if not snap_name.startswith(prefixes):
                continue

            try:
                # Extract snapshot type from name
                # Format: pyznap_YYYY-MM-DD_HHMMSS_type
                snap_type = snap_name.split('_')[-1]
                if snap_type in snapshots:
                    snapshots[snap_type].append(snap)
            except (ValueError, KeyError, IndexError):
                logger.debug(f'Could not categorize snapshot: {snap.name}')
                continue

        # Reverse sort by time (newest first)
        for snaps in snapshots.values():
            snaps.reverse()

        return snapshots

    @staticmethod
    def count_by_type(snapshots: Dict[str, List]) -> Dict[str, int]:
        """
        Count snapshots by type.

        Args:
            snapshots: Categorized snapshots (from categorize())

        Returns:
            Dict mapping types to counts
        """
        return {stype: len(snaps) for stype, snaps in snapshots.items()}


def determine_operations(filesystem, conf: Dict, main_fs: bool = False) -> FilesystemOperations:
    """
    Determine which operations should run on a filesystem.

    Considers configuration and ZFS properties to determine if
    snap, clean, and send operations should be performed.

    Args:
        filesystem: ZFS filesystem object
        conf: Configuration dictionary
        main_fs: Whether this is the main configured filesystem

    Returns:
        FilesystemOperations indicating which operations to perform
    """
    snap = conf.get('snap', False)
    clean = conf.get('clean', False)
    send = bool(conf.get('dest', False))

    # Check snap exclude property
    snap_exclude_property = conf.get('snap_exclude_property')
    if not main_fs and snap_exclude_property:
        try:
            if filesystem.ispropval(snap_exclude_property, check='false'):
                logger.debug(f'Excluding {filesystem.name} from snap/clean by property {snap_exclude_property}=false')
                snap = False
                clean = False
        except Exception as err:
            logger.debug(f'Could not check snap exclude property: {err}')

    # Check send exclude property
    send_exclude_property = conf.get('send_exclude_property')
    if not main_fs and send_exclude_property:
        try:
            if filesystem.ispropval(send_exclude_property, check='false'):
                logger.debug(f'Excluding {filesystem.name} from send by property {send_exclude_property}=false')
                send = False
        except Exception as err:
            logger.debug(f'Could not check send exclude property: {err}')

    # Determine if excluded
    excluded = not (snap or clean or send)

    return FilesystemOperations(snap=snap, clean=clean, send=send, excluded=excluded)


def should_skip_filesystem(fs_name: str, filter_exclude: Optional[List[str]]) -> bool:
    """
    Check if filesystem should be skipped based on exclude filters.

    Args:
        fs_name: Filesystem name
        filter_exclude: List of fnmatch patterns to exclude

    Returns:
        True if filesystem should be skipped
    """
    if not filter_exclude:
        return False

    from fnmatch import fnmatch

    for pattern in filter_exclude:
        if fnmatch(fs_name, pattern):
            logger.debug(f'Excluding filesystem {fs_name} by --exclude {pattern}')
            return True

    return False


def extract_snapshot_info(snapshot) -> Dict[str, any]:
    """
    Extract metadata from a snapshot.

    Args:
        snapshot: ZFS snapshot object

    Returns:
        Dict with timestamp, referenced, and logicalreferenced
    """
    try:
        props = snapshot.getprops()
        creation = int(props['creation'][0])
        timestamp = datetime.fromtimestamp(creation).isoformat()

        return {
            'timestamp': timestamp,
            'referenced': int(props['referenced'][0]),
            'logicalreferenced': int(props.get('logicalreferenced', [0])[0]),
        }
    except (KeyError, ValueError, IndexError) as err:
        logger.debug(f'Could not extract snapshot info: {err}')
        return {'timestamp': None, 'referenced': 0, 'logicalreferenced': 0}


def check_snapshot_counts(categorized: Dict[str, List], policy: Dict[str, int]) -> tuple:
    """
    Check if snapshot counts match policy.

    Args:
        categorized: Categorized snapshots
        policy: Policy dict with expected counts per type

    Returns:
        Tuple of (has_missing, has_extra)
    """
    has_missing = False
    has_extra = False

    for snap_type in SNAPSHOT_TYPES:
        actual = len(categorized.get(snap_type, []))
        expected = policy.get(snap_type, 0)

        if actual < expected:
            has_missing = True
        if actual > expected:
            has_extra = True

    return has_missing, has_extra


def bytes_fmt(num: int) -> str:
    """
    Format bytes to human-readable format.

    Args:
        num: Number of bytes

    Returns:
        Formatted string (e.g., "1.5G", "256M")
    """
    for unit in ['', 'K', 'M', 'G', 'T', 'P']:
        if abs(num) < 1024.0:
            return f'{num:.1f}{unit}'
        num /= 1024.0
    return f'{num:.1f}E'
