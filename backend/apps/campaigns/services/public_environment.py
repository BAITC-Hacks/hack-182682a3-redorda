"""Official participant-kit simulator; never a claim about judging effects.

Only verified organizer code from the server's kit directory is imported.
Uploaded dataset directories supply CSVs, never executable Python modules.
The private second factory return value is discarded without being inspected.
"""

import hashlib
import importlib
import sys
from pathlib import Path
from threading import Lock

from django.conf import settings

from .execution import ExecutionUnavailable

LOCAL_FACTORY = "apps.campaigns.services.public_environment.local_simulation"
_IMPORT_LOCK = Lock()
_HASHES = {
    "scoring_core.py": "ba68595daebb12418000fa4d59ffbbf156df045f589bdbb789f3531145c58327",
    "environment.py": "8003a305ea9a560270ee875b2c48a6b1fbf8198d9c6c680e4827542532bb3dd1",
    "mock_environment.py": "e1e4a480126d2896e89a868194b3ed7f7ee48d7c5fc2c8a2491970d8e231f506",
}


def kit_directory():
    path = Path(settings.PARTICIPANT_KIT_DIR).expanduser()
    return (path if path.is_absolute() else settings.ROOT_DIR / path).resolve()


def local_simulation_available():
    try:
        root = kit_directory()
        return all(hashlib.sha256((root / name).read_bytes()).hexdigest() == checksum
                   for name, checksum in _HASHES.items())
    except OSError:
        return False


def local_simulation(context):
    if not local_simulation_available():
        raise ExecutionUnavailable("Import the verified official participant kit first")
    root = kit_directory()
    with _IMPORT_LOCK:
        # Avoid picking up unrelated modules with the organizer's generic names.
        for name in _HASHES:
            module = sys.modules.get(Path(name).stem)
            if module and Path(getattr(module, "__file__", "")).resolve() != root / name:
                raise ExecutionUnavailable("Participant module conflicts with a loaded module")
        sys.path.insert(0, str(root))
        try:
            module = importlib.import_module("mock_environment")
        finally:
            sys.path.remove(str(root))
    source = context.dataset_path.resolve()
    environment, _ = module.make_mock_env(
        seed=context.seed, data_dir=str(source / "data"),
        profile_path=str(source / "customer_profile.csv"),
    )
    return environment
