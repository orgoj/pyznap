"""
Mock ZFS objects for unit testing.

Provides mock implementations of ZFS filesystems and snapshots
without requiring actual ZFS pools.
"""

from unittest.mock import Mock, MagicMock
from datetime import datetime, timedelta
from collections import OrderedDict


class MockZFSSnapshot:
    """
    Mock implementation of a ZFS snapshot.

    Simulates a ZFS snapshot with properties and metadata.
    """

    def __init__(self, filesystem_name, snapshot_name, creation_time=None,
                 used=1000000, referenced=5000000):
        """
        Initialize a mock ZFS snapshot.

        Args:
            filesystem_name: Name of the parent filesystem
            snapshot_name: Name of the snapshot (without @)
            creation_time: Creation datetime (default: now)
            used: Used space in bytes
            referenced: Referenced space in bytes
        """
        self.name = f"{filesystem_name}@{snapshot_name}"
        self.filesystem_name = filesystem_name
        self.snapshot_name = snapshot_name

        if creation_time is None:
            creation_time = datetime.now()
        self._creation_time = creation_time
        self._timestamp = int(creation_time.timestamp())

        self._used = used
        self._referenced = referenced

    def getprops(self):
        """
        Get snapshot properties.

        Returns:
            Dict with ZFS properties
        """
        props = OrderedDict()
        props['creation'] = (str(self._timestamp), 'default')
        props['used'] = (str(self._used), 'default')
        props['referenced'] = (str(self._referenced), 'default')
        props['logicalreferenced'] = (str(self._referenced), 'default')
        props['compressratio'] = ('1.50x', 'default')
        return props

    def destroy(self, force=False):
        """Mock destroy operation."""
        pass

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"MockZFSSnapshot('{self.name}')"


class MockZFSFilesystem:
    """
    Mock implementation of a ZFS filesystem.

    Simulates a ZFS filesystem with snapshots, properties, and child filesystems.
    """

    def __init__(self, name, snapshots=None, properties=None):
        """
        Initialize a mock ZFS filesystem.

        Args:
            name: Filesystem name (e.g., 'tank/data')
            snapshots: List of MockZFSSnapshot objects
            properties: Dict of ZFS properties
        """
        self.name = name
        self._snapshots = snapshots if snapshots is not None else []
        self._properties = properties if properties is not None else {}
        self._exclude_properties = {}

    def snapshots(self):
        """
        Get list of snapshots.

        Returns:
            List of MockZFSSnapshot objects
        """
        return self._snapshots

    def add_snapshot(self, snapshot):
        """Add a snapshot to this filesystem."""
        self._snapshots.append(snapshot)

    def getprops(self):
        """
        Get filesystem properties.

        Returns:
            OrderedDict with ZFS properties
        """
        default_props = OrderedDict()
        default_props['used'] = ('10000000', 'default')
        default_props['available'] = ('50000000', 'default')
        default_props['referenced'] = ('9000000', 'default')
        default_props['compressratio'] = ('1.50x', 'default')
        default_props['logicalused'] = ('15000000', 'default')
        default_props['logicalreferenced'] = ('13500000', 'default')

        # Merge with custom properties
        default_props.update(self._properties)
        return default_props

    def ispropval(self, property_name, check='false'):
        """
        Check if property has specific value.

        Args:
            property_name: Name of the property
            check: Value to check against (default: 'false')

        Returns:
            bool: True if property has the checked value
        """
        if property_name in self._exclude_properties:
            return self._exclude_properties[property_name] == check
        return False

    def set_property(self, name, value):
        """Set a ZFS property value."""
        self._exclude_properties[name] = value

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"MockZFSFilesystem('{self.name}', snapshots={len(self._snapshots)})"


def create_mock_snapshot(name, hours_ago=0, snap_type='daily', used=1000000, referenced=5000000):
    """
    Create a mock pyznap snapshot.

    Args:
        name: Base filesystem name (e.g., 'tank/data')
        hours_ago: How many hours ago was it created
        snap_type: Type of snapshot (frequent, hourly, daily, weekly, monthly, yearly)
        used: Used space in bytes
        referenced: Referenced space in bytes

    Returns:
        MockZFSSnapshot with pyznap naming convention
    """
    creation_time = datetime.now() - timedelta(hours=hours_ago)
    timestamp_str = creation_time.strftime('%Y-%m-%d_%H%M%S')
    snapshot_name = f"pyznap_{timestamp_str}_{snap_type}"

    return MockZFSSnapshot(
        filesystem_name=name,
        snapshot_name=snapshot_name,
        creation_time=creation_time,
        used=used,
        referenced=referenced
    )


def create_mock_filesystem_with_snapshots(name, snapshot_config):
    """
    Create a mock filesystem with specified snapshots.

    Args:
        name: Filesystem name
        snapshot_config: List of tuples (hours_ago, snap_type)

    Returns:
        MockZFSFilesystem with snapshots

    Example:
        >>> fs = create_mock_filesystem_with_snapshots('tank/data', [
        ...     (1, 'hourly'),
        ...     (25, 'daily'),
        ...     (169, 'weekly')
        ... ])
    """
    snapshots = [
        create_mock_snapshot(name, hours_ago=hours, snap_type=stype)
        for hours, stype in snapshot_config
    ]

    return MockZFSFilesystem(name=name, snapshots=snapshots)
