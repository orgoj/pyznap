#!/usr/bin/env pytest -v
"""
pyznap.test_functions_ssh
~~~~~~~~~~~~~~

ssh tests for pyznap functions.

:copyright: (c) 2018-2019 by Yannick Boetzel.
:license: GPLv3, see LICENSE for more details.
"""

import fnmatch
import logging
import os
import subprocess as sp
from datetime import datetime, timedelta
from subprocess import Popen
from tempfile import NamedTemporaryFile

import pytest

import pyznap.pyzfs as zfs
from pyznap.clean import clean_config
from pyznap.process import DatasetNotFoundError, check_output
from pyznap.send import send_config
from pyznap.ssh import SSH
from pyznap.take import take_config
from tests.test_utils import open_ssh, randomword

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%b %d %H:%M:%S')
logger = logging.getLogger(__name__)
logging.getLogger('paramiko').setLevel(logging.ERROR)


# ssh connection to dest
USER = 'root'
HOST = '127.0.0.1'
PORT = 22
KEY = None

ZPOOL = '/sbin/zpool'
_word = randomword(8)
POOL0 = 'pyznap_source_' + _word
POOL1 = 'pyznap_dest_' + _word


@pytest.fixture(scope='module')
def zpools():
    """Creates two temporary zpools to be called from test functions, source is local and dest on
    remote ssh location. Yields the two pool names and destroys them after testing."""

    sftp_filename = '/tmp/' + randomword(10)
    ssh = None
    sshclient = None
    sftp = None
    created_pools = []

    try:
        # ssh arguments for zfs functions
        ssh = SSH(USER, HOST, port=PORT, key=KEY)
        # need paramiko for sftp file
        sshclient = open_ssh(USER, HOST, port=PORT, key=KEY)
        sftp = sshclient.open_sftp()

        # Create temporary file on which the source zpool is created. Manually create sftp file
        with NamedTemporaryFile() as file0, sftp.open(sftp_filename, 'w') as file1:
            filename0 = file0.name
            filename1 = sftp_filename

            # Fix size to 100Mb
            file0.seek(100 * 1024**2 - 1)
            file0.write(b'0')
            file0.seek(0)
            file1.seek(100 * 1024**2 - 1)
            file1.write(b'0')
            file1.seek(0)

            # Create temporary test pools
            try:
                check_output([ZPOOL, 'create', POOL0, filename0])
                created_pools.append((POOL0, None))
            except sp.CalledProcessError as err:
                logger.error(err)
                return

            try:
                check_output([ZPOOL, 'create', POOL1, filename1], ssh=ssh)
                created_pools.append((POOL1, ssh))
            except sp.CalledProcessError as err:
                logger.error(err)
                return

            try:
                fs0 = zfs.open(POOL0)
                fs1 = zfs.open(POOL1, ssh=ssh)
                assert fs0.name == POOL0
                assert fs1.name == POOL1
            except (DatasetNotFoundError, AssertionError, Exception) as err:
                logger.error(err)
            else:
                yield fs0, fs1

    finally:
        # Destroy temporary test pools (always runs)
        for pool, pool_ssh in created_pools:
            try:
                check_output([ZPOOL, 'destroy', pool], ssh=pool_ssh)
            except sp.CalledProcessError as err:
                logger.error(err)

        # Delete tempfile on dest and close connections
        if sftp:
            try:
                sftp.remove(sftp_filename)
            except Exception:
                pass
            try:
                sftp.close()
            except Exception:
                pass
        if ssh:
            try:
                ssh.close()
            except Exception:
                pass
        if sshclient:
            try:
                sshclient.close()
            except Exception:
                pass


class TestSnapshot:
    @pytest.mark.dependency()
    def test_take_snapshot(self, zpools):
        _, fs = zpools

        config = [
            {
                'name': f'ssh:{PORT:d}:{fs}',
                'key': KEY,
                'frequent': 1,
                'hourly': 1,
                'daily': 1,
                'weekly': 1,
                'monthly': 1,
                'yearly': 1,
                'snap': True,
            }
        ]
        take_config(config)
        take_config(config)

        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in fs.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == 1

    @pytest.mark.dependency(depends=['TestSnapshot::test_take_snapshot'])
    def test_clean_snapshot(self, zpools):
        _, fs = zpools

        config = [
            {
                'name': f'ssh:{PORT:d}:{fs}',
                'key': KEY,
                'frequent': 0,
                'hourly': 0,
                'daily': 0,
                'weekly': 0,
                'monthly': 0,
                'yearly': 0,
                'clean': True,
            }
        ]
        clean_config(config)

        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in fs.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[0][snap_type]

    @pytest.mark.dependency(depends=['TestSnapshot::test_clean_snapshot'])
    def test_take_snapshot_recursive(self, zpools):
        _, fs = zpools
        ssh = fs.ssh

        fs.destroy(force=True)
        config = [
            {
                'name': f'ssh:{PORT:d}:{fs}',
                'key': KEY,
                'frequent': 1,
                'hourly': 1,
                'daily': 1,
                'weekly': 1,
                'monthly': 1,
                'yearly': 1,
                'snap': True,
            }
        ]
        take_config(config)
        fs.snapshots()[-1].destroy(force=True)
        fs.snapshots()[-1].destroy(force=True)

        sub1 = zfs.create(f'{fs.name:s}/sub1', ssh=ssh)
        abc = zfs.create(f'{fs.name:s}/sub1/abc', ssh=ssh)
        sub1_abc = zfs.create(f'{fs.name:s}/sub1_abc', ssh=ssh)
        config += [
            {
                'name': f'ssh:{PORT:d}:{fs}/sub1',
                'key': KEY,
                'frequent': 1,
                'hourly': 1,
                'daily': 1,
                'weekly': 1,
                'monthly': 1,
                'yearly': 1,
                'snap': False,
            }
        ]
        take_config(config)

        # Check fs
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in fs.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[0][snap_type]

        # Check sub1
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in sub1.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[0][snap_type]

        # Check abc
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in abc.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[0][snap_type]

        # Check sub1_abc
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in sub1_abc.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[0][snap_type]

    @pytest.mark.dependency(depends=['TestSnapshot::test_take_snapshot_recursive'])
    def test_clean_recursive(self, zpools):
        _, fs = zpools
        ssh = fs.ssh

        fs.destroy(force=True)
        sub1 = zfs.create(f'{fs.name:s}/sub1', ssh=ssh)
        abc = zfs.create(f'{fs.name:s}/sub1/abc', ssh=ssh)
        abc_efg = zfs.create(f'{fs.name:s}/sub1/abc_efg', ssh=ssh)
        sub2 = zfs.create(f'{fs.name:s}/sub2', ssh=ssh)
        efg = zfs.create(f'{fs.name:s}/sub2/efg', ssh=ssh)
        hij = zfs.create(f'{fs.name:s}/sub2/efg/hij', ssh=ssh)
        klm = zfs.create(f'{fs.name:s}/sub2/efg/hij/klm', ssh=ssh)
        sub3 = zfs.create(f'{fs.name:s}/sub3', ssh=ssh)

        config = [
            {
                'name': f'ssh:{PORT:d}:{fs}',
                'key': KEY,
                'frequent': 1,
                'hourly': 1,
                'daily': 1,
                'weekly': 1,
                'monthly': 1,
                'yearly': 1,
                'snap': True,
            }
        ]
        take_config(config)

        config = [
            {
                'name': f'ssh:{PORT:d}:{fs}',
                'key': KEY,
                'frequent': 1,
                'hourly': 0,
                'daily': 1,
                'weekly': 0,
                'monthly': 0,
                'yearly': 0,
                'clean': True,
                '_parent': None,
            },
            {
                'name': f'ssh:{PORT:d}:{fs}/sub2',
                'key': KEY,
                'frequent': 0,
                'hourly': 1,
                'daily': 0,
                'weekly': 1,
                'monthly': 0,
                'yearly': 1,
                'clean': True,
                '_parent': f'ssh:{PORT:d}:{fs}',
            },
            {
                'name': f'ssh:{PORT:d}:{fs}/sub3',
                'key': KEY,
                'frequent': 1,
                'hourly': 0,
                'daily': 1,
                'weekly': 0,
                'monthly': 1,
                'yearly': 0,
                'clean': False,
                '_parent': f'ssh:{PORT:d}:{fs}',
            },
            {
                'name': f'ssh:{PORT:d}:{fs}/sub1/abc',
                'key': KEY,
                'frequent': 0,
                'hourly': 0,
                'daily': 0,
                'weekly': 1,
                'monthly': 1,
                'yearly': 1,
                'clean': True,
                '_parent': f'ssh:{PORT:d}:{fs}',
            },
            {
                'name': f'ssh:{PORT:d}:{fs}/sub2/efg/hij',
                'key': KEY,
                'frequent': 0,
                'hourly': 0,
                'daily': 0,
                'weekly': 0,
                'monthly': 0,
                'yearly': 0,
                'clean': True,
                '_parent': f'ssh:{PORT:d}:{fs}/sub2',
            },
        ]
        clean_config(config)

        # Check parent filesystem
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in fs.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[0][snap_type]
        # Check sub1
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in sub1.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[0][snap_type]
        # Check sub1/abc
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in abc.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[3][snap_type]
        # Check sub1/abc_efg
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in abc_efg.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[0][snap_type]
        # Check sub2
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in sub2.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[1][snap_type]
        # Check sub2/efg
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in efg.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[1][snap_type]
        # Check sub2/efg/hij
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in hij.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[4][snap_type]
        # Check sub2/efg/hij/klm
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in klm.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == config[4][snap_type]
        # Check sub3
        snapshots = {'frequent': [], 'hourly': [], 'daily': [], 'weekly': [], 'monthly': [], 'yearly': []}
        for snap in sub3.snapshots():
            snap_type = snap.name.split('_')[-1]
            snapshots[snap_type].append(snap)

        for snap_type, snaps in snapshots.items():
            assert len(snaps) == 1


class TestSending:
    @pytest.mark.dependency()
    def test_send_full(self, zpools):
        """Checks if send_snap totally replicates a filesystem"""
        fs0, fs1 = zpools
        ssh = fs1.ssh

        fs0.destroy(force=True)
        fs1.destroy(force=True)

        fs0.snapshot('snap0')
        zfs.create(f'{fs0.name:s}/sub1')
        fs0.snapshot('snap1', recursive=True)
        zfs.create(f'{fs0.name:s}/sub2')
        fs0.snapshot('snap2', recursive=True)
        zfs.create(f'{fs0.name:s}/sub3')
        fs0.snapshot('snap3', recursive=True)
        fs0.snapshot('snap4', recursive=True)
        fs0.snapshot('snap5', recursive=True)
        zfs.create(f'{fs0.name:s}/sub3/abc')
        fs0.snapshot('snap6', recursive=True)
        zfs.create(f'{fs0.name:s}/sub3/abc_abc')
        fs0.snapshot('snap7', recursive=True)
        zfs.create(f'{fs0.name:s}/sub3/efg')
        fs0.snapshot('snap8', recursive=True)
        fs0.snapshot('snap9', recursive=True)
        config = [
            {
                'name': fs0.name,
                'dest': [f'ssh:{PORT:d}:{fs1}'],
                'dest_keys': [KEY],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)

        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'], ssh=ssh)[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

    @pytest.mark.dependency(depends=['TestSending::test_send_full'])
    def test_send_incremental(self, zpools):
        fs0, fs1 = zpools
        ssh = fs1.ssh

        fs0.destroy(force=True)
        fs1.destroy(force=True)

        fs0.snapshot('snap0', recursive=True)
        zfs.create(f'{fs0.name:s}/sub1')
        fs0.snapshot('snap1', recursive=True)
        config = [
            {
                'name': fs0.name,
                'dest': [f'ssh:{PORT:d}:{fs1}'],
                'dest_keys': [KEY],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'], ssh=ssh)[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

        zfs.create(f'{fs0.name:s}/sub2')
        fs0.snapshot('snap2', recursive=True)
        config = [
            {
                'name': fs0.name,
                'dest': [f'ssh:{PORT:d}:{fs1}'],
                'dest_keys': [KEY],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'], ssh=ssh)[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

        zfs.create(f'{fs0.name:s}/sub3')
        fs0.snapshot('snap3', recursive=True)
        config = [
            {
                'name': fs0.name,
                'dest': [f'ssh:{PORT:d}:{fs1}'],
                'dest_keys': [KEY],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'], ssh=ssh)[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

    @pytest.mark.dependency(depends=['TestSending::test_send_incremental'])
    def test_send_catchup_intermediates(self, zpools):
        """Verifies that ALL intermediate snapshots are transferred when dest is behind.

        Scenario:
        1. Source and dest both have snap0
        2. Source creates snap1, snap2, snap3, snap4 (dest has none of these)
        3. Single send_config call should transfer ALL intermediates
        4. Explicitly verify each snapshot exists on destination
        """
        fs0, fs1 = zpools
        ssh = fs1.ssh

        fs0.destroy(force=True)
        fs1.destroy(force=True)

        # Initial sync - both have snap0
        fs0.snapshot('snap0', recursive=True)
        config = [
            {
                'name': fs0.name,
                'dest': [f'ssh:{PORT:d}:{fs1}'],
                'dest_keys': [KEY],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)

        # Verify initial sync
        fs1_snaps = [s.name.split('@')[1] for s in zfs.find(fs1.name, types=['snapshot'], ssh=ssh)]
        assert 'snap0' in fs1_snaps

        # Source creates multiple snapshots while dest is unchanged
        fs0.snapshot('snap1', recursive=True)
        fs0.snapshot('snap2', recursive=True)
        fs0.snapshot('snap3', recursive=True)
        fs0.snapshot('snap4', recursive=True)

        # Single send should transfer ALL intermediates (snap1, snap2, snap3, snap4)
        config = [
            {
                'name': fs0.name,
                'dest': [f'ssh:{PORT:d}:{fs1}'],
                'dest_keys': [KEY],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)

        # Explicitly verify EACH snapshot exists on destination
        fs1_snaps = [s.name.split('@')[1] for s in zfs.find(fs1.name, types=['snapshot'], ssh=ssh)]
        assert 'snap0' in fs1_snaps, 'snap0 (base) missing on dest'
        assert 'snap1' in fs1_snaps, 'snap1 (intermediate) missing on dest'
        assert 'snap2' in fs1_snaps, 'snap2 (intermediate) missing on dest'
        assert 'snap3' in fs1_snaps, 'snap3 (intermediate) missing on dest'
        assert 'snap4' in fs1_snaps, 'snap4 (latest) missing on dest'

        # Also verify count matches
        fs0_snaps = [s.name.split('@')[1] for s in zfs.find(fs0.name, types=['snapshot'])]
        assert len(fs0_snaps) == len(
            fs1_snaps
        ), f'Snapshot count mismatch: source={len(fs0_snaps)}, dest={len(fs1_snaps)}'

    @pytest.mark.dependency(depends=['TestSending::test_send_catchup_intermediates'])
    def test_send_delete_snapshot(self, zpools):
        fs0, fs1 = zpools
        ssh = fs1.ssh

        # Delete recent snapshots on dest
        fs1.snapshots()[-1].destroy(force=True)
        fs1.snapshots()[-1].destroy(force=True)
        config = [
            {
                'name': fs0.name,
                'dest': [f'ssh:{PORT:d}:{fs1}'],
                'dest_keys': [KEY],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'], ssh=ssh)[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

        # Delete recent snapshot on source
        fs0.snapshot('snap4', recursive=True)
        send_config(config)
        fs0.snapshots()[-1].destroy(force=True)
        fs0.snapshot('snap5', recursive=True)
        config = [
            {
                'name': fs0.name,
                'dest': [f'ssh:{PORT:d}:{fs1}'],
                'dest_keys': [KEY],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'], ssh=ssh)[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

    @pytest.mark.dependency(depends=['TestSending::test_send_delete_snapshot'])
    def test_send_delete_sub(self, zpools):
        fs0, fs1 = zpools
        ssh = fs1.ssh

        # Delete subfilesystems
        sub3 = fs1.filesystems()[-1]
        sub3.destroy(force=True)
        fs0.snapshot('snap6', recursive=True)
        sub2 = fs1.filesystems()[-1]
        sub2.destroy(force=True)
        config = [
            {
                'name': fs0.name,
                'dest': [f'ssh:{PORT:d}:{fs1}'],
                'dest_keys': [KEY],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'], ssh=ssh)[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

    @pytest.mark.dependency(depends=['TestSending::test_send_delete_sub'])
    def test_send_delete_old(self, zpools):
        fs0, fs1 = zpools
        ssh = fs1.ssh

        # Delete old snapshot on source
        fs0.snapshots()[0].destroy(force=True)
        fs0.snapshot('snap7', recursive=True)
        config = [
            {
                'name': fs0.name,
                'dest': [f'ssh:{PORT:d}:{fs1}'],
                'dest_keys': [KEY],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'], ssh=ssh)[1:]]
        assert not (set(fs0_children) == set(fs1_children))
        # Assert that snap0 was not deleted from fs1
        for child in set(fs1_children) - set(fs0_children):
            assert child.endswith('snap0')

    @pytest.mark.dependency()
    def test_send_exclude(self, zpools):
        """Checks if send_snap totally replicates a filesystem"""
        fs0, fs1 = zpools
        ssh = fs1.ssh
        fs0.destroy(force=True)
        fs1.destroy(force=True)

        exclude = ['*/sub1', '*/sub3/abc', '*/sub3/efg']
        config = [
            {'name': fs0.name, 'dest': [f'ssh:{PORT:d}:{fs1}'], 'exclude': [exclude], 'dest_auto_create': ['yes']}
        ]

        zfs.create(f'{fs0.name:s}/sub1')
        zfs.create(f'{fs0.name:s}/sub2')
        zfs.create(f'{fs0.name:s}/sub3')
        zfs.create(f'{fs0.name:s}/sub3/abc')
        zfs.create(f'{fs0.name:s}/sub3/abc_abc')
        zfs.create(f'{fs0.name:s}/sub3/efg')
        fs0.snapshot('snap', recursive=True)
        send_config(config)

        fs0_children = set([child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]])
        fs1_children = set(
            [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'], ssh=ssh)[1:]]
        )
        # remove unwanted datasets/snapshots
        for match in exclude:
            fs0_children -= set(fnmatch.filter(fs0_children, match))
            fs0_children -= set(fnmatch.filter(fs0_children, match + '@snap'))

        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

    @pytest.mark.dependency()
    def test_send_compress(self, zpools):
        """Checks if send_snap totally replicates a filesystem"""
        fs0, fs1 = zpools
        ssh = fs1.ssh

        fs0.destroy(force=True)
        fs1.destroy(force=True)

        fs0.snapshot('snap0')
        zfs.create(f'{fs0.name:s}/sub1')
        fs0.snapshot('snap1', recursive=True)
        zfs.create(f'{fs0.name:s}/sub2')
        fs0.snapshot('snap2', recursive=True)
        fs0.snapshot('snap3', recursive=True)
        zfs.create(f'{fs0.name:s}/sub2/abc')
        fs0.snapshot('snap4', recursive=True)
        fs0.snapshot('snap5', recursive=True)

        for compression in ['none', 'abc', 'lzop', 'gzip', 'pigz', 'bzip2', 'xz', 'lz4']:
            fs1.destroy(force=True)
            config = [
                {
                    'name': fs0.name,
                    'dest': [f'ssh:{PORT:d}:{fs1}'],
                    'dest_keys': [KEY],
                    'compress': [compression],
                    'dest_auto_create': ['yes'],
                }
            ]
            send_config(config)

            fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
            fs1_children = [
                child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'], ssh=ssh)[1:]
            ]
            assert (
                set(fs0_children) == set(fs1_children)
            ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'


class TestSendingPull:
    """Checks if snapshots can be pulled from a remote source"""

    @pytest.mark.dependency()
    def test_send_full(self, zpools):
        """Checks if send_snap totally replicates a filesystem"""
        fs1, fs0 = zpools  # here fs0 is the remote pool
        ssh = fs0.ssh

        fs0.destroy(force=True)
        fs1.destroy(force=True)

        fs0.snapshot('snap0')
        zfs.create(f'{fs0.name:s}/sub1', ssh=ssh)
        fs0.snapshot('snap1', recursive=True)
        zfs.create(f'{fs0.name:s}/sub2', ssh=ssh)
        fs0.snapshot('snap2', recursive=True)
        zfs.create(f'{fs0.name:s}/sub3', ssh=ssh)
        fs0.snapshot('snap3', recursive=True)
        fs0.snapshot('snap4', recursive=True)
        fs0.snapshot('snap5', recursive=True)
        zfs.create(f'{fs0.name:s}/sub3/abc', ssh=ssh)
        fs0.snapshot('snap6', recursive=True)
        zfs.create(f'{fs0.name:s}/sub3/abc_abc', ssh=ssh)
        fs0.snapshot('snap7', recursive=True)
        zfs.create(f'{fs0.name:s}/sub3/efg', ssh=ssh)
        fs0.snapshot('snap8', recursive=True)
        fs0.snapshot('snap9', recursive=True)
        config = [
            {
                'name': f'ssh:{PORT:d}:{fs0}',
                'key': KEY,
                'dest': [fs1.name],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)

        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'], ssh=ssh)[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

    @pytest.mark.dependency(depends=['TestSendingPull::test_send_full'])
    def test_send_incremental(self, zpools):
        fs1, fs0 = zpools  # here fs0 is the remote pool
        ssh = fs0.ssh

        fs0.destroy(force=True)
        fs1.destroy(force=True)

        fs0.snapshot('snap0', recursive=True)
        zfs.create(f'{fs0.name:s}/sub1', ssh=ssh)
        fs0.snapshot('snap1', recursive=True)
        config = [
            {
                'name': f'ssh:{PORT:d}:{fs0}',
                'key': KEY,
                'dest': [fs1.name],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'], ssh=ssh)[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

        zfs.create(f'{fs0.name:s}/sub2', ssh=ssh)
        fs0.snapshot('snap2', recursive=True)
        config = [
            {
                'name': f'ssh:{PORT:d}:{fs0}',
                'key': KEY,
                'dest': [fs1.name],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'], ssh=ssh)[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

        zfs.create(f'{fs0.name:s}/sub3', ssh=ssh)
        fs0.snapshot('snap3', recursive=True)
        config = [
            {
                'name': f'ssh:{PORT:d}:{fs0}',
                'key': KEY,
                'dest': [fs1.name],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'], ssh=ssh)[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

    @pytest.mark.dependency(depends=['TestSendingPull::test_send_incremental'])
    def test_send_all_pyznap_snapshots_at_once(self, zpools):
        """Verifies that ALL pyznap snapshots are transferred in a single send (pull mode).

        Scenario:
        1. SSH source creates 5 pyznap hourly snapshots (dest has nothing)
        2. Single send_config call transfers ALL snapshots from first to last
        3. Verify each pyznap snapshot exists on destination
        """
        fs1, fs0 = zpools  # fs0 = remote SSH source, fs1 = local dest
        ssh = fs0.ssh

        fs0.destroy(force=True)
        fs1.destroy(force=True)

        # Create temp config file for pyznap CLI (snapshots on SSH source)
        with NamedTemporaryFile('w', suffix='.conf', delete=False) as conf_file:
            conf_file.write(f'[ssh:{PORT}:{USER}@{HOST}:{fs0.name}]\n')
            if KEY:
                conf_file.write(f'key = {KEY}\n')
            conf_file.write('frequent = 0\nhourly = 10\ndaily = 0\nweekly = 0\nmonthly = 0\nyearly = 0\nsnap = yes\n')
            conf_file.flush()
            config_path = conf_file.name

        try:
            # Create ALL 5 pyznap hourly snapshots on SSH source BEFORE any send
            NUM_SNAPSHOTS = 5
            start_time = datetime(2025, 1, 1, 10, 0, 0)
            for i in range(NUM_SNAPSHOTS):
                snap_time = start_time + timedelta(hours=i)
                faketime_cmd = ['faketime', snap_time.strftime('%Y-%m-%d %H:%M:%S')]
                pyznap_snap = faketime_cmd + ['pyznap', '--config', config_path, 'snap']
                proc = Popen(pyznap_snap, stdout=sp.PIPE, stderr=sp.PIPE)
                stdout, stderr = proc.communicate(timeout=60)
                assert proc.returncode == 0, f'pyznap snap failed at {snap_time}: {stderr.decode()}'

            # Verify SSH source has expected snapshots
            fs0_snaps = [s.name.split('@')[1] for s in zfs.find(fs0.name, types=['snapshot'], ssh=ssh)]
            assert (
                len(fs0_snaps) == NUM_SNAPSHOTS
            ), f'Expected {NUM_SNAPSHOTS} snapshots on source, got {len(fs0_snaps)}: {fs0_snaps}'

            # Verify dest has NO snapshots yet
            fs1_snaps_before = list(zfs.find(fs1.name, types=['snapshot']))
            assert len(fs1_snaps_before) == 0, f'Dest should be empty, has {len(fs1_snaps_before)} snapshots'

            # ONE send_config call transfers ALL snapshots from first to last
            send_cfg = [
                {
                    'name': f'ssh:{PORT:d}:{fs0}',
                    'key': KEY,
                    'dest': [fs1.name],
                    'compress': None,
                    'dest_auto_create': ['yes'],
                }
            ]
            send_config(send_cfg)

            # Verify ALL pyznap snapshots exist on destination
            fs1_snaps = [s.name.split('@')[1] for s in zfs.find(fs1.name, types=['snapshot'])]
            assert (
                len(fs1_snaps) == NUM_SNAPSHOTS
            ), f'Expected {NUM_SNAPSHOTS} snapshots on dest, got {len(fs1_snaps)}: {fs1_snaps}'

            # Verify each is a pyznap hourly snapshot
            for snap in fs1_snaps:
                assert 'pyznap_' in snap, f'Not a pyznap snapshot: {snap}'
                assert '_hourly' in snap, f'Not an hourly snapshot: {snap}'

            # Verify source and dest have identical snapshots
            assert set(fs0_snaps) == set(
                fs1_snaps
            ), f'Snapshot mismatch:\n  source: {sorted(fs0_snaps)}\n  dest: {sorted(fs1_snaps)}'

        finally:
            os.unlink(config_path)

    @pytest.mark.dependency(depends=['TestSendingPull::test_send_all_pyznap_snapshots_at_once'])
    def test_send_delete_snapshot(self, zpools):
        fs1, fs0 = zpools  # here fs0 is the remote pool
        ssh = fs0.ssh

        # Delete recent snapshots on dest
        fs1.snapshots()[-1].destroy(force=True)
        fs1.snapshots()[-1].destroy(force=True)
        config = [
            {
                'name': f'ssh:{PORT:d}:{fs0}',
                'key': KEY,
                'dest': [fs1.name],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'], ssh=ssh)[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

        # Delete recent snapshot on source
        fs0.snapshot('snap4', recursive=True)
        send_config(config)
        fs0.snapshots()[-1].destroy(force=True)
        fs0.snapshot('snap5', recursive=True)
        config = [
            {
                'name': f'ssh:{PORT:d}:{fs0}',
                'key': KEY,
                'dest': [fs1.name],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'], ssh=ssh)[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

    @pytest.mark.dependency(depends=['TestSendingPull::test_send_delete_snapshot'])
    def test_send_delete_sub(self, zpools):
        fs1, fs0 = zpools  # here fs0 is the remote pool
        ssh = fs0.ssh

        # Delete subfilesystems
        sub3 = fs1.filesystems()[-1]
        sub3.destroy(force=True)
        fs0.snapshot('snap6', recursive=True)
        sub2 = fs1.filesystems()[-1]
        sub2.destroy(force=True)
        config = [
            {
                'name': f'ssh:{PORT:d}:{fs0}',
                'key': KEY,
                'dest': [fs1.name],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'], ssh=ssh)[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

    @pytest.mark.dependency(depends=['TestSendingPull::test_send_delete_sub'])
    def test_send_delete_old(self, zpools):
        fs1, fs0 = zpools  # here fs0 is the remote pool
        ssh = fs0.ssh

        # Delete old snapshot on source
        fs0.snapshots()[0].destroy(force=True)
        fs0.snapshot('snap7', recursive=True)
        config = [
            {
                'name': f'ssh:{PORT:d}:{fs0}',
                'key': KEY,
                'dest': [fs1.name],
                'compress': None,
                'dest_auto_create': ['yes'],
            }
        ]
        send_config(config)
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'], ssh=ssh)[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert not (set(fs0_children) == set(fs1_children))
        # Assert that snap0 was not deleted from fs1
        for child in set(fs1_children) - set(fs0_children):
            assert child.endswith('snap0')

    @pytest.mark.dependency()
    def test_send_exclude(self, zpools):
        """Checks if send_snap totally replicates a filesystem"""
        fs1, fs0 = zpools  # here fs0 is the remote pool
        ssh = fs0.ssh
        fs0.destroy(force=True)
        fs1.destroy(force=True)

        exclude = ['*/sub1', '*/sub3/abc', '*/sub3/efg']
        config = [
            {'name': f'ssh:{PORT:d}:{fs0}', 'dest': [fs1.name], 'exclude': [exclude], 'dest_auto_create': ['yes']}
        ]

        zfs.create(f'{fs0.name:s}/sub1', ssh=ssh)
        zfs.create(f'{fs0.name:s}/sub2', ssh=ssh)
        zfs.create(f'{fs0.name:s}/sub3', ssh=ssh)
        zfs.create(f'{fs0.name:s}/sub3/abc', ssh=ssh)
        zfs.create(f'{fs0.name:s}/sub3/abc_abc', ssh=ssh)
        zfs.create(f'{fs0.name:s}/sub3/efg', ssh=ssh)
        fs0.snapshot('snap', recursive=True)
        send_config(config)

        fs0_children = set(
            [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'], ssh=ssh)[1:]]
        )
        fs1_children = set([child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]])
        # remove unwanted datasets/snapshots
        for match in exclude:
            fs0_children -= set(fnmatch.filter(fs0_children, match))
            fs0_children -= set(fnmatch.filter(fs0_children, match + '@snap'))

        assert (
            set(fs0_children) == set(fs1_children)
        ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'

    @pytest.mark.dependency()
    def test_send_compress(self, zpools):
        """Checks if send_snap totally replicates a filesystem"""
        fs1, fs0 = zpools  # here fs0 is the remote pool
        ssh = fs0.ssh

        fs0.destroy(force=True)
        fs1.destroy(force=True)

        fs0.snapshot('snap0')
        zfs.create(f'{fs0.name:s}/sub1', ssh=ssh)
        fs0.snapshot('snap1', recursive=True)
        zfs.create(f'{fs0.name:s}/sub2', ssh=ssh)
        fs0.snapshot('snap2', recursive=True)
        fs0.snapshot('snap3', recursive=True)
        zfs.create(f'{fs0.name:s}/sub2/abc', ssh=ssh)
        fs0.snapshot('snap4', recursive=True)
        fs0.snapshot('snap5', recursive=True)

        for compression in ['none', 'lzop', 'lz4']:
            fs1.destroy(force=True)
            config = [
                {
                    'name': f'ssh:{PORT:d}:{fs0}',
                    'key': KEY,
                    'dest': [fs1.name],
                    'compress': [compression],
                    'dest_auto_create': ['yes'],
                }
            ]
            send_config(config)

            fs0_children = [
                child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'], ssh=ssh)[1:]
            ]
            fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
            assert (
                set(fs0_children) == set(fs1_children)
            ), f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'
