#!/usr/bin/env python3
"""리포의 config/ 템플릿으로 Config App 의 설정 루트를 만든다.

config/ 는 템플릿이다. Config App 은 설정 루트의 parameter file 을 고쳐 쓰고
params/ 에 git 이력을 쌓으므로, 리포 안의 config/ 를 그대로 쓰면 추적 중인 파일이
바뀌고 중첩 저장소가 생긴다. 그래서 저장소 밖에 복사본을 만들어 쓴다.

    python3 tools/init_config_root.py ~/av_config            # 개발 호스트
    sudo python3 tools/init_config_root.py /opt/av_config    # 배포

이미 내용이 있는 경로는 거부한다. 그 안의 적용 이력을 덮지 않기 위해서다.
실차 연동(run_config_app.sh)은 tools/live_config.py 가 따로 설정 루트를 만든다.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "config"
sys.path.insert(0, str(REPO / "src" / "av_config"))

from av_config.gitparams import enclosing_worktree  # noqa: E402

#: 설정 루트에 필요한 것만 복사한다. overlays/ 는 스키마 생성 입력이고
#: live_env.sh 는 실행 스크립트가 읽는 셸 파일이라 설정 루트에 둘 이유가 없다.
RUNTIME_ENTRIES = ("services.yaml", "params", "schemas", "rules")


def create_config_root(dest: Path, template: Path = TEMPLATE) -> Path:
    """template 의 설정을 dest 로 복사하고 params/ 를 git 저장소로 만든다."""
    dest = dest.expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} 에 이미 내용이 있다. 적용 이력을 덮지 않도록 빈 경로만 받는다.")
    worktree = enclosing_worktree(dest)
    if worktree is not None:
        raise ValueError(f"{dest} 가 git 저장소 {worktree} 안에 있다. 저장소 밖 경로를 지정하라.")

    dest.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_ENTRIES:
        source = template / name
        if source.is_dir():
            shutil.copytree(source, dest / name, ignore=shutil.ignore_patterns(".git"))
        elif source.is_file():
            shutil.copy2(source, dest / name)

    params = dest / "params"
    params.mkdir(exist_ok=True)
    git = ["git", "-C", str(params), "-c", "user.name=avconfig", "-c", "user.email=avconfig@localhost"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "--all"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "initial: 기준 설정"], check=True)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dest", type=Path, help="만들 설정 루트 (저장소 밖의 빈 경로)")
    parser.add_argument("--template", type=Path, default=TEMPLATE, help="복사할 템플릿 (기본: 리포의 config/)")
    args = parser.parse_args()
    try:
        root = create_config_root(args.dest, args.template)
    except (FileExistsError, ValueError) as error:
        raise SystemExit(str(error)) from None
    print(f"설정 루트를 만들었다: {root}")
    print(f"실행할 때 AV_CONFIG_ROOT={root} 로 지정한다.")


if __name__ == "__main__":
    main()
