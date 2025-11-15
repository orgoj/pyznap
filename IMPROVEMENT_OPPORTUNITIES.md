# Pyznap - Možnosti Vylepšení

**Datum:** 2025-11-15
**Současný stav:** Verify command implementován, 87 unit testů (100% pass rate)

---

## 🎯 Přehled Priorit

| Priorita | Oblast | Effort | Impact | ROI |
|----------|--------|--------|--------|-----|
| 🔥 VYSOKÁ | Error Recovery & Retry | Střední | Vysoký | ⭐⭐⭐⭐⭐ |
| 🔥 VYSOKÁ | Refactoring send_config() | Vysoký | Střední | ⭐⭐⭐⭐ |
| 📊 STŘEDNÍ | Performance Monitoring | Nízký | Střední | ⭐⭐⭐⭐ |
| 📊 STŘEDNÍ | Integration Testing | Vysoký | Střední | ⭐⭐⭐ |
| 💡 NÍZKÁ | Progress Reporting | Střední | Nízký | ⭐⭐ |
| 💡 NÍZKÁ | Config Validation | Nízký | Nízký | ⭐⭐ |

---

## 🔥 Vysoká Priorita

### 1. Error Recovery a Automatický Retry

**Současný stav:**
- Send operace selhávají při přechodných chybách (network timeout, SSH glitch)
- Uživatel musí manuálně opakovat send
- Žádný exponential backoff
- Resume token support existuje, ale není automatic

**Problém v kódu:**
```python
# pyznap/send.py:130
try:
    snapshots = source_fs.snapshots()
except CalledProcessError as err:
    logger.error('Error while opening source...')
    return 1  # ❌ Okamžitě se vzdá
```

**Navrhované vylepšení:**
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry_error_callback=lambda retry_state: 1
)
def get_source_snapshots(source_fs):
    """Get snapshots with automatic retry on transient failures."""
    try:
        return source_fs.snapshots()
    except CalledProcessError as err:
        if is_transient_error(err):
            logger.warning(f"Transient error, retrying: {err}")
            raise  # Trigger retry
        else:
            logger.error(f"Permanent error: {err}")
            return None

def is_transient_error(err):
    """Check if error is transient (network, timeout, etc.)."""
    transient_patterns = [
        'ssh: connect to host',
        'Connection timed out',
        'Connection refused',
        'Broken pipe',
        'Resource temporarily unavailable'
    ]
    return any(p in str(err) for p in transient_patterns)
```

**Benefits:**
- ✅ Automatické recovery z network glitches
- ✅ Spolehlivější noční backup joby
- ✅ Méně manual intervention
- ✅ Lepší user experience

**Effort:** Střední (2-3 dny)
**Files to modify:**
- `pyznap/send.py` - retry logic for send operations
- `pyznap/utils.py` - helper funkce pro error detection
- `pyznap/ssh.py` - retry logic for SSH connections

---

### 2. Refactoring send_config() - Use Helper Classes

**Současný stav:**
- `send_config()` má 148 řádků
- Cyklomatická složitost ~18
- Těžko testovatelné
- Helper classes již EXISTUJÍ (send_helpers.py), ale NEPOUŽÍVAJÍ SE

**Problém:**
```python
# pyznap/send.py:91 - monolitická funkce
def send_config(config):
    """Takes a config list and sends snapshots accordingly."""
    # 148 řádků spaghetti kódu
    # - parsing destinations
    # - opening filesystems
    # - error handling
    # - cleanup
    # Vše v jedné funkci!
```

**Navrhované vylepšení:**
```python
# Rozdělit na menší, testovatelné funkce
from .send_helpers import (
    ParsedName, DestConfig, SourceContext,
    DestContext, SSHManager
)

def send_config(config):
    """Main entry point for sending snapshots."""
    for conf in config:
        send_filesystem(conf)

def send_filesystem(conf):
    """Send snapshots for one filesystem."""
    # Parse configuration
    dest_config = DestConfig.from_config(conf)

    # Process each destination
    for dest in dest_config.destinations:
        send_to_destination(conf['name'], dest, dest_config)

def send_to_destination(source_name, dest_name, config):
    """Send to a single destination."""
    parsed_dest = ParsedName.parse(dest_name)

    # Use context managers for cleanup
    with SourceContext(source_name) as source_fs:
        with DestContext(parsed_dest, config) as dest_fs:
            # Actual send logic (simplified, focused)
            execute_send(source_fs, dest_fs, config)

def execute_send(source_fs, dest_fs, config):
    """Core send logic - much simpler now!"""
    # Just the send logic, without parsing/cleanup
    snapshots = source_fs.snapshots()
    # ... send logic ...
```

**Benefits:**
- ✅ Každá funkce < 50 řádků
- ✅ Testovatelné unit testy
- ✅ Použití helper classes (které již existují!)
- ✅ Lepší error handling
- ✅ Context managers = automatic cleanup

**Effort:** Vysoký (5-7 dní + testing)
**Risk:** Střední (requires extensive testing)

**Proč to nebylo provedeno:**
- Vyžaduje real ZFS pool pro testování
- Riziko breaking changes
- Potřeba regression testů

---

### 3. Refactoring status_filesystem() - Use Helper Classes

**Současný stav:**
- `status_filesystem()` má 200+ řádků
- Cyklomatická složitost ~15
- Helper classes připravené (status_helpers.py), ale NEPOUŽÍVAJÍ SE

**Navrhované vylepšení:**
```python
from .status_helpers import (
    FilesystemStatus, SnapshotCategorizer,
    determine_operations, bytes_fmt
)

def status_filesystem(filesystem, conf, main_fs=False):
    """Show status for one filesystem - refactored."""
    # Determine what operations should run
    ops = determine_operations(filesystem, conf, main_fs)

    if ops.excluded:
        return None

    # Gather filesystem info using helper class
    fs_status = FilesystemStatus.from_filesystem(filesystem, conf)

    # Categorize snapshots
    categorizer = SnapshotCategorizer()
    categorized = categorizer.categorize(filesystem.snapshots())
    fs_status.categorized_snapshots = categorized

    # Check destinations
    for dest in conf.get('dest', []):
        dest_status = check_destination_status(filesystem, dest, conf)
        fs_status.add_destination(dest_status)

    # Format and print
    print_filesystem_status(fs_status)
```

**Benefits:**
- ✅ Použití existujících helper classes
- ✅ Lepší separace concerns
- ✅ Testovatelnost

**Effort:** Střední-Vysoký (4-6 dní)

---

## 📊 Střední Priorita

### 4. Performance Monitoring & Metrics

**Současný stav:**
- Žádné performance metriky
- Není vidět, jak dlouho trvají operace
- Těžko debugovat pomalé backupy

**Navrhované vylepšení:**
```python
import time
from contextlib import contextmanager

@contextmanager
def measure_performance(operation_name):
    """Context manager for measuring operation time."""
    start = time.time()
    try:
        yield
    finally:
        duration = time.time() - start
        logger.info(f"{operation_name} took {duration:.2f}s")

        # Export to Prometheus if configured
        if metrics_enabled():
            export_metric(f"pyznap_{operation_name}_duration_seconds", duration)

# Usage:
def send_config(config):
    with measure_performance("send_all"):
        for conf in config:
            with measure_performance(f"send_{conf['name']}"):
                send_filesystem(conf)
```

**Metriky k trackování:**
- Duration jednotlivých send operations
- Velikost přenesených dat
- Počet retries
- Chybovost (% failed sends)

**Benefits:**
- ✅ Viditelnost do performance
- ✅ Identifikace bottlenecků
- ✅ Lepší capacity planning
- ✅ SLA monitoring

**Effort:** Nízký (1-2 dny)

---

### 5. Progress Reporting pro Dlouhé Operace

**Současný stav:**
- Žádný progress bar
- Uživatel neví, jestli operace běží nebo zamrzla
- Zvlášť problém u velkých send operations

**Navrhované vylepšení:**
```python
from tqdm import tqdm

def send_snapshots_with_progress(snapshots, dest):
    """Send snapshots with progress bar."""
    with tqdm(total=len(snapshots), desc="Sending snapshots") as pbar:
        for snap in snapshots:
            send_snapshot(snap, dest)
            pbar.update(1)
            pbar.set_postfix({"current": snap.name})
```

**Benefits:**
- ✅ User feedback
- ✅ Estimate time remaining
- ✅ Lepší UX

**Effort:** Střední (2-3 dny)

---

### 6. Integration Testing s Docker ZFS

**Současný stav:**
- Pouze unit testy (87 testů)
- Žádné end-to-end testy
- Nelze testovat real ZFS operace v CI/CD

**Navrhované řešení:**
```dockerfile
# docker/Dockerfile.zfs-test
FROM ubuntu:22.04

# Install ZFS
RUN apt-get update && apt-get install -y \
    zfsutils-linux \
    python3 \
    python3-pip

# Create test pool
RUN dd if=/dev/zero of=/zfs-pool.img bs=1M count=512
RUN zpool create testpool /zfs-pool.img

# Install pyznap
COPY . /pyznap
WORKDIR /pyznap
RUN pip3 install -e .

# Run integration tests
CMD ["pytest", "tests/integration/", "-v"]
```

**Integration tests:**
```python
# tests/integration/test_verify_e2e.py
def test_verify_with_real_zfs():
    """Test verify command against real ZFS pool."""
    # Create source filesystem
    subprocess.run(['zfs', 'create', 'testpool/source'])

    # Create snapshots
    subprocess.run(['zfs', 'snapshot', 'testpool/source@snap1'])

    # Create destination
    subprocess.run(['zfs', 'create', 'testpool/dest'])
    subprocess.run(['zfs', 'send', 'testpool/source@snap1', '|',
                   'zfs', 'recv', 'testpool/dest'])

    # Run verify
    result = subprocess.run(['pyznap', 'verify'], capture_output=True)

    assert result.returncode == 0
    assert 'OK' in result.stdout.decode()
```

**Benefits:**
- ✅ Real-world testing
- ✅ Catch integration bugs
- ✅ Safe refactoring
- ✅ CI/CD validation

**Effort:** Vysoký (3-4 dny)

---

## 💡 Nízká Priorita (Nice to Have)

### 7. Config Validation při Startu

**Současný stav:**
- Config chyby se objeví až při běhu
- Není JSON schema validation

**Navrhované vylepšení:**
```python
from jsonschema import validate, ValidationError

CONFIG_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "snap": {"type": "boolean"},
            "clean": {"type": "boolean"},
            "hourly": {"type": "integer", "minimum": 0},
            "daily": {"type": "integer", "minimum": 0}
        }
    }
}

def validate_config(config):
    """Validate config against schema."""
    try:
        validate(instance=config, schema=CONFIG_SCHEMA)
    except ValidationError as e:
        logger.error(f"Config validation failed: {e.message}")
        sys.exit(1)
```

**Effort:** Nízký (1 den)

---

### 8. Dry-run Mode pro Všechny Operace

**Současný stav:**
- Žádný `--dry-run` flag
- Těžko testovat co se stane

**Navrhované vylepšení:**
```python
# pyznap --dry-run send
# pyznap --dry-run clean
# pyznap --dry-run snap

def send_config(config, dry_run=False):
    if dry_run:
        logger.info("[DRY-RUN] Would send snapshots...")
        return 0
    # ... actual send
```

**Effort:** Nízký (1-2 dny)

---

### 9. Web UI pro Status Overview

**Současný stav:**
- Pouze CLI output
- Těžko vidět overview všech backupů

**Navrhované vylepšení:**
```python
# Simple Flask/FastAPI web dashboard
from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def dashboard():
    """Show overview of all filesystems and backup status."""
    # Run verify for all configured filesystems
    results = run_verify_all()
    return render_template('dashboard.html', results=results)

@app.route('/api/verify')
def api_verify():
    """JSON API for monitoring."""
    return jsonify(run_verify_all())
```

**Features:**
- Dashboard s statusem všech backupů
- Red/yellow/green indikátory
- Grafy lag time
- Alert notifications

**Effort:** Vysoký (7-10 dní)

---

## 🎯 Doporučený Plán Implementace

### Fáze 1: Quick Wins (1-2 týdny)
1. ✅ Error recovery & retry logic (Vysoký ROI)
2. ✅ Performance monitoring (Nízký effort)
3. ✅ Dry-run mode (Nízký effort)

### Fáze 2: Code Quality (3-4 týdny)
4. ✅ Refactoring send_config() (použít helper classes)
5. ✅ Refactoring status_filesystem() (použít helper classes)
6. ✅ Integration testing setup

### Fáze 3: Enhanced Features (volitelné)
7. ⏳ Progress reporting
8. ⏳ Config validation
9. ⏳ Web UI

---

## 📊 Srovnání Současný vs. Budoucí Stav

| Oblast | Současný Stav | Po Vylepšení |
|--------|---------------|--------------|
| **Spolehlivost** | Send selhává při network glitch | Automatic retry, 99%+ success rate |
| **Testování** | 87 unit testů | 87 unit + 20+ integration testů |
| **Code kvalita** | send_config 148 řádků | 5 funkcí po ~30 řádcích |
| **Monitoring** | Žádné metriky | Prometheus metrics, Grafana dashboards |
| **UX** | Žádný progress | Progress bars, time estimates |
| **Config** | Runtime errors | Validation při startu |

---

## 🔧 Konkrétní TODO Items z Kódu

Podle `grep TODO pyznap/*.py`:

### Vyřešené:
- ✅ `status.py:135` - remote uptodate check → **HOTOVO** (verify command)

### Zbývající:

#### 1. `send.py:379` - Create missing skipped filesystem
```python
# TODO: create missing skipped filesystem on destination
```
**Návrh:** Auto-create intermediate filesystems
**Effort:** Nízký (1 den)

#### 2. `status.py:136` - Over/under snapshot checks
```python
# TODO: T/F oversnapshot/undesnapshot/othersnapshots
```
**Návrh:** Rozšířit verify command o tyto kontroly
**Effort:** Střední (2 dny)

#### 3. `main.py:148` - Time shift
```python
# TODO: time shift
```
**Návrh:** Support pro time zones v snapshot times
**Effort:** Nízký (1 den)

#### 4. `pyzfs.py:320-321` - Split force flags
```python
# TODO: split force to allow -f, -r and -R to be specified individually
```
**Návrh:** Refactor force parameter handling
**Effort:** Nízký (1 den)

---

## 💡 Shrnutí Top 3 Doporučení

### 1. 🥇 Error Recovery & Retry
- **Proč:** Nejvyšší user impact, solve real pain point
- **Effort:** Střední
- **ROI:** ⭐⭐⭐⭐⭐

### 2. 🥈 Refactoring send_config()
- **Proč:** Helper classes JIŽ EXISTUJÍ, jen je použít
- **Effort:** Vysoký (ale infrastructure ready)
- **ROI:** ⭐⭐⭐⭐

### 3. 🥉 Performance Monitoring
- **Proč:** Quick win, low effort, high value
- **Effort:** Nízký
- **ROI:** ⭐⭐⭐⭐

---

**Připravil:** Claude Code
**Datum:** 2025-11-15
**Branch:** claude/code-review-opus-agent-01SSFoNuwhDDqHhtQLX5gBP5
