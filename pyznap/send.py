"""
    pyznap.send
    ~~~~~~~~~~~~~~

    Send snapshots.

    :copyright: (c) 2018-2019 by Yannick Boetzel.
    :license: GPLv3, see LICENSE for more details.
"""

import sys
import logging
from io import TextIOWrapper
from datetime import datetime
from subprocess import Popen, PIPE, CalledProcessError
from fnmatch import fnmatch
from time import sleep
from .ssh import SSH, SSHException
from .utils import parse_name, exists, check_recv, bytes_fmt
import pyznap.pyzfs as zfs
from .process import get_dry_run, DatasetBusyError, DatasetNotFoundError, DatasetExistsError


def send_snap(snapshot, dest_name, base=None, ssh_dest=None, raw=False, resume=False, resume_token=None, intermediates=True):
    """Sends snapshot to destination, incrementally and over ssh if specified.

    Parameters:
    ----------
    snapshot : {ZFSSnapshot}
        Snapshot to send
    dest_name : {str}
        Name of the location to send snapshot
    base : {ZFSSnapshot}, optional
        Base snapshot for incremental stream (the default is None, meaning a full stream)
    ssh_dest : {ssh.SSH}, optional
        Open ssh connection for remote backup (the default is None, meaning local backup)
    intermediates : {bool}, optional
        Include intermediate snapshots with -I (default True), or skip them with -i (False)

    Returns
    -------
    int
        0 if success, 1 if not, 2 if CalledProcessError
    """

    logger = logging.getLogger(__name__)
    dest_name_log = '{:s}@{:s}:{:s}'.format(ssh_dest.user, ssh_dest.host, dest_name) if ssh_dest else dest_name

    try:
        ssh_source = snapshot.ssh
        stream_size = snapshot.stream_size(base=base, raw=raw, resume_token=resume_token)

        zfs.STATS.add('zfs_send_snap_count')
        if get_dry_run():
            zfs.STATS.add('send_size', stream_size)
            logger.warning('DRY_RUN: send_snapshot {} --> {} base:{} size:{} resume_token:{} compress:{}/{}'.format(
                snapshot.name, dest_name_log, base, stream_size, resume_token,
                ssh_source.compress if ssh_source else None,
                ssh_dest.decompress if ssh_dest else None
            ))
            return 0

        send = snapshot.send(ssh_dest=ssh_dest, base=base, intermediates=intermediates, raw=raw, resume_token=resume_token)
        recv = zfs.receive(name=dest_name, stdin=send.stdout, ssh=ssh_dest, ssh_source=ssh_source,
                           force=True, nomount=True, stream_size=stream_size, raw=raw, resume=resume)
        send.stdout.close()

        # write pv output to stderr / stdout and capture warnings
        zfs_warnings = []
        for line in TextIOWrapper(send.stderr, newline='\r'):
            line_stripped = line.rstrip()
            # Capture ZFS warnings for logging
            if line_stripped.startswith('warning:'):
                zfs_warnings.append(line_stripped)
            if sys.stdout.isatty():
                sys.stderr.write('  ' + line)
                sys.stderr.flush()
            elif line_stripped:     # is stdout is redirected, write pv to stdout
                sys.stdout.write('  ' + line_stripped + '\n')
                sys.stdout.flush()
        send.stderr.close()

        # Log any ZFS warnings that were captured
        if zfs_warnings:
            logger.warning('ZFS send warnings for {:s}:'.format(dest_name_log))
            for warn in zfs_warnings:
                logger.warning('  {}'.format(warn))

        stdout, stderr = recv.communicate()
        # raise any error that occurred
        if recv.returncode:
            raise CalledProcessError(returncode=recv.returncode, cmd=recv.args, output=stdout, stderr=stderr)

    except (DatasetNotFoundError, DatasetExistsError, DatasetBusyError, OSError, EOFError) as err:
        logger.error('Error while sending to {:s}: {}...'.format(dest_name_log, err))
        return 1
    except CalledProcessError as err:
        logger.error('Error while sending to {:s}: {}...'.format(dest_name_log, err.stderr.rstrip().decode().replace('\n', ' - ')))
        # returncode 2 means we will retry send if requested
        return 2
    except KeyboardInterrupt:
        logger.error('KeyboardInterrupt while sending to {:s}...'.format(dest_name_log))
        raise
    else:
        return 0


def send_filesystem(source_fs, dest_name, ssh_dest=None, raw=False, resume=False, send_last_snapshot=False, dest_auto_create=False):
    """Checks for common snapshots between source and dest.
    If none are found, send the oldest snapshot, then update with the most recent one.
    If there are common snaps, update destination with the most recent one.

    Parameters:
    ----------
    source_fs : {ZFSFilesystem}
        Source zfs filesystem from where to send
    dest_name : {str}
        Name of the location to send to
    ssh_dest : {ssh.SSH}, optional
        Open ssh connection for remote backup (the default is None, meaning local backup)

    Returns
    -------
    int
        0 if success, 1 if not, 2 for ssh errors
    """

    logger = logging.getLogger(__name__)
    dest_name_log = '{:s}@{:s}:{:s}'.format(ssh_dest.user, ssh_dest.host, dest_name) if ssh_dest else dest_name

    logger.debug('Sending {} to {:s}...'.format(source_fs, dest_name_log))

    resume_token = None
    # Check if dest already has a 'zfs receive' ongoing
    if check_recv(dest_name, ssh=ssh_dest):
        return 1

    # get snapshots on source, catch exception if dataset was destroyed since pyznap was started
    try:
        snapshots = source_fs.snapshots()[::-1]
    except (DatasetNotFoundError, DatasetBusyError) as err:
        logger.error('Error while opening source {}: {}...'.format(source_fs, err))
        return 1
    except CalledProcessError as err:
        message = err.stderr.rstrip()
        if message.startswith('ssh: '):
            logger.error('Connection issue while opening source {}: \'{:s}\'...'
                         .format(source_fs, message))
            return 2
        else:
            logger.error('Error while opening source {}: \'{:s}\'...'
                         .format(source_fs, message))
            return 1
    snapnames = [snap.name.split('@')[1] for snap in snapshots]

    try:
        snapshot = snapshots[0]     # Most recent snapshot
        base = snapshots[-1]        # Oldest snapshot
    except IndexError:
        logger.error('No snapshots on {}, cannot send...'.format(source_fs))
        return 1

    try:
        dest_fs = zfs.open(dest_name, ssh=ssh_dest)
    except DatasetNotFoundError:
        if dest_auto_create:
            logger.info('Destination {:s} does not exist, will create it...'.format(dest_name_log))
            if create_dataset(dest_name, dest_name_log, ssh=ssh_dest):
                return 1
        else:
            logger.error('Destination {:s} does not exist, manually create it or use "dest-auto-create" option...'
                            .format(dest_name_log))
            return 1
        dest_snapnames = []
        common = set()
    except CalledProcessError as err:
        message = err.stderr.rstrip()
        if message.startswith('ssh: '):
            logger.error('Connection issue while opening dest {:s}: \'{:s}\'...'
                         .format(dest_name_log, message))
            return 2
        else:
            logger.error('Error while opening dest {:s}: \'{:s}\'...'
                         .format(dest_name_log, message))
            return 1
    else:
        # if dest exists, check for resume token
        resume_token = dest_fs.getprops().get('receive_resume_token', (None, None))[0]
        # find common snapshots between source & dest
        dest_snapnames = [snap.name.split('@')[1] for snap in dest_fs.snapshots()]
        common = set(snapnames) & set(dest_snapnames)

    # if not resume and resume_token is not None:
    #     if not abort:
    #         logger.error('{:s} contains partially-complete state from "zfs receive -s" (~{:s}), '
    #                      'but neither resume nor abort option is given...'
    #                      .format(dest_name_log, bytes_fmt(base.stream_size(raw=raw, resume_token=resume_token))))
    #         return 1
    #     else:
    #         logger.info('{:s} contains partially-complete state from "zfs receive -s" (~{:s}), '
    #                     'will abort it...'
    #                     .format(dest_name_log, bytes_fmt(base.stream_size(raw=raw, resume_token=resume_token))))
    #         if abort_resume(dest_fs):
    #             return 1

    zfs.STATS.add('zfs_send_filesystem_count')

    was_transfer = False
    if resume_token is not None:
        logger.info('Found resume token. Resuming last transfer of {:s} (~{:s})...'
                    .format(dest_name_log, bytes_fmt(base.stream_size(raw=raw, resume_token=resume_token))))
        was_transfer = True
        rc = send_snap(base, dest_name, base=None, ssh_dest=ssh_dest, raw=raw, resume=True, resume_token=resume_token)
        if rc:
            return rc
        # we need to update common snapshots after finishing the resumable send
        dest_snapnames = [snap.name.split('@')[1] for snap in dest_fs.snapshots()]
        common = set(snapnames) & set(dest_snapnames)

    if not common:
        if dest_snapnames:
            logger.error('No common snapshots on {:s}, but snapshots exist. Not sending...'
                         .format(dest_name_log))
            return 1
        else:
            if send_last_snapshot:
                base = snapshot
                for snap in snapshots:
                    if send_last_snapshot in snap.name.split('@')[1]:
                        base = snap
                        break
                logger.info('No common snapshots on {:s}, sending last snapshot {} (~{:s})...'
                            .format(dest_name_log, base, bytes_fmt(base.stream_size(raw=raw))))
            else:
                logger.info('No common snapshots on {:s}, sending oldest snapshot {} (~{:s})...'
                            .format(dest_name_log, base, bytes_fmt(base.stream_size(raw=raw))))
            was_transfer = True
            rc = send_snap(base, dest_name, base=None, ssh_dest=ssh_dest, raw=raw, resume=resume)
            if rc:
                return rc
    else:
        # If there are common snapshots, get the most recent one
        base = next(filter(lambda x: x.name.split('@')[1] in common, snapshots), None)

    if base.name != snapshot.name:
        logger.info('Updating {:s} with recent snapshot {} from {} (~{:s})...'
                    .format(dest_name_log, snapshot, base.name.split('@')[1], bytes_fmt(snapshot.stream_size(base, raw=raw))))
        was_transfer = True
        rc = send_snap(snapshot, dest_name, base=base, ssh_dest=ssh_dest, raw=raw, resume=resume)
        if rc:
            return rc

    if was_transfer:
        zfs.STATS.add('zfs_send_changed_count')
    else:
        zfs.STATS.add('zfs_send_unchanged_count')
    logger.info('{:s} is up to date...'.format(dest_name_log))
    return 0


def send_filesystem_stepwise(source_fs, dest_name, ssh_dest=None, raw=False, resume=False, send_last_snapshot=False, dest_auto_create=False):
    """Sends snapshots one by one (for broken snapshot chains).

    Instead of using 'zfs send -I' which includes all intermediate snapshots,
    sends each snapshot individually with 'zfs send -i'. If a snapshot fails,
    it is skipped and the next one is attempted.

    Parameters:
    ----------
    source_fs : {ZFSFilesystem}
        Source zfs filesystem from where to send
    dest_name : {str}
        Name of the location to send to
    ssh_dest : {ssh.SSH}, optional
        Open ssh connection for remote backup
    raw : {bool}, optional
        Use raw send
    resume : {bool}, optional
        Use resumable send
    send_last_snapshot : {str}, optional
        Start from snapshot containing this string
    dest_auto_create : {bool}, optional
        Create destination if it doesn't exist

    Returns
    -------
    int
        0 if success (at least partially), 1 if complete failure
    """

    logger = logging.getLogger(__name__)
    dest_name_log = '{:s}@{:s}:{:s}'.format(ssh_dest.user, ssh_dest.host, dest_name) if ssh_dest else dest_name

    logger.info('Stepwise sending {} to {:s}...'.format(source_fs, dest_name_log))

    # Check if dest already has a 'zfs receive' ongoing
    if check_recv(dest_name, ssh=ssh_dest):
        return 1

    # Get snapshots on source
    try:
        snapshots = source_fs.snapshots()[::-1]  # newest first
    except (DatasetNotFoundError, DatasetBusyError) as err:
        logger.error('Error while opening source {}: {}...'.format(source_fs, err))
        return 1
    except CalledProcessError as err:
        message = err.stderr.rstrip()
        logger.error('Error while opening source {}: \'{:s}\'...'.format(source_fs, message))
        return 1

    if not snapshots:
        logger.error('No snapshots on {}, cannot send...'.format(source_fs))
        return 1

    snapnames = [snap.name.split('@')[1] for snap in snapshots]
    target_snapshot = snapshots[0]  # Most recent

    # Open or create destination
    try:
        dest_fs = zfs.open(dest_name, ssh=ssh_dest)
        dest_snapnames = [snap.name.split('@')[1] for snap in dest_fs.snapshots()]
    except DatasetNotFoundError:
        if dest_auto_create:
            logger.info('Destination {:s} does not exist, will create it...'.format(dest_name_log))
            if create_dataset(dest_name, dest_name_log, ssh=ssh_dest):
                return 1
            dest_snapnames = []
        else:
            logger.error('Destination {:s} does not exist, manually create it or use "dest-auto-create" option...'.format(dest_name_log))
            return 1
    except CalledProcessError as err:
        logger.error('Error while opening dest {:s}: \'{:s}\'...'.format(dest_name_log, err.stderr.rstrip()))
        return 1

    # Find common snapshots
    common = set(snapnames) & set(dest_snapnames)

    if not common:
        if dest_snapnames:
            logger.error('No common snapshots on {:s}, but snapshots exist. Not sending...'.format(dest_name_log))
            return 1
        else:
            # Send first snapshot (oldest or send_last_snapshot)
            if send_last_snapshot:
                base = target_snapshot
                for snap in snapshots:
                    if send_last_snapshot in snap.name.split('@')[1]:
                        base = snap
                        break
            else:
                base = snapshots[-1]  # oldest
            logger.info('No common snapshots on {:s}, sending initial snapshot {}...'.format(dest_name_log, base))
            rc = send_snap(base, dest_name, base=None, ssh_dest=ssh_dest, raw=raw, resume=resume)
            if rc:
                logger.error('Failed to send initial snapshot, cannot continue stepwise send...')
                return 1
            # Update common after sending first snapshot
            dest_snapnames = [snap.name.split('@')[1] for snap in zfs.open(dest_name, ssh=ssh_dest).snapshots()]
            common = set(snapnames) & set(dest_snapnames)

    # Find base snapshot (most recent common)
    base_snap = next(filter(lambda x: x.name.split('@')[1] in common, snapshots), None)

    if base_snap.name == target_snapshot.name:
        logger.info('{:s} is up to date...'.format(dest_name_log))
        return 0

    # Get list of snapshots to send (from base to target, in order)
    base_idx = snapshots.index(base_snap)
    snapshots_to_send = snapshots[:base_idx][::-1]  # reverse to get oldest first

    logger.info('Sending {:d} snapshots one by one from {} to {}...'.format(
        len(snapshots_to_send), base_snap.name.split('@')[1], target_snapshot.name.split('@')[1]))

    zfs.STATS.add('zfs_send_filesystem_count')

    success_count = 0
    fail_count = 0
    current_base = base_snap

    for snap in snapshots_to_send:
        snap_name = snap.name.split('@')[1]
        logger.info('  Sending snapshot {} (base: {})...'.format(snap_name, current_base.name.split('@')[1]))

        rc = send_snap(snap, dest_name, base=current_base, ssh_dest=ssh_dest, raw=raw, resume=resume, intermediates=False)

        if rc == 0:
            success_count += 1
            current_base = snap  # Use this as base for next snapshot
        else:
            fail_count += 1
            logger.warning('  Skipping snapshot {} (send failed)...'.format(snap_name))
            # Don't update current_base - next snapshot will try from same base

    logger.info('Stepwise send completed: {:d} sent, {:d} skipped...'.format(success_count, fail_count))

    if success_count > 0:
        zfs.STATS.add('zfs_send_changed_count')
        return 0
    else:
        return 1


def send_config(config, settings={}):
    """Tries to sync all entries in the config to their dest. Finds all children of the filesystem
    and calls send_filesystem on each of them.

    Parameters:
    ----------
    config : {list of dict}
        Full config list containing all strategies for different filesystems
    """

    logger = logging.getLogger(__name__)
    logger.info('Sending snapshots...')

    for conf in config:
        if not conf.get('dest', None):
            logger.debug('Ignore config from send {}...'.format(conf['name']))
            continue
        logger.debug('Process config {}...'.format(conf['name']))

        backup_source = conf['name']
        try:
            _type, source_name, user, host, port = parse_name(backup_source)
        except ValueError as err:
            logger.error('Could not parse {:s}: {}...'.format(backup_source, err))
            continue

        # if source is remote, open ssh connection
        if _type == 'ssh':
            key = conf['key'] if conf.get('key', None) else None
            compress = conf['compress'].pop(0) if conf.get('compress', None) else 'lzop'
            try:
                ssh_source = SSH(user, host, port=port, key=key, compress=compress)
            except (FileNotFoundError, SSHException):
                continue
            source_name_log = '{:s}@{:s}:{:s}'.format(user, host, source_name)
        else:
            ssh_source = None
            source_name_log = source_name

        try:
            # Children includes the base filesystem (named 'source_name')
            source_children = zfs.find_exclude(conf, config, ssh=ssh_source, matching=settings['matching'])
        except DatasetNotFoundError as err:
            logger.error('Source {:s} does not exist...'.format(source_name_log))
            continue
        except ValueError as err:
            logger.error(err)
            continue
        except CalledProcessError as err:
            logger.error('Error while opening source {:s}: \'{:s}\'...'
                         .format(source_name_log, err.stderr.rstrip()))
            continue

        send_exclude_property = conf.get('send_exclude_property')

        # Send to every backup destination
        for backup_dest in conf['dest']:
            # get exclude rules
            exclude = conf['exclude'].pop(0) if conf.get('exclude', None) else []
            # check if raw send was requested
            raw = conf['raw_send'].pop(0) if conf.get('raw_send', None) else False
            # check if we need to retry
            retries = conf['retries'].pop(0) if conf.get('retries', None) else 0
            retry_interval = conf['retry_interval'].pop(0) if conf.get('retry_interval', None) else 10
            # check if resumable send was requested
            resume = conf['resume'].pop(0) if conf.get('resume', None) else False
            # check if send_last_snapshot was requested
            send_last_snapshot = conf['send_last_snapshot'].pop(0) if conf.get('send_last_snapshot', None) else False
            if send_last_snapshot == 'no':
                send_last_snapshot = False
            # check if we should create dataset if it doesn't exist
            dest_auto_create = conf['dest_auto_create'].pop(0) if conf.get('dest_auto_create', None) else False

            try:
                _type, dest_name, user, host, port = parse_name(backup_dest)
            except ValueError as err:
                logger.error('Could not parse {:s}: {}...'.format(backup_dest, err))
                continue

            # if dest is remote, open ssh connection
            if _type == 'ssh':
                dest_key = conf['dest_keys'].pop(0) if conf.get('dest_keys', None) else None
                # if 'ssh_source' is set, then 'compress' is already set and we use same compression for both source and dest
                # if not then we take the next entry in config
                if not ssh_source:
                    compress = conf['compress'].pop(0) if conf.get('compress', None) else 'lzop'
                try:
                    ssh_dest = SSH(user, host, port=port, key=dest_key, compress=compress)
                except (FileNotFoundError, SSHException):
                    continue
                dest_name_log = '{:s}@{:s}:{:s}'.format(user, host, dest_name)
            else:
                ssh_dest = None
                dest_name_log = dest_name

            # check if dest exists
            try:
                zfs.open(dest_name, ssh=ssh_dest)
            except DatasetNotFoundError:
                if dest_auto_create:
                    logger.info('Destination {:s} does not exist, will create it...'.format(dest_name_log))
                    if create_dataset(dest_name, dest_name_log, ssh=ssh_dest):
                        continue
                else:
                    logger.error('Destination {:s} does not exist, manually create it or use "dest-auto-create" option...'
                                 .format(dest_name_log))
                    continue
            except ValueError as err:
                logger.error(err)
                continue
            except CalledProcessError as err:
                logger.error('Error while opening dest {:s}: \'{:s}\'...'
                             .format(dest_name_log, err.stderr.rstrip()))
                continue

            # Match children on source to children on dest
            if source_name == '':
                dest_children_names = [dest_name+'/'+child.name for child in source_children]
            else:
                dest_children_names = [child.name.replace(source_name, dest_name) for
                                    child in source_children]
            # Send all children to corresponding children on dest
            for source_fs, dest_name in zip(source_children, dest_children_names):
                # exclude filesystems from rules
                if any(fnmatch(source_fs.name, pattern) for pattern in exclude):
                    logger.debug('Matched {} in exclude rules, not sending...'.format(source_fs))
                    continue
                # check exclude attribute
                if send_exclude_property and source_fs.ispropval(send_exclude_property, check='false'):
                    logger.debug('Not sending {}, have property {:s}=false'.format(source_fs, send_exclude_property))
                    continue
                # TODO: create missing skipped filesystem on destination
                # send not excluded filesystems
                # Choose send function based on single_snapshots setting
                single_snapshots = settings.get('single_snapshots', False)
                send_func = send_filesystem_stepwise if single_snapshots else send_filesystem

                for retry in range(1,retries+2):
                    rc = send_func(source_fs, dest_name, ssh_dest=ssh_dest, raw=raw, resume=resume,
                        send_last_snapshot=send_last_snapshot, dest_auto_create=dest_auto_create)
                    if rc == 2 and retry <= retries:
                        logger.info('Retrying send in {:d}s (retry {:d} of {:d})...'.format(retry_interval, retry, retries))
                        sleep(retry_interval)
                    else:
                        break

            if ssh_dest:
                ssh_dest.close()

        if ssh_source:
            ssh_source.close()


def create_dataset(name, name_log, ssh=None):
    """Creates a dataset and logs success/fail

    Parameters
    ----------
    name : {str}
        Name of the dataset to be created
    name_log : {str}
        Name used for logging
    ssh : {SSH}, optional
        Open ssh connection, by default None

    Returns
    -------
    int
        0 if success, 1 if not
    """
    logger = logging.getLogger(__name__)
    try:
        zfs.create(name, ssh=ssh, force=True)
    except CalledProcessError as err:
        message = err.stderr.rstrip()
        if message == "filesystem successfully created, but it may only be mounted by root":
            logger.info('Successfully created {:s}, but cannot mount as non-root...'.format(name_log))
            return 0
        else:
            logger.info('Error while creating {}: \'{:s}\'...'.format(name_log, message))
            return 1
    except Exception as err:
        logger.error('Error while creating {:s}: {}...'.format(name_log, err))
        return 1
    else:
        logger.info('Successfully created {:s}...'.format(name_log))
        return 0


# def abort_resume(filesystem):
#     """Aborts the resumable receive state (deletes resume token) and logs success/fail

#     Parameters
#     ----------
#     filesystem : {ZFSFilesystem}
#         Name of the receiving dataset to be aborted

#     Returns
#     -------
#     int
#         0 if success, 1 if not
#     """
#     logger = logging.getLogger(__name__)
#     try:
#         filesystem.receive_abort()
#     except CalledProcessError as err:
#         logger.error('Error while aborting resumable receive state on {}: \'{:s}\'...'
#                      .format(filesystem, err.stderr.rstrip()))
#         return 1
#     except Exception as err:
#         logger.error('Error while aborting resumable receive state on {}: {}...'.format(filesystem, err))
#         return 1
#     else:
#         logger.info('Aborted resumable receive state on {:}...'.format(filesystem))
#         return 0
