"""
이미 깔려 있는 도구를 부른다.

Claude Code 와 Codex 는 구독으로 쓰는 명령줄 도구다. 둘 다 사람 없이 도는 모드가
있어서, 대본을 여기로 받으면 API 크레딧을 따로 사지 않아도 된다. 대신 그 사용량은
사람이 직접 쓸 때 쓰는 것과 같은 통에서 나간다 — 매일 도는 자동화가 그것을 먹으면
정작 본인 작업이 막힌다.

프롬프트는 표준입력으로 넘긴다. 8KB 가 넘어가는 데다, 명령줄 인자로 주면 같은
기계의 다른 사용자가 프로세스 목록에서 그대로 읽을 수 있다.

도구에는 아무 권한도 주지 않는다. 대본을 쓰는 프롬프트가 파일을 건드릴 이유가 없고,
그 프롬프트에는 바깥에서 온 글(주제, 사용자가 쓴 요구사항)이 들어 있다.
"""

import os
import subprocess
import tempfile

from loguru import logger

# 한 번 부를 때 기다릴 시간. Claude 는 프롬프트 하나에 일 분을 넘기기도 한다.
TIMEOUT_SECONDS = 300
# 받아들일 최대 응답 길이(글자). 대본은 수백 자다. 이보다 길면 응답이 아니라
# 다른 무엇이다.
MAX_OUTPUT_CHARS = 256 * 1024


def _run(command: list[str], prompt: str, read_from: str = "") -> str:
    """
    도구를 부르고 답을 돌려준다.

    ``read_from`` 이 있으면 그 파일에서 답을 읽는다. Codex 는 진행 상황을 화면에
    같이 뿌려서, 표준출력을 그대로 쓰면 대본 앞뒤에 도구가 한 말이 섞인다.
    """
    try:
        result = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            # 이 도구들은 쓰는 사람의 설정을 따라간다. 작업 디렉터리를 넘겨받은 곳으로
            # 두면 그쪽 설정 파일까지 읽으므로, 부르는 자리를 고정한다.
            cwd=tempfile.gettempdir(),
        )
    except FileNotFoundError:
        raise ValueError(f"{command[0]} is not installed") from None
    except subprocess.TimeoutExpired:
        raise ValueError(f"{command[0]} did not answer in time") from None

    if result.returncode != 0:
        # 도구가 뱉은 말은 어디에도 옮기지 않는다 — 사용자 화면에도, 로그에도.
        # 인증 토큰과 설정 경로가 섞여 나오고, 로그는 나중에 통째로 공유된다.
        # 무엇이 일어났는지 짚는 데는 이름과 종료 코드로 충분하다.
        logger.warning(f"{command[0]} exited with {result.returncode}")
        raise ValueError(f"{command[0]} failed")

    answer = result.stdout
    if read_from:
        try:
            with open(read_from, encoding="utf-8") as handle:
                answer = handle.read(MAX_OUTPUT_CHARS + 1)
        except OSError:
            raise ValueError(f"{command[0]} wrote no answer") from None

    if len(answer) > MAX_OUTPUT_CHARS:
        raise ValueError(f"{command[0]} answered with too much text")
    return answer.strip()


def claude(prompt: str, model_name: str = "") -> str:
    """Claude Code 를 사람 없이 한 번 부른다."""
    command = ["claude", "-p", "--allowedTools", ""]
    if model_name:
        command += ["--model", model_name]
    return _run(command, prompt)


def codex(prompt: str, model_name: str = "") -> str:
    """
    Codex 를 사람 없이 한 번 부른다.

    답은 파일로 받는다. 표준출력에는 진행 상황과 토큰 수가 함께 나와서, 그대로
    쓰면 대본 앞뒤에 도구가 한 말이 붙는다.
    """
    with tempfile.TemporaryDirectory() as workspace:
        answer_path = os.path.join(workspace, "answer.txt")
        command = [
            "codex",
            "exec",
            # 파일을 건드릴 이유가 없다. 프롬프트에는 바깥에서 온 글이 들어 있다.
            "--sandbox",
            "read-only",
            # git 저장소 안에서만 돌려는 검사다. 우리는 대본만 받는다.
            "--skip-git-repo-check",
            "-o",
            answer_path,
        ]
        if model_name:
            command += ["--model", model_name]
        # "-" 는 프롬프트를 표준입력에서 읽으라는 뜻이다.
        command.append("-")
        return _run(command, prompt, read_from=answer_path)


RUNNERS = {"claude_cli": claude, "codex_cli": codex}


def run(provider_id: str, prompt: str, model_name: str = "") -> str:
    """이름에 맞는 도구를 부른다."""
    runner = RUNNERS.get(provider_id)
    if runner is None:
        raise ValueError(f"{provider_id}: unsupported cli provider")
    return runner(prompt, model_name)
