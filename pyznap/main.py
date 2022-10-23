#!/usr/bin/env python
"""
    pyznap.main
    ~~~~~~~~~~~~~~

    ZFS snapshot tool written in python.

    :copyright: (c) 2018-2019 by Yannick Boetzel.
    :license: GPLv3, see LICENSE for more details.
"""

import sys
import os
import logging
from errorhandler import ErrorHandler
from logging.config import fileConfig
from argparse import ArgumentParser
from datetime import datetime
from .utils import read_config, create_config
from .clean import clean_config
from .take import take_config
from .send import send_config
from .status import status_config
from .fix import fix_snapshots
from .process import set_dry_run
import pyznap.pyzfs as zfs
from . import __version__


DIRNAME = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = '/etc/pyznap/'

def check_pid(pidfile_path):
    from sys import argv
    from os import path, unlink, getpid
    import psutil

    if os.path.exists(pidfile_path):
        # pidfile exists... inspect it for freshness.
        try:
            pidno = int(open(pidfile_path, "r").read().strip())
            try:
                proc = psutil.Process(pidno)
                return False
            except psutil.NoSuchProcess:
                # pidfile's stale.
                os.unlink(pidfile_path)
        except ValueError:
            # what?
            os.unlink(pidfile_path)
    return True



def _main():
    """pyznap main function. Parses arguments and calls snap/clean/send functions accordingly.

    Returns
    -------
    int
        Exit code
    """

    settings = {
        "matching": None,
    }

    parser = ArgumentParser(prog='pyznap', description='ZFS snapshot tool written in python (version='+__version__+')')
    parser.add_argument('-q', '--quiet', action="store_true",
                        dest="quiet", help='quiet logging, only errors shown (WARNING)')
    parser.add_argument('-v', '--verbose', action="store_true",
                        dest="verbose", help='print more verbose output (DEBUG)')
    parser.add_argument('-t', '--trace', action="store_true",
                        dest="trace", help='print run tracing output (TRACE)')
    parser.add_argument('-n', '--dry-run', action="store_true",
                        dest="dry_run", help='only test run, no action taken')
    parser.add_argument('--syslog', action="store_true",
                        dest="syslog", help='add logging to syslog (INFO)')
    parser.add_argument('--logconfig', action="store_true",
                        dest="logconfig", help='add config name to log')
    parser.add_argument('--config', action="store",
                        dest="config", help='path to config file')
    parser.add_argument('-m', '--matching', action="store",
                        dest="matching", help='only process matching filesystems')
    parser.add_argument('--pidfile', action="store",
                        dest="pidfile", default=None, help='path to pid file')
    parser.add_argument('-V', '--version', action="store_true",
                        dest="version", help='print version number')

    subparsers = parser.add_subparsers(dest='command')

    parser_setup = subparsers.add_parser('setup', help='initial setup')
    parser_setup.add_argument('-p', '--path', action='store',
                              dest='path', help='pyznap config dir. default is {:s}'.format(CONFIG_DIR))

    parser_snap = subparsers.add_parser('snap', help='zfs snapshot tools')
    parser_snap.add_argument('--take', action="store_true",
                             help='take snapshots according to config file')
    parser_snap.add_argument('--clean', action="store_true",
                             help='clean old snapshots according to config file')
    parser_snap.add_argument('--full', action="store_true",
                             help='take snapshots then clean old according to config file')

    parser_send = subparsers.add_parser('send', help='zfs send/receive tools')
    parser_send.add_argument('-s', '--source', action="store",
                             dest='source', help='source filesystem')
    parser_send.add_argument('-d', '--dest', action="store",
                             dest='dest', help='destination filesystem')
    parser_send.add_argument('-i', '--key', action="store",
                             dest='key', help='ssh key if only source or dest is remote')
    parser_send.add_argument('-j', '--source-key', action="store",
                             dest='source_key', help='ssh key for source if both are remote')
    parser_send.add_argument('-k', '--dest-key', action="store",
                             dest='dest_key', help='ssh key for dest if both are remote')
    parser_send.add_argument('-c', '--compress', action="store",
                             dest='compress', help='compression to use for ssh transfer. default is lzop')
    parser_send.add_argument('-e', '--exclude', nargs = '+',
                             dest='exclude', help='datasets to exclude')
    parser_send.add_argument('-w', '--raw', action="store_true",
                             dest='raw', help='raw zfs send. default is false')
    parser_send.add_argument('-r', '--resume', action="store_true",
                             dest='resume', help='resumable send. default is false')
    parser_send.add_argument('-l', '--last', action="store",
                             dest='send_last_snapshot', help='stat sending from last snapshot containing string')
    parser_send.add_argument('--dest-auto-create', action="store_true",
                             dest='dest_auto_create',
                             help='create destination if it does not exist. default is false')
    parser_send.add_argument('--retries', action="store", type=int,
                             dest='retries', default=0,
                             help='number of retries on error. default is 0')
    parser_send.add_argument('--retry-interval', action="store", type=int,
                             dest='retry_interval', default=10,
                             help='interval in seconds between retries. default is 10')
    parser_send.add_argument('--max-depth', action="store", type=int,
                             dest='max_depth',
                             help='define max depth for child recursion (0 no child, default infinite depth)')

    parser_fix = subparsers.add_parser('fix', help='fix zfs snapshot from other format to pyznap')
    parser_fix.add_argument('-t', '--type', action="store",
                             dest='type', help='snapshot type name')
    parser_fix.add_argument('-f', '--format', action="store", required=True,
                             dest='format', help='snapshot format specification (regexp/@predefined[@zfs-auto-snap,@zfsnap])')
    parser_fix.add_argument('-m', '--map', action="store",
                             dest='map', help='optional type mapping (old=new:...)')
    parser_fix.add_argument('-r', '--recurse', action="store_true",
                             dest='recurse', help='recurse in child filesystems')
    # TODO: time shift
    parser_fix.add_argument('filesystem', nargs='+', help='filesystems to fix')

    subparsers.add_parser('full', help='full cycle: snap --take / send / snap --clean')

    parser_status = subparsers.add_parser('status', help='check filesystem snapshots status')
    parser_status.add_argument('--format', action="store", default='log', choices=['log', 'jsonl', 'html'],
                             dest='status_format', help='status output format')
    parser_status.add_argument('--all', action="store_true",
                             dest='status_all', help='show all ZFS filesystems')
    parser_status.add_argument('--print-config', action="store_true",
                             dest='print_config', help='only print parsed and processed config')
    parser_status.add_argument('--values', action="store",
                             dest='values', help='coma separated values to print')
    parser_status.add_argument('--filter', action="append",
                             dest='filter_values', help='add filter for col=value')
    parser_status.add_argument('--exclude', action="append",
                             dest='filter_exclude', help='exclude name filesystems (fnmatch)')

    if len(sys.argv)==1:
        parser.print_help(sys.stderr)
        sys.exit(1)
    args = parser.parse_args()

    if args.version:
        print(__version__)
        sys.exit()

    if not args.command:
        print('ERROR: No command specified.\n')
        parser.print_help(sys.stderr)
        sys.exit(1)

    e = ErrorHandler()

    loglevel =  logging.INFO
    if args.quiet:
        loglevel = logging.WARNING
    if args.verbose:
        loglevel = logging.DEBUG
    if args.command == 'status' and args.status_format != 'log':
        # for raw status only error show
        loglevel = logging.ERROR
    if args.trace:
        # trace override all
        logging.addLevelName(8, 'TRACE')
        loglevel = 8

    basicloglevel = min(loglevel, logging.INFO) if args.syslog else loglevel
    # logging.basicConfig(level=basicloglevel)
    root_logger = logging.getLogger()
    root_logger.setLevel(basicloglevel)

    config_path = args.config if args.config else os.path.join(CONFIG_DIR, 'pyznap.conf')
    logadd = ' #'+config_path if args.logconfig else '';


    console_fmt = logging.Formatter('%(asctime)s %(levelname)s: %(message)s'+logadd, datefmt='%b %d %H:%M:%S')
    if loglevel < logging.WARNING:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_fmt)
        console_handler.addFilter(lambda record: record.levelno < 30) # logging.WARNING make exception in destroy
        console_handler.setLevel(loglevel)
        root_logger.addHandler(console_handler)
    console_err_handler = logging.StreamHandler(sys.stderr)
    console_err_handler.setFormatter(console_fmt)
    console_err_handler.setLevel(logging.WARNING)
    root_logger.addHandler(console_err_handler)

    if args.syslog:
        # setup logging to syslog
        syslog_handler = logging.handlers.SysLogHandler(address = '/dev/log',
            facility=logging.handlers.SysLogHandler.LOG_DAEMON)
        syslog_handler.setFormatter(logging.Formatter('pyznap: [%(levelname)s] %(message)s'+logadd))
        # syslog always level INFO
        syslog_handler.setLevel(logging.INFO)
        root_logger.addHandler(syslog_handler)

    logger = logging.getLogger(__name__)

    if args.dry_run:
        set_dry_run()

    if args.matching:
        settings['matching'] =  args.matching

    if args.pidfile is not None:
        if not check_pid(args.pidfile):
            logger.info('pidfile {} exists, exiting'.format(args.pidfile))
            sys.exit(1)
        open(args.pidfile, "w").write("{}\n".format(os.getpid()))
    try:
        logger.info('Starting pyznap...')

        if args.command in ('snap', 'send', 'full', 'status'):
            logger.info('Read config={}'.format(config_path))
            config = read_config(config_path)
            if config == None:
                return 1

        if args.command == 'setup':
            path = args.path if args.path else CONFIG_DIR
            create_config(path)

        elif args.command == 'full':
            take_config(config, settings)
            send_config(config, settings)
            clean_config(config, settings)

        elif args.command == 'snap':
            # Default if no args are given
            if not args.take and not args.clean:
                args.full = True

            if args.take or args.full:
                take_config(config, settings)

            if args.clean or args.full:
                clean_config(config, settings)

        elif args.command == 'send':
            if args.source and args.dest:
                # use args.key if either source or dest is remote
                source_key, dest_key = None, None
                if args.dest.startswith('ssh'):
                    dest_key = [args.key] if args.key else None
                elif args.source.startswith('ssh'):
                    source_key = args.key if args.key else None
                # if source_key and dest_key are given, overwrite previous value
                source_key = args.source_key if args.source_key else source_key
                dest_key = [args.dest_key] if args.dest_key else dest_key
                # get exclude rules
                exclude = [args.exclude] if args.exclude else None
                # check if raw send was requested
                raw = [args.raw] if args.raw else None
                # compress ssh zfs send/receive
                compress = [args.compress] if args.compress else None
                # use receive resume token
                resume = [args.resume] if args.resume else None
                # retry zfs send/receive
                retries = [args.retries] if args.retries else None
                # wait interval for retry
                retry_interval = [args.retry_interval] if args.retry_interval else None
                # automatically create dest dataset if it does not exist
                dest_auto_create = [args.dest_auto_create] if args.dest_auto_create else None
                # start send from last snapshot
                send_last_snapshot = [args.send_last_snapshot] if args.send_last_snapshot else None

                send_config([{'name': args.source, 'dest': [args.dest], 'key': source_key,
                              'dest_keys': dest_key, 'compress': compress, 'exclude': exclude,
                              'raw_send': raw, 'resume': resume, 'dest_auto_create': dest_auto_create,
                              'retries': retries, 'retry_interval': retry_interval, 'max_depth': args.max_depth,
                              'send_last_snapshot': send_last_snapshot}], settings=settings)

            elif args.source and not args.dest:
                logger.error('Missing dest...')
            elif args.dest and not args.source:
                logger.error('Missing source...')
            else:
                send_config(config, settings)

        elif args.command == 'fix':
            tmap = args.map
            if tmap:
                tmap = dict(kw.split('=') for kw in args.map.split(':'))
            fix_snapshots(args.filesystem, format=args.format, type=args.type, recurse=args.recurse, type_map=tmap)

        elif args.command == 'status':
            if args.print_config:
                print(str(config))
            else:
                filter_values=None
                if args.filter_values:
                    filter_values = {}
                    for fv in args.filter_values:
                        f, v = fv.split('=')
                        v = {'true': True, 'false': False}.get(v.lower(), v)
                        filter_values[f] = v
                status_config(config, output=args.status_format, show_all=args.status_all,
                    values=tuple(args.values.split(',')) if args.values else None,
                    filter_values=filter_values, filter_exclude=args.filter_exclude, settings=settings)

        zfs.STATS.log()
        logger.info('Finished successfully...')
    finally:
        if args.pidfile is not None:
            os.unlink(args.pidfile)
    return 1 if e.fired else 0


def main():
    """Wrapper around _main function to catch KeyboardInterrupt

    Returns
    -------
    int
        Exit code
    """

    logger = logging.getLogger(__name__)
    try:
        return _main()
    except KeyboardInterrupt:
        logger.error('KeyboardInterrupt - exiting gracefully...')
        return 1


if __name__ == "__main__":
    sys.exit(main())
