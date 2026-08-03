"""저장소에서 확인할 수 있는 것들."""

import unittest
from unittest.mock import patch

from app.services.sources import repo

REPO_BODY = {
    "full_name": "someone/thing",
    "stargazers_count": 292,
    "language": "Rust",
    "license": {"spdx_id": "Apache-2.0"},
    "description": "a tiny thing",
    "open_issues_count": 3,
    "archived": False,
    "created_at": "2024-01-01T00:00:00Z",
    "pushed_at": "2026-08-01T00:00:00Z",
}
ROOT_TREE = {
    "tree": [
        {"path": "tests", "type": "tree"},
        {"path": "docs", "type": "tree"},
        {"path": ".github", "type": "tree"},
        {"path": "Cargo.toml", "type": "blob"},
        {"path": "README.md", "type": "blob"},
    ]
}
WORKFLOWS = [{"name": "ci.yml"}]


def _signals(repo_body=None, tree=None, workflows=None, url="https://github.com/someone/thing"):
    # 저장소 주소가 다른 주소들의 앞부분이라, 구체적인 것부터 본다.
    bodies = [
        ("/contents/.github/workflows", WORKFLOWS if workflows is None else workflows),
        ("/git/trees/", ROOT_TREE if tree is None else tree),
        ("/repos/someone/thing", REPO_BODY if repo_body is None else repo_body),
    ]

    def fake(target, **_kwargs):
        for fragment, body in bodies:
            if fragment in target:
                return body
        return None

    with patch.object(repo.enrich, "get_json", side_effect=fake) as call:
        return repo.fetch_signals(url), call


class TestReading(unittest.TestCase):
    def test_what_the_repository_says_about_itself(self):
        signals, _ = _signals()

        self.assertTrue(signals.seen)
        self.assertEqual(signals.full_name, "someone/thing")
        self.assertEqual(signals.stars, 292)
        self.assertEqual(signals.language, "Rust")
        self.assertEqual(signals.license_name, "Apache-2.0")

    def test_the_top_level_listing_tells_us_what_is_there(self):
        signals, _ = _signals()

        self.assertTrue(signals.has_tests)
        self.assertTrue(signals.has_docs)
        self.assertTrue(signals.is_packaged)
        self.assertTrue(signals.has_ci)

    def test_a_repository_we_read_can_be_scored(self):
        """
        읽기에 성공했는데 "목록 못 봤음" 으로 남으면, 점수가 붙는 소재가 하나도
        없어 마지막 카드가 매번 빠진다.
        """
        signals, _ = _signals()
        self.assertTrue(signals.tree_seen)
        self.assertIsNotNone(repo.maturity(signals))

    def test_the_whole_file_tree_is_not_downloaded(self):
        """
        `recursive=1` 은 파일이 많은 저장소에서 수 MB 다. 상한에 걸려 잘리면 파싱이
        실패해 "아무것도 없다" 가 되고, 그 값이 그대로 점수가 된다.
        """
        _, call = _signals()

        for request in call.call_args_list:
            self.assertNotIn("recursive", request.args[0])

    def test_tests_at_the_top_level_count_too(self):
        """맨 위에 테스트 파일을 늘어놓는 저장소도 있다."""
        for name in ("test_thing.py", "base64_ut.cpp", "thing.spec.ts"):
            with self.subTest(name=name):
                tree = {"tree": [{"path": name, "type": "blob"}]}
                signals, _ = _signals(tree=tree)
                self.assertTrue(signals.has_tests)

    def test_an_empty_workflow_folder_is_not_ci(self):
        """폴더만 있고 안이 비어 있으면 도는 것이 없다."""
        signals, _ = _signals(workflows=[])
        self.assertFalse(signals.has_ci)

    def test_a_license_github_could_not_identify_is_not_a_license(self):
        """
        NOASSERTION 은 파일은 있는데 무엇인지 알아보지 못한 경우다. 정해진
        라이선스가 붙은 것과 같이 볼 수 없다.
        """
        for spdx in ("NOASSERTION", "NONE"):
            with self.subTest(spdx=spdx):
                body = dict(REPO_BODY, license={"spdx_id": spdx})
                signals, _ = _signals(repo_body=body)
                self.assertEqual(signals.license_name, "")

    def test_a_link_that_is_not_a_repository_is_not_looked_up(self):
        with patch.object(repo.enrich, "get_json") as call:
            signals = repo.fetch_signals("https://sf.isopolis.city/")
        self.assertFalse(signals.seen)
        call.assert_not_called()

    def test_a_repository_we_could_not_read_says_so(self):
        signals, _ = _signals(repo_body={})
        self.assertFalse(signals.seen)

    def test_broken_counts_do_not_break_the_reading(self):
        body = dict(REPO_BODY, stargazers_count="많음", open_issues_count=None)
        signals, _ = _signals(repo_body=body)
        self.assertEqual(signals.stars, 0)
        self.assertEqual(signals.open_issues, 0)

    def test_a_timestamp_we_cannot_read_does_not_become_an_age(self):
        body = dict(REPO_BODY, created_at="언젠가", pushed_at=None)
        signals, _ = _signals(repo_body=body)
        self.assertEqual(signals.age_days, 0)


class TestMaturity(unittest.TestCase):
    """
    셀 수 있는 것은 센다. 모델에게 "완성도를 매겨 봐" 라고 물으면 무엇을 보든
    4점이 나오고, 그러면 매 영상이 똑같이 끝난다.
    """

    def _score(self, **overrides):
        values = {
            "seen": True,
            "tree_seen": True,
            "has_tests": True,
            "has_ci": True,
            "license_name": "MIT",
            "age_days": 800,
            "idle_days": 2,
        }
        values.update(overrides)
        return repo.maturity(repo.RepoSignals(**values))

    def test_projects_in_different_shape_get_different_scores(self):
        """전부 같은 점수가 나오면 점수판이 장식이 된다."""
        seasoned = self._score()
        weekend = self._score(
            has_tests=False, has_ci=False, license_name="", age_days=10, idle_days=1
        )
        abandoned = self._score(idle_days=900, age_days=1500)

        self.assertEqual(seasoned[0], repo.MAX_SCORE)
        self.assertLess(weekend[0], seasoned[0])
        self.assertLess(abandoned[0], seasoned[0])
        self.assertEqual(len({seasoned[0], weekend[0], abandoned[0]}), 3)

    def test_the_score_says_what_it_was_based_on(self):
        """근거 없이 숫자만 나오면 시청자가 확인할 방법이 없다."""
        score, reason = self._score(has_tests=False)
        self.assertIn("테스트 없음", reason)

    def test_a_brand_new_project_cannot_be_full_marks(self):
        """신호가 다 있어도 3주차 프로젝트는 아직 검증된 물건이 아니다."""
        score, reason = self._score(age_days=10)
        self.assertLess(score, repo.MAX_SCORE)
        self.assertIn("주차", reason)

    def test_a_repository_that_stopped_scores_lower(self):
        moving = self._score(idle_days=3)[0]
        stopped = self._score(idle_days=400)[0]
        self.assertLess(stopped, moving)

    def test_an_archived_repository_is_not_counted_as_alive(self):
        self.assertLess(self._score(archived=True, idle_days=1)[0], self._score()[0])

    def test_a_listing_we_could_not_read_is_not_scored(self):
        """
        읽기에 실패한 저장소와 정말 테스트가 없는 저장소가 같은 점수를 받으면,
        통신 사정이 점수가 된다.
        """
        self.assertIsNone(self._score(tree_seen=False))
        self.assertIsNone(self._score(seen=False))

    def test_the_score_stays_in_range(self):
        """
        카드는 이 범위만큼 막대를 그린다. 신호를 하나 더 넣어 6점이 나오면
        막대가 칸을 넘어가고, 점수판 전체가 어긋난다.
        """
        from itertools import product

        for tests, ci, license_name, idle, age in product(
            (True, False), (True, False), ("MIT", ""), (1, 400), (5, 5000)
        ):
            with self.subTest(tests=tests, ci=ci, idle=idle, age=age):
                score, _ = self._score(
                    has_tests=tests,
                    has_ci=ci,
                    license_name=license_name,
                    idle_days=idle,
                    age_days=age,
                )
                self.assertGreaterEqual(score, repo.MIN_SCORE)
                self.assertLessEqual(score, repo.MAX_SCORE)


class TestSummaryLine(unittest.TestCase):
    def test_the_line_says_how_alive_it_is(self):
        line = repo.summary_line(
            repo.RepoSignals(seen=True, stars=292, language="Rust", idle_days=0)
        )
        self.assertIn("★292", line)
        self.assertIn("Rust", line)

    def test_nothing_is_said_about_a_repository_we_did_not_see(self):
        self.assertEqual(repo.summary_line(repo.RepoSignals()), "")


if __name__ == "__main__":
    unittest.main()
