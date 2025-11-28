"""
pyznap.take
~~~~~~~~~~~~~~

Take snapshots.

:copyright: (c) 2018-2019 by Yannick Boetzel.
:license: GPLv3, see LICENSE for more details.
"""

import logging
from datetime import datetime, timedelta
from subprocess import CalledProcessError

import pyznap.pyzfs as zfs

from .process import DatasetBusyError, DatasetExistsError, DatasetNotFoundError
from .ssh import SSH, SSHException
from .utils import SNAPSHOT_TYPES, parse_name


def take_snap(filesystem, _type, output_handler=None):
    """Takes a snapshot of type '_type'

    Parameters
    ----------
    filesystem : {ZFSFilesystem}
        Filesystem to take snapshot of
    _type : {str}
        Type of snapshot to take
    output_handler : {OutputHandler}, optional
        Output handler for JSON output

    Returns
    -------
    dict or None
        Operation result if output_handler is provided
    """

    logger = logging.getLogger(__name__)
    now = datetime.now

    def snapname(_type):
        return 'pyznap_{:s}_{:s}'.format(now().strftime('%Y-%m-%d_%H:%M:%S'), _type)

    snap_full_name = f'{filesystem}@{snapname(_type):s}'

    logger.info(f'Taking snapshot {snap_full_name}...')

    operation = {
        'action': 'create',
        'filesystem': str(filesystem),
        'snapshot': snapname(_type),
        'type': _type,
        'status': 'success',
        'error': None,
    }

    try:
        filesystem.snapshot(snapname=snapname(_type))
    except (DatasetBusyError, DatasetExistsError) as err:
        logger.error(err)
        operation['status'] = 'error'
        operation['error'] = str(err)
    except CalledProcessError as err:
        error_msg = f"Error while taking snapshot {snap_full_name}: '{err.stderr.rstrip()}'..."
        logger.error(error_msg)
        operation['status'] = 'error'
        operation['error'] = err.stderr.rstrip()
    except KeyboardInterrupt:
        logger.error(f'KeyboardInterrupt while taking snapshot {snap_full_name}...')
        operation['status'] = 'error'
        operation['error'] = 'KeyboardInterrupt'
        raise

    if output_handler:
        output_handler.add_operation(operation)

    return operation


def take_filesystem(filesystem, conf, output_handler=None):
    """Takes snapshots of a single filesystem according to conf.

    Parameters:
    ----------
    filesystem : {ZFSFilesystem}
        Filesystem to take snapshot of
    conf : {dict}
        Config entry with snapshot strategy
    output_handler : {OutputHandler}, optional
        Output handler for JSON output
    """

    logger = logging.getLogger(__name__)
    logger.debug(f'Taking snapshots on {filesystem}...')
    now = datetime.now

    snapshots = {t: [] for t in SNAPSHOT_TYPES}
    # catch exception if dataset was destroyed since pyznap was started
    try:
        fs_snapshots = filesystem.snapshots()
    except (DatasetNotFoundError, DatasetBusyError) as err:
        logger.error(f'Error while opening {filesystem}: {err}...')
        return 1
    # categorize snapshots
    for snap in fs_snapshots:
        # Ignore snapshots not taken with pyznap or sanoid
        if not snap.name.split('@')[1].startswith(('pyznap', 'autosnap')):
            continue
        try:
            _date, _time, snap_type = snap.name.split('_')[-3:]
            snap_time = datetime.strptime(f'{_date:s}_{_time:s}', '%Y-%m-%d_%H:%M:%S')
            snapshots[snap_type].append((snap, snap_time))
        except (ValueError, KeyError):
            continue

    # Reverse sort by time taken
    for snaps in snapshots.values():
        snaps.reverse()

    if conf['yearly'] and (not snapshots['yearly'] or snapshots['yearly'][0][1].year != now().year):
        take_snap(filesystem, 'yearly', output_handler)

    if conf['monthly'] and (
        not snapshots['monthly']
        or snapshots['monthly'][0][1].month != now().month
        or now() - snapshots['monthly'][0][1] > timedelta(days=31)
    ):
        take_snap(filesystem, 'monthly', output_handler)

    if conf['weekly'] and (
        not snapshots['weekly']
        or snapshots['weekly'][0][1].isocalendar()[1] != now().isocalendar()[1]
        or now() - snapshots['weekly'][0][1] > timedelta(days=7)
    ):
        take_snap(filesystem, 'weekly', output_handler)

    if conf['daily'] and (
        not snapshots['daily']
        or snapshots['daily'][0][1].day != now().day
        or now() - snapshots['daily'][0][1] > timedelta(days=1)
    ):
        take_snap(filesystem, 'daily', output_handler)

    if conf['hourly'] and (
        not snapshots['hourly']
        or snapshots['hourly'][0][1].hour != now().hour
        or now() - snapshots['hourly'][0][1] > timedelta(hours=1)
    ):
        take_snap(filesystem, 'hourly', output_handler)

    if conf['frequent'] and (
        not snapshots['frequent']
        or snapshots['frequent'][0][1].minute != now().minute
        or now() - snapshots['frequent'][0][1] > timedelta(minutes=1)
    ):
        take_snap(filesystem, 'frequent', output_handler)


def take_config(config, settings=None, output_handler=None):
    """Takes snapshots according to strategy given in config.

    Parameters:
    ----------
    config : {list of dict}
        Full config list containing all strategies for different filesystems
    settings : {dict}, optional
        Additional settings
    output_handler : {OutputHandler}, optional
        Output handler for JSON output
    """

    if settings is None:
        settings = {}
    logger = logging.getLogger(__name__)
    logger.info('Taking snapshots...')

    for conf in config:
        if not conf.get('snap', None):
            logger.debug('Ignore config from snap {}...'.format(conf['name']))
            continue
        logger.debug('Process config {}...'.format(conf['name']))

        name = conf['name']
        try:
            _type, fsname, user, host, port = parse_name(name)
        except ValueError as err:
            logger.error(f'Could not parse {name:s}: {err}...')
            continue

        if _type == 'ssh':
            try:
                ssh = SSH(user, host, port=port, key=conf['key'])
            except (FileNotFoundError, SSHException):
                continue
            name_log = f'{user:s}@{host:s}:{fsname:s}'
        else:
            ssh = None
            name_log = fsname

        snap_exclude_property = conf.get('snap_exclude_property')

        try:
            # Children includes the base filesystem (named 'fsname')
            children = zfs.find_exclude(conf, config, matching=settings['matching'])
        except DatasetNotFoundError:
            if conf.get('ignore_not_existing'):
                logger.warning(f'Dataset {name_log:s} does not exist...')
            else:
                logger.error(f'Dataset {name_log:s} does not exist...')
            continue
        except ValueError as err:
            logger.error(err)
            continue
        except CalledProcessError as err:
            logger.error(f"Error while opening {name_log:s}: '{err.stderr.rstrip():s}'...")
            continue
        else:
            # Take recursive snapshot of parent filesystem - ignore exclude property for top fs
            take_filesystem(children[0], conf, output_handler)
            # Take snapshot of all children that don't have all snapshots yet
            for child in children[1:]:
                if snap_exclude_property and child.ispropval(snap_exclude_property, check='false'):
                    logger.debug(f'Ignore dataset {child.name:s}, have property {snap_exclude_property:s}=false')
                else:
                    take_filesystem(child, conf, output_handler)
        finally:
            if ssh:
                ssh.close()
