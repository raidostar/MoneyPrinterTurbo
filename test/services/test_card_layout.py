import unittest

from moviepy import ColorClip

from app.models.schema import VideoParams
from app.services import video


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
