"""
pyznap.status
~~~~~~~~~~~~~~

Status filesystem snapshots.

:copyright: (c) 2018-2019 by Yannick Boetzel.
:license: GPLv3, see LICENSE for more details.
"""

import json
import logging
import os
from collections import OrderedDict
from datetime import datetime
from fnmatch import fnmatch
from subprocess import CalledProcessError

import pyznap.pyzfs as zfs

from .process import DatasetBusyError, DatasetNotFoundError
from .ssh import SSH, SSHException
from .utils import SNAPSHOT_TYPES, bytes_fmt, parse_name

ZFS_SIZE_PROPERTIES = (
    'logicalused',
    'used',
    'usedbychildren',
    'usedbydataset',
    'usedbyrefreservation',
    'usedbysnapshots',
    'written',
    'referenced',
    'logicalreferenced',
)
ZFS_OTHER_PROPERTIES = (
    'type',
    'creation',
    'dedup',
    'compression',
    'compressratio',
    'refcompressratio',
    'mountpoint',
    'origin',
    'recordsize',
    'primarycache',
    'secondarycache',
    'logbias',
)

# output lines
OUTPUT = []


def status_filesystem(
    filesystem,
    conf,
    output='log',
    show_all=False,
    main_fs=False,
    values=None,
    filter=None,
    filter_values=None,
    filter_exclude=None,
):
    """Deletes snapshots of a single filesystem according to conf.

    Parameters:
    ----------
    filesystem : {ZFSFilesystem}
        Filesystem to status
    conf : {dict}
        Config entry with snapshot strategy
    main_fs:
        mark configured filesystem, ignore exclude zfs property
    """

    global OUTPUT

    logger = logging.getLogger(__name__)

    fs_name = str(filesystem)
    if filter_exclude:
        if any(fnmatch(fs_name, pattern) for pattern in filter_exclude):
            logger.debug(f'Exclude filesystem {fs_name} by --exclude')
            return

    logger.debug(f'Checking snapshots on {fs_name}...')
    zfs.STATS.add('checked_count')

    snap = conf.get('snap', False)
    clean = conf.get('clean', False)
    send = bool(conf.get('dest', False))
    excluded = False
    snap_exclude_property = conf['snap_exclude_property']
    if not main_fs and snap_exclude_property and filesystem.ispropval(snap_exclude_property, check='false'):
        zfs.STATS.add('snap_excluded_count')
        logger.debug(f'Ignore dataset fron snap {filesystem.name:s}, have property {snap_exclude_property:s}=false')
        snap = False
        clean = False
    send_exclude_property = conf['send_exclude_property']
    if not main_fs and send_exclude_property and filesystem.ispropval(send_exclude_property, check='false'):
        zfs.STATS.add('send_excluded_count')
        logger.debug(f'Ignore dataset fron send {filesystem.name:s}, have property {snap_exclude_property:s}=false')
        send = False
    if not (snap or clean or send):
        if show_all:
            zfs.STATS.add('excluded_count')
            excluded = True
        else:
            return

    # increase stats count and check excludes in send
    if snap:
        zfs.STATS.add('snap_count')
    if clean:
        zfs.STATS.add('clean_count')
    if send:
        dest = conf.get('dest', False)
        if dest and conf['exclude']:
            # check excluded
            sending = []
            for exclude, dst in zip(conf['exclude'], dest):
                if exclude and any(fnmatch(filesystem.name, pattern) for pattern in exclude):
                    zfs.STATS.add('dest_excluded_count')
                    logger.debug(f'Excluded from send {filesystem} -> {dst}...')
                    sending.append(False)
                else:
                    sending.append(dst)
            dest = sending
        send = send and dest and any(x for x in dest if bool(x))
    else:
        dest = None

    if send:
        zfs.STATS.add('send_count')

    snapshots = {t: [] for t in SNAPSHOT_TYPES}
    # catch exception if dataset was destroyed since pyznap was started
    try:
        fs_snapshots = filesystem.snapshots()
    except (DatasetNotFoundError, DatasetBusyError) as err:
        logger.error(f'Error while opening {filesystem}: {err}...')
        return 1
    have_snapshots = bool(fs_snapshots)
    # categorize snapshots
    for snaps in fs_snapshots:
        # Ignore snapshots not taken with pyznap
        if not snaps.name.split('@')[1].startswith('pyznap'):
            continue
        try:
            snap_type = snaps.name.split('_')[-1]
            snapshots[snap_type].append(snaps)
        except (ValueError, KeyError):
            continue

    # Reverse sort by time taken
    for snaps in snapshots.values():
        snaps.reverse()

    level = logging.INFO

    # prepare data for status
    counts = {}
    for s in snapshots.keys():
        counts[s] = conf.get(s, 0) or 0 if clean else 0
    pyznap_snapshots = sum(len(s) for s in snapshots.values())

    # TODO: remote uptodate check
    # TODO: T/F oversnapshot/undesnapshot/othersnapshots/unvantedsnapshot on exluded fs

    # check needed snapshots count
    missing_snapshots = any(len(snapshots[t]) < counts[t] for t in SNAPSHOT_TYPES)
    extra_snapshots = any(len(snapshots[t]) > counts[t] for t in SNAPSHOT_TYPES)
    if missing_snapshots:
        level = logging.WARNING

    # make status data
    status = OrderedDict()
    status['hostname'] = os.uname()[1]
    status['name'] = fs_name
    status['conf'] = conf['name']
    status['excluded'] = excluded
    status['do-snap'] = snap
    status['do-clean'] = clean
    status['do-send'] = send
    status['conf-snap_exclude_property'] = snap_exclude_property
    status['conf-send_exclude_property'] = send_exclude_property
    status['snapshot-have'] = have_snapshots
    status['snapshot-missing'] = missing_snapshots
    status['snapshot-extra'] = extra_snapshots
    status['snapshot-count-all'] = len(fs_snapshots)
    status['snapshot-count-pyznap'] = pyznap_snapshots
    status['snapshot-count-nopyznap'] = len(fs_snapshots) - pyznap_snapshots
    for stype in SNAPSHOT_TYPES:
        status['snapshot-types-' + stype] = str(len(snapshots[stype])) + '/' + str(counts[stype])

    def bytes_fmt_no_raw(bytes):
        return bytes if output == 'jsonl' else bytes_fmt(bytes)

    status['dest'] = dest
    if dest:
        i = 0
        snapnames = [snap.name.split('@')[1] for snap in fs_snapshots]
        for d in dest:
            if d:
                _prefix = 'dest-' + str(i) + '-'
                _type, _dest_name, _user, _host, _port = parse_name(d)
                status[_prefix + 'type'] = _type
                status[_prefix + 'host'] = _host
                if conf['name']:
                    dest_name = fs_name.replace(conf['name'], _dest_name)
                else:
                    dest_name = _dest_name + '/' + fs_name
                status[_prefix + 'name'] = dest_name
                # check snapshots on dest
                common_snapshots = []
                ssh_dest = get_ssh_for_dest(d, conf)
                try:
                    dest_fs = zfs.open(dest_name, ssh=ssh_dest)
                except DatasetNotFoundError:
                    dest_snapshots = []
                    dest_snapnames = []
                    common = set()
                except CalledProcessError as err:
                    message = err.stderr.rstrip()
                    if message.startswith('ssh: '):
                        logger.error(f"Connection issue while opening dest {dest_name:s}: '{message:s}'...")
                        return 2
                    else:
                        logger.error(f"Error while opening dest {dest_name:s}: '{message:s}'...")
                        return 1
                else:
                    # find common snapshots between source & dest
                    dest_snapshots = dest_fs.snapshots()
                    dest_snapnames = [snap.name.split('@')[1] for snap in dest_snapshots]
                    common = set(snapnames) & set(dest_snapnames)
                    if common:
                        common_snapshots = [s for s in snapnames if s in common]
                status[_prefix + 'snapshot-count'] = len(dest_snapnames)
                status[_prefix + 'snapshot-count-common'] = len(common_snapshots)
                if common_snapshots:
                    status[_prefix + 'snapshot-common-first'] = common_snapshots[0]
                    status[_prefix + 'snapshot-common-last'] = common_snapshots[-1]
                if dest_snapnames:
                    status[_prefix + 'snapshot-dest-first'] = dest_snapnames[0]
                    status[_prefix + 'snapshot-dest-last'] = dest_snapnames[-1]
            i += 1

    def add_snapshot_status(snapshot, label):
        props = snapshot.getprops()
        status['snapshot-info-' + label + '-timestamp'] = datetime.fromtimestamp(int(props['creation'][0])).isoformat()
        status['snapshot-info-' + label + '-referenced'] = bytes_fmt_no_raw(int(props['referenced'][0]))
        status['snapshot-info-' + label + '-logicalreferenced'] = bytes_fmt_no_raw(int(props['logicalreferenced'][0]))

    if fs_snapshots:
        add_snapshot_status(fs_snapshots[0], 'first')
        add_snapshot_status(fs_snapshots[-1], 'last')

    props = filesystem.getprops()
    for p in ZFS_SIZE_PROPERTIES:
        status['zfs-' + p] = bytes_fmt_no_raw(int(props[p][0]))
    for p in ZFS_OTHER_PROPERTIES:
        status['zfs-' + p] = props[p][0] if p in props else '---'

    if filter_values:
        for f, v in filter_values.items():
            if status[f] != v:
                return

    if values:
        fstatus = {}
        for v in values:
            for k in tuple(status.keys()):
                if fnmatch(k, v):
                    fstatus[k] = status[k]
                    del status[k]
        status = fstatus

    if output == 'jsonl':
        print(json.dumps(status))
    elif output == 'html':
        OUTPUT.append(status)
    else:
        logger.log(level, 'STATUS: ' + json.dumps(status))


def status_config(
    config, output='log', show_all=False, values=None, filter_values=None, filter_exclude=None, settings=None
):
    """Check snapshots status according to strategies given in config. Goes through each config,
    opens up ssh connection if necessary and then recursively calls status_filesystem.

    Parameters:
    ----------
    config : {list of dict}
        Full config list containing all strategies for different filesystems
    """

    if settings is None:
        settings = {}
    logger = logging.getLogger(__name__)
    logger.info('Checking snapshots...')

    for conf in config:
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
            # status snapshots of parent filesystem - ignore exclude property for top fs
            status_filesystem(
                children[0],
                conf,
                main_fs=True,
                output=output,
                values=values,
                filter_values=filter_values,
                filter_exclude=filter_exclude,
            )
            # status snapshots of all children that don't have a separate config entry
            for child in children[1:]:
                status_filesystem(
                    child,
                    conf,
                    output=output,
                    show_all=show_all,
                    values=values,
                    filter_values=filter_values,
                    filter_exclude=filter_exclude,
                )
        finally:
            if ssh:
                ssh.close()

    close_ssh_dests()

    if output == 'html':
        output_html(OUTPUT, values=values)


def output_html(data, values=None, tabulator=True):
    # gel all cols names
    cols = []
    for d in data:
        i = 0
        for c in d.keys():
            if c not in cols:
                cols.insert(i, c)
            i = cols.index(c) + 1

    # filter col names by values
    if values:
        fcols = []
        for v in values:
            for c in cols:
                if c not in fcols and fnmatch(c, v):
                    fcols.append(c)
        cols = fcols

    print('<html><head>')
    print(f'<title>pyznap {os.uname()[1]}</title>')
    if tabulator:
        print('<link href="https://unpkg.com/tabulator-tables/dist/css/tabulator.min.css" rel="stylesheet">')
        print(
            '<script type="text/javascript" src="https://unpkg.com/tabulator-tables/dist/js/tabulator.min.js"></script>'
        )
    print('</head><body>')
    print('<table id="pyznap" border="1">')
    print('<thead><tr>')
    for c in cols:
        print('<th>' + c + '</th>')
    print('</tr></thead>')
    for d in data:
        print('<tr>')
        for c in cols:
            v = str(d[c]) if c in d else ''
            print('<td>' + v + '</td>')
        print('</tr>')

    print('</table>')
    if tabulator:
        print("""
            <script>
            //define row context menu
            var headerMenu = [
                {
                    label:"Hide Column",
                    action:function(e, column){
                        column.hide();
                    }
                },
            ]
            var table = new Tabulator("#pyznap", {
              tooltipsHeader:true, //enable header tooltips
              tooltips:true,
              history:true,
              headerSortTristate:true, //enable tristate header sort
              selectable:true, //make rows selectable
              movableColumns:true,
              movableRows: true, //enable user movable rows
              autoColumns:true,
              autoColumnsDefinitions:function(definitions){
                    //definitions - array of column definition objects
                    var frozenCols = ['id', 'hostname', 'name'];

                    definitions.forEach((column) => {
                        column.headerMenu = headerMenu; // add header filter to every column
                        column.headerFilter = "input";
                        if (frozenCols.indexOf(column.field)>-1) {
                            column.frozen = true;
                        }
                    });

                    return definitions;
                  },
            });
            </script>
            """)
    print('</body></html>')


SSH_DESTS = {}


def get_ssh_for_dest(dest, conf):
    try:
        _type, fsname, user, host, port = parse_name(dest)
    except ValueError as err:
        logger = logging.getLogger(__name__)
        logger.error(f'Could not parse {dest:s}: {err}...')
        raise

    if _type == 'ssh':
        dest_key = user + '@' + host + ':' + str(port)
        if dest_key in SSH_DESTS:
            return SSH_DESTS[dest_key]
        try:
            ssh = SSH(user, host, port=port, key=conf['key'])
        except (FileNotFoundError, SSHException):
            raise
        SSH_DESTS[dest_key] = ssh
    else:
        ssh = None

    return ssh


def close_ssh_dests():
    global SSH_DESTS
    # close is called on destroy
    # for dest in SSH_DESTS.values():
    #     dest.close()
    SSH_DESTS = {}
