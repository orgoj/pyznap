"""
pyznap.clean
~~~~~~~~~~~~~~

Clean snapshots.

:copyright: (c) 2018-2019 by Yannick Boetzel.
:license: GPLv3, see LICENSE for more details.
"""

import logging
from subprocess import CalledProcessError

import pyznap.pyzfs as zfs

from .process import DatasetBusyError, DatasetNotFoundError
from .ssh import SSH, SSHException
from .utils import SNAPSHOT_TYPES, parse_name


def clean_snap(snap, output_handler=None):
    """Deletes a snapshot

    Parameters
    ----------
    snap : {ZFSSnapshot}
        Snapshot to destroy
    output_handler : {OutputHandler}, optional
        Output handler for JSON output

    Returns
    -------
    dict
        Operation result
    """

    logger = logging.getLogger(__name__)

    logger.info(f'Deleting snapshot {snap}...')

    operation = {'action': 'delete', 'snapshot': str(snap), 'status': 'success', 'error': None}

    try:
        snap.destroy()
    except DatasetBusyError as err:
        logger.error(err)
        operation['status'] = 'error'
        operation['error'] = str(err)
    except CalledProcessError as err:
        logger.error(f"Error while deleting snapshot {snap}: '{err.stderr.rstrip()}'...")
        operation['status'] = 'error'
        operation['error'] = err.stderr.rstrip()
    except KeyboardInterrupt:
        logger.error(f'KeyboardInterrupt while cleaning snapshot {snap}...')
        operation['status'] = 'error'
        operation['error'] = 'KeyboardInterrupt'
        raise

    if output_handler:
        output_handler.add_operation(operation)

    return operation


def clean_filesystem(filesystem, conf, output_handler=None):
    """Deletes snapshots of a single filesystem according to conf.

    Parameters:
    ----------
    filesystem : {ZFSFilesystem}
        Filesystem to clean
    conf : {dict}
        Config entry with snapshot strategy
    output_handler : {OutputHandler}, optional
        Output handler for JSON output
    """

    logger = logging.getLogger(__name__)
    logger.debug(f'Cleaning snapshots on {filesystem}...')

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
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)
        except (ValueError, KeyError):
            continue

    # Reverse sort by time taken
    for snaps in snapshots.values():
        snaps.reverse()

    for stype in reversed(SNAPSHOT_TYPES):
        for snap in snapshots[stype][conf[stype] :]:
            clean_snap(snap, output_handler)


def clean_config(config, settings=None, output_handler=None):
    """Deletes old snapshots according to strategies given in config. Goes through each config,
    opens up ssh connection if necessary and then recursively calls clean_filesystem.

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
    logger.info('Cleaning snapshots...')

    for conf in config:
        if not conf.get('clean', None):
            logger.debug('Ignore config from clean {}...'.format(conf['name']))
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
        else:
            # Clean snapshots of parent filesystem - ignore exclude property for top fs
            clean_filesystem(children[0], conf, output_handler)
            # Clean snapshots of all children that don't have a seperate config entry
            for child in children[1:]:
                if snap_exclude_property and child.ispropval(snap_exclude_property, check='false'):
                    logger.debug(f'Ignore dataset {child.name:s}, have property {snap_exclude_property:s}=false')
                else:
                    clean_filesystem(child, conf, output_handler)
        finally:
            if ssh:
                ssh.close()
