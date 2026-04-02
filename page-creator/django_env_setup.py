import importlib.util
import os
import sys
from pathlib import Path


def _get_wagtail_project_root_arg() -> str | None:
    for i, arg in enumerate(sys.argv):
        if arg == "--wagtail-project-root":
            if i + 1 >= len(sys.argv):
                raise ValueError(
                    "--wagtail-project-root requires a directory path argument."
                )
            return sys.argv[i + 1]
        if arg.startswith("--wagtail-project-root="):
            value = arg.split("=", 1)[1]
            if not value:
                raise ValueError(
                    "--wagtail-project-root requires a non-empty directory path."
                )
            return value
    return None


def _resolve_django_project_root(current_file: str) -> Path:
    project_root_arg = _get_wagtail_project_root_arg()
    if not project_root_arg:
        raise ValueError(
            "--wagtail-project-root is required and must point to the Django project root."
        )
    project_root = Path(project_root_arg).expanduser()
    if not project_root.is_absolute():
        project_root = (Path.cwd() / project_root).resolve()
    if (project_root / "config" / "settings.py").exists():
        return project_root
    raise FileNotFoundError(
        "Could not locate Django project root at: "
        f"{project_root}"
    )


def _get_scalingo_env_file_arg() -> str | None:
    for i, arg in enumerate(sys.argv):
        if arg == "--scalingo-env-file":
            if i + 1 >= len(sys.argv):
                raise ValueError("--scalingo-env-file requires a file path argument.")
            return sys.argv[i + 1]
        if arg.startswith("--scalingo-env-file="):
            value = arg.split("=", 1)[1]
            if not value:
                raise ValueError("--scalingo-env-file requires a non-empty file path.")
            return value
    return None


def _load_requested_env_file(project_root: Path) -> None:
    env_file_arg = _get_scalingo_env_file_arg()
    if not env_file_arg:
        return

    env_path = Path(env_file_arg).expanduser()
    if not env_path.is_absolute():
        env_path = (Path.cwd() / env_path).resolve()
    if not env_path.exists():
        raise FileNotFoundError(
            f"--scalingo-env-file was provided but file not found: {env_path}"
        )

    from dotenv import load_dotenv

    # Preload variables before Django imports settings.py (which calls load_dotenv()).
    # Because of override=False, these variables will not be overridden by later calls to load_dotenv().
    load_dotenv(dotenv_path=env_path, override=False)


def setup_django(current_file: str) -> None:
    project_root = _resolve_django_project_root(current_file)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    _load_requested_env_file(project_root)

    # If Django is missing, re-run with the target project venv.
    if importlib.util.find_spec("django") is None:
        project_python = project_root / ".venv" / "bin" / "python"
        current_python = Path(sys.executable)
        if (
            project_python.exists()
            and current_python != project_python
        ):
            os.execv(
                str(project_python),
                [str(project_python), current_file, *sys.argv[1:]],
            )
        raise ModuleNotFoundError(
            "Django is not available. Install dependencies in agreste-crawler "
            "or create <wagtail-project-root>/.venv."
        )

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
