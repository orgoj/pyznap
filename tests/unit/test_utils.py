"""
Unit tests for pyznap utility functions.

These tests don't require ZFS or root access.
"""

from tempfile import NamedTemporaryFile

from pyznap.utils import parse_name, read_config


class TestReadConfig:
    def test_read_config(self):
        with NamedTemporaryFile('w') as file:
            name = file.name
            file.write('[rpool/data]\n')
            file.write('hourly = 12\n')
            file.write('monthly = 0\n')
            file.write('clean = no\n')
            file.write('dest = backup/data, tank/data, rpool/data\n')
            file.write('compress = lzop, pigz, gzip\n\n')

            file.write('[rpool]\n')
            file.write('frequent = 4\n')
            file.write('hourly = 24\n')
            file.write('daily = 7\n')
            file.write('weekly = 4\n')
            file.write('monthly = 12\n')
            file.write('yearly = 2\n')
            file.write('snap = yes\n')
            file.write('clean = yes\n')
            file.write('dest = backup, tank\n\n')

            file.write('[rpool/data_2]\n')
            file.write('daily = 14\n')
            file.write('yearly = 0\n')
            file.write('clean = yes\n\n')

            file.write('[tank]\n')
            file.write('dest = backup/tank, rpool/tank, data/tank, zpool/tank\n')
            file.write('exclude = , tank/media/* tank/data* tank/home/*, tank/media* tank/home*, \n')
            file.seek(0)

            config = read_config(name)
            conf0, conf1, conf2, conf3 = config

            assert conf0['name'] == 'rpool'
            assert conf0['key'] is None
            assert conf0['frequent'] == 4
            assert conf0['hourly'] == 24
            assert conf0['daily'] == 7
            assert conf0['weekly'] == 4
            assert conf0['monthly'] == 12
            assert conf0['yearly'] == 2
            assert conf0['snap']
            assert conf0['clean']
            assert conf0['dest'] == ['backup', 'tank']
            assert conf0['dest_keys'] is None

            assert conf1['name'] == 'rpool/data'
            assert conf1['key'] is None
            assert conf1['frequent'] == 4
            assert conf1['hourly'] == 12
            assert conf1['daily'] == 7
            assert conf1['weekly'] == 4
            assert conf1['monthly'] == 0
            assert conf1['yearly'] == 2
            assert conf1['snap']
            assert not conf1['clean']
            assert conf1['dest'] == ['backup/data', 'tank/data', 'rpool/data']
            assert conf1['dest_keys'] is None
            assert conf1['compress'] == ['lzop', 'pigz', 'gzip']

            assert conf2['name'] == 'rpool/data_2'
            assert conf2['key'] is None
            assert conf2['frequent'] == 4
            assert conf2['hourly'] == 24
            assert conf2['daily'] == 14
            assert conf2['weekly'] == 4
            assert conf2['monthly'] == 12
            assert conf2['yearly'] == 0
            assert conf2['snap']
            assert conf2['clean']
            assert conf2['dest'] is None
            assert conf2['dest_keys'] is None

            assert conf3['name'] == 'tank'
            assert conf3['dest'] == ['backup/tank', 'rpool/tank', 'data/tank', 'zpool/tank']
            assert conf3['exclude'] == [
                None,
                ['tank/media/*', 'tank/data*', 'tank/home/*'],
                ['tank/media*', 'tank/home*'],
                None,
            ]


class TestParseName:
    def test_parse_name_ssh(self):
        _type, fsname, user, host, port = parse_name('ssh:23:user@hostname:rpool/data')
        assert _type == 'ssh'
        assert fsname == 'rpool/data'
        assert user == 'user'
        assert host == 'hostname'
        assert port == 23

    def test_parse_name_local(self):
        _type, fsname, user, host, port = parse_name('rpool/data')
        assert _type == 'local'
        assert fsname == 'rpool/data'
        assert user is None
        assert host is None
        assert port is None
