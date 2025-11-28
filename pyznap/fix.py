"""
pyznap.status
~~~~~~~~~~~~~~

Fix filesystem snapshots.

:copyright: (c) 2021 by Michael Heca.
:license: GPLv3, see LICENSE for more details.
"""

import logging
import re
import sys
from datetime import datetime

import pyznap.pyzfs as zfs

from .process import DatasetNotFoundError

FORMATS = {
    '@zfs-auto-snap': r'zfs-auto-snap_(?P<type>[a-z]+)-(?P<year>\d{2,4})-(?P<month>\d{2})-(?P<day>\d{2})-(?P<hour>\d{2})(?P<minute>\d{2})',
    '@zfsnap': r'(?P<year>\d{2,4})-(?P<month>\d{2})-(?P<day>\d{2})_(?P<hour>\d{2}).(?P<minute>\d{2}).(?P<second>\d{2})--(?P<type>\d+[a-z])',
}

MAPS = {
    '@zfsnap': {
        '3d': 'frequent',
        '4d': 'frequent',
        '5d': 'hourly',
        '7d': 'hourly',
        '10d': 'hourly',
        '14d': 'hourly',
        '2w': 'daily',
        '3w': 'daily',
        '5w': 'daily',
        '8w': 'daily',
        '2m': 'daily',
        '3m': 'weekly',
        '90d': 'weekly',
        '6m': 'weekly',
        '7m': 'weekly',
        '8m': 'weekly',
        '9m': 'weekly',
        '1y': 'monthly',
        '12m': 'monthly',
        '18m': 'monthly',
        '24m': 'monthly',
        '2y': 'yearly',
        '3y': 'yearly',
        '4y': 'yearly',
    }
}


def re_get_group(r, group, default=0):
    try:
        result = r.group(group)
    except IndexError:
        result = default
    return result


def re_get_group_int(r, group, default=0):
    return int(re_get_group(r, group, default=default))


def fix_snapshots(filesystems, format=None, type=None, type_map=None, recurse=False):
    """Fix snapshots name

    Parameters:
    ----------
    filesystems : [strings]
        Filesystems to fix
    """

    logger = logging.getLogger(__name__)

    if format.startswith('@'):
        if not type_map and format in MAPS:
            type_map = MAPS[format]
        if format in FORMATS:
            format = FORMATS[format]
        else:
            logger.error(f'Unknown format {format}.')
            sys.exit(1)

    logger.debug('FORMAT: ' + str(format))
    logger.debug('MAP: ' + str(type_map))

    rp = re.compile(format)
    now = datetime.now()
    cur_century = int(now.year / 100) * 100

    # for all specified filesystems
    for fsname in filesystems:
        logger.info(f'Checking snapshots on {fsname}...')
        try:
            parent = zfs.open(fsname)
        except DatasetNotFoundError:
            logger.error(f'Filesystem not exists {fsname}')
            continue

        if recurse:
            # get all child's filesystem
            fstree = zfs.find(fsname, types=['filesystem', 'volume'])
        else:
            # only scan specified filesystem
            fstree = [parent]

        for filesystem in fstree:
            logger.info(f'Fixing {filesystem.name}...')
            snapshots = filesystem.snapshots()
            for snapshot in snapshots:
                snapname = snapshot.snapname()
                try:
                    r = rp.match(snapname)
                except Exception:
                    r = False
                if r:
                    # guess year
                    year = re_get_group_int(r, 'year', default=now.year)
                    if year < 100:
                        year += +cur_century
                    # get type from snap, with optional map or default type if specified
                    snaptype = r.group('type')
                    if type_map:
                        if snaptype in type_map:
                            snaptype = type_map[snaptype]
                    if not snaptype and type:
                        snaptype = type
                    if not snaptype:
                        logger.error(f'Unknown snap type {snaptype} for snapshot {snapname}')
                        continue
                    new_snapname = (
                        'pyznap_'
                        + datetime(
                            year,
                            re_get_group_int(r, 'month', default=now.month),
                            re_get_group_int(r, 'day', default=now.day),
                            hour=re_get_group_int(r, 'hour', default=now.hour),
                            minute=re_get_group_int(r, 'minute', default=now.minute),
                            second=re_get_group_int(r, 'second', default=now.second),
                        ).strftime('%Y-%m-%d_%H:%M:%S')
                        + '_'
                        + snaptype
                    )
                    logger.debug(f'Renaming {snapname} -> {new_snapname}')
                    snapshot.rename(snapshot.fsname() + '@' + new_snapname)
