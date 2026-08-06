"""
이미 깔려 있는 도구를 부른다.

Claude Code 는 구독으로 쓰는 명령줄 도구다. 사람 없이 도는 모드가 있어서, 대본을
여기로 받으면 API 크레딧을 따로 사지 않아도 된다. 대신 그 사용량은 사람이 직접 쓸
때와 같은 통에서 나간다 — 매일 도는 자동화가 그것을 먹으면 정작 본인 작업이 막힌다.

프롬프트는 표준입력으로 넘긴다. 8KB 가 넘어가는 데다, 명령줄 인자로 주면 같은
기계의 다른 사용자가 프로세스 목록에서 그대로 읽을 수 있다.

도구는 전부 끈다. 이 파일에서 가장 중요한 줄이다. 대본 프롬프트에는 바깥에서 온 글이
들어 있고 — 사용자가 친 주제, 오늘의 링크에서 읽어 온 제목, 소재 제공자가 붙인 설명 —
이 도구는 그 글을 지시로 읽는 대리자다. 열어 두면 "이 파일을 읽어서 답에 넣어라"
한 줄로 그 내용이 대본이 되고, 대본은 영상이 되어 텔레그램으로 나간다. 실제로 막지
않은 채 시험해 보니 파일을 그대로 읽어 왔다.

이름을 하나씩 대서 막지 않는다. 그 목록에 없는 것 — 다음 판에 생기는 도구, 붙여 둔
MCP 서버, 플러그인 — 이 그대로 열리기 때문이다. 이 도구가 문서로 밝힌 "전부 끄기"
를 쓰고, 이 기계에 얹힌 것(CLAUDE.md, 스킬, 플러그인, 훅, MCP 서버)도 함께 끈다.

Codex 는 여기에 없다. 같은 시험에서 파일을 읽어 왔는데 도구를 끄는 방법을 찾지
못했다 — ``--sandbox read-only`` 는 쓰기만 막고 읽기와 셸 실행은 그대로 둔다. 더
빠르지만, 막을 수 없는 것을 넣어 둘 자리는 아니다.
"""

import subprocess
import tempfile
import threading

from loguru import logger

# 한 번 부를 때 기다릴 시간. 프롬프트 하나에 일 분을 넘기기도 한다.
TIMEOUT_SECONDS = 300
# 받아들일 최대 응답 길이(글자). 대본은 수백 자다. 이보다 길면 응답이 아니라
# 다른 무엇이다.
MAX_OUTPUT_CHARS = 256 * 1024
# 도구를 끄는 방법. ``--tools ""`` 는 이 도구가 문서로 밝힌 "전부 끄기" 다. 이름을
# 하나씩 대는 방식은 쓰지 않는다 — 그 목록에 없는 것이 그대로 열리기 때문이다.
#
# ``--allowedTools`` 도 쓸 수 없다. 자동 승인 목록이지 제한이 아니라서, 없는 이름
# 하나만 적어 둔 채로도 파일을 그대로 읽어 왔다.
NO_TOOLS = ("--tools", "")
# 이 기계에 얹힌 것도 이 호출에는 끌어오지 않는다. CLAUDE.md, 스킬, 플러그인, 훅,
# MCP 서버, 사용자 명령이 전부 여기에 해당한다. 도구만 끄면 그쪽으로 붙은 것이
# 남고, 답이 이 기계의 설정에 따라 달라진다.
NO_CUSTOMIZATIONS = ("--safe-mode",)


def _run(command: list[str], prompt: str) -> str:
    """
    도구를 부르고 답을 돌려준다.

    받아 놓고 재지 않는다. 다 받은 뒤에 길이를 보면, 끝없이 뱉는 도구 하나가 이
    프로세스의 메모리를 먼저 채운다. 상한까지만 읽고 거기서 끊는다.

    도구가 표준오류로 뱉는 말은 아예 안 받는다. 어차피 옮기지 않는 값이고, 받아만
    두면 그쪽 관이 차서 도구가 멈춘 채로 시간만 흐른다.
    """
    try:
        process = subprocess.Popen(  # noqa: S603 - 인자 목록이라 셸을 거치지 않는다
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            # 이 도구는 도는 자리의 설정 파일을 읽는다. 작업 디렉터리를 넘겨받은
            # 곳으로 두면 그쪽 설정까지 따라가므로, 부르는 자리를 고정한다.
            cwd=tempfile.gettempdir(),
        )
    except FileNotFoundError:
        raise ValueError(f"{command[0]} is not installed") from None

    answer: list[str] = []

    def read_bounded() -> None:
        try:
            process.stdin.write(prompt)
            process.stdin.close()
            # 상한보다 한 글자 더 읽어, 넘겼는지 아닌지를 가른다.
            answer.append(process.stdout.read(MAX_OUTPUT_CHARS + 1))
        except OSError:
            # 도구가 먼저 죽으면 관이 닫힌다. 종료 코드로 판단한다.
            answer.append("")

    reader = threading.Thread(target=read_bounded, daemon=True)
    reader.start()
    reader.join(TIMEOUT_SECONDS)
    if reader.is_alive():
        process.kill()
        raise ValueError(f"{command[0]} did not answer in time")

    said = answer[0] if answer else ""
    if len(said) > MAX_OUTPUT_CHARS:
        process.kill()
        raise ValueError(f"{command[0]} answered with too much text")

    if process.wait(timeout=TIMEOUT_SECONDS) != 0:
        # 도구가 뱉은 말은 어디에도 옮기지 않는다 — 사용자 화면에도, 로그에도.
        # 인증 토큰과 설정 경로가 섞여 나오고, 로그는 나중에 통째로 공유된다.
        # 무엇이 일어났는지 짚는 데는 이름과 종료 코드로 충분하다.
        logger.warning(f"{command[0]} exited with {process.returncode}")
        raise ValueError(f"{command[0]} failed")

    return said.strip()


def claude(prompt: str, model_name: str = "") -> str:
    """Claude Code 를 사람 없이 한 번 부른다. 도구는 전부 끈 채로."""
    command = ["claude", "-p"]
    if model_name:
        command += ["--model", model_name]
    command += [*NO_CUSTOMIZATIONS]
    # 값을 여러 개 받는 옵션이라, 뒤에 다른 플래그를 두지 않는다.
    command += [*NO_TOOLS]
    return _run(command, prompt)


RUNNERS = {"claude_cli": claude}


def run(provider_id: str, prompt: str, model_name: str = "") -> str:
    """이름에 맞는 도구를 부른다."""
    runner = RUNNERS.get(provider_id)
    if runner is None:
        raise ValueError(f"{provider_id}: unsupported cli provider")
    return runner(prompt, model_name)
