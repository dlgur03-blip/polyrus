from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class Workspace:
    """격리된 임시 작업 디렉토리. 검증 입력(코드·테스트)을 여기에만 쓴다.

    경로 탈출 차단: write(name)의 name은 워크스페이스 안으로만 해석된다.
    컨텍스트 매니저로 쓰면 종료 시 정리된다.
    """

    def __init__(self, prefix: str = "polyrus_ws_") -> None:
        self._dir = Path(tempfile.mkdtemp(prefix=prefix)).resolve()

    @property
    def path(self) -> str:
        return str(self._dir)

    def write(self, name: str, content: str) -> str:
        target = (self._dir / name).resolve()
        # 경로 탈출(../, 절대경로 심볼릭) 차단.
        if self._dir != target and self._dir not in target.parents:
            raise ValueError(f"워크스페이스 밖 경로 차단: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return str(target)

    def cleanup(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self) -> Workspace:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.cleanup()


class Sandbox:
    """격리 실행 환경. 모든 외부 명령은 여기를 거친다 (직접 subprocess 금지).

    경쟁자(Hermes)는 local/Docker/SSH/Modal/Daytona 등 다중 백엔드를 둔다.
    여기서는 subprocess 백엔드(타임아웃·임시 워크스페이스·shell=False). 이후 Docker 백엔드 추가 예정.
    보안: shell=False(셸 인젝션 차단) + Workspace 경로 탈출 차단(OpenClaw 취약점 분류 참고).
    """

    def __init__(self, backend: str = "subprocess", timeout_s: int = 30) -> None:
        self.backend = backend
        self.timeout_s = timeout_s

    def workspace(self) -> Workspace:
        return Workspace()

    def run(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        if self.backend != "subprocess":
            raise NotImplementedError(f"백엔드 미지원: {self.backend}")
        full_env = {**os.environ, **(env or {})}
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env=full_env,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                shell=False,  # 셸 인젝션 차단 (cmd는 리스트로만)
            )
            return ExecResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as e:
            return ExecResult(
                returncode=124,
                stdout=e.stdout if isinstance(e.stdout, str) else "",
                stderr=e.stderr if isinstance(e.stderr, str) else "",
                timed_out=True,
            )
        except FileNotFoundError as e:
            # 명령(도구) 미설치. 127 = 셸 관례의 'command not found'.
            return ExecResult(returncode=127, stdout="", stderr=str(e))
