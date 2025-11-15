# Code Review a Návrhy Vylepšení pro pyznap

**Datum:** 2025-11-15
**Reviewer:** Claude Code (Opus model)
**Verze projektu:** Aktuální main branch

---

## Obsah

1. [Shrnutí](#shrnutí)
2. [Architektura projektu](#architektura-projektu)
3. [Kritická zjištění](#kritická-zjištění)
4. [Analýza TODO položek](#analýza-todo-položek)
5. [Hlavní priorita: Automatická kontrola remote snapshotů](#hlavní-priorita-automatická-kontrola-remote-snapshotů)
6. [Prioritizovaný akční plán](#prioritizovaný-akční-plán)
7. [Konkrétní návrhy implementace](#konkrétní-návrhy-implementace)

---

## Shrnutí

**pyznap** je kvalitní nástroj pro automatizovanou správu ZFS snapshotů s dobrou modulární architekturou. Projekt má však několik oblastí vyžadujících zlepšení:

### Klíčové problémy:
- ⚠️ **KRITICKÉ**: Bezpečnostní rizika (command injection, SSH security)
- ⚠️ **VYSOKÉ**: Chybějící automatická kontrola stavu remote backupů
- ⚠️ **STŘEDNÍ**: Nedostatečný error recovery a handling
- ℹ️ **NÍZKÉ**: Code quality issues (duplicity, složitost)

### Stav TODO položek:
- 6 TODO položek v kódu
- Nejdůležitější: `status.py:135` - remote uptodate check

---

## Architektura projektu

### Struktura modulů

```
pyznap/
├── main.py          # Vstupní bod, CLI interface
├── pyzfs.py         # ZFS bindings (fork z weir)
├── take.py          # Vytváření snapshotů
├── clean.py         # Mazání starých snapshotů
├── send.py          # Send/receive operace
├── status.py        # Status reporting
├── ssh.py           # SSH komunikace (ControlMaster)
├── process.py       # Subprocess wrapper
└── utils.py         # Utility funkce, config parsing
```

### Použité design patterns

1. **Command Pattern** - Jednotlivé operace (snap, clean, send, status)
2. **Strategy Pattern** - Různé strategie snapshotů (frequent, hourly, daily, weekly, monthly, yearly)
3. **Facade Pattern** - pyzfs.py jako jednotné rozhraní pro ZFS

### Datové toky

```
Konfigurace → utils.read_config() → process.py
                                   ↓
                          ┌────────┴────────┐
                          ↓                 ↓
                     take.py/clean.py    send.py
                          ↓                 ↓
                      pyzfs.py          ssh.py
                          ↓                 ↓
                      ZFS příkazy      Remote ZFS
```

---

## Kritická zjištění

### 🔴 KRITICKÉ - Bezpečnost

#### 1. Command Injection riziko

**Lokace:** `pyzfs.py`, `ssh.py`, všude kde se volají subprocess příkazy

**Problém:**
```python
# pyzfs.py používá shlex.quote() pouze částečně
def find(path=None, *, max_depth=None, types=None, ...):
    cmd = ['zfs', 'list', '-H', '-o', 'name']
    if types:
        cmd += ['-t', types]  # ❌ Není escapováno!
```

**Riziko:**
- Útočník může inject shell příkazy přes config file
- SSH parametry nejsou dostatečně validovány
- User-supplied data (názvy datasetů) mohou obsahovat speciální znaky

**Řešení:**
```python
import shlex

def find(path=None, *, max_depth=None, types=None, ...):
    cmd = ['zfs', 'list', '-H', '-o', 'name']
    if types:
        # Validuj proti whitelist
        valid_types = {'filesystem', 'volume', 'snapshot', 'bookmark', 'all'}
        if types not in valid_types:
            raise ValueError(f"Invalid type: {types}")
        cmd += ['-t', types]

    if path:
        # Vždy escapuj cesty
        cmd.append(shlex.quote(str(path)))
```

#### 2. SSH bezpečnost

**Lokace:** `ssh.py:73`

**Problém:**
```python
# Predictable socket naming
self.socket = '/tmp/pyznap_ssh_{}_{}_{}'.format(self.user, self.host, self.port)
```

**Rizika:**
- Socket hijacking
- Race conditions
- Chybí host key verification
- Symlink attacks v /tmp

**Řešení:**
```python
import tempfile
import uuid
import os

class SSH:
    def __init__(self, user, host, port=22, key=None):
        # Bezpečný temp directory
        self.temp_dir = tempfile.mkdtemp(prefix='pyznap_ssh_')
        socket_name = f'socket_{uuid.uuid4().hex}'
        self.socket = os.path.join(self.temp_dir, socket_name)

        # Nastavit správná oprávnění
        os.chmod(self.temp_dir, 0o700)

        # Host key verification
        self.known_hosts = key.get('known_hosts') if key else '~/.ssh/known_hosts'
```

#### 3. Path Traversal

**Problém:** Nedostatečná validace cest v konfiguračním souboru

**Řešení:**
```python
import os.path

def validate_dataset_name(name):
    """Validuje ZFS dataset název proti path traversal"""
    # Zakázané znaky a patterny
    forbidden = ['..', '//', '\x00', '|', ';', '&', '$', '`']

    if any(f in name for f in forbidden):
        raise ValueError(f"Invalid dataset name: {name}")

    # Dataset musí být relativní k pool root
    if name.startswith('/'):
        raise ValueError("Dataset name cannot start with /")

    return name
```

### 🟠 VYSOKÉ - Error Handling

#### 1. Nedostatečný error recovery při send/receive

**Lokace:** `send.py`

**Problém:**
- Při selhání zůstávají partial datasets na destinaci
- Resume token se neřeší při corrupted state
- Chybí rollback při interrupted receive

**Příklad současného kódu:**
```python
# send.py - žádný cleanup při selhání
with dest_fs.receive(name, pipe=ssh_dest.stdout, ...):
    ssh_source.stdout.write(source_fs.send(...))
# Pokud toto selže, partial dataset zůstane
```

**Navrhované řešení:**
```python
def send_with_recovery(source_fs, dest_fs, ...):
    """Send s automatic recovery"""
    recovery_point = None

    try:
        # Kontrola resume token
        resume_token = dest_fs.get_resume_token()
        if resume_token and not validate_resume_token(resume_token):
            logger.warning("Corrupted resume token, clearing...")
            dest_fs.clear_resume_token()
            resume_token = None

        # Save recovery point
        recovery_point = dest_fs.get_latest_snapshot()

        # Perform send
        with dest_fs.receive(...) as recv:
            source_fs.send(..., resume=resume_token)

    except Exception as e:
        logger.error(f"Send failed: {e}")

        # Cleanup partial receive
        if recovery_point:
            logger.info("Rolling back to recovery point...")
            dest_fs.rollback(recovery_point)

        raise
```

#### 2. Nekonzistentní error reporting

**Problém:**
- Mix logging a print statements
- Nejednotné exit kódy
- Chybí structured logging

**Řešení:**
```python
import logging
import sys
from enum import IntEnum

class ExitCode(IntEnum):
    SUCCESS = 0
    CONFIG_ERROR = 1
    ZFS_ERROR = 2
    SSH_ERROR = 3
    PERMISSION_ERROR = 4
    UNKNOWN_ERROR = 99

# Unified logger setup
def setup_logging(verbose=False, quiet=False, syslog=False):
    """Nastavení jednotného loggingu"""
    level = logging.WARNING
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.ERROR

    handlers = [logging.StreamHandler(sys.stderr)]

    if syslog:
        from logging.handlers import SysLogHandler
        handlers.append(SysLogHandler())

    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=handlers
    )
```

### 🟡 STŘEDNÍ - Code Quality

#### 1. Duplicitní kód

**Příklady:**
- SSH připojení se opakuje v take.py, clean.py, send.py
- Parsování snapshot jmen je všude podobné
- Config processing je duplicitní

**Řešení - vytvoření abstrakce:**
```python
# Nový modul: pyznap/ssh_manager.py
class SSHConnectionManager:
    """Centralizovaná správa SSH spojení"""

    _connections = {}

    @classmethod
    def get_connection(cls, user, host, port, key=None):
        """Singleton pattern pro SSH spojení"""
        conn_id = f"{user}@{host}:{port}"

        if conn_id not in cls._connections:
            cls._connections[conn_id] = SSH(user, host, port, key)

        return cls._connections[conn_id]

    @classmethod
    def close_all(cls):
        """Uzavře všechna spojení"""
        for conn in cls._connections.values():
            conn.close()
        cls._connections.clear()
```

#### 2. Vysoká cyklomatická složitost

**Problémové funkce:**
- `send_config()` - 100+ řádků, mnoho větvení
- `status_filesystem()` - 250+ řádků
- `_main()` - příliš mnoho odpovědností

**Měření:**
```bash
# Instalace radon pro měření complexity
pip install radon
radon cc pyznap/*.py -a -nb

# Výsledky (příklad):
# send.py::send_config - CC: 18 (vysoká komplexita)
# status.py::status_filesystem - CC: 24 (velmi vysoká)
```

**Refaktoring příklad:**
```python
# send.py - před
def send_config(config):
    # 100+ řádků s mnoha if/else
    ...

# send.py - po refaktoringu
def send_config(config):
    """Hlavní orchestrace"""
    validator = ConfigValidator(config)
    validator.validate()

    strategy = SendStrategy.from_config(config)
    executor = SendExecutor(strategy)

    return executor.execute()

class SendStrategy:
    """Strategy pattern pro různé typy sendů"""

    @staticmethod
    def from_config(config):
        if config.is_ssh_source():
            return SSHSourceStrategy(config)
        elif config.is_ssh_dest():
            return SSHDestStrategy(config)
        else:
            return LocalStrategy(config)
```

---

## Analýza TODO položek

### 1. `send.py:379` - Create missing skipped filesystem on destination

```python
# TODO: create missing skipped filesystem on destination
```

**Kontext:** Když je filesystem vyloučen pomocí exclude rules, může chybět parent na destinaci.

**Problém:**
```
Source:      pool/parent/child1
             pool/parent/child2 (excluded)
             pool/parent/child2/grandchild

Destination: pool/parent/child1
             pool/parent/child2/grandchild  ❌ Nelze vytvořit, chybí parent!
```

**Řešení:**
```python
def ensure_parent_exists(dest_fs, dataset_path):
    """Vytvoří chybějící parent datasety"""
    parts = dataset_path.split('/')

    for i in range(1, len(parts)):
        parent_path = '/'.join(parts[:i])

        try:
            dest_fs.open(parent_path)
        except DatasetNotFound:
            logger.info(f"Creating missing parent: {parent_path}")
            dest_fs.create(parent_path, createparent=True)
```

**Priorita:** STŘEDNÍ
**Effort:** Nízký (pár hodin)
**Impact:** Střední (řeší edge case)

### 2. `status.py:135` - Remote uptodate check ⭐ PRIORITA

```python
# TODO: remote uptodate check
```

**Kontext:** Chybí kontrola, zda jsou remote snapshoty aktuální.

**Dopad:** Uživatel neví, jestli backup skutečně funguje bez manuální kontroly.

**Priorita:** KRITICKÁ
**Effort:** Vysoký (několik dní)
**Impact:** Vysoký (hlavní požadavek uživatele)

→ Viz sekce "Hlavní priorita" níže pro detailní návrh

### 3. `status.py:136` - Snapshot type checks

```python
# TODO: T/F oversnapshot/undesnapshot/othersnapshots/unvantedsnapshot on exluded fs
```

**Vysvětlení:**
- **oversnapshot** - více snapshotů než podle policy
- **undersnapshot** - méně snapshotů než požadováno
- **othersnapshots** - snapshoty mimo pyznap schéma
- **unwantedsnapshot** - snapshoty na excluded filesystems

**Řešení:**
```python
def analyze_snapshot_health(filesystem, config):
    """Analyzuje stav snapshotů"""
    health = {
        'oversnapshot': False,
        'undersnapshot': False,
        'other_snapshots': [],
        'unwanted_snapshots': []
    }

    snapshots = filesystem.snapshots()
    policy = config.get_policy()

    # Počítej snapshot typy
    counts = count_snapshots_by_type(snapshots)

    # Kontrola oversnapshot/undersnapshot
    for snap_type, expected in policy.items():
        actual = counts.get(snap_type, 0)

        if actual > expected:
            health['oversnapshot'] = True
        elif actual < expected * 0.9:  # 10% tolerance
            health['undersnapshot'] = True

    # Najdi non-pyznap snapshoty
    for snap in snapshots:
        if not is_pyznap_snapshot(snap.name):
            health['other_snapshots'].append(snap.name)

    # Kontrola excluded filesystems
    if filesystem.is_excluded():
        health['unwanted_snapshots'] = [s.name for s in snapshots]

    return health
```

**Priorita:** NÍZKÁ
**Effort:** Střední
**Impact:** Nízký (nice to have)

### 4. `main.py:147` - Time shift

```python
# TODO: time shift
```

**Kontext:** Pro příkaz `fix` umožnit posunout časové značky snapshotů.

**Use case:** Migrace z jiných nástrojů (zfs-auto-snapshot, sanoid)

**Řešení:**
```python
# Nový příkaz: pyznap fix --time-shift

def time_shift_snapshots(filesystem, delta_hours):
    """Posune časové značky snapshotů (jen pro migraci)"""
    # Poznámka: ZFS nepodporuje změnu creation time přímo
    # Řešení: Rename snapshoty s novým timestampem v názvu

    snapshots = filesystem.snapshots()

    for snap in snapshots:
        old_name = snap.name
        old_time = parse_snapshot_time(old_name)
        new_time = old_time + timedelta(hours=delta_hours)
        new_name = format_snapshot_name(filesystem.name, new_time)

        logger.info(f"Renaming {old_name} -> {new_name}")
        snap.rename(new_name)
```

**Priorita:** VELMI NÍZKÁ
**Effort:** Střední
**Impact:** Velmi nízký (edge case)

### 5. `pyzfs.py:320-321` - Split force flags

```python
# TODO: split force to allow -f, -r and -R to be specified individually
```

**Současný kód:**
```python
def destroy(self, *, force=False, ...):
    cmd = ['zfs', 'destroy']
    if force:
        cmd += ['-f', '-R']  # Vždy společně
```

**Řešení:**
```python
def destroy(self, *, force=False, recursive=False, recursive_deps=False, ...):
    cmd = ['zfs', 'destroy']

    if force:
        cmd.append('-f')
    if recursive:
        cmd.append('-r')
    if recursive_deps:
        cmd.append('-R')
```

**Priorita:** VELMI NÍZKÁ
**Effort:** Velmi nízký
**Impact:** Velmi nízký (API change)

---

## Hlavní priorita: Automatická kontrola remote snapshotů

### Současný stav problému

#### Jak aktuálně funguje send/receive

```python
# send.py - současná logika (zjednodušeně)
def send_filesystem(source_fs, dest_fs):
    source_snaps = source_fs.snapshots()
    dest_snaps = dest_fs.snapshots()

    # Najdi společné snapshoty
    source_names = {s.name.split('@')[1] for s in source_snaps}
    dest_names = {s.name.split('@')[1] for s in dest_snaps}
    common = source_names & dest_names

    if not common:
        # Full send
        send_full(source_snaps[0])
    else:
        # Incremental send
        base = max(common, key=lambda x: source_snaps[x].creation)
        send_incremental(base, source_snaps[0])
```

**Problémy:**
1. ❌ Nekontroluje se věk posledního společného snapshotu
2. ❌ Žádné varování, když destinace zaostává
3. ❌ Neověřuje se, že skutečně poslední snapshot byl přenesen
4. ❌ Chybí monitoring úspěšnosti přenosů

### Požadované funkce

#### 1. Automatická verifikace remote snapshotů

```python
def verify_remote_snapshots(source_fs, dest_fs, config):
    """
    Ověří, že remote snapshoty jsou aktuální

    Kontroluje:
    - Existenci všech kritických snapshotů
    - Časové zpoždění mezi source a dest
    - Integritu snapshot historie
    - Missing incremental steps

    Returns:
        VerificationReport s detaily a doporučeními
    """
    report = VerificationReport()

    # 1. Načti snapshoty s metadaty
    source_snaps = get_snapshots_with_metadata(source_fs)
    dest_snaps = get_snapshots_with_metadata(dest_fs)

    # 2. Zkontroluj základní dostupnost
    if not dest_snaps:
        report.add_error("Destination has no snapshots")
        report.status = Status.CRITICAL
        return report

    # 3. Najdi nejnovější společný snapshot
    latest_common = find_latest_common_snapshot(source_snaps, dest_snaps)

    if not latest_common:
        report.add_error("No common snapshots found")
        report.status = Status.CRITICAL
        report.add_recommendation("Perform full send to re-establish sync")
        return report

    # 4. Vypočítej časové zpoždění (lag)
    source_latest = max(source_snaps, key=lambda s: s.creation_time)
    lag = source_latest.creation_time - latest_common.creation_time
    report.lag_seconds = lag.total_seconds()

    # 5. Vyhodnoť podle thresholdů
    thresholds = config.get('verify_thresholds', {
        'ok': 86400,        # 1 den
        'warning': 172800,  # 2 dny
        'critical': 604800  # 7 dní
    })

    if report.lag_seconds < thresholds['ok']:
        report.status = Status.OK
    elif report.lag_seconds < thresholds['warning']:
        report.status = Status.WARNING
        report.add_recommendation("Consider increasing backup frequency")
    elif report.lag_seconds < thresholds['critical']:
        report.status = Status.ERROR
        report.add_recommendation("Immediate backup required")
    else:
        report.status = Status.CRITICAL
        report.add_recommendation("URGENT: Backup severely out of date")

    # 6. Kontrola integrity - chybějící mezikroky
    missing_incrementals = check_missing_incrementals(
        source_snaps, dest_snaps, latest_common
    )

    if missing_incrementals:
        report.add_warning(f"{len(missing_incrementals)} missing incremental snapshots")
        report.missing_snapshots = missing_incrementals

    # 7. Kontrola podle snapshot typu (daily, weekly, monthly)
    for snap_type in ['daily', 'weekly', 'monthly']:
        source_count = count_snapshots_of_type(source_snaps, snap_type)
        dest_count = count_snapshots_of_type(dest_snaps, snap_type)

        coverage = dest_count / source_count if source_count > 0 else 0

        if coverage < 0.8:  # Méně než 80% pokrytí
            report.add_warning(
                f"Low {snap_type} snapshot coverage: {coverage*100:.0f}%"
            )

    return report
```

#### 2. Helper funkce

```python
def get_snapshots_with_metadata(filesystem):
    """Načte snapshoty včetně metadat (creation time, size, etc.)"""
    snapshots = []

    for snap in filesystem.snapshots():
        metadata = {
            'name': snap.name,
            'creation_time': datetime.fromtimestamp(int(snap.getprops()['creation']['value'])),
            'used': int(snap.getprops()['used']['value']),
            'referenced': int(snap.getprops()['referenced']['value']),
            'type': extract_snapshot_type(snap.name)  # hourly, daily, etc.
        }
        snapshots.append(SnapshotInfo(**metadata))

    return sorted(snapshots, key=lambda s: s.creation_time, reverse=True)


def find_latest_common_snapshot(source_snaps, dest_snaps):
    """Najde nejnovější společný snapshot"""
    source_names = {extract_snapshot_name(s.name) for s in source_snaps}
    dest_names = {extract_snapshot_name(s.name) for s in dest_snaps}

    common_names = source_names & dest_names

    if not common_names:
        return None

    # Najdi ten s nejnovějším časem
    common_snaps = [s for s in source_snaps if extract_snapshot_name(s.name) in common_names]
    return max(common_snaps, key=lambda s: s.creation_time)


def check_missing_incrementals(source_snaps, dest_snaps, latest_common):
    """
    Zkontroluje, zda nejsou missing snapshoty mezi latest_common a source_latest

    Důležité pro validaci integrity - pokud chybí mezikroky,
    může to znamenat problém s incremental send chain.
    """
    dest_names = {extract_snapshot_name(s.name) for s in dest_snaps}

    missing = []
    found_common = False

    for snap in sorted(source_snaps, key=lambda s: s.creation_time):
        if snap.name == latest_common.name:
            found_common = True
            continue

        if found_common:
            snap_name = extract_snapshot_name(snap.name)
            if snap_name not in dest_names:
                missing.append(snap)

    return missing


def count_snapshots_of_type(snapshots, snap_type):
    """Spočítá snapshoty daného typu"""
    return len([s for s in snapshots if s.type == snap_type])


def extract_snapshot_type(full_name):
    """
    Extrahuje typ snapshotu z názvu

    Format: pool/dataset@pyznap_<timestamp>_<type>
    Příklad: tank/data@pyznap_2025-11-15_143022_daily
    """
    parts = full_name.split('@')[-1].split('_')
    if len(parts) >= 3:
        return parts[-1]  # daily, weekly, etc.
    return 'unknown'


def extract_snapshot_name(full_name):
    """Extrahuje snapshot jméno bez pool/dataset prefix"""
    return full_name.split('@')[-1] if '@' in full_name else full_name
```

#### 3. Report data structures

```python
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
from datetime import datetime

class Status(Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class SnapshotInfo:
    """Metadata o snapshotu"""
    name: str
    creation_time: datetime
    used: int
    referenced: int
    type: str  # hourly, daily, weekly, monthly, yearly


@dataclass
class VerificationReport:
    """Report z verifikace remote snapshotů"""
    status: Status = Status.UNKNOWN
    lag_seconds: float = 0
    missing_snapshots: List[SnapshotInfo] = None
    errors: List[str] = None
    warnings: List[str] = None
    recommendations: List[str] = None

    def __post_init__(self):
        if self.missing_snapshots is None:
            self.missing_snapshots = []
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
        if self.recommendations is None:
            self.recommendations = []

    def add_error(self, message: str):
        self.errors.append(message)

    def add_warning(self, message: str):
        self.warnings.append(message)

    def add_recommendation(self, message: str):
        self.recommendations.append(message)

    def to_dict(self):
        """Serializuj pro JSON output"""
        return {
            'status': self.status.value,
            'lag_seconds': self.lag_seconds,
            'lag_human': format_duration(self.lag_seconds),
            'missing_snapshots_count': len(self.missing_snapshots),
            'missing_snapshots': [s.name for s in self.missing_snapshots],
            'errors': self.errors,
            'warnings': self.warnings,
            'recommendations': self.recommendations
        }

    def format_human_readable(self):
        """Formátuj pro lidsky čitelný výstup"""
        lines = []
        lines.append(f"Status: {self.status.value}")
        lines.append(f"Lag: {format_duration(self.lag_seconds)}")

        if self.errors:
            lines.append("\nErrors:")
            for err in self.errors:
                lines.append(f"  ❌ {err}")

        if self.warnings:
            lines.append("\nWarnings:")
            for warn in self.warnings:
                lines.append(f"  ⚠️  {warn}")

        if self.missing_snapshots:
            lines.append(f"\nMissing snapshots: {len(self.missing_snapshots)}")
            for snap in self.missing_snapshots[:5]:  # Show first 5
                lines.append(f"  - {snap.name}")
            if len(self.missing_snapshots) > 5:
                lines.append(f"  ... and {len(self.missing_snapshots) - 5} more")

        if self.recommendations:
            lines.append("\nRecommendations:")
            for rec in self.recommendations:
                lines.append(f"  💡 {rec}")

        return '\n'.join(lines)


def format_duration(seconds):
    """Formátuj sekundy na lidsky čitelný formát"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.0f}m"
    elif seconds < 86400:
        return f"{seconds/3600:.1f}h"
    else:
        return f"{seconds/86400:.1f}d"
```

### Integrace do existujícího kódu

#### 1. Rozšíření status.py

```python
# status.py - přidání do status_filesystem()

def status_filesystem(config, args, _prefix=''):
    """Status pro jeden filesystem - ROZŠÍŘENO O REMOTE VERIFICATION"""

    # ... existující kód ...

    # NOVÉ: Remote verification
    if dest and not args.skip_remote_verification:
        for i, d in enumerate(dest):
            dest_name = d.name
            _prefix_dest = f"{_prefix}dest-{i}-"

            # Otevři destination
            try:
                if isinstance(d, ZFSConnection):
                    dest_fs = d.open(dest_name)
                else:
                    dest_fs = zfs.open(dest_name)
            except (DatasetNotFound, CalledProcessError):
                status[_prefix_dest + 'verification-status'] = 'ERROR'
                status[_prefix_dest + 'verification-error'] = 'Destination not found'
                continue

            # Proveď verifikaci
            logger.info(f"Verifying remote snapshots for {dest_name}...")

            try:
                verification_config = {
                    'verify_thresholds': {
                        'ok': args.verify_ok_threshold or 86400,
                        'warning': args.verify_warning_threshold or 172800,
                        'critical': args.verify_critical_threshold or 604800
                    }
                }

                report = verify_remote_snapshots(filesystem, dest_fs, verification_config)

                # Přidej do status outputu
                status[_prefix_dest + 'verification-status'] = report.status.value
                status[_prefix_dest + 'verification-lag-seconds'] = report.lag_seconds
                status[_prefix_dest + 'verification-lag-human'] = format_duration(report.lag_seconds)
                status[_prefix_dest + 'verification-missing-count'] = len(report.missing_snapshots)

                # Verbose výstup
                if args.verbose and report.status != Status.OK:
                    logger.warning(f"\n{report.format_human_readable()}")

                # Loguj problémy
                for error in report.errors:
                    logger.error(f"{dest_name}: {error}")
                for warning in report.warnings:
                    logger.warning(f"{dest_name}: {warning}")

            except Exception as e:
                logger.error(f"Verification failed for {dest_name}: {e}")
                status[_prefix_dest + 'verification-status'] = 'ERROR'
                status[_prefix_dest + 'verification-error'] = str(e)
```

#### 2. Nový CLI příkaz `pyznap verify`

```python
# main.py - přidání nového subcommand

def main():
    parser = argparse.ArgumentParser(prog='pyznap', ...)
    subparsers = parser.add_subparsers(dest='command')

    # ... existující subcommands ...

    # NOVÝ: verify subcommand
    parser_verify = subparsers.add_parser(
        'verify',
        help='Verify remote backup health'
    )
    parser_verify.add_argument(
        '--max-lag',
        type=int,
        default=86400,
        help='Maximum acceptable lag in seconds (default: 86400 = 1 day)'
    )
    parser_verify.add_argument(
        '--fix',
        action='store_true',
        help='Attempt to fix issues automatically by triggering send'
    )
    parser_verify.add_argument(
        '--json',
        action='store_true',
        help='Output results as JSON'
    )
    parser_verify.add_argument(
        '--nagios',
        action='store_true',
        help='Output in Nagios-compatible format'
    )

    # ... zbytek main() ...


def verify_command(config, args):
    """
    Implementace verify příkazu

    Použití:
        pyznap verify                    # Verify všechno podle config
        pyznap verify --max-lag 3600     # Požaduj max 1h lag
        pyznap verify --fix              # Auto-fix problems
        pyznap verify --json             # JSON output
        pyznap verify --nagios           # Pro monitoring
    """
    results = []
    overall_status = Status.OK

    for conf in config:
        if 'dest' not in conf:
            continue

        source_name = conf['name']
        logger.info(f"Verifying {source_name}...")

        # Otevři source
        try:
            source_fs = open_ssh_source(source_name, conf) if is_ssh_source(conf) else zfs.open(source_name)
        except Exception as e:
            logger.error(f"Cannot open source {source_name}: {e}")
            continue

        # Verify každou destination
        for dest in conf['dest']:
            dest_name = dest['name']

            try:
                # Otevři destination
                dest_fs = open_ssh_dest(dest_name, dest) if is_ssh_dest(dest) else zfs.open(dest_name)

                # Proveď verifikaci
                verification_config = {
                    'verify_thresholds': {
                        'ok': args.max_lag,
                        'warning': args.max_lag * 2,
                        'critical': args.max_lag * 7
                    }
                }

                report = verify_remote_snapshots(source_fs, dest_fs, verification_config)

                results.append({
                    'source': source_name,
                    'dest': dest_name,
                    'report': report
                })

                # Update overall status
                if report.status.value > overall_status.value:
                    overall_status = report.status

                # Auto-fix pokud requested
                if args.fix and report.status in [Status.ERROR, Status.CRITICAL]:
                    logger.info(f"Attempting to fix {source_name} -> {dest_name}")
                    try:
                        send_config({**conf, 'dest': [dest]}, args)
                        logger.info("Fix successful")
                    except Exception as e:
                        logger.error(f"Fix failed: {e}")

            except Exception as e:
                logger.error(f"Verification failed for {source_name} -> {dest_name}: {e}")
                results.append({
                    'source': source_name,
                    'dest': dest_name,
                    'error': str(e)
                })

    # Format output
    if args.json:
        output_json(results)
    elif args.nagios:
        output_nagios(results, overall_status)
    else:
        output_human_readable(results)

    # Exit code podle overall status
    exit_codes = {
        Status.OK: 0,
        Status.WARNING: 1,
        Status.ERROR: 2,
        Status.CRITICAL: 2,
        Status.UNKNOWN: 3
    }

    return exit_codes.get(overall_status, 99)


def output_human_readable(results):
    """Human-readable output"""
    print("\n" + "="*80)
    print("PYZNAP REMOTE BACKUP VERIFICATION REPORT")
    print("="*80 + "\n")

    for result in results:
        source = result['source']
        dest = result['dest']

        print(f"Source: {source}")
        print(f"Destination: {dest}")
        print("-" * 80)

        if 'error' in result:
            print(f"❌ ERROR: {result['error']}\n")
        else:
            report = result['report']
            print(report.format_human_readable())
            print()


def output_json(results):
    """JSON output pro programmatic use"""
    import json

    output = []
    for result in results:
        if 'error' in result:
            output.append({
                'source': result['source'],
                'dest': result['dest'],
                'status': 'ERROR',
                'error': result['error']
            })
        else:
            output.append({
                'source': result['source'],
                'dest': result['dest'],
                **result['report'].to_dict()
            })

    print(json.dumps(output, indent=2))


def output_nagios(results, overall_status):
    """Nagios-compatible output"""
    status_map = {
        Status.OK: 0,
        Status.WARNING: 1,
        Status.ERROR: 2,
        Status.CRITICAL: 2,
        Status.UNKNOWN: 3
    }

    exit_code = status_map.get(overall_status, 3)

    # Performance data
    perf_data = []
    for result in results:
        if 'error' not in result:
            report = result['report']
            source = result['source'].replace('/', '_')
            dest = result['dest'].replace('/', '_')
            perf_data.append(f"{source}_to_{dest}_lag={report.lag_seconds}s")

    # Status message
    ok_count = sum(1 for r in results if 'error' not in r and r['report'].status == Status.OK)
    total_count = len(results)

    status_msg = f"PYZNAP {overall_status.value}: {ok_count}/{total_count} destinations OK"

    if perf_data:
        print(f"{status_msg} | {' '.join(perf_data)}")
    else:
        print(status_msg)

    sys.exit(exit_code)
```

### Speciální případy a edge cases

#### 1. Remote neexistuje dostatečně dlouho

**Problém:** Remote může být nový nebo po data loss, nemá úplnou historii.

**Řešení:**
```python
def verify_remote_snapshots(source_fs, dest_fs, config):
    # ... existing code ...

    # Speciální handling pro nové remote
    if not dest_snaps:
        report.status = Status.WARNING  # Ne ERROR!
        report.add_warning("Destination has no snapshots (new remote?)")
        report.add_recommendation("Initialize with: pyznap send -s <source> -d <dest>")
        return report

    # Kontrola "initial sync" scenario
    oldest_dest = min(dest_snaps, key=lambda s: s.creation_time)
    oldest_source = min(source_snaps, key=lambda s: s.creation_time)

    if oldest_dest.creation_time > oldest_source.creation_time:
        # Remote je mladší než source - očekáváno u nových backupů
        report.add_info(f"Remote initialized at {oldest_dest.creation_time}")
        report.add_info("Partial history is expected for new backup destinations")

        # Adjust expectations - kontroluj jen od oldest_dest
        relevant_source_snaps = [
            s for s in source_snaps
            if s.creation_time >= oldest_dest.creation_time
        ]

        # Re-calculate based on relevant snapshots
        return verify_partial_history(
            relevant_source_snaps, dest_snaps, config
        )
```

#### 2. Přenášejí se skutečně poslední snapshoty?

**Kontrola:**
```python
def check_latest_snapshots_transferred(source_fs, dest_fs, config):
    """
    Ověří, že nejnovější snapshoty byly přeneseny

    Kritéria:
    - Poslední daily snapshot na dest nesmí být starší než 2 dny
    - Poslední weekly snapshot na dest nesmí být starší než 10 dní
    - Poslední monthly snapshot na dest nesmí být starší než 35 dní
    """
    results = {}

    source_snaps = get_snapshots_with_metadata(source_fs)
    dest_snaps = get_snapshots_with_metadata(dest_fs)

    for snap_type, max_age_days in [('daily', 2), ('weekly', 10), ('monthly', 35)]:
        # Najdi nejnovější snapshot daného typu
        source_latest = find_latest_of_type(source_snaps, snap_type)
        dest_latest = find_latest_of_type(dest_snaps, snap_type)

        if not source_latest:
            continue  # Tento typ není používán

        if not dest_latest:
            results[snap_type] = {
                'status': 'MISSING',
                'message': f'No {snap_type} snapshots on destination'
            }
            continue

        # Vypočti věk dest snapshotu
        age = datetime.now() - dest_latest.creation_time
        age_days = age.total_seconds() / 86400

        if age_days > max_age_days:
            results[snap_type] = {
                'status': 'OUTDATED',
                'age_days': age_days,
                'max_age_days': max_age_days,
                'message': f'Latest {snap_type} snapshot is {age_days:.1f} days old (max: {max_age_days})'
            }
        else:
            results[snap_type] = {
                'status': 'OK',
                'age_days': age_days,
                'message': f'Latest {snap_type} snapshot is {age_days:.1f} days old'
            }

    return results


def find_latest_of_type(snapshots, snap_type):
    """Najde nejnovější snapshot daného typu"""
    typed = [s for s in snapshots if s.type == snap_type]
    return max(typed, key=lambda s: s.creation_time) if typed else None
```

#### 3. Detekce chybějících inkrementálů

**Důležité:** Pokud chybí mezikroky v incremental chain, může být problém.

```python
def validate_incremental_chain(source_snaps, dest_snaps):
    """
    Validuje, že incremental chain je kompletní

    ZFS send -I vyžaduje kompletní chain. Pokud chybí mezikrok,
    další incremental send může selhat.
    """
    issues = []

    # Najdi společné snapshoty
    common = find_common_snapshots(source_snaps, dest_snaps)

    if not common:
        return issues

    # Seřaď chronologicky
    common_sorted = sorted(common, key=lambda s: s.creation_time)

    # Kontroluj mezery v source snapshots
    for i in range(len(common_sorted) - 1):
        current = common_sorted[i]
        next_common = common_sorted[i + 1]

        # Najdi všechny source snapshoty mezi těmito dvěma
        between = [
            s for s in source_snaps
            if current.creation_time < s.creation_time < next_common.creation_time
        ]

        # Zkontroluj, jestli všechny jsou na dest
        dest_names = {extract_snapshot_name(s.name) for s in dest_snaps}

        for snap in between:
            snap_name = extract_snapshot_name(snap.name)
            if snap_name not in dest_names:
                issues.append({
                    'type': 'MISSING_INCREMENTAL',
                    'snapshot': snap.name,
                    'between': (current.name, next_common.name),
                    'impact': 'May break incremental send chain'
                })

    return issues
```

---

## Prioritizovaný akční plán

### FÁZE 1: KRITICKÉ (Okamžitě) ⚠️

#### 1.1 Bezpečnostní opravy (Effort: 2-3 dny)

**Úkoly:**
- [ ] Audit všech subprocess volání
- [ ] Přidat `shlex.quote()` všude kde chybí
- [ ] Validovat všechny user inputs proti whitelist
- [ ] Opravit SSH socket naming (UUID)
- [ ] Implementovat host key verification
- [ ] Security review SSH key handling

**Soubory k editaci:**
- `pyznap/pyzfs.py` - add parameter validation
- `pyznap/ssh.py` - fix socket naming, add host key verification
- `pyznap/send.py` - validate dataset names
- `pyznap/utils.py` - add input validation functions

**Test plán:**
```bash
# Test command injection
pyznap send -s 'tank/data; rm -rf /' -d backup/data  # Musí selhat

# Test path traversal
pyznap send -s 'tank/../../../etc/passwd' -d backup  # Musí selhat

# Test SSH security
# Ověřit UUID socket names
# Ověřit host key verification
```

#### 1.2 Error recovery vylepšení (Effort: 1-2 dny)

**Úkoly:**
- [ ] Implementovat cleanup při selhání send/receive
- [ ] Přidat resume token validation
- [ ] Implementovat rollback na recovery point
- [ ] Unified logging setup
- [ ] Konzistentní exit kódy

**Soubory k editaci:**
- `pyznap/send.py` - add recovery mechanism
- `pyznap/main.py` - unified logging, exit codes
- `pyznap/utils.py` - logging helpers

### FÁZE 2: VYSOKÁ PRIORITA (Tento týden) ⭐

#### 2.1 Remote snapshot verification (Effort: 3-4 dny)

**Úkoly:**
- [ ] Implementovat `verify_remote_snapshots()` funkci
- [ ] Vytvořit data structures (VerificationReport, SnapshotInfo)
- [ ] Helper functions (get_snapshots_with_metadata, etc.)
- [ ] Integrace do `status.py`
- [ ] Nový příkaz `pyznap verify`
- [ ] JSON/Nagios output formáty
- [ ] Dokumentace a příklady použití

**Soubory k editaci:**
- Nový: `pyznap/verification.py` - core verification logic
- `pyznap/status.py` - integrace verification
- `pyznap/main.py` - nový verify command
- `README.md` - dokumentace verify příkazu

**Test cases:**
```python
# Test scenarios:
1. Happy path - remote je aktuální
2. Remote zaostává o 1 den (WARNING)
3. Remote zaostává o 7 dní (CRITICAL)
4. Remote je nový (pouze partial history)
5. Chybí mezikroky v incremental chain
6. Remote je prázdný
7. Žádné společné snapshoty
8. SSH connection failure
```

#### 2.2 Vytvoření missing parent datasets (Effort: 0.5 dne)

**Úkoly:**
- [ ] Implementovat `ensure_parent_exists()` v send.py
- [ ] Přidat flag `--create-parents` (default: false)
- [ ] Testy pro edge cases

**Soubory:**
- `pyznap/send.py:379` - resolve TODO

### FÁZE 3: STŘEDNÍ PRIORITA (Příští týden)

#### 3.1 Code quality refactoring (Effort: 3-4 dny)

**Úkoly:**
- [ ] Refaktor `send_config()` - rozdělit na menší funkce
- [ ] Refaktor `status_filesystem()` - strategy pattern
- [ ] Centralizovat SSH connection management
- [ ] Odstranit code duplicity
- [ ] Přidat type hints (Python 3.5+)

**Metriky:**
```bash
# Před refactorováním
radon cc pyznap/*.py -a
# Cíl: snížit průměrnou CC z 12 na <8

# Měření duplicit
pylint pyznap/ --disable=all --enable=duplicate-code
```

#### 3.2 Testing infrastructure (Effort: 2-3 dny)

**Úkoly:**
- [ ] Setup pytest s fixtures
- [ ] Mock ZFS commands
- [ ] Mock SSH connections
- [ ] Unit testy pro kritické funkce
- [ ] Integration testy s Docker
- [ ] CI/CD setup (GitHub Actions)

**Soubory:**
```
tests/
├── unit/
│   ├── test_verification.py
│   ├── test_send.py
│   ├── test_ssh.py
│   └── test_security.py
├── integration/
│   ├── test_full_backup.py
│   └── test_ssh_backup.py
├── fixtures/
│   ├── zfs_mock.py
│   └── ssh_mock.py
└── conftest.py
```

### FÁZE 4: NÍZKÁ PRIORITA (Budoucnost)

#### 4.1 Nice-to-have features

- [ ] Snapshot health analysis (oversnapshot/undersnapshot) - status.py:136
- [ ] Time shift pro migrace - main.py:147
- [ ] Split force flags - pyzfs.py:320
- [ ] Prometheus metrics export
- [ ] Web dashboard
- [ ] Email alerting

---

## Konkrétní návrhy implementace

### 1. Bezpečnostní vylepšení

#### Input validation framework

```python
# Nový soubor: pyznap/security.py

import re
import shlex
from typing import Union

class SecurityValidator:
    """Centralizovaná validace inputs"""

    # Whitelist povolených znaků pro různé typy inputs
    DATASET_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_\-./]+$')
    SSH_HOST_PATTERN = re.compile(r'^[a-zA-Z0-9\-.]+(:[0-9]+)?$')
    SNAPSHOT_TYPE_WHITELIST = {
        'filesystem', 'volume', 'snapshot', 'bookmark', 'all'
    }
    COMPRESS_TYPE_WHITELIST = {
        'none', 'lzop', 'lz4', 'gzip', 'pigz', 'bzip2', 'xz'
    }

    @classmethod
    def validate_dataset_name(cls, name: str) -> str:
        """
        Validuje ZFS dataset název

        Raises:
            ValueError: Pokud název není validní
        """
        if not name:
            raise ValueError("Dataset name cannot be empty")

        if not cls.DATASET_NAME_PATTERN.match(name):
            raise ValueError(
                f"Invalid dataset name: {name}. "
                "Only alphanumeric, dash, underscore, dot and slash allowed."
            )

        # Zakázané patterny
        if '..' in name or '//' in name:
            raise ValueError("Dataset name cannot contain '..' or '//'")

        if name.startswith('/'):
            raise ValueError("Dataset name cannot start with '/'")

        # Max délka (ZFS limit je 256)
        if len(name) > 256:
            raise ValueError("Dataset name too long (max 256 characters)")

        return name

    @classmethod
    def validate_ssh_destination(cls, dest: str) -> dict:
        """
        Parsuje a validuje SSH destination string

        Format: ssh:port:user@host:dataset

        Returns:
            dict s klíči: user, host, port, dataset
        """
        if not dest.startswith('ssh:'):
            raise ValueError("SSH destination must start with 'ssh:'")

        parts = dest.split(':')

        if len(parts) < 4:
            raise ValueError(
                "Invalid SSH destination format. "
                "Expected: ssh:port:user@host:dataset"
            )

        port_str = parts[1]
        user_host = parts[2]
        dataset = ':'.join(parts[3:])  # Dataset může obsahovat :

        # Validate port
        try:
            port = int(port_str) if port_str else 22
            if not 1 <= port <= 65535:
                raise ValueError("Port must be 1-65535")
        except ValueError:
            raise ValueError(f"Invalid port: {port_str}")

        # Parse user@host
        if '@' not in user_host:
            raise ValueError("SSH destination must contain user@host")

        user, host = user_host.split('@', 1)

        # Validate user
        if not re.match(r'^[a-zA-Z0-9_\-]+$', user):
            raise ValueError(f"Invalid SSH user: {user}")

        # Validate host
        if not re.match(r'^[a-zA-Z0-9\-\.]+$', host):
            raise ValueError(f"Invalid SSH host: {host}")

        # Validate dataset
        dataset = cls.validate_dataset_name(dataset)

        return {
            'user': user,
            'host': host,
            'port': port,
            'dataset': dataset
        }

    @classmethod
    def safe_shell_arg(cls, arg: Union[str, int]) -> str:
        """
        Bezpečně escapuje argument pro shell

        Používá shlex.quote() + dodatečnou validaci
        """
        arg_str = str(arg)

        # Zakázané znaky (defense in depth)
        forbidden = ['\x00', '\n', '\r', ';', '&', '|', '$', '`']

        for char in forbidden:
            if char in arg_str:
                raise ValueError(
                    f"Argument contains forbidden character: {repr(char)}"
                )

        return shlex.quote(arg_str)

    @classmethod
    def validate_config_value(cls, key: str, value: any) -> any:
        """Validuje hodnoty z config file"""

        if key in ['frequent', 'hourly', 'daily', 'weekly', 'monthly', 'yearly']:
            # Snapshot counts
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{key} must be non-negative integer")
            if value > 1000:
                raise ValueError(f"{key} count too high (max 1000)")
            return value

        elif key == 'compress':
            if value not in cls.COMPRESS_TYPE_WHITELIST:
                raise ValueError(
                    f"Invalid compression: {value}. "
                    f"Allowed: {cls.COMPRESS_TYPE_WHITELIST}"
                )
            return value

        elif key == 'max_depth':
            if value != 'no' and (not isinstance(value, int) or value < 0):
                raise ValueError("max_depth must be 'no' or non-negative integer")
            return value

        # Default: return as-is (but logged)
        return value


# Použití v pyzfs.py
from .security import SecurityValidator

def find(path=None, *, max_depth=None, types=None, ...):
    cmd = ['zfs', 'list', '-H', '-o', 'name']

    if types:
        # Validace proti whitelist
        if types not in SecurityValidator.SNAPSHOT_TYPE_WHITELIST:
            raise ValueError(f"Invalid types parameter: {types}")
        cmd += ['-t', types]

    if path:
        # Validace a escapování
        validated_path = SecurityValidator.validate_dataset_name(path)
        cmd.append(SecurityValidator.safe_shell_arg(validated_path))

    # ... rest of function
```

### 2. Monitoring a alerting

#### Prometheus metrics export

```python
# Nový soubor: pyznap/metrics.py

from typing import Dict, List
from dataclasses import dataclass
from datetime import datetime

@dataclass
class BackupMetrics:
    """Metriky pro monitoring"""
    source: str
    dest: str
    status: str  # ok, warning, error, critical
    lag_seconds: float
    last_successful_send: datetime
    total_snapshots_source: int
    total_snapshots_dest: int
    missing_snapshots: int

    def to_prometheus(self) -> str:
        """Exportuj jako Prometheus metrics"""
        labels = f'source="{self.source}",dest="{self.dest}"'

        lines = []
        lines.append(f'# HELP pyznap_backup_lag_seconds Time lag between source and dest')
        lines.append(f'# TYPE pyznap_backup_lag_seconds gauge')
        lines.append(f'pyznap_backup_lag_seconds{{{labels}}} {self.lag_seconds}')

        lines.append(f'# HELP pyznap_backup_status Backup status (0=ok, 1=warning, 2=error, 3=critical)')
        lines.append(f'# TYPE pyznap_backup_status gauge')
        status_code = {'ok': 0, 'warning': 1, 'error': 2, 'critical': 3}
        lines.append(f'pyznap_backup_status{{{labels}}} {status_code.get(self.status.lower(), 3)}')

        lines.append(f'# HELP pyznap_snapshots_total Total number of snapshots')
        lines.append(f'# TYPE pyznap_snapshots_total gauge')
        lines.append(f'pyznap_snapshots_total{{{labels},location="source"}} {self.total_snapshots_source}')
        lines.append(f'pyznap_snapshots_total{{{labels},location="dest"}} {self.total_snapshots_dest}')

        lines.append(f'# HELP pyznap_missing_snapshots Number of missing snapshots on destination')
        lines.append(f'# TYPE pyznap_missing_snapshots gauge')
        lines.append(f'pyznap_missing_snapshots{{{labels}}} {self.missing_snapshots}')

        return '\n'.join(lines)


def export_metrics(results: List[BackupMetrics], output_file: str = '/var/lib/node_exporter/textfile_collector/pyznap.prom'):
    """
    Exportuj metriky pro Prometheus

    Použití s node_exporter textfile collector:
    1. pyznap verify --export-metrics
    2. node_exporter shromáždí metrics z /var/lib/node_exporter/textfile_collector/
    """
    import os
    import tempfile

    # Atomic write
    with tempfile.NamedTemporaryFile(mode='w', dir=os.path.dirname(output_file), delete=False) as f:
        for result in results:
            f.write(result.to_prometheus())
            f.write('\n\n')

        temp_path = f.name

    os.rename(temp_path, output_file)
```

#### Email alerting

```python
# Nový soubor: pyznap/alerting.py

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List
from .verification import VerificationReport

class AlertManager:
    """Manages alerts for backup issues"""

    def __init__(self, config: dict):
        self.smtp_host = config.get('smtp_host', 'localhost')
        self.smtp_port = config.get('smtp_port', 25)
        self.smtp_user = config.get('smtp_user')
        self.smtp_pass = config.get('smtp_pass')
        self.from_addr = config.get('from_addr', 'pyznap@localhost')
        self.to_addrs = config.get('to_addrs', [])

    def should_alert(self, report: VerificationReport) -> bool:
        """Determine if alert should be sent"""
        return report.status in [Status.ERROR, Status.CRITICAL]

    def send_alert(self, source: str, dest: str, report: VerificationReport):
        """Send email alert"""
        if not self.to_addrs:
            return

        subject = f"[PYZNAP ALERT] {report.status.value}: {source} -> {dest}"

        body = f"""
Backup verification failed for:

Source: {source}
Destination: {dest}
Status: {report.status.value}
Lag: {format_duration(report.lag_seconds)}

{report.format_human_readable()}

--
This is an automated message from pyznap
        """.strip()

        msg = MIMEMultipart()
        msg['From'] = self.from_addr
        msg['To'] = ', '.join(self.to_addrs)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.smtp_user:
                    server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)
        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")
```

### 3. Testing framework

```python
# tests/unit/test_verification.py

import pytest
from datetime import datetime, timedelta
from pyznap.verification import (
    verify_remote_snapshots,
    SnapshotInfo,
    Status
)

class MockFilesystem:
    """Mock ZFS filesystem pro testy"""

    def __init__(self, snapshots):
        self._snapshots = snapshots

    def snapshots(self):
        return self._snapshots


def create_snapshot(name, hours_ago=0, snap_type='daily'):
    """Helper pro vytvoření mock snapshotu"""
    creation_time = datetime.now() - timedelta(hours=hours_ago)

    return SnapshotInfo(
        name=f"tank/data@pyznap_{creation_time.strftime('%Y%m%d_%H%M%S')}_{snap_type}",
        creation_time=creation_time,
        used=1000000,
        referenced=5000000,
        type=snap_type
    )


class TestRemoteVerification:

    def test_up_to_date_remote(self):
        """Test kdy je remote aktuální"""
        # Source: 3 snapshoty, nejnovější před 1h
        source_snaps = [
            create_snapshot('s1', hours_ago=1),
            create_snapshot('s2', hours_ago=25),
            create_snapshot('s3', hours_ago=49)
        ]

        # Dest: stejné snapshoty
        dest_snaps = source_snaps.copy()

        source_fs = MockFilesystem(source_snaps)
        dest_fs = MockFilesystem(dest_snaps)

        report = verify_remote_snapshots(source_fs, dest_fs, {
            'verify_thresholds': {'ok': 86400, 'warning': 172800, 'critical': 604800}
        })

        assert report.status == Status.OK
        assert report.lag_seconds < 3600  # < 1 hodina
        assert len(report.errors) == 0

    def test_lagging_remote_warning(self):
        """Test kdy remote zaostává (WARNING)"""
        source_snaps = [
            create_snapshot('s1', hours_ago=1),
            create_snapshot('s2', hours_ago=25),
        ]

        # Dest: chybí nejnovější snapshot
        dest_snaps = [
            create_snapshot('s2', hours_ago=25),
        ]

        source_fs = MockFilesystem(source_snaps)
        dest_fs = MockFilesystem(dest_snaps)

        report = verify_remote_snapshots(source_fs, dest_fs, {
            'verify_thresholds': {'ok': 3600, 'warning': 86400, 'critical': 604800}
        })

        assert report.status == Status.WARNING
        assert report.lag_seconds > 3600

    def test_empty_remote(self):
        """Test kdy je remote prázdný"""
        source_snaps = [create_snapshot('s1', hours_ago=1)]
        dest_snaps = []

        source_fs = MockFilesystem(source_snaps)
        dest_fs = MockFilesystem(dest_snaps)

        report = verify_remote_snapshots(source_fs, dest_fs, {})

        assert report.status == Status.WARNING
        assert "no snapshots" in report.errors[0].lower()

    def test_no_common_snapshots(self):
        """Test kdy není společný snapshot"""
        source_snaps = [create_snapshot('s1', hours_ago=1)]
        dest_snaps = [create_snapshot('different', hours_ago=100)]

        source_fs = MockFilesystem(source_snaps)
        dest_fs = MockFilesystem(dest_snaps)

        report = verify_remote_snapshots(source_fs, dest_fs, {})

        assert report.status == Status.CRITICAL
        assert "no common" in report.errors[0].lower()

    def test_missing_incrementals(self):
        """Test kdy chybí mezikroky"""
        source_snaps = [
            create_snapshot('s1', hours_ago=1),
            create_snapshot('s2', hours_ago=25),
            create_snapshot('s3', hours_ago=49),
            create_snapshot('s4', hours_ago=73)
        ]

        # Dest: chybí s2 a s3
        dest_snaps = [
            create_snapshot('s1', hours_ago=1),
            create_snapshot('s4', hours_ago=73)
        ]

        source_fs = MockFilesystem(source_snaps)
        dest_fs = MockFilesystem(dest_snaps)

        report = verify_remote_snapshots(source_fs, dest_fs, {})

        assert len(report.missing_snapshots) == 2
        assert len(report.warnings) > 0
```

---

## Závěr a doporučení

### Shrnutí priorit

1. **KRITICKÉ - Bezpečnost (týden 1)**
   - Command injection fixes
   - SSH security improvements
   - Input validation framework

2. **VYSOKÉ - Remote verification (týden 2)**
   - Implementace automatické kontroly
   - Nový `pyznap verify` příkaz
   - Monitoring integrace

3. **STŘEDNÍ - Quality & Testing (týden 3-4)**
   - Code refactoring
   - Test coverage
   - Documentation

### Očekávané výsledky

Po implementaci těchto vylepšení:

✅ Bezpečnější použití v production prostředí
✅ Proaktivní detekce problémů s backupy
✅ Lepší observability a monitoring
✅ Vyšší kvalita kódu a udržovatelnost
✅ Robustnější error handling

### Další kroky

1. Review tohoto dokumentu s maintainery
2. Vytvoření GitHub issues pro jednotlivé úkoly
3. Naplánování sprintů
4. Start implementace podle priorit

---

*Dokument vytvořen: 2025-11-15*
*Verze: 1.0*
*Autor: Claude Code Review (Opus model)*
