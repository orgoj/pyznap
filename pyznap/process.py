"""
pyznap.process
~~~~~~~~~~~~~~

Catch ZFS subprocess errors, forked from https://bitbucket.org/stevedrake/weir/.

:copyright: (c) 2015-2019 by Stephen Drake, Yannick Boetzel.
:license: GPLv3, see LICENSE for more details.
"""

import errno as _errno
import logging
import re
import subprocess as sp

DRY_RUN = False

PIPE = sp.PIPE


class ZFSError(OSError):
    def __init__(self, dataset):
        super().__init__(self.errno, self.strerror, dataset)


class DatasetNotFoundError(ZFSError):
    errno = _errno.ENOENT
    strerror = 'dataset does not exist'


class DatasetExistsError(ZFSError):
    errno = _errno.EEXIST
    strerror = 'dataset already exists'


class DatasetBusyError(ZFSError):
    errno = _errno.EBUSY
    strerror = 'dataset is busy'


class HoldTagNotFoundError(ZFSError):
    errno = _errno.ENOENT
    strerror = 'no such tag on this dataset'


class HoldTagExistsError(ZFSError):
    errno = _errno.EEXIST
    strerror = 'tag already exists on this dataset'


class CompletedProcess(sp.CompletedProcess):
    def check_returncode(self):
        """Check for known errors of the form "cannot <action> <dataset>: <reason>"

        Raises
        ------
        DatasetNotFoundError, DatasetExistsError, DatasetBusyError, HoldTagNotFoundError, HoldTagExistsError
            Raises corresponding ZFS error
        """

        if self.returncode == 1:
            pattern = r'^cannot ([^ ]+(?: [^ ]+)*?) ([^ ]+): (.+)$'
            # only use first line of stderr to match zfs errors
            match = re.search(pattern, self.stderr.splitlines()[0])
            if match:
                _, dataset, reason = match.groups()
                if dataset[0] == dataset[-1] == "'":
                    dataset = dataset[1:-1]
                for error in (
                    DatasetNotFoundError,
                    DatasetExistsError,
                    DatasetBusyError,
                    HoldTagNotFoundError,
                    HoldTagExistsError,
                ):
                    if reason == error.strerror:
                        raise error(dataset)

        # did not match known errors, defer to superclass
        super().check_returncode()


def set_dry_run(dry_run=True):
    """Set dry run flag"""
    global DRY_RUN
    DRY_RUN = dry_run
    if DRY_RUN:
        logger = logging.getLogger(__name__)
        logger.warning('DRY_RUN: Ignoring filesystem modification.')


def get_dry_run():
    """Return dry run flag value"""
    global DRY_RUN
    return DRY_RUN


def check_output_dry(*popenargs, timeout=None, ssh=None, **kwargs):
    """Dry run wrapper for check_output"""
    if DRY_RUN:
        logger = logging.getLogger(__name__)
        logger.warning('DRY_RUN: ({}) {}'.format(ssh, ' '.join(*popenargs)))
        return None
    else:
        check_output(*popenargs, timeout=timeout, ssh=ssh, **kwargs)


def check_output(*popenargs, timeout=None, ssh=None, **kwargs):
    """Run command with arguments and return its output. Works over ssh.
    Parameters:
    ----------
    *popenargs : {}
        Variable length argument list, same as Popen constructor
    **kwargs : {}
        Arbitrary keyword arguments, same as Popen constructor
    timeout : {float}, optional
        Timeout in seconds, if process takes too long TimeoutExpired will be raised (the default is
        None, meaning no timeout)
    ssh : {ssh.SSH}, optional
        Open ssh connection for remote execution (the default is None)
    Raises
    ------
    ValueError
        Raise ValueError for forbidden kwargs
    Returns
    -------
    None or list of lists
        List of all lines from the output, separated at '\t' into lists
    """

    if 'stdout' in kwargs:
        raise ValueError('stdout argument not allowed, it will be overridden.')
    if 'universal_newlines' in kwargs:
        raise ValueError('universal_newlines argument not allowed, it will be overridden.')
    if 'input' in kwargs:
        raise ValueError('input argument not allowed, it will be overridden.')

    ret = run(*popenargs, stdout=PIPE, stderr=PIPE, timeout=timeout, universal_newlines=True, ssh=ssh, **kwargs)
    ret.check_returncode()
    out = ret.stdout

    return None if out is None else [line.split('\t') for line in out.splitlines()]


def run(*popenargs, timeout=None, check=False, ssh=None, **kwargs):
    """Run command with ZFS arguments and return a CompletedProcess instance. Works over ssh.
    Parameters:
    ----------
    *popenargs : {}
        Variable length argument list, same as Popen constructor
    **kwargs : {}
        Arbitrary keyword arguments, same as Popen constructor
    timeout : {float}, optional
        Timeout in seconds, if process takes too long TimeoutExpired will be raised (the default is
        None, meaning no timeout)
    check : {bool}, optional
        Check return code (the default is False, meaning return code will not be checked)
    ssh : {ssh.SSH}, optional
        Open ssh connection for remote execution (the default is None)
    Raises
    ------
    sp.TimeoutExpired
        Raised if process takes longer than given timeout
    sp.CalledProcessError
        Raised if check=True and return code != 0
    Returns
    -------
    subprocess.CompletedProcess
        Return instance of CompletedProcess with given return code, stdout and stderr
    """

    logger = logging.getLogger(__name__)

    if ssh:
        popenargs = (ssh.cmd + popenargs[0], *popenargs[1:])

    logger.log(8, 'RUN: {:s}'.format(' '.join(*popenargs)))
    with sp.Popen(*popenargs, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except sp.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            raise sp.TimeoutExpired(process.args, timeout, output=stdout, stderr=stderr) from None
        except Exception:
            process.kill()
            process.wait()
            raise
        retcode = process.poll()

    if check and retcode:
        raise sp.CalledProcessError(retcode, popenargs[0], output=stdout, stderr=stderr)

    return CompletedProcess(popenargs[0], retcode, stdout, stderr)
