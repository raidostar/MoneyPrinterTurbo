"""첫 화면에 올 클립 고르기."""

import unittest
from unittest.mock import patch

from app.services import opening

PEXELS = "https://www.pexels.com/video/{slug}-{asset}/"


def _source(name, slug, asset="1234567"):
    return {
        "local_file": name,
        "source_page": PEXELS.format(slug=slug, asset=asset),
    }


class TestReadingWhatWeGot(unittest.TestCase):
    """
    검색어가 아니라 받아 온 것으로 골라야 한다. "baby exiting pool" 로 찾은 것이
    사람 없는 빈 수영장이었고, 그날 영상의 첫 두 초는 초록색 물만 나왔다.
    """

    def test_the_title_comes_out_of_the_address(self):
        self.assertEqual(
            opening.describe(
                "https://www.pexels.com/video/child-on-a-bed-only-wearing-a-diaper-8425000/"
            ),
            "child on a bed only wearing a diaper",
        )

    def test_the_asset_number_is_not_part_of_the_description(self):
        """번호는 식별자다. 설명으로 넘기면 모델이 그것까지 읽고 판단한다."""
        self.assertNotIn("8425000", opening.describe(PEXELS.format(slug="a-pool", asset="8425000")))

    def test_an_address_we_cannot_read_gives_nothing(self):
        for url in (
            "",
            None,
            "https://example.com/video/child-on-a-bed-1234/",
            "https://www.pexels.com/photo/child-1234/",
            "not a url",
        ):
            with self.subTest(url=url):
                self.assertEqual(opening.describe(url), "")

    def test_a_very_long_description_is_cut(self):
        """주소에서 오는 값이다. 그대로 넣으면 긴 것 하나가 판단할 내용을 밀어낸다."""
        described = opening.describe(PEXELS.format(slug="-".join(["word"] * 200), asset="1"))
        self.assertLessEqual(len(described), opening.MAX_DESCRIPTION_LENGTH)


class TestChoosing(unittest.TestCase):
    def setUp(self):
        self.sources = [
            _source("empty.mp4", "a-luxury-golden-pool"),
            _source("baby.mp4", "child-on-a-bed-only-wearing-a-diaper"),
            _source("resort.mp4", "aerial-view-of-luxurious-poolside-resort"),
        ]

    def test_the_chosen_clip_comes_back(self):
        chosen = opening.pick("물에서 나오는데", self.sources, lambda prompt: "1")
        self.assertEqual(chosen, "baby.mp4")

    def test_the_first_line_and_every_choice_reach_the_model(self):
        """
        무엇에 맞춰 고르는지 안 알려주면 아무 클립이나 고른 것과 같다.
        """
        seen = {}
        opening.pick("물에서 나오는데", self.sources, lambda prompt: seen.update(p=prompt) or "0")

        self.assertIn("물에서 나오는데", seen["p"])
        for source in self.sources:
            self.assertIn(opening.describe(source["source_page"]), seen["p"])

    def test_an_answer_with_words_around_it_still_works(self):
        """숫자만 답하라고 해도 문장이나 JSON 으로 감싸 온다."""
        for answer in ("1", " 1 ", "Index: 1", "```json\n1\n```", '"1"'):
            with self.subTest(answer=answer):
                self.assertEqual(
                    opening.pick("첫 문장", self.sources, lambda prompt: answer),
                    "baby.mp4",
                )

    def test_an_answer_we_cannot_use_changes_nothing(self):
        """
        못 고르면 받은 순서를 그대로 쓰면 된다. 없는 번호를 첫 클립으로 접으면
        아무 근거 없이 맨 앞의 것이 정답이 된다.
        """
        # 오류 문구에 숫자가 섞여 있으면(모델 제공자는 상태 코드를 붙여 온다) 그
        # 숫자가 고른 번호로 읽힌다. 실패가 멀쩡한 선택으로 바뀌는 자리다.
        for answer in (
            "",
            "없음",
            "9",
            "-1",
            "Error: connection refused",
            "Error: 1 request failed",
            "Error: 429 too many requests",
            None,
        ):
            with self.subTest(answer=answer):
                self.assertEqual(
                    opening.pick("첫 문장", self.sources, lambda prompt: answer), ""
                )

    def test_a_provider_failure_is_not_fatal(self):
        """첫 화면 하나 때문에 이미 만들어 둔 영상을 버릴 이유가 없다."""

        def explode(prompt):
            raise RuntimeError("network gone")

        with patch.object(opening.logger, "warning"):
            self.assertEqual(opening.pick("첫 문장", self.sources, explode), "")

    def test_nothing_to_choose_between_asks_nobody(self):
        """부를 이유가 없는 호출은 돈만 쓴다."""
        asked = []
        for sources in ([], [self.sources[0]], [{"local_file": "x.mp4"}]):
            with self.subTest(count=len(sources)):
                self.assertEqual(
                    opening.pick("첫 문장", sources, lambda p: asked.append(p) or "0"), ""
                )
        self.assertEqual(asked, [])

    def test_no_first_line_asks_nobody(self):
        asked = []
        self.assertEqual(
            opening.pick("", self.sources, lambda p: asked.append(p) or "0"), ""
        )
        self.assertEqual(asked, [])

    def test_the_same_clip_is_not_offered_twice(self):
        """같은 것이 두 번 뜨면 고를 자리 하나가 그냥 사라진다."""
        doubled = self.sources + [_source("baby.mp4", "child-on-a-bed-only-wearing-a-diaper")]
        seen = {}
        opening.pick("첫 문장", doubled, lambda prompt: seen.update(p=prompt) or "0")

        self.assertEqual(seen["p"].count("child on a bed only wearing a diaper"), 1)

    def test_the_list_of_choices_is_bounded(self):
        """소재는 얼마든지 늘어날 수 있다. 첫 칸은 하나뿐이라 판단만 길어진다."""
        many = [_source(f"{i}.mp4", f"clip-number-{i}") for i in range(50)]
        seen = {}
        opening.pick("첫 문장", many, lambda prompt: seen.update(p=prompt) or "0")

        offered = [line for line in seen["p"].splitlines() if line.strip().startswith(("0.", "1.", "2."))]
        self.assertLessEqual(len(seen["p"].splitlines()), 60)
        self.assertTrue(offered)


class TestWhatTheModelIsToldToWeigh(unittest.TestCase):
    def test_a_subject_beats_matching_scenery(self):
        """
        빈 풍경은 검색어와 아무리 맞아도 첫 칸에 오면 안 된다. 사람이 없으면
        시청자는 읽을 것도 볼 것도 없이 그냥 넘긴다.
        """
        seen = {}
        opening.pick(
            "물에서 나오는데",
            [_source("a.mp4", "a-luxury-golden-pool"), _source("b.mp4", "a-child-crying")],
            lambda prompt: seen.update(p=prompt) or "0",
        )

        said = seen["p"].lower()
        self.assertIn("person", said)
        self.assertIn("empty scenery", said)


class TestPuttingItFirst(unittest.TestCase):
    """
    고른 것을 맨 앞으로 옮기지 않으면 고른 의미가 없다. 첫 한두 초에 나오는 것이
    바뀌어야 시청자가 넘기지 않는다.
    """

    def setUp(self):
        from app.services import material

        self.material = material
        self.paths = ["/m/empty.mp4", "/m/baby.mp4", "/m/resort.mp4"]
        self.sources = [
            _source("empty.mp4", "a-luxury-golden-pool"),
            _source("baby.mp4", "child-on-a-bed-only-wearing-a-diaper"),
            _source("resort.mp4", "aerial-view-of-luxurious-poolside-resort"),
        ]

    def _first(self, chosen, paths=None, line="물에서 나오는데"):
        with patch.object(self.material.opening, "pick", return_value=chosen):
            return self.material._opening_first(
                list(self.paths if paths is None else paths), self.sources, line
            )

    def test_the_chosen_clip_moves_to_the_front(self):
        self.assertEqual(self._first("baby.mp4")[0], "/m/baby.mp4")

    def test_nothing_else_is_lost(self):
        """앞으로 옮기다 하나를 떨어뜨리면 그만큼 영상이 짧아진다."""
        ordered = self._first("baby.mp4")
        self.assertEqual(sorted(ordered), sorted(self.paths))

    def test_no_choice_keeps_the_order(self):
        self.assertEqual(self._first(""), self.paths)

    def test_a_name_we_do_not_have_keeps_the_order(self):
        """
        기록과 파일이 어긋난 것이다. 조용히 넘어가면 다음에 같은 일이 나도 모른다.
        """
        with patch.object(self.material.logger, "warning") as warned:
            self.assertEqual(self._first("gone.mp4"), self.paths)
        warned.assert_called_once()

    def test_one_clip_is_not_worth_asking_about(self):
        with patch.object(self.material.opening, "pick") as pick:
            self.material._opening_first(["/m/only.mp4"], self.sources, "첫 문장")
        pick.assert_not_called()

    def test_no_first_line_is_not_worth_asking_about(self):
        with patch.object(self.material.opening, "pick") as pick:
            self.material._opening_first(list(self.paths), self.sources, "")
        pick.assert_not_called()


class TestWhichLineToMatch(unittest.TestCase):
    def test_the_first_sentence_is_what_the_opening_matches(self):
        """
        영상의 첫 한두 초에 나오는 것이 이 문장이다. 주제로 고르면 대본이 어디서
        시작하든 같은 그림이 온다.
        """
        from app.services import task

        self.assertEqual(
            task._opening_line("물에서 나오는데요. 푸켓 첫날이었어요. 여벌도 없었구요."),
            "물에서 나오는데요.",
        )

    def test_a_script_without_a_full_stop_still_gives_something(self):
        from app.services import task

        self.assertEqual(task._opening_line("물에서 나오는데"), "물에서 나오는데")

    def test_an_empty_script_gives_nothing(self):
        from app.services import task

        for script in ("", "   ", None):
            with self.subTest(script=script):
                self.assertEqual(task._opening_line(script), "")

    def test_a_very_long_sentence_is_cut(self):
        """프롬프트에 들어가는 값이다. 상한이 없으면 대본 하나가 통째로 실린다."""
        from app.services import task

        self.assertLessEqual(len(task._opening_line("가" * 5000)), 200)


if __name__ == "__main__":
    unittest.main()
