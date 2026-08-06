"""이미 깔려 있는 도구를 부르는 길."""

import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from app.services import llm_cli


class _Pipe:
    """받아 적기만 하는 stdin. 코드가 닫은 뒤에도 무엇이 들어갔는지 봐야 한다."""

    def __init__(self):
        self.written = ""
        self.closed = False

    def write(self, text):
        self.written += text

    def close(self):
        self.closed = True


class _FakeProcess:
    """도구인 척한다. read(n) 은 진짜처럼 n 글자까지만 돌려준다."""

    def __init__(self, text="답", returncode=0, endless=False, alive=False):
        self.text = text
        self.returncode = returncode
        self.endless = endless
        self.alive = alive
        self.args = ["claude"]
        self.pid = 4242
        self.killed = False
        self.waits = 0
        self.read_sizes = []
        self.stdin = _Pipe()
        self.stdout = self

    def read(self, size):
        self.read_sizes.append(size)
        return "가" * size if self.endless else self.text

    def kill(self):
        self.killed = True
        self.alive = False

    def poll(self):
        return None if self.alive else self.returncode

    def wait(self, timeout=None):
        self.waits += 1
        if self.alive:
            raise subprocess.TimeoutExpired("claude", timeout or 0)
        return self.returncode


def _answers(text="답", returncode=0, endless=False, alive=False):
    made = []

    def open_process(command, **kwargs):
        made.append(_FakeProcess(text, returncode, endless, alive))
        return made[-1]

    open_process.made = made
    return open_process


class TestHowItIsCalled(unittest.TestCase):
    def test_the_prompt_goes_in_through_standard_input(self):
        """
        프롬프트는 8KB 가 넘고 바깥에서 온 글이 들어 있다. 명령줄 인자로 주면 같은
        기계의 다른 사용자가 프로세스 목록에서 그대로 읽는다.
        """
        for name, runner in llm_cli.RUNNERS.items():
            with self.subTest(provider=name):
                opener = _answers()
                with patch.object(subprocess, "Popen", side_effect=opener) as ran:
                    runner("비밀이 섞인 프롬프트")

                self.assertEqual(
                    opener.made[0].stdin.written, "비밀이 섞인 프롬프트"
                )
                self.assertNotIn(
                    "비밀이 섞인 프롬프트", " ".join(ran.call_args.args[0])
                )

    def test_every_tool_is_turned_off(self):
        """
        이 도구는 프롬프트를 지시로 읽는 대리자고, 프롬프트에는 바깥에서 온 글이
        들어 있다. 하나라도 열려 있으면 그 하나로 이 기계의 파일을 읽어 간다.
        """
        with patch.object(subprocess, "Popen", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")

        command = ran.call_args.args[0]
        # 이 도구가 문서로 밝힌 "전부 끄기" 는 --tools 에 빈 값이다.
        self.assertEqual(command[command.index("--tools") + 1], "")

    def test_the_denial_is_not_a_list_of_names(self):
        """
        이름을 하나씩 대면 그 목록에 없는 것이 열린다 — 다음 판에 생기는 도구,
        붙여 둔 MCP 서버, 플러그인. 전부 끄는 쪽이어야 한다.
        """
        with patch.object(subprocess, "Popen", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")

        command = ran.call_args.args[0]
        self.assertNotIn("--disallowedTools", command)
        self.assertNotIn("Bash", command)

    def test_an_allow_list_is_not_used_to_restrict(self):
        """
        --allowedTools 는 자동 승인 목록이지 제한이 아니다. 없는 이름 하나만 적어
        둬도 파일을 그대로 읽어 왔다.
        """
        with patch.object(subprocess, "Popen", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")

        self.assertNotIn("--allowedTools", ran.call_args.args[0])

    def test_what_is_bolted_onto_this_machine_is_not_pulled_in(self):
        """
        도구만 끄면 CLAUDE.md, 스킬, 플러그인, 훅, MCP 서버가 그대로 남아, 답이
        이 기계의 설정에 따라 달라진다.
        """
        with patch.object(subprocess, "Popen", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")

        self.assertIn("--safe-mode", ran.call_args.args[0])

    def test_nothing_follows_the_empty_tool_set(self):
        """
        값을 여러 개 받는 옵션이다. 뒤에 플래그를 두면 그것이 도구 이름으로 먹혀서,
        전부 끄려던 것이 그 하나만 켜 두는 설정이 된다.
        """
        with patch.object(subprocess, "Popen", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트", "claude-opus-5")

        command = ran.call_args.args[0]
        self.assertEqual(command[command.index("--tools") :], ["--tools", ""])

    def test_it_does_not_wait_forever(self):
        """읽기가 안 끝나면 기다리기를 그만두고 도구를 죽인다."""
        opener = _answers(alive=True)
        with patch.object(llm_cli, "TIMEOUT_SECONDS", 0.2):
            with patch.object(subprocess, "Popen", side_effect=opener):
                with patch.object(_FakeProcess, "read", lambda self, n: time.sleep(30)):
                    with patch.object(llm_cli.os, "killpg") as killed:
                        with self.assertRaises(ValueError):
                            llm_cli.claude("프롬프트")

        killed.assert_called_once()

    def test_it_runs_somewhere_neutral(self):
        """
        이 도구들은 도는 자리의 설정 파일을 읽는다. 넘겨받은 곳에서 부르면 그쪽
        설정까지 따라간다.
        """
        with patch.object(subprocess, "Popen", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")
        self.assertEqual(ran.call_args.kwargs["cwd"], tempfile.gettempdir())

    def test_codex_is_not_offered(self):
        """
        같은 시험에서 파일을 읽어 왔는데, 도구를 끄는 방법을 찾지 못했다.
        read-only 는 쓰기만 막고 읽기와 셸 실행은 그대로 둔다.
        """
        from app.models.llm_provider import get_llm_provider

        self.assertNotIn("codex_cli", llm_cli.RUNNERS)
        self.assertIsNone(get_llm_provider("codex_cli"))

    def test_a_model_is_passed_only_when_asked_for(self):
        with patch.object(subprocess, "Popen", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")
        self.assertNotIn("--model", ran.call_args.args[0])

        with patch.object(subprocess, "Popen", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트", "claude-opus-5")
        command = ran.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], "claude-opus-5")


class TestReadingTheAnswer(unittest.TestCase):
    def test_the_answer_comes_back_trimmed(self):
        with patch.object(subprocess, "Popen", side_effect=_answers("  대본  \n")):
            self.assertEqual(llm_cli.claude("프롬프트"), "대본")

    def test_a_flood_of_text_is_refused_without_swallowing_it(self):
        """
        다 받아 놓고 길이를 재면, 끝없이 뱉는 도구 하나가 이 프로세스의 메모리를
        먼저 채운다. 상한까지만 읽고 거기서 끊어야 한다.
        """
        # 쏟아내는 중이니 아직 살아 있다.
        opener = _answers(endless=True, alive=True)
        with patch.object(subprocess, "Popen", side_effect=opener):
            with patch.object(llm_cli.os, "killpg") as killed:
                with self.assertRaises(ValueError) as caught:
                    llm_cli.claude("프롬프트")

        # 왜 그만뒀는지가 중요하다. 다른 이유로 실패해도 시험은 통과해 버린다.
        self.assertIn("too much text", str(caught.exception))
        process = opener.made[0]
        # 읽은 양이 상한을 한 글자만 넘는다. 판정에 필요한 최소한이다.
        self.assertEqual(process.read_sizes, [llm_cli.MAX_OUTPUT_CHARS + 1])
        killed.assert_called_once()

    def test_the_tool_never_writes_into_our_error_channel(self):
        """받아만 두고 안 읽으면 그쪽 관이 차서 도구가 멈춘 채로 시간만 흐른다."""
        with patch.object(subprocess, "Popen", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")

        self.assertEqual(ran.call_args.kwargs["stderr"], subprocess.DEVNULL)


class TestWhenItGoesWrong(unittest.TestCase):
    def test_a_missing_tool_says_so(self):
        with patch.object(subprocess, "Popen", side_effect=FileNotFoundError()):
            with self.assertRaises(ValueError) as caught:
                llm_cli.claude("프롬프트")
        self.assertIn("not installed", str(caught.exception))

    def test_a_tool_that_never_answers_is_not_waited_on_forever(self):
        opener = _answers(alive=True)

        def never_returns(self, size):
            time.sleep(30)
            return ""

        with patch.object(llm_cli, "TIMEOUT_SECONDS", 0.2):
            with patch.object(subprocess, "Popen", side_effect=opener):
                with patch.object(_FakeProcess, "read", never_returns):
                    with patch.object(llm_cli.os, "killpg") as killed:
                        with self.assertRaises(ValueError) as caught:
                            llm_cli.claude("프롬프트")

        self.assertIn("in time", str(caught.exception))
        # 기다리다 만 도구를 그대로 두면 프로세스가 쌓인다.
        killed.assert_called_once()

    def test_what_the_tool_printed_is_not_handed_to_the_user(self):
        """
        오류 문구에는 경로와 설정이 섞여 있고, 그 문구는 사용자에게 그대로 보인다.
        """
        secret = "/Users/kh/.config/openai/auth.json token=sk-abcdef0123456789"
        with patch.object(subprocess, "Popen", side_effect=_answers(returncode=1)):
            with patch.object(llm_cli.logger, "warning") as warned:
                with self.assertRaises(ValueError) as caught:
                    llm_cli.claude("프롬프트")

        self.assertNotIn(secret, str(caught.exception))
        # 로그도 마찬가지다. 나중에 통째로 공유되는 자리다.
        said = " ".join(str(call.args[0]) for call in warned.call_args_list)
        self.assertNotIn(secret, said)
        self.assertNotIn("sk-abcdef0123456789", said)
        # 무엇이 일어났는지는 여전히 알 수 있어야 한다.
        self.assertIn("claude", said)
        self.assertIn("1", said)

    def test_the_process_is_always_reaped(self):
        """
        죽이고 거두지 않으면 좀비가 남는다. 이 봇은 몇 달씩 도는 프로세스라, 영상
        한 편마다 하나씩 쌓이면 결국 프로세스를 더 못 만든다.
        """
        opener = _answers()
        with patch.object(subprocess, "Popen", side_effect=opener):
            llm_cli.claude("프롬프트")

        self.assertGreaterEqual(opener.made[0].waits, 1)

    def test_a_tool_that_will_not_die_is_killed_and_reaped(self):
        """
        말은 끝냈는데 안 죽는 경우다. 답을 받았어도 기다려 줄 수는 없고, 그대로
        두면 남는다.
        """
        opener = _answers(alive=True)
        with patch.object(llm_cli, "TIMEOUT_SECONDS", 0.2):
            with patch.object(subprocess, "Popen", side_effect=opener):
                with patch.object(llm_cli.os, "killpg") as killed:
                    with self.assertRaises(ValueError) as caught:
                        llm_cli.claude("프롬프트")

        self.assertIn("did not finish", str(caught.exception))
        killed.assert_called_once()

    def test_the_whole_group_goes_and_not_just_the_parent(self):
        """이 도구는 제 밑으로 또 프로세스를 만든다. 부모만 죽이면 자식이 남는다."""
        opener = _answers(alive=True)
        with patch.object(llm_cli, "TIMEOUT_SECONDS", 0.2):
            with patch.object(subprocess, "Popen", side_effect=opener) as ran:
                with patch.object(llm_cli.os, "killpg"):
                    with self.assertRaises(ValueError):
                        llm_cli.claude("프롬프트")

        self.assertTrue(ran.call_args.kwargs["start_new_session"])

    def test_an_unknown_name_runs_nothing(self):
        """
        모르는 이름을 아무 도구로나 흘려보내면, 설정에 오타 하나로 다른 도구가
        조용히 돌아간다.
        """
        with patch.object(subprocess, "Popen", side_effect=_answers()) as ran:
            with self.assertRaises(ValueError):
                llm_cli.run("없는도구", "프롬프트")

        ran.assert_not_called()


class TestThroughTheService(unittest.TestCase):
    def test_the_provider_reaches_the_runner(self):
        from app.config import config
        from app.services import llm

        with patch.dict(config.app, {"llm_provider": "claude_cli"}):
            with patch.object(llm_cli, "run", return_value="대본") as run:
                self.assertEqual(llm._generate_response("프롬프트"), "대본")

        self.assertEqual(run.call_args.args[0], "claude_cli")
        self.assertEqual(run.call_args.args[1], "프롬프트")

    def test_neither_needs_a_key_or_an_address(self):
        """
        구독으로 쓰는 도구다. 키를 요구하면 설정을 채울 방법이 없어 못 쓴다.
        """
        from app.models.llm_provider import get_llm_provider

        for name in llm_cli.RUNNERS:
            with self.subTest(provider=name):
                spec = get_llm_provider(name)
                self.assertIsNotNone(spec)
                self.assertEqual(spec.adapter, "cli")
                self.assertFalse(spec.requires_api_key)
                self.assertFalse(spec.requires_base_url)
                self.assertFalse(spec.requires_model_name)

    def test_a_failure_comes_back_as_an_error_string(self):
        """
        부르는 쪽은 "Error: " 로 시작하는 글을 실패로 본다. 예외를 그대로 올리면
        봇이 대본 대신 예외로 멈춘다.
        """
        from app.config import config
        from app.services import llm

        with patch.dict(config.app, {"llm_provider": "claude_cli"}):
            with patch.object(llm_cli, "run", side_effect=ValueError("claude failed")):
                said = llm._generate_response("프롬프트")

        self.assertTrue(said.startswith("Error: "))


if __name__ == "__main__":
    unittest.main()
