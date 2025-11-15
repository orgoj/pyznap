# Refactoring Plan pro pyznap

## Analýza problematických funkcí

### 1. `send_config()` v send.py (řádek 248, 148 řádků)

**Problémy:**
- Vysoká cyklomatická složitost (CC: ~18)
- Příliš mnoho vnořených smyček
- Duplicitní kód pro SSH source a dest
- Dlouhá funkce s mnoha odpovědnostmi

**Odpovědnosti:**
1. Parsování config
2. Otevírání SSH spojení (source)
3. Získání source children
4. Iterace přes destinations
5. Otevírání SSH spojení (dest)
6. Matching source/dest children
7. Exclude filtering
8. Volání send_filesystem()
9. Error handling a retries

**Navržený refactoring:**

```python
# Nová struktura:

class SendConfig:
    """Encapsuluje konfiguraci pro send operaci"""
    def __init__(self, config_entry):
        self.source = config_entry['name']
        self.dest_configs = config_entry.get('dest', [])
        self.exclude = config_entry.get('exclude', [])
        # ... další parametry

    def get_dest_config(self, index):
        """Vrátí konfiguraci pro konkrétní destination"""
        return DestConfig(
            name=self.dest_configs[index],
            exclude=self.exclude[index] if index < len(self.exclude) else [],
            raw=self.raw_send[index] if index < len(self.raw_send) else False,
            # ...
        )


class SSHManager:
    """Centralizovaná správa SSH spojení"""
    _connections = {}

    @classmethod
    def get_or_create(cls, parsed_name, key=None, compress='lzop'):
        """Singleton pattern pro SSH spojení"""
        conn_id = f"{parsed_name.user}@{parsed_name.host}:{parsed_name.port}"
        if conn_id not in cls._connections:
            cls._connections[conn_id] = SSH(
                parsed_name.user,
                parsed_name.host,
                port=parsed_name.port,
                key=key,
                compress=compress
            )
        return cls._connections[conn_id]


def send_config_refactored(config, settings={}):
    """
    Vysoko-úrovňová orchestrace send operací

    Refactorováno pro lepší čitelnost a testovatelnost.
    """
    logger = logging.getLogger(__name__)
    logger.info('Sending snapshots...')

    for conf_entry in config:
        if not conf_entry.get('dest'):
            logger.debug(f"Ignore config from send {conf_entry['name']}...")
            continue

        send_cfg = SendConfig(conf_entry)
        send_single_config(send_cfg, settings)


def send_single_config(send_cfg: SendConfig, settings):
    """
    Zpracuje jeden config entry - posílá na všechny destinations
    """
    logger = logging.getLogger(__name__)
    logger.debug(f"Process config {send_cfg.source}...")

    # 1. Otevři source
    source_ctx = open_source(send_cfg)
    if not source_ctx:
        return

    # 2. Získej source filesystems
    source_children = get_source_children(source_ctx, send_cfg, settings)
    if not source_children:
        source_ctx.close()
        return

    # 3. Pošli na každou destination
    for i, dest_name in enumerate(send_cfg.dest_configs):
        dest_cfg = send_cfg.get_dest_config(i)
        send_to_destination(source_children, source_ctx, dest_cfg)

    source_ctx.close()


def open_source(send_cfg: SendConfig) -> Optional[SourceContext]:
    """
    Otevře source filesystem (lokální nebo SSH)

    Returns:
        SourceContext nebo None při chybě
    """
    logger = logging.getLogger(__name__)

    try:
        parsed = parse_name(send_cfg.source)
    except ValueError as err:
        logger.error(f'Could not parse {send_cfg.source}: {err}...')
        return None

    ssh = None
    if parsed.type == 'ssh':
        try:
            ssh = SSHManager.get_or_create(
                parsed,
                key=send_cfg.key,
                compress=send_cfg.compress
            )
        except (FileNotFoundError, SSHException) as err:
            logger.error(f'Could not connect to {parsed.host}: {err}')
            return None

    return SourceContext(
        name=parsed.name,
        ssh=ssh,
        display_name=parsed.display_name
    )


def get_source_children(source_ctx, send_cfg, settings):
    """Získá seznam source children filesystems"""
    logger = logging.getLogger(__name__)

    try:
        return zfs.find_exclude(
            send_cfg.raw_config,
            send_cfg.all_configs,
            ssh=source_ctx.ssh,
            matching=settings['matching']
        )
    except DatasetNotFoundError:
        logger.error(f'Source {source_ctx.display_name} does not exist...')
        return None
    except ValueError as err:
        logger.error(err)
        return None
    except CalledProcessError as err:
        logger.error(f'Error while opening source {source_ctx.display_name}: {err.stderr.rstrip()}...')
        return None


def send_to_destination(source_children, source_ctx, dest_cfg):
    """
    Pošle source children na jednu destination
    """
    logger = logging.getLogger(__name__)

    # 1. Otevři destination
    dest_ctx = open_destination(dest_cfg)
    if not dest_ctx:
        return

    # 2. Match children
    dest_children = match_children(
        source_children,
        source_ctx.name,
        dest_ctx.name
    )

    # 3. Pošli každý filesystem
    for source_fs, dest_name in dest_children:
        if should_exclude(source_fs, dest_cfg):
            continue

        send_with_retries(
            source_fs,
            dest_name,
            dest_ctx,
            dest_cfg
        )

    dest_ctx.close()


def should_exclude(filesystem, dest_cfg) -> bool:
    """Zkontroluje jestli filesystem by měl být excluded"""
    logger = logging.getLogger(__name__)

    # Exclude rules
    if any(fnmatch(filesystem.name, pattern) for pattern in dest_cfg.exclude):
        logger.debug(f'Matched {filesystem} in exclude rules, not sending...')
        return True

    # Exclude property
    if dest_cfg.send_exclude_property:
        if filesystem.ispropval(dest_cfg.send_exclude_property, check='false'):
            logger.debug(f'Not sending {filesystem}, have property {dest_cfg.send_exclude_property}=false')
            return True

    return False


def send_with_retries(source_fs, dest_name, dest_ctx, dest_cfg):
    """Pošle filesystem s retry mechanikou"""
    logger = logging.getLogger(__name__)

    for retry in range(1, dest_cfg.retries + 2):
        rc = send_filesystem(
            source_fs,
            dest_name,
            ssh_dest=dest_ctx.ssh,
            raw=dest_cfg.raw,
            resume=dest_cfg.resume,
            send_last_snapshot=dest_cfg.send_last_snapshot,
            dest_auto_create=dest_cfg.dest_auto_create
        )

        if rc == 2 and retry <= dest_cfg.retries:
            logger.info(f'Retrying send in {dest_cfg.retry_interval}s (retry {retry} of {dest_cfg.retries})...')
            sleep(dest_cfg.retry_interval)
        else:
            break


@dataclass
class SourceContext:
    """Context pro source filesystem"""
    name: str
    ssh: Optional[SSH]
    display_name: str

    def close(self):
        if self.ssh:
            self.ssh.close()


@dataclass
class DestContext:
    """Context pro destination filesystem"""
    name: str
    ssh: Optional[SSH]
    display_name: str

    def close(self):
        if self.ssh:
            self.ssh.close()


@dataclass
class DestConfig:
    """Konfigurace pro jednu destination"""
    name: str
    exclude: List[str]
    raw: bool
    resume: bool
    send_last_snapshot: Union[str, bool]
    dest_auto_create: bool
    retries: int
    retry_interval: int
    send_exclude_property: Optional[str]
```

**Výhody refactoringu:**
- ✅ Každá funkce má jednu odpovědnost (SRP)
- ✅ Funkcema méně než 30 řádků
- ✅ Snížená cyklomatická složitost (CC < 5 pro každou funkci)
- ✅ Testovatelnost - můžeme mockovat jednotlivé části
- ✅ Centralizovaná správa SSH spojení
- ✅ Jasná data flow: config → source → dest → send

---

### 2. `status_filesystem()` v status.py (řádek 30, 200+ řádků)

**Problémy:**
- 9 parametrů (too many)
- Vysoká cyklomatická složitost
- Více odpovědností:
  - Exclude filtering
  - Snapshot kategorizace
  - Snapshot counting
  - Destination status
  - Output formátování
  - Statistics tracking

**Navržený refactoring:**

```python
# Nová struktura:

@dataclass
class FilesystemStatus:
    """Zapouzdřuje status informace o filesystemu"""
    hostname: str
    name: str
    conf_name: str
    excluded: bool
    do_snap: bool
    do_clean: bool
    do_send: bool
    snapshots: Dict[str, List]
    destinations: List['DestStatus']

    def to_dict(self) -> OrderedDict:
        """Serializuj do OrderedDict pro output"""
        status = OrderedDict()
        status['hostname'] = self.hostname
        status['name'] = self.name
        # ... atd
        return status

    def should_warn(self) -> bool:
        """Určí jestli by měl být warning level"""
        return self.has_missing_snapshots()

    def has_missing_snapshots(self) -> bool:
        """Zkontroluje jestli chybí snapshoty podle policy"""
        # logika
        pass


@dataclass
class DestStatus:
    """Status pro jednu destination"""
    type: str
    host: str
    name: str
    snapshot_count: int
    common_snapshots: List[str]

    def to_dict(self, prefix: str) -> dict:
        """Serializuj s prefixem"""
        return {
            f'{prefix}type': self.type,
            f'{prefix}host': self.host,
            # ...
        }


class SnapshotCategorizer:
    """Kategorizuje snapshoty podle typu"""

    @staticmethod
    def categorize(fs_snapshots) -> Dict[str, List]:
        """
        Roztřídí snapshoty do kategorií

        Returns:
            Dict s klíči: frequent, hourly, daily, weekly, monthly, yearly
        """
        snapshots = {t: [] for t in SNAPSHOT_TYPES}

        for snap in fs_snapshots:
            # Ignore non-pyznap snapshots
            snap_name = snap.name.split('@')[1]
            if not snap_name.startswith('pyznap'):
                continue

            try:
                snap_type = snap_name.split('_')[-1]
                if snap_type in snapshots:
                    snapshots[snap_type].append(snap)
            except (ValueError, KeyError):
                continue

        # Reverse sort by time
        for snaps in snapshots.values():
            snaps.reverse()

        return snapshots


class DestinationChecker:
    """Kontroluje status destinations"""

    def __init__(self, filesystem, conf):
        self.filesystem = filesystem
        self.conf = conf
        self.ssh_cache = {}

    def check_all_destinations(self) -> List[DestStatus]:
        """Zkontroluje všechny destinations"""
        dest_statuses = []

        dest_list = self.conf.get('dest', [])
        fs_snapshots = self.filesystem.snapshots()
        snapnames = [snap.name.split('@')[1] for snap in fs_snapshots]

        for i, dest in enumerate(dest_list):
            if not dest:
                continue

            dest_status = self.check_single_destination(
                dest, snapnames
            )
            dest_statuses.append(dest_status)

        return dest_statuses

    def check_single_destination(self, dest, snapnames) -> DestStatus:
        """Zkontroluje jednu destination"""
        logger = logging.getLogger(__name__)

        parsed = parse_name(dest)
        dest_name = self.compute_dest_name(parsed.name)

        # Otevři dest přes SSH pokud potřeba
        ssh_dest = self.get_ssh_connection(parsed)

        try:
            dest_fs = zfs.open(dest_name, ssh=ssh_dest)
            dest_snapshots = dest_fs.snapshots()
            dest_snapnames = [s.name.split('@')[1] for s in dest_snapshots]
            common = set(snapnames) & set(dest_snapnames)
            common_snapshots = [s for s in snapnames if s in common]
        except DatasetNotFoundError:
            dest_snapnames = []
            common_snapshots = []
        except CalledProcessError as err:
            logger.error(f'Error opening dest {dest_name}: {err.stderr}')
            dest_snapnames = []
            common_snapshots = []

        return DestStatus(
            type=parsed.type,
            host=parsed.host,
            name=dest_name,
            snapshot_count=len(dest_snapnames),
            common_snapshots=common_snapshots
        )

    def compute_dest_name(self, dest_base):
        """Vypočítá dest name z conf"""
        fs_name = str(self.filesystem)
        conf_name = self.conf['name']

        if conf_name:
            return fs_name.replace(conf_name, dest_base)
        else:
            return f"{dest_base}/{fs_name}"


def status_filesystem_refactored(
    filesystem,
    conf,
    output='log',
    show_all=False,
    main_fs=False,
    filter_exclude=None
):
    """
    Vytvoří status report pro filesystem

    Refactorováno pro lepší čitelnost a testovatelnost.
    """
    logger = logging.getLogger(__name__)
    fs_name = str(filesystem)

    # 1. Check exclude filters
    if filter_exclude and should_skip_filesystem(fs_name, filter_exclude):
        return

    logger.debug(f'Checking snapshots on {fs_name}...')
    zfs.STATS.add('checked_count')

    # 2. Determine operations (snap, clean, send)
    operations = determine_operations(filesystem, conf, main_fs)
    if not operations.any_enabled() and not show_all:
        return

    # 3. Get and categorize snapshots
    try:
        fs_snapshots = filesystem.snapshots()
    except (DatasetNotFoundError, DatasetBusyError) as err:
        logger.error(f'Error while opening {filesystem}: {err}...')
        return 1

    categorized = SnapshotCategorizer.categorize(fs_snapshots)

    # 4. Check destinations if needed
    dest_statuses = []
    if operations.send:
        checker = DestinationChecker(filesystem, conf)
        dest_statuses = checker.check_all_destinations()

    # 5. Build status object
    status = FilesystemStatus(
        hostname=os.uname()[1],
        name=fs_name,
        conf_name=conf['name'],
        excluded=operations.excluded,
        do_snap=operations.snap,
        do_clean=operations.clean,
        do_send=operations.send,
        snapshots=categorized,
        destinations=dest_statuses
    )

    # 6. Output podle formátu
    output_status(status, output, filter_values=filter_values)

    # 7. Update statistics
    update_statistics(operations, dest_statuses)

    return 0


@dataclass
class FilesystemOperations:
    """Zapouzdřuje které operace by měly běžet na filesystemu"""
    snap: bool
    clean: bool
    send: bool
    excluded: bool

    def any_enabled(self) -> bool:
        return self.snap or self.clean or self.send


def determine_operations(filesystem, conf, main_fs) -> FilesystemOperations:
    """
    Určí které operace by měly běžet podle conf a exclude properties
    """
    logger = logging.getLogger(__name__)

    snap = conf.get('snap', False)
    clean = conf.get('clean', False)
    send = bool(conf.get('dest', False))
    excluded = False

    # Check snap exclude property
    snap_exclude_property = conf['snap_exclude_property']
    if not main_fs and snap_exclude_property:
        if filesystem.ispropval(snap_exclude_property, check='false'):
            zfs.STATS.add('snap_excluded_count')
            logger.debug(f'Ignore dataset from snap {filesystem.name}, have property {snap_exclude_property}=false')
            snap = False
            clean = False

    # Check send exclude property
    send_exclude_property = conf['send_exclude_property']
    if not main_fs and send_exclude_property:
        if filesystem.ispropval(send_exclude_property, check='false'):
            zfs.STATS.add('send_excluded_count')
            logger.debug(f'Ignore dataset from send {filesystem.name}, have property {send_exclude_property}=false')
            send = False

    if not (snap or clean or send):
        excluded = True

    return FilesystemOperations(
        snap=snap,
        clean=clean,
        send=send,
        excluded=excluded
    )


def output_status(status: FilesystemStatus, output_format: str, **kwargs):
    """Output status podle formátu"""
    if output_format == 'log':
        output_log_format(status)
    elif output_format == 'jsonl':
        output_jsonl_format(status, **kwargs)
    else:
        raise ValueError(f"Unknown output format: {output_format}")
```

**Výhody refactoringu:**
- ✅ Separace concerns - každá třída/funkce má jednu odpovědnost
- ✅ Méně parametrů (max 4)
- ✅ Dataclasses pro structured data
- ✅ Testovatelnost - můžeme testovat každou část izolovaně
- ✅ Reusabilita - např. DestinationChecker lze použít i jinde
- ✅ Lepší error handling - izolovaný v jednotlivých funkcích

---

## Testování

### Unit testy pro send.py

```python
# tests/unit/test_send_refactored.py

import pytest
from unittest.mock import Mock, patch
from pyznap.send import (
    SendConfig,
    SourceContext,
    should_exclude,
    send_with_retries,
    SSHManager
)

class TestSendConfig:
    def test_init(self):
        config = {
            'name': 'tank/data',
            'dest': ['backup/data'],
            'exclude': [['*.tmp']],
            'raw_send': [False],
            'retries': [3]
        }

        send_cfg = SendConfig(config)
        assert send_cfg.source == 'tank/data'
        assert len(send_cfg.dest_configs) == 1

    def test_get_dest_config(self):
        config = {
            'name': 'tank/data',
            'dest': ['backup/data'],
            'exclude': [['*.tmp']],
            'raw_send': [False]
        }

        send_cfg = SendConfig(config)
        dest_cfg = send_cfg.get_dest_config(0)

        assert dest_cfg.name == 'backup/data'
        assert dest_cfg.exclude == ['*.tmp']
        assert dest_cfg.raw is False


class TestShouldExclude:
    def test_exclude_by_pattern(self):
        filesystem = Mock()
        filesystem.name = 'tank/data/temp.tmp'

        dest_cfg = Mock()
        dest_cfg.exclude = ['*.tmp']
        dest_cfg.send_exclude_property = None

        assert should_exclude(filesystem, dest_cfg) is True

    def test_exclude_by_property(self):
        filesystem = Mock()
        filesystem.name = 'tank/data'
        filesystem.ispropval.return_value = True

        dest_cfg = Mock()
        dest_cfg.exclude = []
        dest_cfg.send_exclude_property = 'com.sun:auto-snapshot'

        assert should_exclude(filesystem, dest_cfg) is True

    def test_no_exclude(self):
        filesystem = Mock()
        filesystem.name = 'tank/data'
        filesystem.ispropval.return_value = False

        dest_cfg = Mock()
        dest_cfg.exclude = []
        dest_cfg.send_exclude_property = None

        assert should_exclude(filesystem, dest_cfg) is False


class TestSSHManager:
    def test_singleton_pattern(self):
        parsed = Mock()
        parsed.user = 'root'
        parsed.host = 'example.com'
        parsed.port = 22

        with patch('pyznap.send.SSH') as mock_ssh:
            ssh1 = SSHManager.get_or_create(parsed)
            ssh2 = SSHManager.get_or_create(parsed)

            assert ssh1 is ssh2
            assert mock_ssh.call_count == 1


class TestSendWithRetries:
    @patch('pyznap.send.send_filesystem')
    @patch('pyznap.send.sleep')
    def test_success_first_try(self, mock_sleep, mock_send):
        mock_send.return_value = 0

        source_fs = Mock()
        dest_name = 'backup/data'
        dest_ctx = Mock()
        dest_cfg = Mock()
        dest_cfg.retries = 3
        dest_cfg.retry_interval = 10
        dest_cfg.raw = False
        dest_cfg.resume = False
        dest_cfg.send_last_snapshot = False
        dest_cfg.dest_auto_create = False

        send_with_retries(source_fs, dest_name, dest_ctx, dest_cfg)

        assert mock_send.call_count == 1
        assert mock_sleep.call_count == 0

    @patch('pyznap.send.send_filesystem')
    @patch('pyznap.send.sleep')
    def test_retry_on_connection_error(self, mock_sleep, mock_send):
        # First 2 attempts fail, 3rd succeeds
        mock_send.side_effect = [2, 2, 0]

        source_fs = Mock()
        dest_name = 'backup/data'
        dest_ctx = Mock()
        dest_cfg = Mock()
        dest_cfg.retries = 3
        dest_cfg.retry_interval = 10
        dest_cfg.raw = False
        dest_cfg.resume = False
        dest_cfg.send_last_snapshot = False
        dest_cfg.dest_auto_create = False

        send_with_retries(source_fs, dest_name, dest_ctx, dest_cfg)

        assert mock_send.call_count == 3
        assert mock_sleep.call_count == 2  # Slept before retry 2 and 3
```

### Unit testy pro status.py

```python
# tests/unit/test_status_refactored.py

import pytest
from unittest.mock import Mock
from pyznap.status import (
    SnapshotCategorizer,
    FilesystemOperations,
    determine_operations,
    should_skip_filesystem,
    FilesystemStatus
)

class TestSnapshotCategorizer:
    def test_categorize_pyznap_snapshots(self):
        snap1 = Mock()
        snap1.name = 'tank/data@pyznap_2025-01-15_120000_hourly'

        snap2 = Mock()
        snap2.name = 'tank/data@pyznap_2025-01-14_120000_daily'

        snap3 = Mock()
        snap3.name = 'tank/data@manual_snapshot'  # Non-pyznap

        fs_snapshots = [snap1, snap2, snap3]

        result = SnapshotCategorizer.categorize(fs_snapshots)

        assert len(result['hourly']) == 1
        assert len(result['daily']) == 1
        assert len(result['weekly']) == 0
        assert snap3 not in [s for snaps in result.values() for s in snaps]

    def test_reverse_sort(self):
        snap1 = Mock()
        snap1.name = 'tank/data@pyznap_2025-01-15_120000_hourly'

        snap2 = Mock()
        snap2.name = 'tank/data@pyznap_2025-01-15_110000_hourly'

        fs_snapshots = [snap2, snap1]  # Out of order

        result = SnapshotCategorizer.categorize(fs_snapshots)

        # Should be reverse sorted (newest first)
        assert result['hourly'][0] == snap1


class TestDetermineOperations:
    def test_all_enabled(self):
        filesystem = Mock()
        conf = {
            'snap': True,
            'clean': True,
            'dest': ['backup/data'],
            'snap_exclude_property': None,
            'send_exclude_property': None
        }

        ops = determine_operations(filesystem, conf, main_fs=True)

        assert ops.snap is True
        assert ops.clean is True
        assert ops.send is True
        assert ops.excluded is False

    def test_excluded_by_snap_property(self):
        filesystem = Mock()
        filesystem.name = 'tank/data'
        filesystem.ispropval.return_value = True

        conf = {
            'snap': True,
            'clean': True,
            'dest': [],
            'snap_exclude_property': 'com.sun:auto-snapshot',
            'send_exclude_property': None
        }

        ops = determine_operations(filesystem, conf, main_fs=False)

        assert ops.snap is False  # Disabled by property
        assert ops.clean is False  # Also disabled
        assert ops.send is False

    def test_main_fs_ignores_exclude_property(self):
        filesystem = Mock()
        filesystem.name = 'tank/data'
        filesystem.ispropval.return_value = True

        conf = {
            'snap': True,
            'clean': True,
            'dest': [],
            'snap_exclude_property': 'com.sun:auto-snapshot',
            'send_exclude_property': None
        }

        ops = determine_operations(filesystem, conf, main_fs=True)

        assert ops.snap is True  # NOT disabled because main_fs=True
        assert ops.clean is True


class TestFilesystemStatus:
    def test_to_dict(self):
        status = FilesystemStatus(
            hostname='myhost',
            name='tank/data',
            conf_name='tank/data',
            excluded=False,
            do_snap=True,
            do_clean=True,
            do_send=False,
            snapshots={'hourly': [], 'daily': []},
            destinations=[]
        )

        result = status.to_dict()

        assert result['hostname'] == 'myhost'
        assert result['name'] == 'tank/data'
        assert result['do-snap'] is True

    def test_has_missing_snapshots(self):
        # TODO: Implement po refactoringu
        pass
```

---

## Dokumentace

### Docstrings standard

Použijeme Google style docstrings:

```python
def send_filesystem(source_fs, dest_name, ssh_dest=None, raw=False, resume=False):
    """
    Pošle filesystem na destination.

    Provede ZFS send/receive operaci z source na destination. Pokud existují
    společné snapshoty, provede incremental send. Jinak full send.

    Args:
        source_fs (ZFSFilesystem): Source filesystem k odeslání
        dest_name (str): Název destination filesystemu
        ssh_dest (SSH, optional): SSH spojení pro remote dest. Defaults to None.
        raw (bool, optional): Použít raw send (-w). Defaults to False.
        resume (bool, optional): Použít resumable send/receive. Defaults to False.

    Returns:
        int: Return code:
            0 - Success
            1 - Error (dataset issues, permission, etc.)
            2 - Connection error (retry possible)

    Raises:
        DatasetNotFoundError: Pokud source nebo dest neexistuje
        CalledProcessError: Pokud ZFS příkaz selže

    Example:
        >>> source = zfs.open('tank/data')
        >>> send_filesystem(source, 'backup/data', raw=True)
        0

    Note:
        - Vyžaduje root oprávnění pro ZFS operace
        - Pro raw send musí být ZFS >= 0.8.0
        - Resume vyžaduje ZFS >= 0.7.0
    """
    # implementation
    pass
```

### Type hints

Přidáme type hints pro lepší IDE support a type checking:

```python
from typing import Optional, List, Dict, Union
from dataclasses import dataclass

@dataclass
class SendConfig:
    """Konfigurace pro send operaci"""
    source: str
    dest_configs: List[str]
    exclude: List[List[str]]
    raw_send: List[bool]
    resume: List[bool]
    retries: List[int]

    def get_dest_config(self, index: int) -> 'DestConfig':
        """Vrátí konfiguraci pro destination na indexu"""
        pass


def send_filesystem(
    source_fs: 'ZFSFilesystem',
    dest_name: str,
    ssh_dest: Optional['SSH'] = None,
    raw: bool = False,
    resume: bool = False
) -> int:
    """Pošle filesystem na destination"""
    pass
```

---

## Implementační plán

### Fáze 1: Příprava (1 den)
- [ ] Vytvořit feature branch `refactor/send-and-status`
- [ ] Setup unit test framework
- [ ] Přidat pytest fixtures pro mock ZFS a SSH

### Fáze 2: Refactoring send.py (2 dny)
- [ ] Vytvořit helper třídy (SendConfig, SourceContext, DestContext)
- [ ] Implementovat SSHManager
- [ ] Rozdělit send_config() na menší funkce
- [ ] Přidat unit testy pro každou funkci
- [ ] Přidat docstrings a type hints
- [ ] Udržet zpětnou kompatibilitu

### Fáze 3: Refactoring status.py (2 dny)
- [ ] Vytvořit helper třídy (FilesystemStatus, SnapshotCategorizer, etc.)
- [ ] Rozdělit status_filesystem() na menší funkce
- [ ] Přidat unit testy
- [ ] Přidat docstrings a type hints
- [ ] Udržet zpětnou kompatibilitu

### Fáze 4: Integration testing (1 den)
- [ ] Otestovat s reálným ZFS poolem
- [ ] Otestovat s SSH remote
- [ ] Regression testing - ověřit že vše funguje jako před refactoringem
- [ ] Performance testing

### Fáze 5: Dokumentace (0.5 dne)
- [ ] Update README.md
- [ ] Vytvořit CONTRIBUTING.md s coding standards
- [ ] Update docstrings

### Fáze 6: Review a merge (0.5 dne)
- [ ] Code review
- [ ] Fix issues
- [ ] Merge do main branch

**Celkový odhad: 6-7 dní**

---

## Metriky úspěchu

Před refactoringem:
```bash
radon cc pyznap/send.py pyznap/status.py -a
# Průměrná CC: ~12
# Funkce s CC > 10: 3

wc -l pyznap/send.py pyznap/status.py
# send_config: 148 řádků
# status_filesystem: 200+ řádků
```

Po refactoringu (cíle):
```bash
radon cc pyznap/send.py pyznap/status.py -a
# Průměrná CC: < 6
# Funkce s CC > 10: 0
# Max délka funkce: < 50 řádků

pytest tests/unit/ --cov=pyznap --cov-report=term
# Test coverage: > 80%
```

---

## Zpětná kompatibilita

Zachováme původní API:
- `send_config(config, settings)` - zachována, interně volá refactorovaný kód
- `status_filesystem(...)` - zachována se všemi parametry

Deprecated funkce:
- Žádné - všechny public funkce zůstávají

Breaking changes:
- Žádné v public API
- Interní funkce mohou být změněny (jsou považovány za private)
