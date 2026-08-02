"""같은 작업이 두 번 제출되는 것을 막는다."""

import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import const
from app.models.schema import VideoParams
from app.services import state as sm
from app.services import webui_task


class TestDuplicateSubmit(unittest.TestCase):
    def setUp(self):
        self.params = VideoParams(video_subject="커피")

    def test_a_second_submit_of_a_running_task_is_ignored(self):
        """
        페이지가 다시 실행되면서 같은 작업 ID 로 제출이 반복될 수 있다. 막지 않으면
        같은 영상을 만드는 렌더링이 여러 개 떠서 같은 출력 파일에 동시에 쓴다.
        """
        with patch.object(webui_task._task_manager, "add_task") as add_task:
            with patch.object(
                sm.state,
                "get_task",
                return_value={"state": const.TASK_STATE_PROCESSING},
            ):
                webui_task.submit_generation("busy-task", self.params)

        add_task.assert_not_called()

    def test_a_finished_task_can_be_submitted_again(self):
        """다시 만들기는 막으면 안 된다. 돌고 있는 것만 거절한다."""
        with patch.object(webui_task._task_manager, "add_task") as add_task:
            with patch.object(
                sm.state,
                "get_task",
                return_value={"state": const.TASK_STATE_COMPLETE},
            ):
                webui_task.submit_generation("done-task", self.params)

        add_task.assert_called_once()

    def test_an_unknown_task_is_submitted(self):
        """처음 만드는 작업은 상태가 없다."""
        with patch.object(webui_task._task_manager, "add_task") as add_task:
            with patch.object(sm.state, "get_task", return_value=None):
                webui_task.submit_generation("new-task", self.params)

        add_task.assert_called_once()


class TestPendingIdIsCleared(unittest.TestCase):
    def test_the_reserved_id_is_dropped_after_submitting(self):
        """
        예약해 둔 ID 가 남아 있으면, 버튼 상태가 살아 있는 다음 실행이 같은 ID 로
        다시 제출한다. 그러면 같은 작업이 겹쳐 뜬다.
        """
        source = Path("webui/Main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        cleared = any(
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "pop"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "pending_generation_task_id"
            for node in ast.walk(tree)
        )
        self.assertTrue(cleared, "제출 후 예약 ID 를 비우지 않는다")


if __name__ == "__main__":
    unittest.main()
