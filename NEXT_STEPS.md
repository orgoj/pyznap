# Pyznap - Další kroky a prioritní úkoly

## 📋 Shrnutí code review

Provedl jsem komplexní code review projektu pyznap pomocí Claude Opus modelu. Detailní výsledky najdeš v souboru `CODE_REVIEW_AND_IMPROVEMENTS.md`.

### Klíčová zjištění:

✅ **Pozitivní:**
- Dobrá modulární architektura
- Funkční snapshot management
- Podpora SSH remote backupů

⚠️ **Problémy:**
- **KRITICKÉ**: Bezpečnostní rizika (command injection, SSH security)
- **VYSOKÉ**: Chybí automatická kontrola stavu remote backupů (tvůj hlavní požadavek!)
- **STŘEDNÍ**: Nedostatečný error recovery
- **NÍZKÉ**: Code quality issues

### TODO položky v projektu:

```
pyznap/send.py:379      - TODO: create missing skipped filesystem on destination
pyznap/status.py:135    - TODO: remote uptodate check  ⭐ PRIORITA
pyznap/status.py:136    - TODO: oversnapshot/undersnapshot checks
pyznap/main.py:147      - TODO: time shift
pyznap/pyzfs.py:320-321 - TODO: split force flags
```

---

## 🎯 Tvoje hlavní priorita: Automatická kontrola remote snapshotů

### Současný problém:

Pyznap aktuálně **NEKONTROLUJE**, jestli jsou remote snapshoty skutečně aktuální. Pouze zjistí společné snapshoty a pošle incremental diff, ale:

- ❌ Nevaruje, když remote zaostává o několik dní
- ❌ Neověřuje, že poslední snapshot byl skutečně přenesen
- ❌ Neřeší situaci, kdy remote má neúplnou historii
- ❌ Není způsob, jak zjistit health stav všech backupů

### Navržené řešení:

Implementovat **nový příkaz `pyznap verify`** + integraci do `pyznap status`.

#### Funkce:

```bash
# Základní verifikace
pyznap verify

# S auto-fixem
pyznap verify --fix

# Pro monitoring (Nagios/Prometheus)
pyznap verify --nagios
pyznap verify --json

# Custom thresholdy
pyznap verify --max-lag 3600  # max 1 hodina zpoždění
```

#### Co bude kontrolovat:

1. ✅ Časové zpoždění mezi source a destination
2. ✅ Existenci všech kritických snapshotů (daily, weekly, monthly)
3. ✅ Integritu incremental chain
4. ✅ Chybějící mezikroky
5. ✅ Speciální handling pro nové remote (neúplná historie je OK)

#### Příklad výstupu:

```
================================================================================
PYZNAP REMOTE BACKUP VERIFICATION REPORT
================================================================================

Source: tank/data
Destination: ssh::backup@remote:backup/data
--------------------------------------------------------------------------------
Status: WARNING
Lag: 1.2d

Warnings:
  ⚠️  Destination is 1.2 days behind
  ⚠️  Low daily snapshot coverage: 75%

Recommendations:
  💡 Consider increasing backup frequency
```

---

## 🚀 Doporučený plán implementace

### FÁZE 1: Bezpečnost (3-4 dny) ⚠️ KRITICKÉ

**Proč první:** Bezpečnostní díry jsou kritické, zejména command injection.

**Úkoly:**
1. Vytvořit `pyznap/security.py` s validation frameworkem
2. Opravit všechny subprocess volání (přidat `shlex.quote()`)
3. Opravit SSH socket naming (UUID místo predictable)
4. Přidat host key verification
5. Security testing

**Soubory k editaci:**
- `pyznap/pyzfs.py`
- `pyznap/ssh.py`
- `pyznap/send.py`
- Nový: `pyznap/security.py`

### FÁZE 2: Remote Snapshot Verification (4-5 dní) ⭐ TVOJE PRIORITA

**Proč důležité:** Tvůj hlavní požadavek - automatická kontrola backupů.

**Úkoly:**
1. Vytvořit `pyznap/verification.py` modul
2. Implementovat `verify_remote_snapshots()` funkci
3. Vytvořit data structures (VerificationReport, SnapshotInfo)
4. Integrovat do `status.py`
5. Přidat nový příkaz `pyznap verify` do `main.py`
6. Implementovat output formáty (human, JSON, Nagios)
7. Testy

**Soubory k vytvoření/editaci:**
- Nový: `pyznap/verification.py` (~500 řádků)
- `pyznap/status.py` (+50 řádků)
- `pyznap/main.py` (+100 řádků pro verify command)
- `tests/test_verification.py`

**Klíčové funkce k implementaci:**

```python
# Core verification
verify_remote_snapshots(source_fs, dest_fs, config) -> VerificationReport

# Helper funkce
get_snapshots_with_metadata(filesystem) -> List[SnapshotInfo]
find_latest_common_snapshot(source, dest) -> SnapshotInfo
check_missing_incrementals(source, dest, latest_common) -> List[SnapshotInfo]
validate_incremental_chain(source, dest) -> List[Issue]

# Speciální případy
verify_partial_history() - pro nové remote
check_latest_snapshots_transferred() - kontrola posledních snapshotů
```

### FÁZE 3: Error Handling (2-3 dny)

**Úkoly:**
1. Cleanup při selhání send/receive
2. Resume token validation
3. Rollback mechanism
4. Unified logging
5. Konzistentní exit kódy

**Soubory:**
- `pyznap/send.py`
- `pyznap/main.py`

### FÁZE 4: Code Quality & Testing (4-5 dní)

**Úkoly:**
1. Refactoring velkých funkcí
2. Odstranění duplicit
3. Unit testy
4. Integration testy
5. CI/CD setup

---

## 📝 Quick Start - Jak začít s implementací

### Krok 1: Připrav branch

```bash
git checkout -b feature/remote-verification
```

### Krok 2: Začni s verification modulem

Vytvořit nový soubor `pyznap/verification.py`:

```python
from dataclasses import dataclass
from enum import Enum
from typing import List
from datetime import datetime

class Status(Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

@dataclass
class SnapshotInfo:
    name: str
    creation_time: datetime
    used: int
    referenced: int
    type: str

@dataclass
class VerificationReport:
    status: Status = Status.UNKNOWN
    lag_seconds: float = 0
    missing_snapshots: List[SnapshotInfo] = None
    errors: List[str] = None
    warnings: List[str] = None
    recommendations: List[str] = None

    # ... methods

def verify_remote_snapshots(source_fs, dest_fs, config):
    """Hlavní verifikační funkce"""
    # Implementace podle návrhu v CODE_REVIEW_AND_IMPROVEMENTS.md
    pass
```

### Krok 3: Přidej testy

```bash
pytest tests/test_verification.py -v
```

### Krok 4: Integrace do status

V `pyznap/status.py` přidat volání verification funkce.

### Krok 5: Nový CLI příkaz

V `pyznap/main.py` přidat `verify` subcommand.

---

## 🔍 Speciální případy, které musíš řešit

### 1. Remote neexistuje dostatečně dlouho

**Problém:** Nový backup server nemá úplnou historii.

**Řešení:**
- Detekovat že oldest_dest > oldest_source
- Kontrolovat pouze relevantní snapshoty (od oldest_dest)
- Status: WARNING místo ERROR
- Info message: "Partial history expected for new remote"

### 2. Kontrola že se přenášejí poslední snapshoty

**Implementace:**
```python
def check_latest_snapshots_transferred(source_fs, dest_fs):
    """
    Ověří že nejnovější snapshoty byly přeneseny

    Thresholdy:
    - daily: max 2 dny staré
    - weekly: max 10 dní staré
    - monthly: max 35 dní staré
    """
    for snap_type, max_age_days in [('daily', 2), ('weekly', 10), ('monthly', 35)]:
        dest_latest = find_latest_of_type(dest_snaps, snap_type)
        age_days = (datetime.now() - dest_latest.creation_time).days

        if age_days > max_age_days:
            # WARNING nebo ERROR
            pass
```

### 3. Chybějící incremental snapshoty

**Problém:** Pokud chybí mezikrok, může být přerušen incremental chain.

**Detekce:**
```python
# Najdi všechny snapshoty mezi latest_common a source_latest
# Zkontroluj že všechny jsou na dest
# Pokud nějaké chybí -> WARNING + seznam
```

---

## 📊 Monitorování a alerting

### Integrace s Prometheus

```bash
# Exportuj metriky
pyznap verify --export-metrics

# Výsledek: /var/lib/node_exporter/textfile_collector/pyznap.prom
```

### Nagios monitoring

```bash
# Nagios check
pyznap verify --nagios

# Exit kódy:
# 0 = OK
# 1 = WARNING
# 2 = CRITICAL
```

### Email alerting

Konfigurace v `/etc/pyznap/alerts.conf`:

```ini
[alerts]
smtp_host = localhost
smtp_port = 25
from_addr = pyznap@example.com
to_addrs = admin@example.com,backup-team@example.com

# Alertovat pouze při ERROR/CRITICAL
alert_on = ERROR,CRITICAL
```

---

## 🧪 Testing strategie

### Unit testy

```python
# tests/unit/test_verification.py

def test_up_to_date_remote():
    """Remote je aktuální -> Status.OK"""

def test_lagging_remote_warning():
    """Remote zaostává o 1 den -> Status.WARNING"""

def test_lagging_remote_critical():
    """Remote zaostává o týden -> Status.CRITICAL"""

def test_empty_remote():
    """Remote je prázdný -> Status.WARNING + recommendation"""

def test_no_common_snapshots():
    """Žádné společné snapshoty -> Status.CRITICAL"""

def test_missing_incrementals():
    """Chybí mezikroky -> Warning + seznam missing"""

def test_new_remote_partial_history():
    """Nový remote, neúplná historie -> Status.OK"""
```

### Integration testy

```bash
# S Docker ZFS poolem
docker run --privileged test-zfs-pool
# Simulovat různé scénáře
```

---

## 📚 Dokumentace k aktualizaci

### README.md

Přidat sekci:

```markdown
#### Verification Command

Verify backup health:

    pyznap verify

Options:
  --max-lag SECONDS     Maximum acceptable lag (default: 86400)
  --fix                 Automatically fix issues by running send
  --json                Output as JSON
  --nagios              Nagios-compatible output
  --export-metrics      Export Prometheus metrics

Example:
    pyznap verify --max-lag 3600 --fix
```

### Man page

Vytvořit `man pyznap-verify`.

---

## ✅ Checklist pro implementaci

### Remote Verification Feature

- [ ] Vytvořit `pyznap/verification.py`
- [ ] Implementovat `VerificationReport` dataclass
- [ ] Implementovat `SnapshotInfo` dataclass
- [ ] Implementovat `verify_remote_snapshots()`
- [ ] Implementovat helper funkce:
  - [ ] `get_snapshots_with_metadata()`
  - [ ] `find_latest_common_snapshot()`
  - [ ] `check_missing_incrementals()`
  - [ ] `validate_incremental_chain()`
  - [ ] `check_latest_snapshots_transferred()`
- [ ] Implementovat speciální případy:
  - [ ] Nový remote (partial history)
  - [ ] Empty remote
  - [ ] No common snapshots
- [ ] Integrace do `status.py`
- [ ] Nový CLI příkaz v `main.py`:
  - [ ] `pyznap verify` základní
  - [ ] `--max-lag` option
  - [ ] `--fix` option
  - [ ] `--json` output
  - [ ] `--nagios` output
  - [ ] `--export-metrics` pro Prometheus
- [ ] Output formátování:
  - [ ] Human-readable
  - [ ] JSON
  - [ ] Nagios
  - [ ] Prometheus metrics
- [ ] Testy:
  - [ ] Unit testy pro všechny funkce
  - [ ] Edge case testy
  - [ ] Integration testy
- [ ] Dokumentace:
  - [ ] Docstrings
  - [ ] README update
  - [ ] Examples

### Bezpečnost

- [ ] Vytvořit `pyznap/security.py`
- [ ] Implementovat `SecurityValidator`
- [ ] Opravit command injection v `pyzfs.py`
- [ ] Opravit SSH security v `ssh.py`
- [ ] Security testy

### Error Handling

- [ ] Cleanup při selhání send
- [ ] Resume token validation
- [ ] Rollback mechanism
- [ ] Unified logging
- [ ] Konzistentní exit kódy

---

## 💡 Tip: Postup implementace

Doporučuji implementovat v tomto pořadí:

1. **Začni s data structures** (SnapshotInfo, VerificationReport)
   - Rychlé, definuje API
   - Můžeš začít psát testy

2. **Implementuj základní verify logic**
   - `verify_remote_snapshots()` bez speciálních případů
   - Pouze základní kontrola lag

3. **Přidej helper funkce postupně**
   - Každou otestuj izolovaně

4. **Rozšiř o speciální případy**
   - Partial history
   - Empty remote
   - Missing incrementals

5. **Integrace do CLI**
   - Nový command
   - Output formats

6. **Polish a dokumentace**

---

## 🎉 Očekávané výsledky

Po implementaci budeš mít:

✅ **Proaktivní monitoring** - víš okamžitě když backup zaostává
✅ **Automatická detekce problémů** - chybějící snapshoty, broken chain
✅ **Flexibilní thresholdy** - přizpůsobitelné pro různé use cases
✅ **Auto-fix možnost** - `--fix` automaticky spustí send
✅ **Monitoring integrace** - Nagios, Prometheus, email alerts
✅ **Podpora nových remote** - inteligentní handling partial history

---

## 📞 Další pomoc

Pokud budeš potřebovat pomoct s:
- Konkrétní implementací některé funkce
- Debugging problémů
- Review kódu
- Návrh testů

Stačí se zeptat! Mám detailní znalost celého codebase a návrhu řešení.

---

*Vytvořeno: 2025-11-15*
*Pro detaily viz: CODE_REVIEW_AND_IMPROVEMENTS.md*
