"""
pyznap.send
~~~~~~~~~~~~~~

Send snapshots.

:copyright: (c) 2018-2019 by Yannick Boetzel.
:license: GPLv3, see LICENSE for more details.
"""

import copy
import logging
import sys
from fnmatch import fnmatch
from io import TextIOWrapper
from subprocess import CalledProcessError
from time import sleep

import pyznap.pyzfs as zfs

from .process import DatasetBusyError, DatasetExistsError, DatasetNotFoundError, get_dry_run
from .ssh import SSH, SSHException
from .utils import bytes_fmt, check_recv, parse_name

# Transient (retryable) error patterns for SSH/network failures.
# Note: matching is English-only; deploy with LANG=C in cron/systemd for consistency.
TRANSIENT_PATTERNS = [
    'Connection refused',
    'Connection timed out',
    'broken pipe',
    'Software caused connection abort',
    'Network is unreachable',
]


def _is_transient_error(error_msg):
    """Return True if error_msg indicates a transient (retryable) network/SSH failure."""
    msg = str(error_msg).lower()
    return any(p.lower() in msg for p in TRANSIENT_PATTERNS)


def send_snap(
    snapshot, dest_name, base=None, ssh_dest=None, raw=False, resume=False, resume_token=None, intermediates=True
):
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
    dest_name_log = f'{ssh_dest.user:s}@{ssh_dest.host:s}:{dest_name:s}' if ssh_dest else dest_name

    try:
        ssh_source = snapshot.ssh
        stream_size = snapshot.stream_size(base=base, raw=raw, resume_token=resume_token, intermediates=intermediates)

        zfs.STATS.add('zfs_send_snap_count')
        if get_dry_run():
            zfs.STATS.add('send_size', stream_size)
            logger.warning(
                f'DRY_RUN: send_snapshot {snapshot.name} --> {dest_name_log} base:{base} size:{stream_size} resume_token:{resume_token} compress:{ssh_source.compress if ssh_source else None}/{ssh_dest.decompress if ssh_dest else None}'
            )
            return 0

        send = snapshot.send(
            ssh_dest=ssh_dest, base=base, intermediates=intermediates, raw=raw, resume_token=resume_token
        )
        logger.debug(f'Using force receive (-F) for {dest_name_log}')
        recv = zfs.receive(
            name=dest_name,
            stdin=send.stdout,
            ssh=ssh_dest,
            ssh_source=ssh_source,
            force=True,
            nomount=True,
            stream_size=stream_size,
            raw=raw,
            resume=resume,
        )
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
            elif line_stripped:  # is stdout is redirected, write pv to stdout
                sys.stdout.write('  ' + line_stripped + '\n')
                sys.stdout.flush()
        send.stderr.close()

        # Log any ZFS warnings that were captured
        if zfs_warnings:
            logger.warning(f'ZFS send warnings for {dest_name_log:s}:')
            for warn in zfs_warnings:
                logger.warning(f'  {warn}')

        stdout, stderr = recv.communicate()
        # raise any error that occurred
        if recv.returncode:
            raise CalledProcessError(returncode=recv.returncode, cmd=recv.args, output=stdout, stderr=stderr)

    except (DatasetNotFoundError, DatasetExistsError, DatasetBusyError, OSError, EOFError) as err:
        logger.error(f'Error while sending to {dest_name_log:s}: {err}...')
        return 1
    except CalledProcessError as err:
        logger.error(
            'Error while sending to {:s}: {}...'.format(
                dest_name_log, err.stderr.rstrip().decode().replace('\n', ' - ')
            )
        )
        # returncode 2 means we will retry send if requested
        return 2
    except KeyboardInterrupt:
        logger.error(f'KeyboardInterrupt while sending to {dest_name_log:s}...')
        raise
    else:
        return 0


def send_filesystem(
    source_fs, dest_name, ssh_dest=None, raw=False, resume=False, send_last_snapshot=False, dest_auto_create=False
):
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
    dest_name_log = f'{ssh_dest.user:s}@{ssh_dest.host:s}:{dest_name:s}' if ssh_dest else dest_name

    logger.debug(f'Sending {source_fs} to {dest_name_log:s}...')

    resume_token = None
    # Check if dest already has a 'zfs receive' ongoing
    if check_recv(dest_name, ssh=ssh_dest):
        return 1

    # get snapshots on source, catch exception if dataset was destroyed since pyznap was started
    try:
        snapshots = source_fs.snapshots()[::-1]
    except (DatasetNotFoundError, DatasetBusyError) as err:
        logger.error(f'Error while opening source {source_fs}: {err}...')
        return 1
    except CalledProcessError as err:
        message = err.stderr.rstrip()
        if message.startswith('ssh: '):
            logger.error(f"Connection issue while opening source {source_fs}: '{message:s}'...")
            return 2
        else:
            logger.error(f"Error while opening source {source_fs}: '{message:s}'...")
            return 1
    snapnames = [snap.name.split('@')[1] for snap in snapshots]

    try:
        snapshot = snapshots[0]  # Most recent snapshot
        base = snapshots[-1]  # Oldest snapshot
    except IndexError:
        logger.error(f'No snapshots on {source_fs}, cannot send...')
        return 1

    try:
        dest_fs = zfs.open(dest_name, ssh=ssh_dest)
    except DatasetNotFoundError:
        if dest_auto_create:
            logger.info(f'Destination {dest_name_log:s} does not exist, will create it...')
            if create_dataset(dest_name, dest_name_log, ssh=ssh_dest):
                return 1
        else:
            logger.error(
                f'Destination {dest_name_log:s} does not exist, manually create it or use "dest-auto-create" option...'
            )
            return 1
        dest_snapnames = []
        common = set()
    except CalledProcessError as err:
        message = err.stderr.rstrip()
        if message.startswith('ssh: '):
            logger.error(f"Connection issue while opening dest {dest_name_log:s}: '{message:s}'...")
            return 2
        else:
            logger.error(f"Error while opening dest {dest_name_log:s}: '{message:s}'...")
            return 1
    else:
        # if dest exists, check for resume token
        resume_token = dest_fs.getprops().get('receive_resume_token', (None, None))[0]
        # find common snapshots between source & dest
        dest_snapnames = [snap.name.split('@')[1] for snap in dest_fs.snapshots()]
        common = set(snapnames) & set(dest_snapnames)
        if not resume and resume_token is not None:
            logger.error(
                f'{dest_name_log:s} contains partially-complete state from "zfs receive -s" (~{bytes_fmt(base.stream_size(raw=raw, resume_token=resume_token)):s}), '
                'but resume option is not set. Either set resume=yes or manually run: '
                f'zfs receive -A {dest_name}'
            )
            return 1

    zfs.STATS.add('zfs_send_filesystem_count')

    was_transfer = False
    if resume and resume_token is not None:
        logger.info(
            f'Found resume token. Resuming last transfer of {dest_name_log:s} (~{bytes_fmt(base.stream_size(raw=raw, resume_token=resume_token)):s})...'
        )
        was_transfer = True
        rc = send_snap(base, dest_name, base=None, ssh_dest=ssh_dest, raw=raw, resume=True, resume_token=resume_token)
        if rc:
            return rc
        # we need to update common snapshots after finishing the resumable send
        dest_snapnames = [snap.name.split('@')[1] for snap in dest_fs.snapshots()]
        common = set(snapnames) & set(dest_snapnames)

    if not common:
        if dest_snapnames:
            logger.error(f'No common snapshots on {dest_name_log:s}, but snapshots exist. Not sending...')
            return 1
        else:
            if send_last_snapshot:
                base = snapshot
                for snap in snapshots:
                    if send_last_snapshot in snap.name.split('@')[1]:
                        base = snap
                        break
                logger.info(
                    f'No common snapshots on {dest_name_log:s}, sending last snapshot {base} (~{bytes_fmt(base.stream_size(raw=raw)):s})...'
                )
            else:
                logger.info(
                    f'No common snapshots on {dest_name_log:s}, sending oldest snapshot {base} (~{bytes_fmt(base.stream_size(raw=raw)):s})...'
                )
            was_transfer = True
            rc = send_snap(base, dest_name, base=None, ssh_dest=ssh_dest, raw=raw, resume=resume)
            if rc:
                return rc
    else:
        # If there are common snapshots, get the most recent one
        base = next(filter(lambda x: x.name.split('@')[1] in common, snapshots), None)

    if base.name != snapshot.name:
        logger.info(
            'Updating {:s} with recent snapshot {} from {} (~{:s})...'.format(
                dest_name_log, snapshot, base.name.split('@')[1], bytes_fmt(snapshot.stream_size(base, raw=raw))
            )
        )
        was_transfer = True
        rc = send_snap(snapshot, dest_name, base=base, ssh_dest=ssh_dest, raw=raw, resume=resume)
        if rc:
            return rc

    if was_transfer:
        zfs.STATS.add('zfs_send_changed_count')
    else:
        zfs.STATS.add('zfs_send_unchanged_count')
    logger.info(f'{dest_name_log:s} is up to date...')
    return 0


def send_filesystem_stepwise(
    source_fs, dest_name, ssh_dest=None, raw=False, resume=False, send_last_snapshot=False, dest_auto_create=False
):
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
    dest_name_log = f'{ssh_dest.user:s}@{ssh_dest.host:s}:{dest_name:s}' if ssh_dest else dest_name

    logger.info(f'Stepwise sending {source_fs} to {dest_name_log:s}...')

    # Check if dest already has a 'zfs receive' ongoing
    if check_recv(dest_name, ssh=ssh_dest):
        return 1

    # Get snapshots on source
    try:
        snapshots = source_fs.snapshots()[::-1]  # newest first
    except (DatasetNotFoundError, DatasetBusyError) as err:
        logger.error(f'Error while opening source {source_fs}: {err}...')
        return 1
    except CalledProcessError as err:
        message = err.stderr.rstrip()
        logger.error(f"Error while opening source {source_fs}: '{message:s}'...")
        return 1

    if not snapshots:
        logger.error(f'No snapshots on {source_fs}, cannot send...')
        return 1

    snapnames = [snap.name.split('@')[1] for snap in snapshots]
    target_snapshot = snapshots[0]  # Most recent

    # Open or create destination
    resume_token = None
    try:
        dest_fs = zfs.open(dest_name, ssh=ssh_dest)
        # Check for resume token (partial receive state)
        resume_token = dest_fs.getprops().get('receive_resume_token', (None, None))[0]
        dest_snapnames = [snap.name.split('@')[1] for snap in dest_fs.snapshots()]
    except DatasetNotFoundError:
        if dest_auto_create:
            logger.info(f'Destination {dest_name_log:s} does not exist, will create it...')
            if create_dataset(dest_name, dest_name_log, ssh=ssh_dest):
                return 1
            dest_snapnames = []
        else:
            logger.error(
                f'Destination {dest_name_log:s} does not exist, manually create it or use "dest-auto-create" option...'
            )
            return 1
    except CalledProcessError as err:
        logger.error(f"Error while opening dest {dest_name_log:s}: '{err.stderr.rstrip():s}'...")
        return 1

    # Handle resume token if present (from interrupted transfer)
    if resume_token is not None:
        if resume:
            # Use oldest snapshot for size estimation
            base_for_resume = snapshots[-1] if snapshots else None
            size_str = (
                bytes_fmt(base_for_resume.stream_size(raw=raw, resume_token=resume_token)) if base_for_resume else '?'
            )
            logger.info(f'Found resume token. Resuming last transfer of {dest_name_log:s} (~{size_str:s})...')
            rc = send_snap(
                base_for_resume,
                dest_name,
                base=None,
                ssh_dest=ssh_dest,
                raw=raw,
                resume=True,
                resume_token=resume_token,
            )
            if rc:
                logger.error('Failed to resume transfer, cannot continue stepwise send...')
                return rc
            # Update snapshots after resume completes
            dest_fs = zfs.open(dest_name, ssh=ssh_dest)
            dest_snapnames = [snap.name.split('@')[1] for snap in dest_fs.snapshots()]
        else:
            logger.error(
                f'{dest_name_log:s} contains partially-complete state from "zfs receive -s". '
                f'Use resume=yes to continue or manually abort with: zfs receive -A {dest_name}'
            )
            return 1

    # Find common snapshots
    common = set(snapnames) & set(dest_snapnames)

    if not common:
        if dest_snapnames:
            logger.error(f'No common snapshots on {dest_name_log:s}, but snapshots exist. Not sending...')
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
            logger.info(f'No common snapshots on {dest_name_log:s}, sending initial snapshot {base}...')
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
        logger.info(f'{dest_name_log:s} is up to date...')
        return 0

    # Get list of snapshots to send (from base to target, in order)
    base_idx = snapshots.index(base_snap)
    snapshots_to_send = snapshots[:base_idx][::-1]  # reverse to get oldest first

    logger.info(
        'Sending {:d} snapshots one by one from {} to {}...'.format(
            len(snapshots_to_send), base_snap.name.split('@')[1], target_snapshot.name.split('@')[1]
        )
    )

    zfs.STATS.add('zfs_send_filesystem_count')

    success_count = 0
    fail_count = 0
    current_base = base_snap

    for snap in snapshots_to_send:
        snap_name = snap.name.split('@')[1]
        logger.info('  Sending snapshot {} (base: {})...'.format(snap_name, current_base.name.split('@')[1]))

        try:
            rc = send_snap(
                snap, dest_name, base=current_base, ssh_dest=ssh_dest, raw=raw, resume=resume, intermediates=False
            )
        except Exception as err:
            if _is_transient_error(str(err)):
                logger.warning(f'  Transient error sending {snap_name}: {err}')
                return 2
            logger.error(f'  Permanent error sending {snap_name}: {err}')
            return 1

        if rc == 0:
            success_count += 1
            current_base = snap  # Use this as base for next snapshot
        elif rc == 2:
            logger.warning(f'  Transient error sending {snap_name}, aborting stepwise send for retry...')
            return 2
        else:
            fail_count += 1
            logger.warning(f'  Skipping snapshot {snap_name} (send failed)...')
            # Don't update current_base - next snapshot will try from same base

    logger.info(f'Stepwise send completed: {success_count:d} sent, {fail_count:d} skipped...')

    if success_count > 0:
        zfs.STATS.add('zfs_send_changed_count')
        return 0
    else:
        return 1


def send_config(config, settings=None):
    """Tries to sync all entries in the config to their dest. Finds all children of the filesystem
    and calls send_filesystem on each of them.

    Parameters:
    ----------
    config : {list of dict}
        Full config list containing all strategies for different filesystems
    """

    if settings is None:
        settings = {}
    logger = logging.getLogger(__name__)
    logger.info('Sending snapshots...')

    for conf in config:
        conf = copy.deepcopy(conf)  # protect original config from .pop(0) mutations
        if not conf.get('dest', None):
            logger.debug('Ignore config from send {}...'.format(conf['name']))
            continue
        logger.debug('Process config {}...'.format(conf['name']))

        backup_source = conf['name']
        try:
            _type, source_name, user, host, port = parse_name(backup_source)
        except ValueError as err:
            logger.error(f'Could not parse {backup_source:s}: {err}...')
            continue

        # if source is remote, open ssh connection
        if _type == 'ssh':
            key = conf['key'] if conf.get('key', None) else None
            compress = conf['compress'].pop(0) if conf.get('compress', None) else 'lzop'
            try:
                ssh_source = SSH(user, host, port=port, key=key, compress=compress)
            except (FileNotFoundError, SSHException):
                continue
            source_name_log = f'{user:s}@{host:s}:{source_name:s}'
        else:
            ssh_source = None
            source_name_log = source_name

        try:
            # Children includes the base filesystem (named 'source_name')
            source_children = zfs.find_exclude(conf, config, ssh=ssh_source, matching=settings.get('matching'))
        except DatasetNotFoundError:
            logger.error(f'Source {source_name_log:s} does not exist...')
            continue
        except ValueError as err:
            logger.error(err)
            continue
        except CalledProcessError as err:
            logger.error(f"Error while opening source {source_name_log:s}: '{err.stderr.rstrip():s}'...")
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
            # check if single_snapshots (stepwise send) was requested
            single_snapshots_conf = conf['single_snapshots'].pop(0) if conf.get('single_snapshots', None) else None

            try:
                _type, dest_name, user, host, port = parse_name(backup_dest)
            except ValueError as err:
                logger.error(f'Could not parse {backup_dest:s}: {err}...')
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
                dest_name_log = f'{user:s}@{host:s}:{dest_name:s}'
            else:
                ssh_dest = None
                dest_name_log = dest_name

            # check if dest exists
            try:
                zfs.open(dest_name, ssh=ssh_dest)
            except DatasetNotFoundError:
                if dest_auto_create:
                    logger.info(f'Destination {dest_name_log:s} does not exist, will create it...')
                    if create_dataset(dest_name, dest_name_log, ssh=ssh_dest):
                        continue
                else:
                    logger.error(
                        f'Destination {dest_name_log:s} does not exist, manually create it or use "dest-auto-create" option...'
                    )
                    continue
            except ValueError as err:
                logger.error(err)
                continue
            except CalledProcessError as err:
                logger.error(f"Error while opening dest {dest_name_log:s}: '{err.stderr.rstrip():s}'...")
                continue

            # Match children on source to children on dest
            if source_name == '':
                dest_children_names = [dest_name + '/' + child.name for child in source_children]
            else:
                dest_children_names = [child.name.replace(source_name, dest_name) for child in source_children]
            # Send all children to corresponding children on dest
            for source_fs, dest_name in zip(source_children, dest_children_names):
                # exclude filesystems from rules
                if any(fnmatch(source_fs.name, pattern) for pattern in exclude):
                    logger.debug(f'Matched {source_fs} in exclude rules, not sending...')
                    continue
                # check exclude attribute
                if send_exclude_property and source_fs.ispropval(send_exclude_property, check='false'):
                    logger.debug(f'Not sending {source_fs}, have property {send_exclude_property:s}=false')
                    continue
                # TODO: create missing skipped filesystem on destination
                # send not excluded filesystems
                # Choose send function based on single_snapshots setting (config or CLI flag)
                single_snapshots = (
                    single_snapshots_conf
                    if single_snapshots_conf is not None
                    else settings.get('single_snapshots', False)
                )
                send_func = send_filesystem_stepwise if single_snapshots else send_filesystem

                for retry in range(1, retries + 2):
                    rc = send_func(
                        source_fs,
                        dest_name,
                        ssh_dest=ssh_dest,
                        raw=raw,
                        resume=resume,
                        send_last_snapshot=send_last_snapshot,
                        dest_auto_create=dest_auto_create,
                    )
                    if rc == 2 and retry <= retries:
                        logger.info(f'Retrying send in {retry_interval:d}s (retry {retry:d} of {retries:d})...')
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
        if message == 'filesystem successfully created, but it may only be mounted by root':
            logger.info(f'Successfully created {name_log:s}, but cannot mount as non-root...')
            return 0
        else:
            logger.info(f"Error while creating {name_log}: '{message:s}'...")
            return 1
    except Exception as err:
        logger.error(f'Error while creating {name_log:s}: {err}...')
        return 1
    else:
        logger.info(f'Successfully created {name_log:s}...')
        return 0


def abort_resume(filesystem):
    """Aborts the resumable receive state (deletes resume token) and logs success/fail

    Parameters
    ----------
    filesystem : {ZFSFilesystem}
        Name of the receiving dataset to be aborted

    Returns
    -------
    int
        0 if success, 1 if not
    """
    logger = logging.getLogger(__name__)
    try:
        filesystem.receive_abort()
    except CalledProcessError as err:
        logger.error(f"Error while aborting resumable receive state on {filesystem}: '{err.stderr.rstrip():s}'...")
        return 1
    except Exception as err:
        logger.error(f'Error while aborting resumable receive state on {filesystem}: {err}...')
        return 1
    else:
        logger.info(f'Aborted resumable receive state on {filesystem}...')
        return 0
