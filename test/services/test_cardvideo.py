"""카드 대본을 영상으로."""

import os
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.models.schema import VideoParams
from app.services import cardvideo
from app.services.cardnews import Card
from app.services.cardscript import CardScript


def _script(count=3):
    return CardScript(
        cards=tuple(Card(title=f"제목 {i}") for i in range(count)),
        narrations=tuple(f"나레이션 {i}" for i in range(count)),
    )


@contextmanager
def _silent_moviepy(clip_duration=7.0):
    """
    moviepy 는 함수 안에서 import 된다. 모듈 속성이 아니라 moviepy 쪽을 갈아야
    실제 인코딩 없이 조립 흐름만 확인할 수 있다.
    """
    with (
        patch("moviepy.AudioFileClip", MagicMock()),
        patch("moviepy.CompositeAudioClip", MagicMock()),
        patch("moviepy.concatenate_audioclips", MagicMock()),
        patch.object(cardvideo.cardnews, "build_card_news_clip") as build,
    ):
        build.return_value.duration = clip_duration
        yield build


def _params():
    params = VideoParams(video_subject="t")
    params.bgm_type = ""
    params.n_threads = 1
    return params


class TestNarrationDrivesTiming(unittest.TestCase):
    """카드가 화면에 머무는 시간은 그 카드 나레이션의 실제 길이다."""

    def test_each_card_is_held_for_its_own_narration(self):
        """
        통짜 나레이션을 글자 수로 나누면 뒤로 갈수록 화면과 소리가 밀린다.
        카드별로 재야 어긋나지 않는다.
        """
        lengths = iter([1.0, 4.0, 2.0])
        with patch.object(
            cardvideo, "_narrate", side_effect=lambda *a, **k: next(lengths)
        ), _silent_moviepy() as build:
            with tempfile.TemporaryDirectory() as work:
                cardvideo.render_card_news("t", _script(3), _params(), work)

        self.assertEqual(build.call_args.args[1], [1.0, 4.0, 2.0])

    def test_a_card_whose_narration_fails_still_appears(self):
        """
        한 장이 실패했다고 영상 전체를 버리지 않는다. 소리 없이 지나가고
        나머지는 그대로 나온다.
        """
        with patch.object(
            cardvideo, "_narrate", return_value=0.0
        ), _silent_moviepy(7.5) as build:
            with tempfile.TemporaryDirectory() as work:
                cardvideo.render_card_news("t", _script(3), _params(), work)

        self.assertEqual(
            build.call_args.args[1], [cardvideo.FALLBACK_CARD_SECONDS] * 3
        )

    def test_an_empty_narration_is_not_sent_to_the_voice_service(self):
        """빈 글을 합성하면 요금과 시간만 쓰고 아무 소리도 안 나온다."""
        script = CardScript(
            cards=(Card(title="하나"), Card(title="둘")), narrations=("", "   ")
        )
        with patch.object(cardvideo, "_narrate") as narrate, _silent_moviepy(5.0):
            with tempfile.TemporaryDirectory() as work:
                cardvideo.render_card_news("t", script, _params(), work)

        narrate.assert_not_called()


class TestNarrationRetry(unittest.TestCase):
    def test_a_failed_synthesis_is_retried(self):
        """일시적인 실패 하나로 그 카드가 조용해지지 않게 한다."""
        params = _params()
        with tempfile.TemporaryDirectory() as work:
            target = os.path.join(work, "card.mp3")

            def tts(**kwargs):
                # 두 번째 시도에서만 파일을 남긴다.
                if tts.calls:
                    open(kwargs["voice_file"], "wb").write(b"x")
                tts.calls += 1
                return object() if tts.calls > 1 else None

            tts.calls = 0
            with (
                patch.object(cardvideo.voice, "tts", side_effect=tts),
                patch.object(cardvideo.voice, "get_audio_duration", return_value=3.0),
            ):
                self.assertEqual(cardvideo._narrate("말", target, params), 3.0)

    def test_giving_up_reports_no_duration(self):
        with tempfile.TemporaryDirectory() as work:
            with patch.object(cardvideo.voice, "tts", return_value=None):
                seconds = cardvideo._narrate(
                    "말", os.path.join(work, "card.mp3"), _params()
                )
        self.assertEqual(seconds, 0.0)


class TestOutput(unittest.TestCase):
    def test_the_result_reports_what_was_made(self):
        """호출자는 파일 경로와 길이를 알아야 다음 단계로 넘길 수 있다."""
        with patch.object(
            cardvideo, "_narrate", return_value=2.0
        ), _silent_moviepy(6.0):
            with tempfile.TemporaryDirectory() as work:
                result = cardvideo.render_card_news("t", _script(3), _params(), work)

        self.assertIsNotNone(result)
        self.assertEqual(result.card_count, 3)
        self.assertAlmostEqual(result.duration, 6.0)
        self.assertTrue(result.video_path.endswith("cardnews.mp4"))

    def test_a_script_with_no_cards_makes_nothing(self):
        script = CardScript(cards=(), narrations=())
        with tempfile.TemporaryDirectory() as work:
            self.assertIsNone(
                cardvideo.render_card_news("t", script, _params(), work)
            )


if __name__ == "__main__":
    unittest.main()
