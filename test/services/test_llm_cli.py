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
    """codex 는 답을 파일로 쓴다. 흉내 낼 때도 써 줘야 실제 경로를 지난다."""

    def run(command, **kwargs):
        if "-o" in command:
            path = command[command.index("-o") + 1]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
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

    def test_the_tool_gets_no_permissions(self):
        """대본을 쓰는 프롬프트가 파일을 건드릴 이유가 없다."""
        with patch.object(subprocess, "run", return_value=_done()) as ran:
            llm_cli.claude("프롬프트")
        self.assertIn("--allowedTools", ran.call_args.args[0])

        with patch.object(subprocess, "run", side_effect=_answers()) as ran:
            llm_cli.codex("프롬프트")
        command = ran.call_args.args[0]
        self.assertIn("read-only", command)

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

    def test_codex_reads_the_answer_from_the_file_it_wrote(self):
        """
        표준출력에는 진행 상황과 토큰 수가 함께 나온다. 그대로 쓰면 대본 앞뒤에
        도구가 한 말이 붙는다.
        """

        def write_answer(command, **kwargs):
            path = command[command.index("-o") + 1]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("진짜 대본")
            return _done(stdout="tokens used 2,578\n진행 상황\n")

        with patch.object(subprocess, "run", side_effect=write_answer):
            self.assertEqual(llm_cli.codex("프롬프트"), "진짜 대본")

    def test_codex_that_wrote_nothing_is_a_failure(self):
        with patch.object(subprocess, "run", return_value=_done()):
            with self.assertRaises(ValueError):
                llm_cli.codex("프롬프트")

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

        with patch.dict(config.app, {"llm_provider": "codex_cli"}):
            with patch.object(llm_cli, "run", return_value="대본") as run:
                self.assertEqual(llm._generate_response("프롬프트"), "대본")

        self.assertEqual(run.call_args.args[0], "codex_cli")
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
