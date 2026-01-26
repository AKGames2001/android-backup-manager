import os
import sys
import json
from datetime import date

# Built-in safe defaults (fallbacks)
ADB_PATH = r"adb/adb.exe"
SOURCE_DIR = "/sdcard/"
BASE_BACKUP_DIR = r"backups"
DEFAULT_USER = "User"

INDEX_FILENAME = "index.json"
RECORD_FILENAME = "record.json"
FAILED_CSV_FILENAME = os.path.join("data", "failed-files.csv")

def ensure_dir(path: str):
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def ensure_file(path: str, initial=None):
    ensure_dir(os.path.dirname(path))
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            if initial is None:
                f.write("")
            else:
                json.dump(initial, f)

def today_dirname() -> str:
    return date.today().strftime("%Y-%m-%d")

def path_for_user_session(base_dir: str, user: str, use_date: bool = True) -> str:
    if use_date:
        return os.path.join(base_dir, user, today_dirname())
    return os.path.join(base_dir, user)

def index_path_for_user(basedir: str, user: str) -> str:
    return os.path.join(basedir, user, INDEX_FILENAME)

def record_path_for_user(base_dir: str, user: str) -> str:
    return os.path.join(base_dir, user, RECORD_FILENAME)

def restore_record_path_for_user(base_dir: str, user: str) -> str:
    return os.path.join(base_dir, user, "restore_record.json")

def failed_csv_path_for_user(base_dir: str, user: str) -> str:
    return os.path.join(base_dir, user, "failed-files.csv")

# Discontinued functions! : Keeping for backwards compatibility.
# def failed_csv_path() -> str:
#     return FAILED_CSV_FILENAME

# ---------- Runtime location helpers ----------

def _exe_dir() -> str:
    # Directory containing the executable (or the script in source runs)
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def _bundle_dir() -> str | None:
    # PyInstaller sets sys._MEIPASS to the bundle’s content directory
    # (onedir: the _internal/ contents; onefile: a temp dir).
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return getattr(sys, "_MEIPASS", None)
    return None

def resolve_data_path(rel_path: str) -> str:
    """
    Return absolute path to a resource; raise if not found.
    Search order (read): MEIPASS -> exe dir -> exe/_internal -> package dir -> CWD.
    """
    bases = []
    meipass = _bundle_dir()
    if meipass:
        bases.append(meipass)                 # bundled resources
    exedir = _exe_dir()
    bases.append(exedir)                      # next to exe
    bases.append(os.path.join(exedir, "_internal"))  # compatibility with onedir layout
    bases.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))  # package/repo
    bases.append(os.getcwd())                 # CWD as last resort

    tried = []
    for base in bases:
        p = os.path.abspath(os.path.join(base, rel_path))
        tried.append(p)
        if os.path.exists(p):
            return p
    # Never return a list or print; fail loudly and clearly
    raise FileNotFoundError(f"Resource not found: {rel_path}. Tried: {tried}")

# ---------- app_config.json helpers ----------

def app_config_candidates() -> list[str]:
    """
    Return potential locations for app_config.json (read paths only).
    """
    cands = []
    meipass = _bundle_dir()
    if meipass:
        cands.append(os.path.join(meipass, "config", "app_config.json"))  # bundled default
    exedir = _exe_dir()
    cands.append(os.path.join(exedir, "config", "app_config.json"))       # machine-installed
    cands.append(os.path.join(exedir, "_internal", "config", "app_config.json"))  # onedir fallback
    cands.append(os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), "config", "app_config.json"))  # repo
    cands.append(os.path.join(os.getcwd(), "config", "app_config.json"))  # CWD
    return cands

def _user_config_dir() -> str:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "AndroidBackupManager")

def current_app_config_path() -> str | None:
    for p in app_config_candidates():
        if os.path.exists(p):
            return p
    return None

def ensure_app_config_dir() -> str:
    # Write to per-user AppData to avoid Program Files permission issues
    target = _user_config_dir()
    ensure_dir(target)
    return target

def write_app_config(cfg: dict) -> str:
    # Persist per-user config (safe without admin rights)
    out_dir = ensure_app_config_dir()
    out_path = os.path.join(out_dir, "app_config.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return out_path


def needs_first_run() -> bool:
    """
    Decide whether to show the setup wizard.
    """
    try:
        adb_ok = bool(ADB_PATH) and os.path.isfile(ADB_PATH)
        base_ok = bool(BASE_BACKUP_DIR) and os.path.isdir(BASE_BACKUP_DIR)
        user_ok = bool((DEFAULT_USER or "").strip())
        return not (adb_ok and base_ok and user_ok)
    except Exception:
        return True

# ---------- Config loading ----------

def _load_json(p: str) -> dict:
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _normalize_cfg(cfg: dict) -> dict:
    out = dict(cfg or {})
    # Normalize slashes and absolutize ADB path if relative
    adb = out.get("ADB_PATH")
    if isinstance(adb, str) and adb:
        adb = os.path.normpath(adb)
        if not os.path.isabs(adb):
            # Allow relative paths relative to the exe dir
            adb = os.path.abspath(os.path.join(_exe_dir(), adb))
        out["ADB_PATH"] = adb
    base = out.get("BASE_BACKUP_DIR")
    if isinstance(base, str) and base:
        out["BASE_BACKUP_DIR"] = os.path.normpath(base)
    return out

def _load_runtime_config() -> dict:
    # 1) Machine default installed with app (or bundled default)
    machine_cfg_path = current_app_config_path() or resolve_data_path("config/app_config.json")
    cfg = _load_json(machine_cfg_path)

    # 2) Optional per-user override (e.g., %APPDATA%\AndroidBackupManager\app_config.json)
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    user_cfg_path = os.path.join(appdata, "AndroidBackupManager", "app_config.json")
    if os.path.exists(user_cfg_path):
        cfg.update(_load_json(user_cfg_path))
    return _normalize_cfg(cfg)

_cfg = _load_runtime_config()

# Effective runtime values
ADB_PATH = _cfg.get("ADB_PATH", ADB_PATH)
SOURCE_DIR = _cfg.get("SOURCE_DIR", SOURCE_DIR)
BASE_BACKUP_DIR = _cfg.get("BASE_BACKUP_DIR", BASE_BACKUP_DIR)
DEFAULT_USER = _cfg.get("DEFAULT_USER", DEFAULT_USER)
