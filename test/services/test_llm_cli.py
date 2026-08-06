"""이미 깔려 있는 도구를 부르는 길."""

import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.services import llm_cli


def _done(stdout="답", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _answers(text="답"):
    def run(command, **kwargs):
        return _done(stdout=text)

    return run


class TestHowItIsCalled(unittest.TestCase):
    def test_the_prompt_goes_in_through_standard_input(self):
        """
        프롬프트는 8KB 가 넘고 바깥에서 온 글이 들어 있다. 명령줄 인자로 주면 같은
        기계의 다른 사용자가 프로세스 목록에서 그대로 읽는다.
        """
        for name, runner in llm_cli.RUNNERS.items():
            with self.subTest(provider=name):
                with patch.object(subprocess, "run", side_effect=_answers()) as ran:
                    runner("비밀이 섞인 프롬프트")

                self.assertEqual(ran.call_args.kwargs["input"], "비밀이 섞인 프롬프트")
                self.assertNotIn("비밀이 섞인 프롬프트", " ".join(ran.call_args.args[0]))

    def test_every_tool_is_turned_off(self):
        """
        이 도구는 프롬프트를 지시로 읽는 대리자고, 프롬프트에는 바깥에서 온 글이
        들어 있다. 하나라도 열려 있으면 그 하나로 이 기계의 파일을 읽어 간다.
        """
        with patch.object(subprocess, "run", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")

        command = ran.call_args.args[0]
        # 이 도구가 문서로 밝힌 "전부 끄기" 는 --tools 에 빈 값이다.
        self.assertEqual(command[command.index("--tools") + 1], "")

    def test_the_denial_is_not_a_list_of_names(self):
        """
        이름을 하나씩 대면 그 목록에 없는 것이 열린다 — 다음 판에 생기는 도구,
        붙여 둔 MCP 서버, 플러그인. 전부 끄는 쪽이어야 한다.
        """
        with patch.object(subprocess, "run", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")

        command = ran.call_args.args[0]
        self.assertNotIn("--disallowedTools", command)
        self.assertNotIn("Bash", command)

    def test_an_allow_list_is_not_used_to_restrict(self):
        """
        --allowedTools 는 자동 승인 목록이지 제한이 아니다. 없는 이름 하나만 적어
        둬도 파일을 그대로 읽어 왔다.
        """
        with patch.object(subprocess, "run", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")

        self.assertNotIn("--allowedTools", ran.call_args.args[0])

    def test_what_is_bolted_onto_this_machine_is_not_pulled_in(self):
        """
        도구만 끄면 CLAUDE.md, 스킬, 플러그인, 훅, MCP 서버가 그대로 남아, 답이
        이 기계의 설정에 따라 달라진다.
        """
        with patch.object(subprocess, "run", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트")

        self.assertIn("--safe-mode", ran.call_args.args[0])

    def test_nothing_follows_the_empty_tool_set(self):
        """
        값을 여러 개 받는 옵션이다. 뒤에 플래그를 두면 그것이 도구 이름으로 먹혀서,
        전부 끄려던 것이 그 하나만 켜 두는 설정이 된다.
        """
        with patch.object(subprocess, "run", side_effect=_answers()) as ran:
            llm_cli.claude("프롬프트", "claude-opus-5")

        command = ran.call_args.args[0]
        self.assertEqual(command[command.index("--tools") :], ["--tools", ""])

    def test_it_does_not_wait_forever(self):
        for name, runner in llm_cli.RUNNERS.items():
            with self.subTest(provider=name):
                with patch.object(subprocess, "run", side_effect=_answers()) as ran:
                    runner("프롬프트")
                self.assertEqual(
                    ran.call_args.kwargs["timeout"], llm_cli.TIMEOUT_SECONDS
                )

    def test_it_runs_somewhere_neutral(self):
        """
        이 도구들은 도는 자리의 설정 파일을 읽는다. 넘겨받은 곳에서 부르면 그쪽
        설정까지 따라간다.
        """
        with patch.object(subprocess, "run", return_value=_done()) as ran:
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
        with patch.object(subprocess, "run", return_value=_done()) as ran:
            llm_cli.claude("프롬프트")
        self.assertNotIn("--model", ran.call_args.args[0])

        with patch.object(subprocess, "run", return_value=_done()) as ran:
            llm_cli.claude("프롬프트", "claude-opus-5")
        command = ran.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], "claude-opus-5")


class TestReadingTheAnswer(unittest.TestCase):
    def test_the_answer_comes_back_trimmed(self):
        with patch.object(subprocess, "run", return_value=_done(stdout="  대본  \n")):
            self.assertEqual(llm_cli.claude("프롬프트"), "대본")

    def test_a_flood_of_text_is_refused(self):
        """대본은 수백 자다. 이보다 크면 답이 아니라 다른 무엇이다."""
        with patch.object(
            subprocess,
            "run",
            return_value=_done(stdout="가" * (llm_cli.MAX_OUTPUT_CHARS + 1)),
        ):
            with self.assertRaises(ValueError):
                llm_cli.claude("프롬프트")


class TestWhenItGoesWrong(unittest.TestCase):
    def test_a_missing_tool_says_so(self):
        with patch.object(subprocess, "run", side_effect=FileNotFoundError()):
            with self.assertRaises(ValueError) as caught:
                llm_cli.claude("프롬프트")
        self.assertIn("not installed", str(caught.exception))

    def test_a_tool_that_never_answers_is_not_waited_on_forever(self):
        with patch.object(
            subprocess, "run", side_effect=subprocess.TimeoutExpired("claude", 1)
        ):
            with self.assertRaises(ValueError) as caught:
                llm_cli.claude("프롬프트")
        self.assertIn("in time", str(caught.exception))

    def test_what_the_tool_printed_is_not_handed_to_the_user(self):
        """
        오류 문구에는 경로와 설정이 섞여 있고, 그 문구는 사용자에게 그대로 보인다.
        """
        secret = "/Users/kh/.config/openai/auth.json token=sk-abcdef0123456789"
        with patch.object(
            subprocess, "run", return_value=_done(returncode=1, stderr=secret)
        ):
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

    def test_an_unknown_name_runs_nothing(self):
        """
        모르는 이름을 아무 도구로나 흘려보내면, 설정에 오타 하나로 다른 도구가
        조용히 돌아간다.
        """
        with patch.object(subprocess, "run", side_effect=_answers()) as ran:
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
