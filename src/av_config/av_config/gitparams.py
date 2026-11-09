"""parameter file 의 git 이력 관리 (명세 §6.1-4, §6.2, §10.3).

롤백과 복원은 항상 **새 commit(revert)** 으로 쌓는다. reset --hard 로 과거를
덮어쓰면 실패한 적용 시도가 이력에서 사라져 감사 로그 요건과 모순된다.

git CLI 를 subprocess 로 쓴다. pygit2 는 ARM64(Jetson) 휠 사정이 들쭉날쭉해서
배포 변수를 늘리지 않는다.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

_TIMEOUT = 15.0


class GitError(Exception):
    """git 명령이 실패했다."""


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    when: str
    subject: str


class GitParams:
    """params/ 디렉터리를 감싸는 git 저장소."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo

    # ------------------------------------------------------------------

    def _run(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
        if check and proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} 실패 (exit {proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout

    def is_repo(self) -> bool:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
        return proc.returncode == 0

    def head(self) -> str | None:
        """현재 HEAD. commit 이 하나도 없으면 None."""
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    def is_clean(self) -> bool:
        """추적 중인 파일에 커밋되지 않은 변경이 없는지."""
        return self._run("status", "--porcelain").strip() == ""

    def dirty_files(self) -> list[str]:
        out: list[str] = []
        for line in self._run("status", "--porcelain").splitlines():
            if len(line) > 3:
                out.append(line[3:].strip())
        return out

    # ------------------------------------------------------------------

    def commit(self, paths: list[str], message: str, author: str) -> str:
        """지정 파일을 commit 하고 sha 를 돌려준다.

        author 는 감사 로그용이다. 모든 change set 에 사용자, 시각, 코멘트,
        결과를 기록한다 (§10.3).
        """
        if not paths:
            raise GitError("commit 할 파일이 없다")
        self._run("add", "--", *paths)
        # 실제로 바뀐 것이 없으면 commit 하지 않는다
        if self._run("diff", "--cached", "--name-only").strip() == "":
            _log.info("내용 변화가 없어 commit 을 건너뛴다")
            head = self.head()
            if head is None:
                raise GitError("빈 저장소에서 변화 없는 commit 을 요청받았다")
            return head
        self._run(
            "-c",
            f"user.name={_safe(author)}",
            "-c",
            "user.email=avconfig@localhost",
            "commit",
            "-m",
            message,
        )
        head = self.head()
        if head is None:
            raise GitError("commit 후 HEAD 를 읽을 수 없다")
        return head

    def revert(self, sha: str, author: str) -> str:
        """sha 를 되돌리는 새 commit 을 만든다 (§6.2).

        이력은 선형으로 유지되고, 언제 누가 어디로 롤백했는지까지 남는다.
        """
        self._run(
            "-c",
            f"user.name={_safe(author)}",
            "-c",
            "user.email=avconfig@localhost",
            "revert",
            "--no-edit",
            sha,
        )
        head = self.head()
        if head is None:
            raise GitError("revert 후 HEAD 를 읽을 수 없다")
        return head

    def restore(self, sha: str, author: str, message: str) -> str:
        """특정 시점의 파일 내용을 되살리되 새 commit 으로 쌓는다 (§7.3).

        reset 이 아니므로 그 사이의 이력이 사라지지 않는다.
        """
        self._run("checkout", sha, "--", ".")
        self._run("add", "--all")
        if self._run("diff", "--cached", "--name-only").strip() == "":
            head = self.head()
            if head is None:
                raise GitError("빈 저장소를 복원할 수 없다")
            return head
        self._run(
            "-c",
            f"user.name={_safe(author)}",
            "-c",
            "user.email=avconfig@localhost",
            "commit",
            "-m",
            message,
        )
        head = self.head()
        if head is None:
            raise GitError("restore 후 HEAD 를 읽을 수 없다")
        return head

    def log(self, limit: int = 50) -> list[Commit]:
        """변경 이력 (§7.3 GET /history)."""
        if self.head() is None:
            return []
        fmt = "%H%x1f%an%x1f%aI%x1f%s"
        out = self._run("log", f"--max-count={limit}", f"--format={fmt}")
        commits: list[Commit] = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append(
                    Commit(sha=parts[0], author=parts[1], when=parts[2], subject=parts[3])
                )
        return commits

    def show_diff(self, sha: str) -> str:
        return self._run("show", "--format=", "--unified=3", sha)


def _safe(value: str) -> str:
    """git -c 값에 줄바꿈이나 따옴표가 섞이지 않게 한다."""
    return value.replace("\n", " ").replace("\r", " ").strip() or "avconfig"
