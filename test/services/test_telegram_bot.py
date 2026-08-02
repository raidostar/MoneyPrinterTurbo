"""텔레그램 봇."""

import unittest
from unittest.mock import patch

from app.services import telegram_bot as bot


def _message(chat_id, text):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


def _callback(chat_id, data):
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "message": {"chat": {"id": chat_id}},
        },
    }


class TestAccessControl(unittest.TestCase):
    """봇 이름은 누구나 검색할 수 있다."""

    def test_a_message_from_another_chat_is_ignored(self):
        """
        막지 않으면 모르는 사람이 이 기계에서 영상 생성을 돌리고 모델 사용료를
        쓰게 된다.
        """
        shorts = bot.ShortsBot()
        shorts.chat_id = 111

        with patch.object(bot, "_send") as send, patch.object(
            bot.llm, "generate_script"
        ) as generate:
            shorts.handle_update(_message(999, "/새영상 닭가슴살"))

        send.assert_not_called()
        generate.assert_not_called()

    def test_a_callback_from_another_chat_cannot_start_a_render(self):
        """버튼 눌림도 같은 경로로 들어온다. 메시지만 막으면 반쪽이다."""
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        shorts.pending = {"subject": "s", "script": "본문"}

        with patch.object(bot, "_send"), patch.object(
            shorts, "_start_render"
        ) as render, patch.object(bot, "_answer_callback"):
            shorts.handle_update(_callback(999, "approve"))

        render.assert_not_called()

    def test_the_allowed_chat_gets_through(self):
        """막기만 하고 내 메시지도 막으면 봇이 아니다."""
        shorts = bot.ShortsBot()
        shorts.chat_id = 111

        with patch.object(bot, "_send"), patch.object(
            bot.llm, "generate_script", return_value="대본"
        ) as generate:
            shorts.handle_update(_message(111, "/새영상 닭가슴살"))

        generate.assert_called_once()

    def test_setup_mode_acts_on_nothing(self):
        """
        chat_id 를 모르면 봇을 켤 수 없다는 문제가 있어 설정 전에도 돌아간다.
        그동안에는 chat id 만 기록하고 어떤 요청도 처리하지 않아야 한다.
        """
        shorts = bot.ShortsBot()
        shorts.chat_id = 0

        with patch.object(bot, "_send") as send, patch.object(
            bot.llm, "generate_script"
        ) as generate, patch.object(bot.logger, "info") as info:
            shorts.handle_update(_message(424242, "/새영상 닭가슴살"))

        send.assert_not_called()
        generate.assert_not_called()
        # 설정에 적을 값을 알려주는 것이 이 모드의 전부다.
        self.assertIn("424242", " ".join(str(c.args[0]) for c in info.call_args_list))


class TestApprovalFlow(unittest.TestCase):
    def test_the_script_is_not_rendered_until_it_is_approved(self):
        """
        렌더링은 십수 분이 걸린다. 마음에 들지 않는 대본에 그 시간을 쓰기 전에
        멈출 수 있어야 승인 흐름을 둔 의미가 있다.
        """
        shorts = bot.ShortsBot()
        shorts.chat_id = 111

        with patch.object(bot, "_send"), patch.object(
            bot.llm, "generate_script", return_value="대본"
        ), patch.object(shorts, "_start_render") as render:
            shorts.handle_update(_message(111, "/새영상 닭가슴살"))

        render.assert_not_called()
        self.assertEqual(shorts.pending["script"], "대본")

    def test_cancelling_drops_the_pending_script(self):
        """취소했는데 다음 승인에서 예전 대본이 살아나면 안 된다."""
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        shorts.pending = {"subject": "s", "script": "본문"}

        with patch.object(bot, "_send"), patch.object(bot, "_answer_callback"):
            shorts.handle_update(_callback(111, "cancel"))

        self.assertEqual(shorts.pending, {})

    def test_approving_with_nothing_pending_does_not_render(self):
        """취소한 뒤 예전 메시지의 승인 버튼을 다시 눌러도 아무 일이 없어야 한다."""
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        shorts.pending = {}

        with patch.object(bot, "_send"), patch.object(
            shorts, "_start_render"
        ) as render, patch.object(bot, "_answer_callback"):
            shorts.handle_update(_callback(111, "approve"))

        render.assert_not_called()

    def test_a_plain_message_replaces_the_script(self):
        """대본을 직접 고쳐 보내는 것이 봇에서 할 수 있는 유일한 편집이다."""
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        shorts.pending = {"subject": "s", "script": "원래 대본"}

        with patch.object(bot, "_send"):
            shorts.handle_update(_message(111, "내가 고친 대본"))

        self.assertEqual(shorts.pending["script"], "내가 고친 대본")

    def test_a_failed_script_generation_does_not_leave_a_pending_draft(self):
        """
        `_generate_response` 는 실패를 예외가 아니라 "Error: " 문자열로 알린다.
        거르지 않으면 오류 메시지가 대본으로 승인 대기에 올라간다.
        """
        shorts = bot.ShortsBot()
        shorts.chat_id = 111

        with patch.object(bot, "_send"), patch.object(
            bot.llm, "generate_script", return_value="Error: connection refused"
        ):
            shorts.handle_update(_message(111, "/새영상 닭가슴살"))

        self.assertEqual(shorts.pending, {})


class TestSecrets(unittest.TestCase):
    def test_a_failed_call_does_not_log_the_token(self):
        """
        봇 토큰은 요청 주소에 들어간다. 예외 문구에 주소가 붙어 나오는 라이브러리가
        있어, 메시지를 그대로 남기면 토큰이 로그로 샌다.
        """
        with patch.dict(bot.config.telegram, {"bot_token": "12345:SECRET-TOKEN"}, clear=False):
            with patch.object(
                bot.requests,
                "post",
                side_effect=RuntimeError("failed to POST https://api.telegram.org/bot12345:SECRET-TOKEN/x"),
            ), patch.object(bot.logger, "warning") as warning:
                result = bot._call("sendMessage", chat_id=1, text="x")

        self.assertIsNone(result)
        warning.assert_called_once()
        self.assertNotIn("SECRET-TOKEN", warning.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
