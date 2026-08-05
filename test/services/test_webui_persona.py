"""화면에서 만든 대본에도 사람이 붙고, 그 사람이 기록까지 간다."""

import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

WEBUI_MAIN = str(Path("webui") / "Main.py")


class TestGeneratedScriptCarriesItsSpeaker(unittest.TestCase):
    """
    화면에서 만든 대본은 설정에 적힌 사람으로 쓰인다. 그 이름을 안 실어 두면
    작업 쪽이 직접 쓴 대본으로 보고 사람을 지워, 기록에는 아무도 안 남는다.
    그러면 그 대본이 어떻게 나왔는지 되짚을 수 없다.
    """

    def _generate(self, configured, script_style="product"):
        from app.config import config as config_module

        merged = dict(config_module.app)
        merged["product_persona"] = configured

        app = AppTest.from_file(WEBUI_MAIN, default_timeout=90)
        app.session_state["ui_language"] = "ko"
        app.session_state["video_subject"] = "실리콘 주방집게"
        with (
            patch.object(config_module, "app", merged),
            patch("app.services.llm.generate_script", return_value="만들어진 대본"),
            patch("app.services.llm.generate_terms", return_value=["tongs"]),
        ):
            app.run()
            # 위젯을 눌러서 고른다. 세션 상태에 직접 써 넣으면 스타일이 바뀔 때
            # 프롬프트 칸을 되돌리는 처리가 안 돌아, 화면과 다른 상태가 된다.
            app.selectbox(key="script_style_select_ko").select(script_style).run()
            next(
                button for button in app.button if button.key == "auto_generate_script"
            ).click().run()
        return app

    def _peek(self, app, key):
        try:
            return app.session_state[key]
        except Exception:
            return None

    def test_the_speaker_is_written_into_the_session(self):
        app = self._generate("haerinmom")
        self.assertEqual(self._peek(app, "product_persona"), "haerinmom")

    def test_without_a_configured_speaker_nothing_is_recorded(self):
        app = self._generate("")
        self.assertEqual(self._peek(app, "product_persona"), "")

    def test_other_styles_record_nobody(self):
        """설명형 대본에는 사람이 붙지 않는다. 붙었다고 남기면 기록이 틀린다."""
        app = self._generate("haerinmom", script_style="informative")
        self.assertEqual(self._peek(app, "product_persona"), "")


if __name__ == "__main__":
    unittest.main()
