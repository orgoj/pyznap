"""
pyznap.utils
~~~~~~~~~~~~~~

Helper functions.

:copyright: (c) 2018-2019 by Yannick Boetzel.
:license: GPLv3, see LICENSE for more details.
"""

import glob
import logging
import os
import re

# TODO: Migrate from pkg_resources to importlib.resources when dropping Python 3.6-3.8 support
import warnings
from configparser import (
    ConfigParser,
    DuplicateOptionError,
    DuplicateSectionError,
    MissingSectionHeaderError,
    NoOptionError,
)
from subprocess import PIPE, CalledProcessError, TimeoutExpired

from .process import run
from .ssh import SSHException

warnings.filterwarnings('ignore', message='pkg_resources is deprecated', category=UserWarning)
from pkg_resources import resource_string  # noqa: E402

SNAPSHOT_TYPES = ('frequent', 'hourly', 'daily', 'weekly', 'monthly', 'yearly')


def validate_config(config):
    """
    Validate configuration after parsing.

    Checks for common configuration errors and logs them.

    Parameters:
    ----------
    config : list
        Parsed configuration list

    Returns:
    -------
    list
        List of validation error messages (empty if no errors)
    """
    logger = logging.getLogger(__name__)
    errors = []
    warnings = []

    for entry in config:
        name = entry.get('name', '?')
        if not name:
            name = '//'  # Root filesystem

        # 1. Check snapshot type values are valid integers >= 0
        for snap_type in SNAPSHOT_TYPES:
            val = entry.get(snap_type)
            if val is not None:
                if not isinstance(val, int):
                    errors.append(f"{name}: {snap_type} must be an integer, got '{val}'")
                elif val < 0:
                    errors.append(f'{name}: {snap_type} must be >= 0, got {val}')

        # 2. Check boolean options have valid values
        for bool_opt in ['snap', 'clean', 'ignore_not_existing']:
            val = entry.get(bool_opt)
            if val is not None and not isinstance(val, bool):
                errors.append(f"{name}: {bool_opt} must be yes/no, got '{val}'")

        # 3. Check dest/dest_keys/compress/exclude array alignment
        dest = entry.get('dest')
        if dest and isinstance(dest, list):
            dest_count = len(dest)

            # Check dest_keys
            dest_keys = entry.get('dest_keys')
            if dest_keys and isinstance(dest_keys, list):
                if len(dest_keys) != dest_count:
                    errors.append(
                        f'{name}: dest has {dest_count} entries but dest_keys has {len(dest_keys)} '
                        f'(must be equal or omit dest_keys)'
                    )

            # Check compress
            compress = entry.get('compress')
            if compress and isinstance(compress, list):
                if len(compress) != dest_count:
                    errors.append(
                        f'{name}: dest has {dest_count} entries but compress has {len(compress)} '
                        f'(must be equal or omit compress)'
                    )

            # Check exclude
            exclude = entry.get('exclude')
            if exclude and isinstance(exclude, list):
                if len(exclude) != dest_count:
                    errors.append(
                        f'{name}: dest has {dest_count} entries but exclude has {len(exclude)} '
                        f'(must be equal or omit exclude)'
                    )

            # Check raw_send
            raw_send = entry.get('raw_send')
            if raw_send and isinstance(raw_send, list):
                if len(raw_send) != dest_count:
                    errors.append(
                        f'{name}: dest has {dest_count} entries but raw_send has {len(raw_send)} '
                        f'(must be equal or omit raw_send)'
                    )

            # Check resume
            resume = entry.get('resume')
            if resume and isinstance(resume, list):
                if len(resume) != dest_count:
                    errors.append(
                        f'{name}: dest has {dest_count} entries but resume has {len(resume)} '
                        f'(must be equal or omit resume)'
                    )

            # Check retries
            retries = entry.get('retries')
            if retries and isinstance(retries, list):
                if len(retries) != dest_count:
                    errors.append(
                        f'{name}: dest has {dest_count} entries but retries has {len(retries)} '
                        f'(must be equal or omit retries)'
                    )

        # 4. Check SSH key files exist
        key = entry.get('key')
        if key and key is not None and not os.path.isfile(key):
            errors.append(f'{name}: SSH key file not found: {key}')

        # Check dest_keys files exist
        dest_keys = entry.get('dest_keys')
        if dest_keys and isinstance(dest_keys, list):
            for i, key in enumerate(dest_keys):
                if key and key is not None and not os.path.isfile(key):
                    errors.append(f'{name}: dest_keys[{i}] file not found: {key}')

        # 5. Warn if dest is set but snap is not enabled (nothing to send)
        has_dest = dest and len(dest) > 0
        snap_enabled = entry.get('snap') is True

        # Check if any snapshot type is configured
        has_snapshot_config = any(
            entry.get(st) and isinstance(entry.get(st), int) and entry.get(st) > 0 for st in SNAPSHOT_TYPES
        )

        if has_dest and not snap_enabled and has_snapshot_config:
            warnings.append(
                f'{name}: has dest configured but snap=no - no snapshots will be sent (set snap=yes or remove dest)'
            )

        # 6. Warn if snap is enabled but no snapshot types configured
        if snap_enabled and not has_snapshot_config:
            warnings.append(f'{name}: snap=yes but no snapshot types configured (set hourly, daily, weekly, etc.)')

        # 7. Check max_depth is valid
        max_depth = entry.get('max_depth')
        if max_depth is not None and not isinstance(max_depth, int):
            errors.append(f"{name}: max_depth must be an integer or 'no', got '{max_depth}'")

    # Log all warnings
    for warning in warnings:
        logger.warning(f'Config validation warning: {warning}')

    # Log and return errors
    if errors:
        for error in errors:
            logger.error(f'Config validation error: {error}')

    return errors


def exists(executable='', ssh=None):
    """Tests if an executable exists on the system.

    Parameters:
    ----------
    executable : {str}, optional
        Name of the executable to test (the default is an empty string)
    ssh : {SSH}, optional
        Open ssh connection (the default is None, which means check is done locally)

    Returns
    -------
    bool
        True if executable exists, False if not
    """

    logger = logging.getLogger(__name__)
    name_log = f'{ssh.user:s}@{ssh.host:s}' if ssh else 'localhost'

    cmd = ['which', executable]
    try:
        retcode = run(cmd, stdout=PIPE, stderr=PIPE, timeout=5, universal_newlines=True, ssh=ssh).returncode
    except (TimeoutExpired, SSHException) as err:
        logger.error(f"Error while checking if {executable:s} exists on {name_log:s}: '{err}'...")
        return False

    return not bool(retcode)  # return False if retcode != 0


def read_config(path):
    """Reads a config file and outputs a list of dicts with the given snapshot strategy.

    Parameters:
    ----------
    path : {str}
        Path to the config file

    Raises
    ------
    FileNotFoundError
        If path does not exist

    Returns
    -------
    list of dict
        Full config list containing all strategies for different filesystems
    """

    logger = logging.getLogger(__name__)

    if os.path.isfile(path):
        cfgfiles = path
    else:
        cfgfiles = glob.glob(os.path.expanduser(path))
        if cfgfiles == []:
            logger.error(f'Error while loading config: File {path:s} does not exist.')
            return None

    parser = ConfigParser()
    try:
        files = parser.read(cfgfiles)
        logger.info('Parsed configs: ' + str(files))
    except (MissingSectionHeaderError, DuplicateSectionError, DuplicateOptionError) as e:
        logger.error(f'Error while loading config: {e}')
        return None

    config = []
    options = [
        'key',
        'snap',
        'clean',
        'dest',
        'dest_keys',
        'compress',
        'exclude',
        'raw_send',
        'resume',
        'dest_auto_create',
        'retries',
        'retry_interval',
        'ignore_not_existing',
        'send_last_snapshot',
        'max_depth',
        'snap_exclude_property',
        'send_exclude_property',
    ]
    options += list(SNAPSHOT_TYPES)

    for section in parser.sections():
        dic = {}
        config.append(dic)
        dic['name'] = '' if section == '//' else section

        for option in options:
            try:
                value = parser.get(section, option)
            except NoOptionError:
                dic[option] = None
            else:
                if option in ['key']:
                    dic[option] = value if os.path.isfile(value) else None
                elif option in SNAPSHOT_TYPES:
                    try:
                        dic[option] = int(value)
                    except ValueError:
                        logger.error(
                            f"Invalid value for {option} in section [{section}]: '{value}' (must be an integer)"
                        )
                        return None
                elif option in ['max_depth']:
                    try:
                        dic[option] = int(value) if value and value != 'no' else -1
                    except ValueError:
                        logger.error(
                            f"Invalid value for max_depth in section [{section}]: '{value}' (must be an integer or 'no')"
                        )
                        return None
                elif option in ['snap', 'clean', 'ignore_not_existing']:
                    dic[option] = {'yes': True, 'no': False}.get(value.lower(), None)
                elif option in ['snap_exclude_property', 'send_exclude_property']:
                    dic[option] = value.strip() if value.strip() else False
                elif option in ['dest', 'compress', 'send_last_snapshot']:
                    dic[option] = [i.strip() for i in value.split(',')]
                elif option in ['dest_keys']:
                    dic[option] = [i.strip() if os.path.isfile(i.strip()) else None for i in value.split(',')]
                elif option in ['exclude']:
                    dic[option] = [
                        [i.strip() for i in s.strip().split(' ')] if s.strip() else None for s in value.split(',')
                    ]
                elif option in ['raw_send', 'resume', 'dest_auto_create']:
                    dic[option] = [{'yes': True, 'no': False}.get(i.strip().lower(), None) for i in value.split(',')]
                elif option in ['retries', 'retry_interval']:
                    dic[option] = [int(i) for i in value.split(',')]

    # Sort by pathname - must be before propagation
    config = sorted(config, key=lambda entry: entry['name'].split('/'))

    # Find closest parent
    for child in config:
        child['_parent'] = None
        for parent in reversed(config):
            if child['name'].startswith(parent['name'] + '/') or (
                parent['name'] == '' and parent['name'] != child['name']
            ):
                child['_parent'] = parent['name']
                break

    # Pass through values recursively
    for parent in config:
        for child in config:
            if parent['name'] == child['_parent']:
                child_parent = '/'.join(child['name'].split('/')[:-1])  # get parent of child filesystem
                if child_parent.startswith(parent['name']):
                    for option in [
                        'key',
                        'snap',
                        'clean',
                        'ignore_not_existing',
                        'send_last_snapshot',
                        'max_depth',
                        'snap_exclude_property',
                        'send_exclude_property',
                    ] + list(SNAPSHOT_TYPES):
                        child[option] = child[option] if child[option] is not None else parent[option]

    # Validate configuration
    validation_errors = validate_config(config)
    if validation_errors:
        logger.error(
            f'Configuration validation failed with {len(validation_errors)} error(s). Please fix your config file.'
        )
        return None

    return config


def parse_name(value):
    """Splits a string of the form 'ssh:port:user@host:rpool/data' into its parts separated by ':'.

    Parameters:
    ----------
    value : {str}
        String to split up

    Returns
    -------
    (str, str, str, str, int)
        Tuple containing the different parts of the string
    """

    if value.startswith('ssh'):
        _type, port, host, fsname = value.split(':', maxsplit=3)
        port = int(port) if port else 22
        user, host = host.split('@', maxsplit=1)
    else:
        _type, user, host, port = 'local', None, None, None
        fsname = value
    return _type, fsname, user, host, port


def create_config(path):
    """Initial configuration: Creates dir 'path' and puts sample config there

    Parameters
    ----------
    path : str
        Path to dir where to store config file

    """

    logger = logging.getLogger(__name__)

    CONFIG_FILE = os.path.join(path, 'pyznap.conf')
    config = resource_string(__name__, 'config/pyznap.conf').decode('utf-8')

    logger.info('Initial setup...')

    if not os.path.isdir(path):
        logger.info(f'Creating directory {path:s}...')
        try:
            os.mkdir(path, mode=int('755', base=8))
        except (PermissionError, FileNotFoundError, OSError) as e:
            logger.error(f'Could not create {path:s}: {e}')
            logger.error('Aborting setup...')
            return 1
    else:
        logger.info(f'Directory {path:s} does already exist...')

    if not os.path.isfile(CONFIG_FILE):
        logger.info(f'Creating sample config {CONFIG_FILE:s}...')
        try:
            with open(CONFIG_FILE, 'w') as file:
                file.write(config)
        except (PermissionError, FileNotFoundError, OSError) as e:
            logger.error(f'Could not write to file {CONFIG_FILE:s}: {e}')
        else:
            try:
                os.chmod(CONFIG_FILE, mode=int('644', base=8))
            except (PermissionError, OSError):
                logger.error(f'Could not set correct permissions on file {CONFIG_FILE:s}. Please do so manually...')
    else:
        logger.info(f'File {CONFIG_FILE:s} does already exist...')

    return 0


def check_recv(fsname, ssh=None):
    """Checks if there is already a 'zfs receive' for that dataset ongoing

    Parameters
    ----------
    fsname : str
        Name of the dataset
    ssh : SSH, optional
        Open ssh connection (the default is None, which means check is done locally)

    Returns
    -------
    bool
        True if there is a 'zfs receive' ongoing or if an error is raised during checking. False if
        there is no 'zfs receive'.
    """

    logger = logging.getLogger(__name__)
    fsname_log = f'{ssh.user:s}@{ssh.host:s}:{fsname:s}' if ssh else fsname

    try:
        out = run(['ps', '-Ao', 'args='], stdout=PIPE, stderr=PIPE, timeout=5, universal_newlines=True, ssh=ssh).stdout
    except (TimeoutExpired, SSHException) as err:
        logger.error(f"Error while checking 'zfs receive' on {fsname_log:s}: '{err}'...")
        return True
    except CalledProcessError as err:
        logger.error(f"Error while checking 'zfs receive' on {fsname_log:s}: '{err.stderr.rstrip():s}'...")
        return True
    else:
        match = re.search(rf'zfs (receive|recv).*({fsname:s})(?=\n)', out)
        if match:
            logger.error(f"Cannot send to {fsname_log:s}, process '{match.group():s}' already running...")
            return True

    return False


def bytes_fmt(num):
    """Converts bytes to a human readable format

    Parameters
    ----------
    num : int,float
        Number of bytes

    Returns
    -------
    float
        Human readable format with binary prefixes
    """

    for x in ['B', 'K', 'M', 'G', 'T', 'P', 'E', 'Z']:
        if num < 1024:
            return f'{num:3.1f}{x:s}'
        num /= 1024
    else:
        return '{:3.1f}{:s}'.format(num, 'Y')
