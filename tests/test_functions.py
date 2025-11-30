#!/usr/bin/env pytest -v
"""
pyznap.test_functions
~~~~~~~~~~~~~~

Tests for pyznap functions.

:copyright: (c) 2018-2019 by Yannick Boetzel.
:license: GPLv3, see LICENSE for more details.
"""

import fnmatch
import logging
import subprocess as sp
from tempfile import NamedTemporaryFile

import pytest

import pyznap.pyzfs as zfs
from pyznap.clean import clean_config
from pyznap.process import DatasetNotFoundError
from pyznap.send import send_config
from pyznap.take import take_config
from tests.test_utils import randomword

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%b %d %H:%M:%S')
logger = logging.getLogger(__name__)


ZPOOL = '/sbin/zpool'
_word = randomword(8)
POOL0 = 'pyznap_source_' + _word
POOL1 = 'pyznap_dest_' + _word


@pytest.fixture(scope='module')
def zpools():
    """Creates two temporary zpools to be called from test functions. Yields the two pool names
    and destroys them after testing."""

    created_pools = []

    # Create temporary files on which the zpools are created
    with NamedTemporaryFile() as file0, NamedTemporaryFile() as file1:
        filename0 = file0.name
        filename1 = file1.name

        # Fix size to 100Mb
        file0.seek(100 * 1024**2 - 1)
        file0.write(b'0')
        file0.seek(0)
        file1.seek(100 * 1024**2 - 1)
        file1.write(b'0')
        file1.seek(0)

        try:
            # Create temporary test pools
            for pool, filename in zip([POOL0, POOL1], [filename0, filename1]):
                try:
                    sp.check_call([ZPOOL, 'create', pool, filename])
                    created_pools.append(pool)
                except sp.CalledProcessError as err:
                    logger.error(err)
                    return

            try:
                fs0 = zfs.open(POOL0)
                fs1 = zfs.open(POOL1)
                assert fs0.name == POOL0
                assert fs1.name == POOL1
            except (DatasetNotFoundError, AssertionError, Exception) as err:
                logger.error(err)
            else:
                yield fs0, fs1

        finally:
            # Destroy temporary test pools (always runs)
            for pool in created_pools:
                try:
                    sp.check_call([ZPOOL, 'destroy', pool])
                except sp.CalledProcessError as err:
                    logger.error(err)


class TestSnapshot:
    @pytest.mark.dependency()
    def test_take_snapshot(self, zpools):
        fs, _ = zpools
        config = [
            {
                'name': fs.name,
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
        fs, _ = zpools
        config = [
            {
                'name': fs.name,
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
        fs, _ = zpools
        fs.destroy(force=True)
        config = [
            {
                'name': fs.name,
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

        sub1 = zfs.create(f'{fs.name:s}/sub1')
        abc = zfs.create(f'{fs.name:s}/sub1/abc')
        sub1_abc = zfs.create(f'{fs.name:s}/sub1_abc')
        config += [
            {
                'name': f'{fs}/sub1',
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
        fs, _ = zpools
        fs.destroy(force=True)
        sub1 = zfs.create(f'{fs.name:s}/sub1')
        abc = zfs.create(f'{fs.name:s}/sub1/abc')
        abc_efg = zfs.create(f'{fs.name:s}/sub1/abc_efg')
        sub2 = zfs.create(f'{fs.name:s}/sub2')
        efg = zfs.create(f'{fs.name:s}/sub2/efg')
        hij = zfs.create(f'{fs.name:s}/sub2/efg/hij')
        klm = zfs.create(f'{fs.name:s}/sub2/efg/hij/klm')
        sub3 = zfs.create(f'{fs.name:s}/sub3')

        config = [
            {
                'name': fs.name,
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
                'name': fs.name,
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
                'name': f'{fs}/sub2',
                'frequent': 0,
                'hourly': 1,
                'daily': 0,
                'weekly': 1,
                'monthly': 0,
                'yearly': 1,
                'clean': True,
                '_parent': fs.name,
            },
            {
                'name': f'{fs}/sub3',
                'frequent': 1,
                'hourly': 0,
                'daily': 1,
                'weekly': 0,
                'monthly': 1,
                'yearly': 0,
                'clean': False,
                '_parent': fs.name,
            },
            {
                'name': f'{fs}/sub1/abc',
                'frequent': 0,
                'hourly': 0,
                'daily': 0,
                'weekly': 1,
                'monthly': 1,
                'yearly': 1,
                'clean': True,
                '_parent': fs.name,
            },
            {
                'name': f'{fs}/sub2/efg/hij',
                'frequent': 0,
                'hourly': 0,
                'daily': 0,
                'weekly': 0,
                'monthly': 0,
                'yearly': 0,
                'clean': True,
                '_parent': f'{fs}/sub2',
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
        fs0.destroy(force=True)
        fs1.destroy(force=True)
        config = [{'name': fs0.name, 'dest': [fs1.name], 'dest_auto_create': ['yes']}]

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
        send_config(config)

        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert set(fs0_children) == set(fs1_children), (
            f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'
        )

    @pytest.mark.dependency(depends=['TestSending::test_send_full'])
    def test_send_incremental(self, zpools):
        fs0, fs1 = zpools
        fs0.destroy(force=True)
        fs1.destroy(force=True)

        def make_config():
            return [{'name': fs0.name, 'dest': [fs1.name], 'dest_auto_create': ['yes']}]

        fs0.snapshot('snap0', recursive=True)
        zfs.create(f'{fs0.name:s}/sub1')
        fs0.snapshot('snap1', recursive=True)
        send_config(make_config())
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert set(fs0_children) == set(fs1_children), (
            f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'
        )

        zfs.create(f'{fs0.name:s}/sub2')
        fs0.snapshot('snap2', recursive=True)
        send_config(make_config())
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert set(fs0_children) == set(fs1_children), (
            f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'
        )

        zfs.create(f'{fs0.name:s}/sub3')
        fs0.snapshot('snap3', recursive=True)
        send_config(make_config())
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert set(fs0_children) == set(fs1_children), (
            f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'
        )

    @pytest.mark.dependency(depends=['TestSending::test_send_incremental'])
    def test_send_delete_snapshot(self, zpools):
        fs0, fs1 = zpools

        def make_config():
            return [{'name': fs0.name, 'dest': [fs1.name], 'dest_auto_create': ['yes']}]

        # Delete recent snapshots on dest
        fs1.snapshots()[-1].destroy(force=True)
        fs1.snapshots()[-1].destroy(force=True)
        send_config(make_config())
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert set(fs0_children) == set(fs1_children), (
            f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'
        )

        # Delete recent snapshot on source
        fs0.snapshot('snap4', recursive=True)
        send_config(make_config())
        fs0.snapshots()[-1].destroy(force=True)
        fs0.snapshot('snap5', recursive=True)
        send_config(make_config())
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert set(fs0_children) == set(fs1_children), (
            f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'
        )

    @pytest.mark.dependency(depends=['TestSending::test_send_delete_snapshot'])
    def test_send_delete_sub(self, zpools):
        fs0, fs1 = zpools

        def make_config():
            return [{'name': fs0.name, 'dest': [fs1.name], 'dest_auto_create': ['yes']}]

        # Delete subfilesystems
        sub3 = fs1.filesystems()[-1]
        sub3.destroy(force=True)
        fs0.snapshot('snap6', recursive=True)
        sub2 = fs1.filesystems()[-1]
        sub2.destroy(force=True)
        send_config(make_config())
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert set(fs0_children) == set(fs1_children), (
            f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'
        )

    @pytest.mark.dependency(depends=['TestSending::test_send_delete_sub'])
    def test_send_delete_old(self, zpools):
        fs0, fs1 = zpools

        def make_config():
            return [{'name': fs0.name, 'dest': [fs1.name], 'dest_auto_create': ['yes']}]

        # Delete old snapshot on source
        fs0.snapshots()[0].destroy(force=True)
        fs0.snapshot('snap7', recursive=True)
        send_config(make_config())
        fs0_children = [child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]]
        fs1_children = [child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]]
        assert not (set(fs0_children) == set(fs1_children))
        # Assert that snap0 was not deleted from fs1
        for child in set(fs1_children) - set(fs0_children):
            assert child.endswith('snap0')

    @pytest.mark.dependency()
    def test_send_exclude(self, zpools):
        """Checks if exclude rules work"""
        fs0, fs1 = zpools
        fs0.destroy(force=True)
        fs1.destroy(force=True)

        exclude = ['*/sub1', '*/sub3/abc', '*/sub3/efg']
        config = [{'name': fs0.name, 'dest': [fs1.name], 'exclude': [exclude], 'dest_auto_create': ['yes']}]

        zfs.create(f'{fs0.name:s}/sub1')
        zfs.create(f'{fs0.name:s}/sub2')
        zfs.create(f'{fs0.name:s}/sub3')
        zfs.create(f'{fs0.name:s}/sub3/abc')
        zfs.create(f'{fs0.name:s}/sub3/abc_abc')
        zfs.create(f'{fs0.name:s}/sub3/efg')
        fs0.snapshot('snap', recursive=True)
        send_config(config)

        fs0_children = set([child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]])
        fs1_children = set([child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]])
        # remove unwanted datasets/snapshots
        for match in exclude:
            fs0_children -= set(fnmatch.filter(fs0_children, match))
            fs0_children -= set(fnmatch.filter(fs0_children, match + '@snap'))

        assert set(fs0_children) == set(fs1_children), (
            f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'
        )

    @pytest.mark.dependency()
    def test_send_raw(self, zpools):
        """Checks if raw_send works"""
        fs0, fs1 = zpools
        fs0.destroy(force=True)
        fs1.destroy(force=True)

        raw_send = ['yes']
        config = [{'name': fs0.name, 'dest': [fs1.name], 'raw_send': raw_send, 'dest_auto_create': ['yes']}]

        zfs.create(f'{fs0.name:s}/sub1', props={'compression': 'gzip'})
        zfs.create(f'{fs0.name:s}/sub2', props={'compression': 'lz4'})
        zfs.create(f'{fs0.name:s}/sub3', props={'compression': 'gzip'})
        zfs.create(f'{fs0.name:s}/sub3/abc')
        zfs.create(f'{fs0.name:s}/sub3/abc_abc')
        zfs.create(f'{fs0.name:s}/sub3/efg')
        fs0.snapshot('snap', recursive=True)
        send_config(config)

        fs0_children = set([child.name.replace(fs0.name, '') for child in zfs.find(fs0.name, types=['all'])[1:]])
        fs1_children = set([child.name.replace(fs1.name, '') for child in zfs.find(fs1.name, types=['all'])[1:]])

        assert set(fs0_children) == set(fs1_children), (
            f'Snapshot mismatch: only_in_src={set(fs0_children) - set(fs1_children)}, only_in_dest={set(fs1_children) - set(fs0_children)}'
        )
