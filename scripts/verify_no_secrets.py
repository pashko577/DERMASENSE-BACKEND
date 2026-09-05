"""Gancho previo al commit: ninguna credencial en el repositorio.

Regla 1 de AGENTS.md, sin excepciones: ni en codigo, ni en documentacion, ni en
commits. Solo `.env.example` con marcadores de posicion.

Uso:
    python scripts/verify_no_secrets.py            # revisa el arbol de trabajo
    python scripts/verify_no_secrets.py --staged   # revisa lo que se va a commitear

Instalacion como gancho de git:
    printf '#!/bin/sh\\npython scripts/verify_no_secrets.py --staged\\n' > .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Cada patron busca un secreto *con valor*, no su mencion. `.env.example` y esta
# misma documentacion nombran las variables sin asignarles nada, y eso debe
# seguir pasando la revision.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("clave de Anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}")),
    ("JWT con valor", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.")),
    # Un valor real tiene al menos 12 caracteres de alfabeto de clave. Los
    # marcadores de la documentacion ('...', '<tu-clave>', vacio) no los tienen,
    # y deben seguir pasando la revision.
    (
        "service_role con valor",
        re.compile(r"SUPABASE_SERVICE_ROLE_KEY\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
    ),
    ("clave secreta de Supabase", re.compile(r"\bsb_secret_[A-Za-z0-9_\-]{10,}")),
    (
        "JWT secret con valor",
        re.compile(r"SUPABASE_JWT_SECRET\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
    ),
    ("clave de OpenAI", re.compile(r"sk-[A-Za-z0-9]{32,}")),
]

SKIP_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "dist",
    "build",
}

SKIP_SUFFIXES = {".pyc", ".xlsx", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff2"}

# Este archivo contiene los patrones por definicion.
SELF = Path(__file__).name


def _git_ignored(paths: list[Path]) -> set[Path]:
    """Rutas que git ya ignora.

    `.env` con la clave real es exactamente lo que debe existir en la maquina de
    quien desarrolla, y esta en `.gitignore`, asi que no puede llegar al
    repositorio sin un `git add -f` deliberado. Marcarlo en cada ejecucion solo
    ensena a ignorar la herramienta, y una alarma que se ignora no protege nada.
    El modo `--staged`, que es el que corre como gancho de commit, si mira todo
    lo que se va a commitear.
    """
    if not paths:
        return set()

    # Rutas relativas y con barras normales: `git check-ignore` rechaza una ruta
    # absoluta de Windows y devuelve, en silencio, que no ignora nada.
    relative = {path.relative_to(ROOT).as_posix(): path for path in paths}

    # Se habla con git en bytes y con separador NUL (`-z`). En modo texto, Windows
    # traduce cada '\n' a '\r\n' al escribir en la tuberia, git recibe '.env\r' y
    # responde que no ignora nada: un falso negativo silencioso.
    payload = b"\0".join(name.encode("utf-8") for name in relative) + b"\0"

    try:
        result = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            cwd=ROOT,
            input=payload,
            capture_output=True,
            check=False,
        )
    except OSError:
        # Sin git no se puede saber que esta ignorado: se revisa todo, que es el
        # lado seguro del error.
        return set()

    ignored: set[Path] = set()
    for chunk in result.stdout.split(b"\0"):
        name = chunk.decode("utf-8", errors="ignore").strip()
        if name in relative:
            ignored.add(relative[name])
    return ignored


def _iter_worktree_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        files.append(path)

    ignored = _git_ignored(files)
    return [path for path in files if path not in ignored]


def _iter_staged_files() -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    paths = []
    for line in result.stdout.splitlines():
        candidate = ROOT / line.strip()
        if candidate.is_file() and candidate.suffix.lower() not in SKIP_SUFFIXES:
            paths.append(candidate)
    return paths


def scan(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if path.name == SELF:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            for label, pattern in PATTERNS:
                if pattern.search(line):
                    relative = path.relative_to(ROOT)
                    findings.append(f"{relative}:{line_number}: posible {label}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Busca credenciales en el repositorio.")
    parser.add_argument(
        "--staged", action="store_true", help="Revisa solo lo preparado para commit"
    )
    args = parser.parse_args()

    paths = _iter_staged_files() if args.staged else _iter_worktree_files()
    findings = scan(paths)

    if findings:
        print("SECRETOS DETECTADOS:\n", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        print(
            "\nNinguna credencial entra al repositorio. Muevela a .env "
            "(ignorado) y deja solo el marcador en .env.example.",
            file=sys.stderr,
        )
        return 1

    print(f"Sin secretos en {len(paths)} archivos revisados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
