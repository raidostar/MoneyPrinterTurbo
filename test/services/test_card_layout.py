import unittest
from pathlib import Path
from unittest.mock import patch

from moviepy import ColorClip

from app.models.schema import VideoParams
from app.services import llm, video


FONTS_DIR = Path(__file__).parent.parent.parent / "resource" / "fonts"


def _params(**overrides):
    params = VideoParams(video_subject="layout test")
    params.layout = "card"
    params.layout_background_color = "#FFFFFF"
    params.layout_video_height_ratio = 0.55
    for key, value in overrides.items():
        setattr(params, key, value)
    return params


class TestCardLayout(unittest.TestCase):
    """쇼츠 템플릿용 카드 레이아웃."""

    @staticmethod
    def _source(width=1080, height=1920):
        return ColorClip(size=(width, height), color=(30, 90, 200)).with_duration(2)

    def test_canvas_keeps_the_output_resolution(self):
        """레이아웃은 배치만 바꾼다. 출력 해상도가 달라지면 인코딩 설정이 어긋난다."""
        source = self._source()
        result = video.apply_card_layout(source, _params())
        try:
            self.assertEqual(result.size, source.size)
            self.assertEqual(result.duration, source.duration)
        finally:
            result.close()
            source.close()

    def test_background_is_visible_above_and_below_the_video(self):
        """
        헤드라인과 자막을 놓으려면 위아래에 배경이 실제로 보여야 한다.
        영상이 화면을 다 덮어 버리면 템플릿이 성립하지 않는다.
        """
        source = self._source()
        result = video.apply_card_layout(source, _params(layout_video_height_ratio=0.5))
        try:
            frame = result.get_frame(0)
            height = frame.shape[0]
            self.assertEqual(list(frame[5, 540]), [255, 255, 255])
            self.assertEqual(list(frame[height - 5, 540]), [255, 255, 255])
            self.assertEqual(list(frame[height // 2, 540]), [30, 90, 200])
        finally:
            result.close()
            source.close()

    def test_video_fills_the_full_width(self):
        """
        가로에 여백이 생기면 카드가 아니라 그냥 작아진 영상으로 보인다.
        세로 소재도 가로를 채우고 위아래를 잘라야 한다.
        """
        source = self._source(1080, 1920)
        result = video.apply_card_layout(source, _params(layout_video_height_ratio=0.5))
        try:
            frame = result.get_frame(0)
            middle = frame.shape[0] // 2
            self.assertEqual(list(frame[middle, 0]), [30, 90, 200])
            self.assertEqual(list(frame[middle, frame.shape[1] - 1]), [30, 90, 200])
        finally:
            result.close()
            source.close()

    def test_background_color_is_configurable(self):
        source = self._source()
        result = video.apply_card_layout(
            source, _params(layout_background_color="#111111")
        )
        try:
            self.assertEqual(list(result.get_frame(0)[5, 540]), [17, 17, 17])
        finally:
            result.close()
            source.close()

    def test_generate_video_applies_the_layout(self):
        """
        헬퍼가 옳아도 generate_video 가 호출하지 않으면 사용자에게는 효과가 없다.
        호출부가 사라지는 회귀를 잡는다.
        """
        import ast
        from pathlib import Path

        source = (
            Path(__file__).parent.parent.parent / "app" / "services" / "video.py"
        ).read_text(encoding="utf-8")
        target = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "generate_video"
        )
        called = {
            node.func.id
            for node in ast.walk(target)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("apply_card_layout", called)


if __name__ == "__main__":
    unittest.main()


class TestHeadline(unittest.TestCase):
    """상단 헤드라인 생성과 렌더링."""

    def test_generated_headline_has_two_lines(self):
        """
        헤드라인은 두 줄로 얹힌다. `_generate_response` 가 반환값에서 개행을 모두
        지우므로 개행을 구분자로 쓰면 한 줄로 붙는다. 다른 구분자를 쓰는지 확인한다.
        """
        with patch.object(llm, "_generate_response", return_value="첫 줄|둘째 줄"):
            headline = llm.generate_headline(
                video_subject="주제", video_script="대본", language="ko-KR"
            )

        self.assertEqual(headline.split("\n"), ["첫 줄", "둘째 줄"])

    def test_headline_falls_back_when_the_model_fails(self):
        """헤드라인은 보조 요소다. 생성 실패가 영상 생성을 막아서는 안 된다."""
        with patch.object(llm, "_generate_response", side_effect=RuntimeError("down")):
            headline = llm.generate_headline(
                video_subject="헬스 초보가 닭가슴살 때문에 고생한 이야기",
                video_script="",
                language="ko-KR",
            )

        self.assertTrue(headline)
        self.assertLessEqual(len(headline.split("\n")), llm.HEADLINE_LINES)

    def test_headline_is_capped_at_two_lines(self):
        """모델이 더 많이 뱉어도 레이아웃이 감당할 수 있는 만큼만 쓴다."""
        with patch.object(llm, "_generate_response", return_value="a|b|c|d"):
            headline = llm.generate_headline(video_subject="x", video_script="y")

        self.assertEqual(headline.split("\n"), ["a", "b"])

    def test_empty_input_produces_no_headline(self):
        self.assertEqual(llm.generate_headline(), "")

    def test_headline_is_drawn_above_the_video(self):
        """
        문구가 상단 여백에 실제로 그려지는지 확인한다. 여백이 배경색 그대로면
        헤드라인이 렌더링되지 않은 것이다.
        """
        source = ColorClip(size=(1080, 1920), color=(30, 90, 200)).with_duration(2)
        params = _params(layout_video_height_ratio=0.5)
        params.headline = "첫 줄\n둘째 줄"
        params.headline_color = "#111111"
        try:
            plain = video.apply_card_layout(source, _params(layout_video_height_ratio=0.5))
            with_headline = video.apply_card_layout(
                source, params, str(FONTS_DIR / "Pretendard-Bold.ttf")
            )
            top_plain = plain.get_frame(0)[:400]
            top_headline = with_headline.get_frame(0)[:400]

            self.assertTrue(
                (top_plain != top_headline).any(),
                "상단 여백이 그대로다 — 헤드라인이 그려지지 않았다",
            )
        finally:
            plain.close()
            with_headline.close()
            source.close()


class TestSubtitlePlacementAndCorners(unittest.TestCase):
    """자막 여백 배치와 둥근 모서리."""

    def test_below_video_only_applies_to_the_card_layout(self):
        """전체화면에는 여백이 없다. 옵션만 켜고 레이아웃이 아니면 무시해야 한다."""
        params = _params(layout="fullscreen")
        params.subtitle_below_video = True
        self.assertFalse(video._subtitle_below_video_enabled(params))

        params.layout = "card"
        self.assertTrue(video._subtitle_below_video_enabled(params))

    def test_subtitle_color_changes_when_it_moves_off_the_video(self):
        """
        기본 자막색은 흰색이다. 흰 배경 여백으로 옮기면 그대로 사라지므로 색도
        함께 바뀌어야 한다.
        """
        params = _params()
        params.text_fore_color = "#FFFFFF"
        params.subtitle_below_color = "#111111"

        self.assertEqual(video._subtitle_color(params), "#FFFFFF")

        params.subtitle_below_video = True
        self.assertEqual(video._subtitle_color(params), "#111111")

    def test_rounded_corners_make_the_corner_show_the_background(self):
        """모서리를 깎으면 그 자리에 배경이 비쳐야 한다."""
        source = ColorClip(size=(1080, 1920), color=(30, 90, 200)).with_duration(2)
        square = _params(layout_video_height_ratio=0.5)
        rounded = _params(layout_video_height_ratio=0.5)
        rounded.layout_corner_radius = 80
        try:
            a = video.apply_card_layout(source, square)
            b = video.apply_card_layout(source, rounded)
            top = (1920 - int(1920 * 0.5)) // 2

            # 영상 좌상단 모서리 안쪽 픽셀
            self.assertEqual(list(a.get_frame(0)[top + 3, 3]), [30, 90, 200])
            self.assertEqual(list(b.get_frame(0)[top + 3, 3]), [255, 255, 255])
        finally:
            a.close()
            b.close()
            source.close()
