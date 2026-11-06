import importlib.util
import os
import sys
from pathlib import Path


def _get_wagtail_project_root_arg() -> str | None:
    for i, arg in enumerate(sys.argv):
        if arg == "--wagtail-project-root":
            if i + 1 >= len(sys.argv):
                raise ValueError(
                    "--wagtail-project-root requires "
                    "a directory path argument."
                )
            return sys.argv[i + 1]
        if arg.startswith("--wagtail-project-root="):
            value = arg.split("=", 1)[1]
            if not value:
                raise ValueError(
                    "--wagtail-project-root requires "
                    "a non-empty directory path."
                )
            return value
    return None


def _resolve_django_project_root(current_file: str) -> Path:
    project_root_arg = _get_wagtail_project_root_arg()
    if not project_root_arg:
        raise ValueError(
            "--wagtail-project-root is required and must point "
            "to the Django project root."
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


def _crawler_root(current_file: str) -> Path:
    """Repo root (directory that contains the ``django_setup`` package)."""
    here = Path(current_file).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "django_setup").is_dir():
            return candidate
    return Path(current_file).resolve().parents[1]


def _reexec_with_project_python(project_python: Path, current_file: str) -> None:
    """
    Re-run in the Wagtail project's venv.

    Crawler and Wagtail projects may share the same uv-managed interpreter
    binary while using different site-packages; compare Django availability,
    not sys.executable paths.
    """
    crawler_root = _crawler_root(current_file)
    env = os.environ.copy()
    pythonpath_parts = [str(crawler_root)]
    if env.get("PYTHONPATH"):
        pythonpath_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    if "-m" in sys.argv:
        module_index = sys.argv.index("-m")
        module_name = sys.argv[module_index + 1]
        os.execve(
            str(project_python),
            [
                str(project_python),
                "-m",
                module_name,
                *sys.argv[module_index + 2 :],
            ],
            env,
        )

    os.execve(
        str(project_python),
        [str(project_python), current_file, *sys.argv[1:]],
        env,
    )


def setup_django(current_file: str) -> None:
    project_root = _resolve_django_project_root(current_file)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    if importlib.util.find_spec("django") is None:
        project_python = project_root / ".venv" / "bin" / "python"
        if project_python.exists():
            _reexec_with_project_python(project_python, current_file)
        raise ModuleNotFoundError(
            "Django is not available. Install dependencies in the Wagtail "
            f"project venv at {project_root / '.venv'}."
        )

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
