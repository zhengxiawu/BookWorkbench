"""Small Git wrapper used by the Runtime MVP."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional


class GitError(RuntimeError):
    pass


def run_git(args: List[str], cwd: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def ensure_repo(root: str | Path, remote_url: Optional[str] = None) -> None:
    root = Path(root)
    if not (root / ".git").exists():
        result = run_git(["init"], root)
        if result.returncode != 0:
            raise GitError(result.stderr.strip() or result.stdout.strip())
    if remote_url:
        remotes = run_git(["remote"], root).stdout.splitlines()
        if "origin" in remotes:
            run_git(["remote", "set-url", "origin", remote_url], root)
        else:
            result = run_git(["remote", "add", "origin", remote_url], root)
            if result.returncode != 0:
                raise GitError(result.stderr.strip() or result.stdout.strip())


def status(root: str | Path) -> str:
    return run_git(["status", "--short", "--branch"], root).stdout


def commit_all(root: str | Path, message: str, *, name: str = "BookWorkbench Agent", email: str = "agent@bookworkbench.local") -> None:
    root = Path(root)
    add = run_git(["add", "-A"], root)
    if add.returncode != 0:
        raise GitError(add.stderr.strip() or add.stdout.strip())
    diff = run_git(["diff", "--cached", "--quiet"], root)
    if diff.returncode == 0:
        return
    commit = subprocess.run(
        [
            "git",
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "commit",
            "-m",
            message,
        ],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if commit.returncode != 0:
        raise GitError(commit.stderr.strip() or commit.stdout.strip())


def amend_all(root: str | Path, *, name: str = "BookWorkbench Agent", email: str = "agent@bookworkbench.local") -> None:
    """Stage current changes and fold them into the latest commit without changing its message."""

    root = Path(root)
    add = run_git(["add", "-A"], root)
    if add.returncode != 0:
        raise GitError(add.stderr.strip() or add.stdout.strip())
    diff = run_git(["diff", "--cached", "--quiet"], root)
    if diff.returncode == 0:
        return
    amend = subprocess.run(
        [
            "git",
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "commit",
            "--amend",
            "--no-edit",
        ],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if amend.returncode != 0:
        raise GitError(amend.stderr.strip() or amend.stdout.strip())
