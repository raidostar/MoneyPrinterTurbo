"""텔레그램 봇."""

import time
import unittest
import unittest.mock
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from app.services import telegram_bot as bot

# 텔레그램이 callback_data 로 받아 주는 최대 크기. 넘기면 메시지 자체가 거부되어
# 버튼이 아예 안 나온다.
MAX_CALLBACK_DATA_BYTES = 64


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
        shorts.pending = {"subject": "s", "script": "본문", "draft_id": "abc12345"}

        with patch.object(bot, "_send"), patch.object(
            shorts, "_start_render"
        ) as render, patch.object(bot, "_answer_callback"):
            shorts.handle_update(_callback(999, "approve:abc12345"))

        render.assert_not_called()

    def test_the_allowed_chat_gets_through(self):
        """막기만 하고 내 메시지도 막으면 봇이 아니다."""
        shorts = bot.ShortsBot()
        shorts.chat_id = 111

        # 사람이 여럿이면 누구로 쓸지 먼저 묻는다. 여기서 보려는 것은 그게 아니라
        # 내 메시지가 통과하는지다.
        only = {"haerinmom": bot.persona.PERSONAS["haerinmom"]}
        with (
            patch.dict(bot.persona.PERSONAS, only, clear=True),
            patch.object(bot, "_send"),
            patch.object(bot.llm, "generate_script", return_value="대본") as generate,
        ):
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

        only = {"haerinmom": bot.persona.PERSONAS["haerinmom"]}
        with (
            patch.dict(bot.persona.PERSONAS, only, clear=True),
            patch.object(bot, "_send"),
            patch.object(bot.llm, "generate_script", return_value="대본"),
            patch.object(shorts, "_start_render") as render,
        ):
            shorts.handle_update(_message(111, "/새영상 닭가슴살"))

        render.assert_not_called()
        self.assertEqual(shorts.pending["script"], "대본")

    def test_cancelling_drops_the_pending_script(self):
        """취소했는데 다음 승인에서 예전 대본이 살아나면 안 된다."""
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        shorts.pending = {"subject": "s", "script": "본문", "draft_id": "abc12345"}

        with patch.object(bot, "_send"), patch.object(bot, "_answer_callback"):
            shorts.handle_update(_callback(111, "cancel:abc12345"))

        self.assertEqual(shorts.pending, {})

    def test_approving_with_nothing_pending_does_not_render(self):
        """취소한 뒤 예전 메시지의 승인 버튼을 다시 눌러도 아무 일이 없어야 한다."""
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        shorts.pending = {}

        with patch.object(bot, "_send"), patch.object(
            shorts, "_start_render"
        ) as render, patch.object(bot, "_answer_callback"):
            shorts.handle_update(_callback(111, "approve:abc12345"))

        render.assert_not_called()

    def test_a_plain_message_replaces_the_script(self):
        """대본을 직접 고쳐 보내는 것이 봇에서 할 수 있는 유일한 편집이다."""
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        shorts.pending = {"subject": "s", "script": "원래 대본", "draft_id": "abc12345"}

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


class TestStaleButtonsAndBounds(unittest.TestCase):
    def test_a_button_from_a_replaced_draft_is_refused(self):
        """
        다시 뽑은 뒤 예전 메시지의 승인을 누르면, 보고 있는 것과 다른 대본이
        만들어진다. 승인한 것과 만들어지는 것이 달라서는 안 된다.
        """
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        shorts.pending = {"subject": "s", "script": "새 대본", "draft_id": "new00000"}

        with patch.object(bot, "_send"), patch.object(
            shorts, "_start_render"
        ) as render, patch.object(bot, "_answer_callback"):
            shorts.handle_update(_callback(111, "approve:old00000"))

        render.assert_not_called()

    def test_each_draft_gets_its_own_button_id(self):
        """번호가 같으면 지난 대본의 버튼을 구분할 수 없다."""
        shorts = bot.ShortsBot()
        shorts.chat_id = 111

        with patch.object(bot, "_send"):
            shorts._offer_draft("s", "첫 대본")
            first = shorts.pending["draft_id"]
            shorts._offer_draft("s", "둘째 대본")

        self.assertNotEqual(first, shorts.pending["draft_id"])

    def test_a_draft_always_fits_in_one_telegram_message(self):
        """
        대본 전문과 글자 수를 한 메시지에 실어 버튼을 붙인다. 텔레그램 한도를
        넘으면 그 메시지가 통째로 거절되어, 승인할 방법 자체가 사라진다.
        """
        shorts = bot.ShortsBot()
        shorts.chat_id = 111

        with patch.object(bot, "_send") as send:
            shorts._offer_draft("주제", "가" * 50_000)

        text = send.call_args.args[1]
        self.assertLess(len(text), bot.MAX_TELEGRAM_MESSAGE_LENGTH)
        self.assertIsNotNone(send.call_args.kwargs.get("buttons"))

    def test_an_overlong_script_is_cut_before_it_reaches_the_pipeline(self):
        """
        대본은 키워드 생성 프롬프트와 TTS 로 흘러간다. 봇으로 들어오는 값도 다른
        입구와 같은 상한을 받아야 한다.
        """
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        shorts.pending = {"subject": "s", "script": "짧은 대본", "draft_id": "abc12345"}

        with patch.object(bot, "_send"):
            shorts.handle_update(_message(111, "가" * 50_000))

        self.assertLessEqual(len(shorts.pending["script"]), bot.MAX_SCRIPT_LENGTH)


class TestMalformedResponses(unittest.TestCase):
    """봇 API 응답은 외부 입력이다."""

    def test_a_non_object_body_does_not_raise(self):
        """예상 밖의 본문 하나가 폴링 루프를 끝내면 밖에 있는 동안 봇이 죽는다."""
        with patch.object(bot.requests, "post") as post:
            post.return_value.json.return_value = ["unexpected"]
            self.assertIsNone(bot._call("sendMessage", chat_id=1, text="x"))

    def test_a_non_iterable_result_does_not_kill_the_poller(self):
        """
        `result` 가 순회할 수 없는 값이면 for 문에서 예외가 나고, 그 예외는
        폴링 루프 밖으로 나가 봇을 끝낸다.
        """
        shorts = bot.ShortsBot()
        with patch.object(bot, "_call", return_value=42):
            shorts.poll_once()
        self.assertEqual(shorts.offset, 0)

    def test_an_update_without_a_usable_id_still_advances_the_offset(self):
        """
        번호를 읽지 못한 업데이트를 그냥 건너뛰면 같은 것을 계속 다시 받아,
        봇이 그 자리에 갇힌다.
        """
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        with patch.object(bot, "_call", return_value=[{"update_id": "nope"}]):
            shorts.poll_once()
        self.assertEqual(shorts.offset, 1)


class TestNonPrivateChats(unittest.TestCase):
    def test_a_group_chat_is_refused_even_with_a_matching_id(self):
        """
        그룹 대화 id 를 설정에 넣어 두면 그 방의 누구나 이 기계에서 렌더링을 돌리고
        모델 사용료를 쓴다. chat id 하나로는 한 사람으로 좁혀지지 않는다.
        """
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        update = _message(111, "/새영상 닭가슴살")
        update["message"]["chat"]["type"] = "supergroup"

        with patch.object(bot, "_send") as send, patch.object(
            bot.llm, "generate_script"
        ) as generate:
            shorts.handle_update(update)

        send.assert_not_called()
        generate.assert_not_called()


class TestResponseSize(unittest.TestCase):
    def test_an_oversized_body_is_dropped_before_it_is_parsed(self):
        """
        응답은 외부 입력이다. 통째로 메모리에 올려 파싱하면, 거대한 본문 하나로
        이 기계가 흔들린다.
        """
        huge = b'{"ok": true, "result": "' + b"x" * (bot.MAX_RESPONSE_BYTES + 10) + b'"}'

        with patch.object(bot.requests, "post") as post:
            response = post.return_value
            response.__enter__ = lambda self_: self_
            response.__exit__ = lambda *a: False
            response.raw.read.return_value = huge
            result = bot._call("getUpdates")

        self.assertIsNone(result)
        # 크기를 넘겼는지 알 수 있을 만큼만 읽어야 한다.
        self.assertEqual(
            response.raw.read.call_args.args[0], bot.MAX_RESPONSE_BYTES + 1
        )

    def test_the_video_upload_response_is_bounded_too(self):
        """
        업로드 응답도 같은 외부 입력이다. 여기만 통째로 파싱하면 상한을 둔 의미가 없다.
        """
        with patch.object(bot.os.path, "getsize", return_value=1024), patch(
            "builtins.open", unittest.mock.mock_open(read_data=b"x")
        ), patch.object(bot, "_read_bounded_json", return_value=None) as reader, patch.object(
            bot, "_send"
        ), patch.object(bot.requests, "post"):
            bot._send_video(1, "video.mp4", "caption")

        reader.assert_called_once()

    def test_a_rejection_message_is_trimmed_before_logging(self):
        """
        거절 사유는 상대가 쓴 문구다. 길이 제한도 없고 제어문자가 섞일 수도 있어,
        그대로 남기면 로그를 보는 화면이 조작되거나 로그가 통째로 밀린다.
        """
        with patch.object(bot, "_read_bounded_json", return_value={
            "ok": False, "description": "\x1b[2Jbad " + "x" * 5000
        }), patch.object(bot.requests, "post"), patch.object(
            bot.logger, "warning"
        ) as warning:
            self.assertIsNone(bot._call("sendMessage", chat_id=1, text="x"))

        message = warning.call_args.args[0]
        self.assertLess(len(message), 500)
        self.assertNotIn("\x1b", message)

    def test_the_poller_asks_for_a_bounded_batch(self):
        """한 번에 받는 업데이트 수에 상한이 없으면 배치 하나가 그대로 부하가 된다."""
        shorts = bot.ShortsBot()
        with patch.object(bot, "_call", return_value=[]) as call:
            shorts.poll_once()

        self.assertLessEqual(call.call_args.kwargs["limit"], 100)


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

    def test_a_render_failure_does_not_log_provider_credentials(self):
        """
        렌더링은 LLM·TTS·스톡 영상 제공자를 모두 거친다. 그 예외 메시지에는 자격
        증명이 붙은 주소가 섞일 수 있어, 트레이스백째로 남기면 그대로 로그에 남는다.
        """
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        leaky = RuntimeError("failed: https://user:hunter2@api.example.com/v1")

        with patch.object(bot, "_send"), patch.object(
            bot.tm, "start", side_effect=leaky
        ), patch.object(bot.logger, "error") as error, patch.object(
            bot.logger, "exception"
        ) as exception:
            shorts._render("주제", "대본")

        exception.assert_not_called()
        error.assert_called_once()
        self.assertNotIn("hunter2", error.call_args.args[0])


class TestDailyFlow(unittest.TestCase):
    """매일 후보를 보내고 고른 것만 만든다."""

    def setUp(self):
        # 후보를 보여 줄 때 소재를 한국어로 옮기고 저장소를 살펴본다. 여기서
        # 보려는 것은 목록 자체라, 밖으로 나가지 않게 막아 둔다.
        digest = patch.object(bot.llm, "digest_candidates", return_value={})
        signals = patch.object(
            bot.repo, "fetch_signals", return_value=bot.repo.RepoSignals()
        )
        digest.start()
        signals.start()
        self.addCleanup(digest.stop)
        self.addCleanup(signals.stop)

    def _bot(self):
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        return shorts

    def test_today_offers_candidates_without_rendering(self):
        """
        고르기 전에 만들기 시작하면, 안 쓸 소재에 십 분을 쓴다.
        """
        from app.services.daily import DailyPick, DailyRun
        from app.services.sources.base import SourceItem

        run = DailyRun(picks=tuple(
            DailyPick(item=SourceItem(source="hackernews", item_id=str(i), title=f"글 {i}"), reason="100 points")
            for i in range(3)
        ))
        shorts = self._bot()
        with (
            patch.object(bot.daily, "pick_items", return_value=run),
            patch.object(bot, "_send") as send,
            patch.object(bot.cardscript, "build_card_script") as build,
        ):
            shorts.handle_update(_message(111, "/오늘"))

        build.assert_not_called()
        self.assertEqual(len(shorts.candidates), 3)
        self.assertGreaterEqual(send.call_count, 3)

    def test_picking_a_candidate_drafts_cards(self):
        from app.services.sources.base import SourceItem

        shorts = self._bot()
        shorts.candidates = {"tok": SourceItem(source="hackernews", item_id="1", title="글")}
        script = SimpleNamespace(
            cards=(bot.cardnews.Card(index_label="01", title="제목", body=("하나",)),),
            narrations=("말",),
            narration_text="말",
        )
        with (
            patch.object(bot.cardscript, "build_card_script", return_value=script),
            patch.object(bot, "_send"),
            patch.object(bot, "_answer_callback"),
        ):
            shorts.handle_update(_callback(111, "pick:tok"))

        self.assertIs(shorts.pending["card_script"], script)

    def test_the_manifest_says_what_the_video_was_made_from(self):
        """
        렌더러는 매니페스트를 보완만 한다. 없으면 아무것도 안 남아, 이 경로로 만든
        영상은 무엇으로 만들었는지 되짚을 수 없다.
        """
        from app.services.sources.base import SourceItem

        item = SourceItem(
            source="hackernews", item_id="42", title="글", points=117,
            url="https://example.com/x",
        )
        script = SimpleNamespace(
            cards=(bot.cardnews.Card(title="제목", body=("하나",)),),
            narrations=("말",),
        )
        made = SimpleNamespace(video_path="out.mp4", duration=30.0, card_count=1)

        shorts = self._bot()
        with (
            patch.object(bot.cardvideo, "render_card_news", return_value=made),
            patch.object(bot.task_artifacts, "write_script_data") as write,
            patch.object(bot.daily, "mark_used"),
            patch.object(bot, "_send_video"),
            patch.object(bot, "_send"),
        ):
            shorts._render_cards(item, script)

        payload = write.call_args.args[1]
        self.assertEqual(payload["source"]["item_id"], "42")
        self.assertEqual(payload["cards"][0]["narration"], "말")
        self.assertIn("params", payload)

    def test_a_quiet_refresh_drops_yesterdays_buttons(self):
        """
        새 목록이 없다고 예전 목록을 남겨 두면, 어제 버튼이 오늘도 먹혀 이미 만든
        소재를 다시 만든다.
        """
        from app.services.daily import DailyRun
        from app.services.sources.base import SourceItem

        for run in (DailyRun(), DailyRun(source_reachable=False)):
            with self.subTest(run=run):
                shorts = self._bot()
                shorts.candidates = {
                    "old": SourceItem(source="hackernews", item_id="1", title="어제 글")
                }
                with (
                    patch.object(bot.daily, "pick_items", return_value=run),
                    patch.object(bot, "_send"),
                ):
                    shorts._offer_today()

                self.assertEqual(shorts.candidates, {})

    def test_a_token_cannot_be_used_twice(self):
        """
        만든 뒤에 같은 버튼을 또 누르면 같은 것을 다시 만든다. 모델 호출과
        렌더링 비용이 그대로 두 번 든다.
        """
        from app.services.sources.base import SourceItem

        shorts = self._bot()
        shorts.candidates = {"tok": SourceItem(source="hackernews", item_id="1", title="글")}
        script = SimpleNamespace(cards=(), narrations=(), narration_text="말")
        with (
            patch.object(bot.cardscript, "build_card_script", return_value=script) as build,
            patch.object(bot, "_send"),
            patch.object(bot, "_answer_callback"),
        ):
            shorts.handle_update(_callback(111, "pick:tok"))
            shorts.handle_update(_callback(111, "pick:tok"))

        self.assertEqual(build.call_count, 1)

    def test_a_button_from_a_previous_list_is_refused(self):
        shorts = self._bot()
        shorts.candidates = {}
        with (
            patch.object(bot.cardscript, "build_card_script") as build,
            patch.object(bot, "_send"),
            patch.object(bot, "_answer_callback"),
        ):
            shorts.handle_update(_callback(111, "pick:gone"))

        build.assert_not_called()

    def test_what_was_made_is_recorded_and_a_failure_is_not(self):
        """
        만든 소재는 내일 다시 나오면 안 되고, 만들다 실패한 소재는 다시 나와야
        한다. 기록하지 않으면 같은 것을 계속 다시 만든다.
        """
        from app.services.sources.base import SourceItem

        item = SourceItem(source="hackernews", item_id="1", title="글")
        made = SimpleNamespace(video_path="out.mp4", duration=30.0, card_count=5)

        script = SimpleNamespace(cards=(), narrations=())
        shorts = self._bot()
        with (
            patch.object(bot.cardvideo, "render_card_news", return_value=made),
            patch.object(bot.task_artifacts, "write_script_data"),
            patch.object(bot.daily, "mark_used") as mark,
            patch.object(bot, "_send_video"),
            patch.object(bot, "_send"),
        ):
            shorts._render_cards(item, script)
        mark.assert_called_once_with(item)

        shorts = self._bot()
        with (
            patch.object(bot.cardvideo, "render_card_news", return_value=None),
            patch.object(bot.task_artifacts, "write_script_data"),
            patch.object(bot.daily, "mark_used") as mark,
            patch.object(bot, "_send"),
        ):
            shorts._render_cards(item, script)
        mark.assert_not_called()


class TestDailySchedule(unittest.TestCase):
    def _bot(self, hour):
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        self._hour = hour
        return shorts

    def _at(self, hour):
        return time.struct_time((2026, 8, 3, hour, 0, 0, 0, 215, 0))

    def test_nothing_is_sent_before_the_hour(self):
        shorts = self._bot("9")
        with (
            patch.dict(bot.config.telegram, {"daily_hour": "9"}, clear=False),
            patch.object(shorts, "_offer_today") as offer,
        ):
            self.assertFalse(shorts.maybe_run_daily(self._at(8)))
        offer.assert_not_called()

    def test_it_only_fires_once_a_day(self):
        """
        시각만 보면 그 시간대 안에서 폴링이 도는 동안 계속 보낸다. 몇 분마다
        같은 목록이 오게 된다.
        """
        shorts = self._bot("9")
        with (
            patch.dict(bot.config.telegram, {"daily_hour": "9"}, clear=False),
            patch.object(bot.daily, "load_last_run", side_effect=["", "2026-08-03"]),
            patch.object(bot.daily, "save_last_run") as save,
            patch.object(shorts, "_offer_today", return_value=True) as offer,
        ):
            self.assertTrue(shorts.maybe_run_daily(self._at(9)))
            self.assertFalse(shorts.maybe_run_daily(self._at(10)))

        self.assertEqual(offer.call_count, 1)
        # 날짜를 남기지 않으면 다시 켰을 때 그날 목록이 또 나간다.
        save.assert_called_once_with("2026-08-03")

    def test_a_quiet_day_is_not_retried_all_day(self):
        """
        새 글이 없는 날도 오늘 몫을 마친 것이다. 실패로 치면 폴링마다 소스에 다시
        물어보고 같은 안내를 계속 보낸다.
        """
        from app.services.daily import DailyRun

        shorts = self._bot("9")
        with (
            patch.dict(bot.config.telegram, {"daily_hour": "9"}, clear=False),
            patch.object(bot.daily, "pick_items", return_value=DailyRun()) as pick,
            patch.object(bot.daily, "load_last_run", side_effect=["", "2026-08-03"]),
            patch.object(bot.daily, "save_last_run"),
            patch.object(bot, "_send"),
        ):
            self.assertTrue(shorts.maybe_run_daily(self._at(9)))
            self.assertFalse(shorts.maybe_run_daily(self._at(10)))

        self.assertEqual(pick.call_count, 1)

    def test_a_quiet_day_says_so_instead_of_showing_an_empty_list(self):
        """후보가 없는데 목록 안내만 지나가면 무슨 일이 있었는지 알 수 없다."""
        from app.services.daily import DailyRun

        shorts = self._bot("9")
        with (
            patch.object(bot.daily, "pick_items", return_value=DailyRun()),
            patch.object(bot, "_send") as send,
        ):
            self.assertTrue(shorts._offer_today())

        messages = " ".join(str(call.args[1]) for call in send.call_args_list)
        self.assertIn("새로 다룰 만한 게 없어요", messages)
        self.assertEqual(shorts.candidates, {})

    def test_an_unreachable_source_is_tried_again(self):
        """못 닿은 건 마친 게 아니다."""
        from app.services.daily import DailyRun

        shorts = self._bot("9")
        with (
            patch.dict(bot.config.telegram, {"daily_hour": "9"}, clear=False),
            patch.object(bot.daily, "pick_items", return_value=DailyRun(source_reachable=False)),
            patch.object(bot.daily, "load_last_run", return_value=""),
            patch.object(bot.daily, "save_last_run") as save,
            patch.object(bot, "_send"),
        ):
            self.assertFalse(shorts.maybe_run_daily(self._at(9)))

        save.assert_not_called()

    def test_a_storage_failure_does_not_resend_all_day(self):
        """
        날짜를 못 쓰면 다음 폴링이 기록이 없다고 보고 또 보낸다. 저장 실패가
        도배로 번지면 안 된다.
        """
        shorts = self._bot("9")
        with (
            patch.dict(bot.config.telegram, {"daily_hour": "9"}, clear=False),
            patch.object(bot.daily, "load_last_run", return_value=""),
            patch.object(bot.daily, "save_last_run", return_value=False),
            patch.object(shorts, "_offer_today", return_value=True) as offer,
        ):
            self.assertTrue(shorts.maybe_run_daily(self._at(9)))
            self.assertFalse(shorts.maybe_run_daily(self._at(10)))

        self.assertEqual(offer.call_count, 1)

    def test_restarting_does_not_resend_todays_list(self):
        """메모리에만 두면 봇을 다시 켤 때마다 그날 목록이 또 나간다."""
        with (
            patch.dict(bot.config.telegram, {"daily_hour": "9"}, clear=False),
            patch.object(bot.daily, "load_last_run", return_value="2026-08-03"),
            patch.object(bot.ShortsBot, "_offer_today") as offer,
        ):
            self.assertFalse(self._bot("9").maybe_run_daily(self._at(11)))

        offer.assert_not_called()

    def test_a_failed_offer_is_tried_again_later(self):
        """
        잠깐의 장애로 그날 하루가 통째로 넘어가면 안 된다. 보여 주는 데 성공한
        다음에 날짜를 적어야 한다.
        """
        shorts = self._bot("9")
        clock = iter([0.0, 0.0, bot.DAILY_RETRY_SECONDS + 1, bot.DAILY_RETRY_SECONDS + 1])
        with (
            patch.dict(bot.config.telegram, {"daily_hour": "9"}, clear=False),
            patch.object(bot.daily, "load_last_run", return_value=""),
            patch.object(bot.daily, "save_last_run"),
            patch.object(bot.time, "monotonic", side_effect=lambda: next(clock)),
            patch.object(shorts, "_offer_today", side_effect=[False, True]) as offer,
        ):
            self.assertFalse(shorts.maybe_run_daily(self._at(9)))
            self.assertTrue(shorts.maybe_run_daily(self._at(10)))

        self.assertEqual(offer.call_count, 2)

    def test_an_outage_is_not_retried_every_poll(self):
        """
        폴링은 초 단위로 돈다. 그 주기로 다시 물어보면 장애가 이어지는 동안
        요청과 안내가 하루 종일 쌓인다.
        """
        shorts = self._bot("9")
        with (
            patch.dict(bot.config.telegram, {"daily_hour": "9"}, clear=False),
            patch.object(bot.daily, "load_last_run", return_value=""),
            patch.object(bot.time, "monotonic", return_value=0.0),
            patch.object(shorts, "_offer_today", return_value=False) as offer,
        ):
            for _ in range(20):
                self.assertFalse(shorts.maybe_run_daily(self._at(9)))

        self.assertEqual(offer.call_count, 1)

    def test_no_hour_means_no_schedule(self):
        """정하지 않았으면 직접 칠 때만 돈다."""
        shorts = self._bot("")
        with (
            patch.dict(bot.config.telegram, {"daily_hour": ""}, clear=False),
            patch.object(shorts, "_offer_today") as offer,
        ):
            self.assertFalse(shorts.maybe_run_daily(self._at(23)))
        offer.assert_not_called()

    def test_a_nonsense_hour_is_ignored_rather_than_crashing(self):
        for bad in ("아침", "25", "-1"):
            with self.subTest(hour=bad):
                shorts = self._bot(bad)
                with (
                    patch.dict(bot.config.telegram, {"daily_hour": bad}, clear=False),
                    patch.object(shorts, "_offer_today") as offer,
                ):
                    self.assertFalse(shorts.maybe_run_daily(self._at(12)))
                offer.assert_not_called()


class TestCandidateList(unittest.TestCase):
    """
    소재는 대부분 영어로 올라온다. 제목만 그대로 보내면 고르는 사람이 매번 링크를
    열어 봐야 하고, 그러면 목록을 보내는 의미가 없다.
    """

    def _offer(self, digest=None, signals=None, titles=("Show HN: A tiny thing",)):
        from app.services.daily import DailyPick, DailyRun
        from app.services.sources.base import SourceItem

        run = DailyRun(
            picks=tuple(
                DailyPick(
                    item=SourceItem(
                        source="hackernews",
                        item_id=str(index),
                        title=title,
                        url=f"https://github.com/someone/thing{index}",
                    ),
                    reason="117 points",
                )
                for index, title in enumerate(titles)
            )
        )
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        with (
            patch.object(bot.daily, "pick_items", return_value=run),
            patch.object(bot.llm, "digest_candidates", return_value=digest or {}),
            patch.object(
                bot.repo,
                "fetch_signals",
                return_value=signals or bot.repo.RepoSignals(),
            ),
            patch.object(bot, "_send") as send,
        ):
            shorts._offer_today()
        return " ".join(str(call.args[1]) for call in send.call_args_list)

    def test_the_list_is_written_in_korean(self):
        said = self._offer(
            digest={1: {"title": "Kakehashi — Linux에서 macOS 실행", "summary": "JIT 없이 Mach-O 실행"}}
        )
        self.assertIn("Linux에서 macOS 실행", said)
        self.assertIn("JIT 없이 Mach-O 실행", said)

    def test_a_candidate_we_could_not_translate_still_shows_up(self):
        """옮기지 못했다고 목록에서 빼면, 그 소재는 영영 안 보인다."""
        said = self._offer(digest={}, titles=("Show HN: A tiny thing",))
        self.assertIn("Show HN: A tiny thing", said)

    def test_the_list_says_how_alive_the_project_is(self):
        """별 수와 마지막 커밋은 열어 보지 않고 판단할 수 있는 값이다."""
        said = self._offer(
            signals=bot.repo.RepoSignals(
                seen=True, stars=292, language="Rust", idle_days=0
            )
        )
        self.assertIn("★292", said)
        self.assertIn("Rust", said)

    def test_translating_happens_once_for_the_whole_list(self):
        """건마다 부르면 후보 다섯 개에 다섯 번을 쓴다."""
        from app.services.daily import DailyPick, DailyRun
        from app.services.sources.base import SourceItem

        run = DailyRun(
            picks=tuple(
                DailyPick(item=SourceItem(source="hackernews", item_id=str(i), title=f"글 {i}"))
                for i in range(5)
            )
        )
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        with (
            patch.object(bot.daily, "pick_items", return_value=run),
            patch.object(bot.llm, "digest_candidates", return_value={}) as digest,
            patch.object(bot.repo, "fetch_signals", return_value=bot.repo.RepoSignals()),
            patch.object(bot, "_send"),
        ):
            shorts._offer_today()

        digest.assert_called_once()


class TestScoresAreShownAndKept(unittest.TestCase):
    """
    화면에 나가는 판정은 승인 전에 보여야 하고, 나간 뒤에는 기록에 남아야 한다.
    """

    def _script(self):
        return SimpleNamespace(
            cards=(
                bot.cardnews.Card(index_label="01", title="여는 장", body=("하나",)),
                bot.cardnews.Card(
                    index_label="02",
                    title="점수",
                    scores=(
                        bot.cardnews.Score("완성도", 4, "테스트 있음"),
                        bot.cardnews.Score("바로 쓰기", 2, "GPU 필요"),
                    ),
                ),
            ),
            narrations=("말", "정리하면"),
            narration_text="말 정리하면",
        )

    def test_the_approval_message_shows_the_scores(self):
        """
        "점수" 한 글자만 보고 승인하면, 못 본 판정이 붙은 영상이 그대로 계정에
        올라간다.
        """
        from app.services.sources.base import SourceItem

        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        with (
            patch.object(bot.cardscript, "build_card_script", return_value=self._script()),
            patch.object(bot, "_send") as send,
        ):
            shorts._draft_cards(SourceItem(source="hackernews", item_id="1", title="글"))

        said = " ".join(str(call.args[1]) for call in send.call_args_list)
        self.assertIn("완성도", said)
        self.assertIn("테스트 있음", said)
        self.assertIn("GPU 필요", said)
        # 값도 보여야 한다. 이름만 보고는 몇 점인지 알 수 없다.
        self.assertIn("4", said)
        self.assertIn("2", said)

    def test_the_manifest_keeps_the_scores_that_went_out(self):
        """남기지 않으면 어떤 점수를 왜 줬는지 나중에 되짚을 수 없다."""
        from app.services.sources.base import SourceItem

        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        item = SourceItem(source="hackernews", item_id="1", title="글")
        made = SimpleNamespace(video_path="out.mp4", duration=30.0, card_count=2)
        with (
            patch.object(bot.task_artifacts, "write_script_data") as write,
            patch.object(bot.cardvideo, "render_card_news", return_value=made),
            patch.object(bot.daily, "mark_used"),
            patch.object(bot, "_send_video"),
            patch.object(bot, "_send"),
            patch.object(shorts, "_offer_publish"),
        ):
            shorts._render_cards(item, self._script())

        cards = write.call_args.args[1]["cards"]
        self.assertEqual(cards[0]["scores"], [])
        self.assertEqual(
            cards[1]["scores"],
            [
                {"label": "완성도", "value": 4, "reason": "테스트 있음"},
                {"label": "바로 쓰기", "value": 2, "reason": "GPU 필요"},
            ],
        )


class TestPublishing(unittest.TestCase):
    """만든 영상을 계정에 올린다."""

    def _bot(self):
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        return shorts

    def _target(self):
        return bot.publish.PublishTarget(
            channel=bot.publish.CARD_NEWS, profile="p", platforms=("youtube",)
        )

    def test_it_asks_before_publishing(self):
        """
        시험용 영상이 계정에 올라가면 되돌릴 수 없고, 무료 사용량도 거기서 나간다.
        """
        shorts = self._bot()
        with (
            patch.object(bot.publish, "resolve_target", return_value=self._target()),
            patch.object(bot.publish, "auto_publishes", return_value=False),
            patch.object(bot.publish, "publish") as published,
            patch.object(bot, "_send") as send,
        ):
            shorts._offer_publish(bot.publish.CARD_NEWS, "/tmp/a.mp4", "제목")

        published.assert_not_called()
        self.assertEqual(len(shorts.uploads), 1)
        self.assertIn("publish:", str(send.call_args.kwargs["buttons"]))

    def test_the_button_publishes_what_it_was_made_for(self):
        shorts = self._bot()
        shorts.uploads = {"tok": (bot.publish.CARD_NEWS, "/tmp/a.mp4", "제목")}
        with (
            patch.object(bot, "_answer_callback"),
            patch.object(bot, "_send"),
            patch.object(shorts, "_publish_now") as now,
            patch.object(bot.threading, "Thread") as thread,
        ):
            thread.side_effect = lambda target, args, daemon: SimpleNamespace(
                start=lambda: target(*args)
            )
            shorts.handle_update(_callback(111, "publish:tok"))

        now.assert_called_once_with(bot.publish.CARD_NEWS, "/tmp/a.mp4", "제목")

    def test_the_button_is_spent_when_used(self):
        """남겨 두면 다시 눌러 같은 영상을 또 올리게 된다."""
        shorts = self._bot()
        shorts.uploads = {"tok": (bot.publish.CARD_NEWS, "/tmp/a.mp4", "제목")}
        with (
            patch.object(bot, "_answer_callback"),
            patch.object(bot, "_send"),
            patch.object(shorts, "_publish_now"),
            patch.object(bot.threading, "Thread"),
        ):
            shorts.handle_update(_callback(111, "publish:tok"))
        self.assertEqual(shorts.uploads, {})

    def test_a_stale_button_does_not_publish(self):
        shorts = self._bot()
        with (
            patch.object(bot, "_answer_callback"),
            patch.object(bot, "_send"),
            patch.object(shorts, "_publish_now") as now,
        ):
            shorts.handle_update(_callback(111, "publish:gone"))
        now.assert_not_called()

    def test_an_unconfigured_channel_asks_nothing(self):
        """올릴 곳이 없는데 버튼을 붙이면 눌러도 아무 일이 안 일어난다."""
        shorts = self._bot()
        with (
            patch.object(bot.publish, "resolve_target", return_value=None),
            patch.object(bot, "_send") as send,
        ):
            shorts._offer_publish(bot.publish.CARD_NEWS, "/tmp/a.mp4", "제목")
        send.assert_not_called()
        self.assertEqual(shorts.uploads, {})

    def test_turning_it_on_publishes_without_asking(self):
        shorts = self._bot()
        with (
            patch.object(bot.publish, "resolve_target", return_value=self._target()),
            patch.object(bot.publish, "auto_publishes", return_value=True),
            patch.object(shorts, "_publish_now") as now,
            patch.object(bot, "_send"),
        ):
            shorts._offer_publish(bot.publish.CARD_NEWS, "/tmp/a.mp4", "제목")
        now.assert_called_once_with(bot.publish.CARD_NEWS, "/tmp/a.mp4", "제목")

    def _render_shorts(self, shorts):
        with (
            patch.object(bot.tm, "start", return_value={"videos": ["/tmp/s.mp4"]}),
            patch.object(bot, "_send_video"),
            patch.object(shorts, "_offer_publish") as offer,
        ):
            shorts._render("주제", "대본")
        return offer

    def test_a_finished_short_is_offered_for_publishing(self):
        """물어보고 올리기로 해 뒀는데 아무것도 안 물어보면 영상이 안 나간다."""
        shorts = self._bot()
        with patch.object(bot.publish, "auto_publishes", return_value=False):
            offer = self._render_shorts(shorts)

        offer.assert_called_once_with(bot.publish.SHORTS, "/tmp/s.mp4", "주제")

    def test_a_short_that_the_pipeline_already_sent_is_not_offered(self):
        """영상 파이프라인이 이미 올렸다. 여기서 또 부르면 두 번 나간다."""
        shorts = self._bot()
        with patch.object(bot.publish, "auto_publishes", return_value=True):
            offer = self._render_shorts(shorts)

        offer.assert_not_called()

    def test_an_unresolved_upload_is_not_reported_as_a_plain_failure(self):
        """
        다시 누르면 두 번 올라갈 수 있다. 실패했다고만 하면 그렇게 하게 된다.
        """
        shorts = self._bot()
        unresolved = bot.publish.PublishResult(
            ok=False, error="connection reset", unknown=True
        )
        with (
            patch.object(bot.publish, "publish", return_value=unresolved),
            patch.object(bot, "_send") as send,
        ):
            shorts._publish_now(bot.publish.CARD_NEWS, "/tmp/a.mp4", "제목")

        said = " ".join(str(call.args[1]) for call in send.call_args_list)
        self.assertIn("확인", said)

    def test_a_finished_card_video_is_offered_for_publishing(self):
        """만들어 놓고 올리지 않으면 자동화가 마지막 한 칸에서 끊긴다."""
        from app.services.sources.base import SourceItem

        shorts = self._bot()
        item = SourceItem(source="hackernews", item_id="1", title="어떤 도구")
        script = SimpleNamespace(
            cards=(bot.cardnews.Card(index_label="01", title="제목", body=("하나",)),),
            narrations=("말",),
        )
        rendered = SimpleNamespace(video_path="/tmp/cardnews.mp4", duration=30, card_count=1)
        with (
            patch.object(bot.task_artifacts, "write_script_data"),
            patch.object(bot.cardvideo, "render_card_news", return_value=rendered),
            patch.object(bot.daily, "mark_used"),
            patch.object(bot, "_send_video"),
            patch.object(shorts, "_offer_publish") as offer,
        ):
            shorts._render_cards(item, script)

        offer.assert_called_once_with(
            bot.publish.CARD_NEWS, "/tmp/cardnews.mp4", "어떤 도구"
        )


if __name__ == "__main__":
    unittest.main()


class TestPersonaPicker(unittest.TestCase):
    """
    채널마다 말하는 사람이 다르고, 그 사람이 어디로 올라갈지도 정한다. 잘못
    고르면 해린맘 영상이 골프 채널에 올라간다.
    """

    def _bot(self):
        shorts = bot.ShortsBot()
        shorts.chat_id = 111
        return shorts

    def _two_people(self):
        return {
            "haerinmom": bot.persona.PERSONAS["haerinmom"],
            "kimbujang": bot.persona.PERSONAS["kimbujang"],
        }

    def test_it_asks_who_when_there_is_more_than_one(self):
        shorts = self._bot()
        with (
            patch.dict(bot.persona.PERSONAS, self._two_people(), clear=True),
            patch.object(shorts, "_draft_script") as draft,
            patch.object(bot, "_send") as send,
        ):
            shorts.handle_update(_message(111, "/새영상 실리콘 주방집게"))

        draft.assert_not_called()
        said = str(send.call_args.kwargs["buttons"])
        self.assertIn("해린맘", said)
        self.assertIn("김부장", said)

    def test_many_people_still_fit_on_the_screen(self):
        """
        한 줄에 그대로 밀어 넣으면 텔레그램이 폭을 나눠 써서, 넷만 되어도 이름이
        잘려 무엇을 고르는지 안 보인다.
        """
        crowd = {
            f"person{index}": replace(
                bot.persona.PERSONAS["haerinmom"],
                key=f"person{index}",
                name=f"사람{index}",
            )
            for index in range(7)
        }
        shorts = self._bot()
        with (
            patch.dict(bot.persona.PERSONAS, crowd, clear=True),
            patch.object(shorts, "_draft_script") as draft,
            patch.object(bot, "_send") as send,
        ):
            shorts.handle_update(_message(111, "/새영상 실리콘 주방집게"))

        draft.assert_not_called()
        rows = send.call_args.kwargs["buttons"]
        # 몇 개까지 한 줄에 넣을지는 정해 둔 값을 따르되, 그 값 자체가 커지면
        # 같은 문제가 다시 생기므로 여기서 함께 묶어 둔다.
        self.assertLessEqual(bot.PERSONA_BUTTONS_PER_ROW, 3)
        for row in rows:
            self.assertLessEqual(len(row), bot.PERSONA_BUTTONS_PER_ROW)

        # 줄로 나누다 한 사람이 빠지면 그 채널로는 영상을 못 만든다.
        offered = [button["text"] for row in rows for button in row]
        self.assertEqual(
            sorted(offered), sorted(speaker.name for speaker in crowd.values())
        )

    def test_every_button_fits_what_telegram_accepts(self):
        """
        callback_data 가 64바이트를 넘으면 메시지 자체가 거부된다 — 버튼이 아예
        안 나오고 주제만 남아, 눌러 볼 것이 없다.
        """
        shorts = self._bot()
        with (
            patch.object(shorts, "_draft_script"),
            patch.object(bot, "_send") as send,
        ):
            shorts.handle_update(_message(111, "/새영상 실리콘 주방집게"))

        for row in send.call_args.kwargs["buttons"]:
            for button in row:
                with self.subTest(button=button["text"]):
                    self.assertLessEqual(
                        len(button["callback_data"].encode("utf-8")),
                        MAX_CALLBACK_DATA_BYTES,
                    )

    def test_one_person_is_not_worth_asking_about(self):
        """고를 것이 하나뿐인데 물어보면 누르는 일만 는다."""
        shorts = self._bot()
        only = {"haerinmom": bot.persona.PERSONAS["haerinmom"]}
        with (
            patch.dict(bot.persona.PERSONAS, only, clear=True),
            patch.object(shorts, "_draft_script") as draft,
            patch.object(bot, "_send"),
        ):
            shorts.handle_update(_message(111, "/새영상 실리콘 주방집게"))

        draft.assert_called_once_with("실리콘 주방집게", "haerinmom")

    def test_the_button_writes_as_the_person_it_names(self):
        shorts = self._bot()
        shorts.subjects = {"tok": "골프 거리측정기"}
        with (
            patch.object(bot, "_answer_callback"),
            patch.object(bot, "_send"),
            patch.object(shorts, "_draft_script") as draft,
            patch.object(bot.threading, "Thread") as thread,
        ):
            thread.side_effect = lambda target, args, daemon: SimpleNamespace(
                start=lambda: target(*args)
            )
            shorts.handle_update(_callback(111, "persona:tok:kimbujang"))

        draft.assert_called_once_with("골프 거리측정기", "kimbujang")

    def test_the_button_is_spent_when_used(self):
        """남겨 두면 지난 주제로 또 만들게 된다."""
        shorts = self._bot()
        shorts.subjects = {"tok": "골프 거리측정기"}
        with (
            patch.object(bot, "_answer_callback"),
            patch.object(bot, "_send"),
            patch.object(shorts, "_draft_script"),
            patch.object(bot.threading, "Thread"),
        ):
            shorts.handle_update(_callback(111, "persona:tok:kimbujang"))

        self.assertEqual(shorts.subjects, {})

    def test_a_stale_button_writes_nothing(self):
        shorts = self._bot()
        with (
            patch.object(bot, "_answer_callback"),
            patch.object(bot, "_send"),
            patch.object(shorts, "_draft_script") as draft,
        ):
            shorts.handle_update(_callback(111, "persona:gone:kimbujang"))

        draft.assert_not_called()

    def test_the_chosen_person_reaches_the_script(self):
        shorts = self._bot()
        with (
            patch.object(bot.llm, "generate_script", return_value="대본") as generate,
            patch.object(bot, "_send"),
        ):
            shorts._draft_script("골프 거리측정기", "kimbujang")

        self.assertEqual(generate.call_args.kwargs["product_persona"], "kimbujang")
        self.assertEqual(generate.call_args.kwargs["script_style"], "product")

    def test_the_chosen_person_survives_to_the_render(self):
        """
        대본을 여기서 만들어 넘기므로, 누구로 썼는지도 같이 가야 한다. 안 그러면
        기록에 아무도 안 남아 그 대본이 어떻게 나왔는지 되짚을 수 없다.
        """
        shorts = self._bot()
        with patch.object(bot, "_send"):
            shorts._offer_draft("골프 거리측정기", "대본", "kimbujang")

        self.assertEqual(shorts.pending["product_persona"], "kimbujang")

        with (
            patch.object(bot.threading, "Thread") as thread,
            patch.object(bot, "_send"),
        ):
            captured = {}
            thread.side_effect = lambda target, args, daemon: SimpleNamespace(
                start=lambda: captured.update(args=args)
            )
            shorts._start_render()

        self.assertEqual(captured["args"], ("골프 거리측정기", "대본", "kimbujang"))

    def test_the_chosen_person_reaches_the_draft(self):
        """대본을 만들 때만 쓰고 넘기지 않으면, 그다음 단계부터 아무도 없다."""
        shorts = self._bot()
        with (
            patch.object(bot.llm, "generate_script", return_value="대본"),
            patch.object(bot, "_send"),
        ):
            shorts._draft_script("골프 거리측정기", "kimbujang")

        self.assertEqual(shorts.pending["product_persona"], "kimbujang")

    def test_a_retry_asks_the_same_person(self):
        """
        다시 뽑기는 같은 사람에게 다시 쓰라는 뜻이다. 화자를 안 넘기면 한 번
        누를 때마다 고른 것과 다른 말투가 나오고, 기록에도 아무도 안 남는다.
        """
        shorts = self._bot()
        shorts.subjects = {"tok": "골프 거리측정기"}
        with (
            patch.object(bot, "_answer_callback"),
            patch.object(bot, "_send"),
            patch.object(bot.llm, "generate_script", return_value="대본") as generate,
            patch.object(bot.threading, "Thread") as thread,
        ):
            thread.side_effect = lambda target, args, daemon: SimpleNamespace(
                start=lambda: target(*args)
            )
            shorts.handle_update(_callback(111, "persona:tok:kimbujang"))
            shorts.handle_update(
                _callback(111, f"retry:{shorts.pending['draft_id']}")
            )

        self.assertEqual(generate.call_args.kwargs["product_persona"], "kimbujang")
        self.assertEqual(shorts.pending["product_persona"], "kimbujang")

    def test_an_edited_script_keeps_the_person(self):
        """
        보여 준 대본을 손봐서 다시 보낸 것이지 새로 쓴 것이 아니다. 여기서 화자를
        지우면 오타 하나 고쳤다고 기록에서 사람이 사라진다.
        """
        shorts = self._bot()
        with patch.object(bot, "_send"):
            shorts._offer_draft("골프 거리측정기", "대본", "kimbujang")
            shorts._handle_message({"text": "대본을 조금 고쳤다"})

        self.assertEqual(shorts.pending["script"], "대본을 조금 고쳤다")
        self.assertEqual(shorts.pending["product_persona"], "kimbujang")

    def test_the_voice_follows_the_person(self):
        """
        화면에 저장된 목소리 하나를 그대로 쓰면 해린맘 대본을 남자 목소리가 읽는다.
        사람이 없을 때까지 덮어쓰면 반대로 화면에서 맞춰 둔 값이 무시된다.
        """
        saved = "ko-KR-HyunsuMultilingualNeural-Male"
        cases = [
            ("haerinmom", bot.persona.PERSONAS["haerinmom"].voice),
            ("kimbujang", bot.persona.PERSONAS["kimbujang"].voice),
            ("없는사람", saved),
            ("", saved),
        ]
        for named, expected in cases:
            with self.subTest(persona=named):
                with patch.object(bot.config, "ui", {"voice_name": saved}):
                    params = bot._build_params("실리콘 주방집게", "대본", named)

                self.assertEqual(params.voice_name, expected)

    def test_the_params_carry_the_person(self):
        params = bot._build_params("골프 거리측정기", "대본", "kimbujang")

        self.assertEqual(params.product_persona, "kimbujang")
        self.assertEqual(params.script_style, "product")

    def test_a_script_with_nobody_is_not_a_product_script(self):
        """
        사람이 없으면 제품 대본이 아니다. 스타일만 붙여 두면 기록이 사실과 달라진다.
        """
        params = bot._build_params("주제", "직접 쓴 대본", "")
        self.assertEqual(params.product_persona, "")
        self.assertNotEqual(params.script_style, "product")
