"""채널을 운영하는 사람."""

import unittest
from unittest.mock import patch

from app.services import llm, persona


class TestRegistry(unittest.TestCase):
    def test_the_configured_persona_exists(self):
        """
        설정 예시에 적어 둔 이름이 없으면, 그대로 복사한 사람은 사람 없이 대본을
        쓰게 된다.
        """
        self.assertIn("haerinmom", persona.PERSONAS)

    def test_an_unknown_name_is_not_quietly_replaced(self):
        """
        오타를 기본값으로 바꾸면 엉뚱한 사람 말투로 몇 편이 나가고, 기록에는
        오타가 남는다. 사람 없이 쓰는 편이 낫다.
        """
        self.assertIsNone(persona.resolve("없는사람"))
        self.assertIsNone(persona.resolve(""))
        self.assertIsNone(persona.resolve(None))

    def test_a_known_name_comes_back(self):
        speaker = persona.resolve("haerinmom")
        self.assertEqual(speaker.key, "haerinmom")
        self.assertEqual(speaker.name, "해린맘")

    def test_an_unknown_name_is_not_written_into_the_log(self):
        """이 칸에 다른 것을 잘못 넣어 보낼 수 있다."""
        secret = "sk-abcdef0123456789"
        with patch.object(persona.logger, "warning") as warned:
            persona.resolve(secret)

        said = " ".join(str(call.args[0]) for call in warned.call_args_list)
        self.assertNotIn(secret, said)


class TestWhatThePersonaSays(unittest.TestCase):
    """
    말투만으로는 모자란다. "한 번 있었던 일" 을 쓰라고 시켜 두었으므로, 그 일이
    일어나는 자리도 사람마다 달라야 한다.
    """

    def setUp(self):
        self.speaker = persona.PERSONAS["haerinmom"]
        self.prompt = self.speaker.as_prompt()

    def test_the_speaker_is_named(self):
        self.assertIn("해린맘", self.prompt)

    def test_the_endings_are_fixed_here(self):
        """
        채널 하나는 한 사람이 말하는 곳이다. 여기서 어미를 안 정하면 영상마다
        다른 사람이 말하게 된다.
        """
        self.assertIn("~했어요", self.prompt)
        self.assertIn("~하더라구요", self.prompt)

    def test_the_places_the_scenes_come_from_are_named(self):
        """장면이 여기서 나온다. 없으면 아무 데서나 일어난 일이 된다."""
        for place in ("어린이집", "kitchen"):
            with self.subTest(place=place):
                self.assertIn(place, self.prompt)

    def test_the_words_this_person_never_uses_are_listed(self):
        """하나만 섞여도 지어낸 티가 난다."""
        for word in ("꿀템", "대박", "인생템"):
            with self.subTest(word=word):
                self.assertIn(word, self.prompt)

    def test_what_is_not_their_world_is_listed(self):
        """살림 채널에서 스코어가 나오는 순간 지어낸 티가 난다."""
        self.assertIn("golf", self.prompt)

    def test_every_persona_says_all_of_it(self):
        """한 칸이라도 비면 그 부분은 모델이 알아서 지어낸다."""
        for key, speaker in persona.PERSONAS.items():
            with self.subTest(persona=key):
                for part in (speaker.name, speaker.life, speaker.register, speaker.money):
                    self.assertTrue(part.strip())
                self.assertIn(part, speaker.as_prompt())


class TestInThePrompt(unittest.TestCase):
    def _prompt(self, **kwargs):
        values = {
            "video_subject": "실리콘 주방집게",
            "script_style": "product",
            "product_voice": "confession",
        }
        values.update(kwargs)
        return llm.build_script_prompt(**values)

    def test_the_speaker_reaches_the_prompt(self):
        prompt = self._prompt(product_persona="haerinmom")
        self.assertIn(persona.PERSONAS["haerinmom"].as_prompt(), prompt)

    def test_writing_without_one_still_works(self):
        """
        일회성 영상은 사람 없이도 만들 수 있어야 한다. 설정에 아무도 안 적어 둔
        상태가 그것이다 — 인자를 비우는 것은 "설정에 적힌 사람을 쓰라" 는 뜻이다.
        """
        with patch.object(persona.config, "app", {"product_persona": ""}):
            prompt = self._prompt(product_persona="")

        self.assertNotIn("## Who is speaking", prompt)
        self.assertIn(llm.PRODUCT_VOICES["confession"], prompt)
        # 여는 방식은 어미를 정하지 않는다. 사람도 없으면 정한 곳이 아무 데도
        # 없어져, 영상마다 존댓말과 반말이 섞인다.
        self.assertIn(llm.NO_PERSONA_REGISTER, prompt)

    def test_the_speaker_comes_before_the_opening(self):
        """
        사람이 말투를 정하고 여는 방식이 그 위에 얹힌다. 순서가 뒤바뀌면 나중 것이
        이겨서, 여는 방식이 말투까지 정하는 것처럼 읽힌다.
        """
        prompt = self._prompt(product_persona="haerinmom")

        self.assertLess(
            prompt.index("## Who is speaking"),
            prompt.index(llm.PRODUCT_VOICES["confession"]),
        )

    def test_other_styles_do_not_get_a_speaker(self):
        for style in ("informative", "story"):
            with self.subTest(style=style):
                prompt = self._prompt(script_style=style, product_persona="haerinmom")
                self.assertNotIn("## Who is speaking", prompt)

    def test_a_speaker_replaces_the_default_register(self):
        """
        둘 다 들어가면 서로 다른 어미를 시킨다. 사람이 있으면 그 사람이 정한다.
        """
        with patch.object(persona.config, "app", {"product_persona": "haerinmom"}):
            prompt = self._prompt(product_persona="haerinmom")

        self.assertNotIn(llm.NO_PERSONA_REGISTER, prompt)
        self.assertIn("~했어요", prompt)

    def test_every_product_prompt_fixes_the_endings_somewhere(self):
        """
        어미를 정한 곳이 없으면 한 채널 안에서 말투가 흔들린다. 사람이 있든
        없든 어느 한쪽에서는 정해져야 한다.
        """
        for configured in ("haerinmom", ""):
            with self.subTest(configured=configured or "(없음)"):
                with patch.object(
                    persona.config, "app", {"product_persona": configured}
                ):
                    prompt = self._prompt(product_persona="")
                self.assertRegex(prompt, r"~했어요|~했음")

    def test_the_configured_speaker_fills_an_empty_argument(self):
        """
        부르는 쪽이 이름을 안 넘겨도 사람이 붙어야 한다. 안 그러면 화면에서 만든
        대본에만 사람이 빠진다.
        """
        with patch.object(persona.config, "app", {"product_persona": "haerinmom"}):
            prompt = self._prompt(product_persona="")

        self.assertIn("## Who is speaking", prompt)

    def test_a_hand_written_prompt_is_not_overridden(self):
        prompt = self._prompt(
            product_persona="haerinmom",
            custom_system_prompt="Only write two sentences.",
        )
        self.assertNotIn("## Who is speaking", prompt)


class TestOneResolutionPath(unittest.TestCase):
    """
    고르는 자리가 여럿이면 화면에서 만든 대본과 작업으로 만든 대본이 서로 다른
    사람 목소리로 나온다.
    """

    def test_no_name_means_the_configured_speaker(self):
        with patch.object(persona.config, "app", {"product_persona": "haerinmom"}):
            self.assertEqual(persona.for_script("").key, "haerinmom")

    def test_a_name_given_is_used_as_given(self):
        with patch.object(persona.config, "app", {"product_persona": ""}):
            self.assertEqual(persona.for_script("haerinmom").key, "haerinmom")

    def test_a_typo_does_not_become_the_configured_speaker(self):
        """
        둘을 같이 다루면 오타 하나가 설정에 적힌 사람으로 조용히 바뀐다.
        """
        with patch.object(persona.config, "app", {"product_persona": "haerinmom"}):
            self.assertIsNone(persona.for_script("haerinmomm"))

    def test_the_prompt_builder_uses_the_same_rule(self):
        """
        화면에서 만드는 대본도 설정에 적힌 사람으로 써야 한다. 부르는 쪽이
        이름을 안 넘겨도 사람이 붙어야 한다.
        """
        with patch.object(persona.config, "app", {"product_persona": "haerinmom"}):
            prompt = llm.build_script_prompt(
                video_subject="주제", script_style="product"
            )
        self.assertIn(persona.PERSONAS["haerinmom"].as_prompt(), prompt)


class TestRecordedOnTheTask(unittest.TestCase):
    def _params(self, **overrides):
        from app.models.schema import VideoParams

        values = {
            "video_subject": "실리콘 주방집게",
            "video_script": "",
            "script_style": "product",
        }
        values.update(overrides)
        return VideoParams(**values)

    def _generate(self, params):
        from app.services import task as tm

        with (
            patch.object(tm.llm, "pick_product_voice", return_value="days"),
            patch.object(tm.llm, "generate_script", return_value="대본") as generate,
        ):
            tm.generate_script("task-id", params)
        return generate

    def test_the_configured_speaker_is_used_and_recorded(self):
        params = self._params()
        with patch.object(persona.config, "app", {"product_persona": "haerinmom"}):
            generate = self._generate(params)

        self.assertEqual(params.product_persona, "haerinmom")
        self.assertEqual(generate.call_args.kwargs["product_persona"], "haerinmom")

    def test_a_speaker_asked_for_wins_over_the_setting(self):
        """지난 작업을 되살렸을 때 다른 사람이 말하면 안 된다."""
        params = self._params(product_persona="haerinmom")
        with patch.object(persona.config, "app", {"product_persona": ""}):
            generate = self._generate(params)

        self.assertEqual(generate.call_args.kwargs["product_persona"], "haerinmom")

    def test_an_unknown_name_is_recorded_as_none(self):
        """
        모르는 이름은 사람 없이 쓰인다. 요청값을 그대로 남기면 그 사람이 쓴
        것처럼 보인다.
        """
        params = self._params(product_persona="없는사람")
        with patch.object(
            persona.config, "app", {"product_persona": "haerinmom"}
        ):
            self._generate(params)

        # 설정에 사람이 적혀 있어도 그리로 조용히 바뀌면 안 된다. 오타 하나로
        # 지난 작업이 다른 사람 목소리로 되살아난다.
        self.assertEqual(params.product_persona, "")

    def test_a_supplied_script_records_who_the_caller_named(self):
        """
        화면과 봇은 대본을 먼저 만들어 넘긴다. 그 대본에도 화자가 있는데, 이미
        대본이 있다는 이유로 지우면 기록에 아무도 안 남아 그 대본이 어떻게 나왔는지
        되짚을 수 없다. 반대로 넘기는 쪽이 안 밝혔거나 모르는 이름을 줬을 때
        설정에 적힌 사람을 끌어다 붙이면, 쓰지도 않은 사람이 기록에 남는다.
        """
        from app.services import task as tm

        cases = [
            ("haerinmom", "haerinmom"),  # 밝힌 사람은 그대로 남는다
            ("없는사람", ""),  # 모르는 이름은 무명
            ("", ""),  # 사람이 직접 쓴 대본
        ]
        for named, recorded in cases:
            with self.subTest(named=named):
                params = self._params(video_script="넘겨받은 대본", product_persona=named)
                with patch.object(
                    persona.config, "app", {"product_persona": "kimbujang"}
                ):
                    with patch.object(tm.llm, "generate_script"):
                        tm.generate_script("task-id", params)

                self.assertEqual(params.product_persona, recorded)


class TestTheRuleIsInOnePlace(unittest.TestCase):
    """
    화면과 작업이 각자 판단하면, 화면에서 만든 대본에는 사람이 붙었는데 기록에는
    안 남는 일이 생긴다. 그러면 그 대본이 어떻게 나왔는지 되짚을 수 없다.
    """

    def test_a_product_script_gets_the_configured_speaker(self):
        with patch.object(persona.config, "app", {"product_persona": "haerinmom"}):
            self.assertEqual(persona.key_for("product", "", ""), "haerinmom")

    def test_other_styles_get_nobody(self):
        with patch.object(persona.config, "app", {"product_persona": "haerinmom"}):
            for style in ("informative", "story"):
                with self.subTest(style=style):
                    self.assertEqual(persona.key_for(style, "", ""), "")

    def test_a_hand_written_prompt_gets_nobody(self):
        with patch.object(persona.config, "app", {"product_persona": "haerinmom"}):
            self.assertEqual(persona.key_for("product", "Write two lines.", ""), "")

    def test_a_typo_gets_nobody(self):
        with patch.object(persona.config, "app", {"product_persona": "haerinmom"}):
            self.assertEqual(persona.key_for("product", "", "haerinmomm"), "")

    def test_the_task_layer_uses_it(self):
        """
        작업 쪽이 따로 판단하면 화면이 정한 사람과 기록이 갈린다.
        """
        from app.services import task as tm

        from app.models.schema import VideoParams

        params = VideoParams(
            video_subject="주제", video_script="", script_style="product"
        )
        with (
            patch.object(persona.config, "app", {"product_persona": "haerinmom"}),
            patch.object(tm.llm, "pick_product_voice", return_value="days"),
            patch.object(tm.llm, "generate_script", return_value="대본"),
            patch.object(
                tm.persona_service, "key_for", return_value="haerinmom"
            ) as key_for,
        ):
            tm.generate_script("task-id", params)

        key_for.assert_called_once_with("product", "", "")
if __name__ == "__main__":
    unittest.main()
