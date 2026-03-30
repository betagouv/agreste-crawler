import importlib.util
import os
import sys
from pathlib import Path


def _load_requested_env_file(sites_faciles_root: Path) -> None:
    if "--scalingo-env" not in sys.argv:
        return

    env_path = sites_faciles_root / ".env.scalingo"
    if not env_path.exists():
        raise FileNotFoundError(
            f"--scalingo-env was provided but file not found: {env_path}"
        )

    from dotenv import load_dotenv

    # Preload variables before Django imports settings.py (which calls load_dotenv()).
    # Because of override=False, these variables will not be overridden by later calls to load_dotenv().
    load_dotenv(dotenv_path=env_path, override=False)


def setup_django(current_file: str) -> None:
    # Add sibling sites-faciles project root on PYTHONPATH.
    sites_faciles_root = (
        Path(current_file).resolve().parents[2] / "sites-faciles"
    )
    if str(sites_faciles_root) not in sys.path:
        sys.path.insert(0, str(sites_faciles_root))

    _load_requested_env_file(sites_faciles_root)

    # If Django is missing, re-run with sites-faciles venv.
    if importlib.util.find_spec("django") is None:
        sites_faciles_python = sites_faciles_root / ".venv" / "bin" / "python"
        current_python = Path(sys.executable)
        if (
            sites_faciles_python.exists()
            and current_python != sites_faciles_python
        ):
            os.execv(
                str(sites_faciles_python),
                [str(sites_faciles_python), current_file, *sys.argv[1:]],
            )
        raise ModuleNotFoundError(
            "Django is not available. Install dependencies in agreste-crawler "
            "or create ../sites-faciles/.venv."
        )

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
