"""만든 영상을 채널의 계정들에 올린다."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.services import publish

BASE = {
    "upload_post_enabled": True,
    "upload_post_api_key": "key",
    "upload_post_username": "shorts-profile",
    "upload_post_platforms": ["tiktok", "instagram"],
    "upload_post_cardnews_username": "cardnews-profile",
    "upload_post_cardnews_platforms": ["youtube", "tiktok", "threads", "x"],
    "upload_post_youtube_privacy_status": "public",
}


def _config(**overrides):
    values = dict(BASE)
    values.update(overrides)
    return patch.object(publish.config, "app", values)


class TestTarget(unittest.TestCase):
    def test_each_channel_goes_to_its_own_profile(self):
        """
        카드뉴스와 쇼츠는 보는 사람이 다르다. 한 계정에 섞으면 알고리즘이 채널
        성격을 못 잡는다.
        """
        with _config():
            self.assertEqual(
                publish.resolve_target(publish.CARD_NEWS).profile, "cardnews-profile"
            )
            self.assertEqual(
                publish.resolve_target(publish.SHORTS).profile, "shorts-profile"
            )

    def test_a_channel_without_a_profile_does_not_borrow_another(self):
        """
        엉뚱한 계정에 올라간 영상은 지워도 이미 나간 뒤다. 안 올리는 편이 낫다.
        """
        with _config(upload_post_cardnews_username=""):
            self.assertIsNone(publish.resolve_target(publish.CARD_NEWS))
            # 다른 채널은 그대로 올라가야 한다.
            self.assertIsNotNone(publish.resolve_target(publish.SHORTS))

    def test_publishing_off_means_no_channel_publishes(self):
        for key in ("upload_post_enabled", "upload_post_api_key"):
            with self.subTest(key=key):
                with _config(**{key: type(BASE[key])()}):
                    self.assertIsNone(publish.resolve_target(publish.CARD_NEWS))

    def test_an_unknown_platform_is_dropped_instead_of_failing_the_lot(self):
        """
        모르는 이름 하나가 섞이면 요청 전체가 거절된다. 오타 하나에 그날 업로드가
        통째로 날아가는 것보다 그 하나만 빼고 보내는 편이 낫다.
        """
        with _config(upload_post_cardnews_platforms=["youtube", "twitter", "TikTok"]):
            target = publish.resolve_target(publish.CARD_NEWS)
        self.assertEqual(target.platforms, ("youtube", "tiktok"))

    def test_a_channel_with_no_usable_platform_does_not_publish(self):
        with _config(upload_post_cardnews_platforms=["nowhere"]):
            self.assertIsNone(publish.resolve_target(publish.CARD_NEWS))

    def test_an_unknown_channel_does_not_publish(self):
        with _config():
            self.assertIsNone(publish.resolve_target("somewhere-else"))

    def test_asking_first_is_the_default(self):
        """시험용 영상이 계정에 올라가면 되돌릴 수 없다."""
        with _config():
            self.assertFalse(publish.auto_publishes(publish.CARD_NEWS))
        with _config(upload_post_cardnews_auto_upload=True):
            self.assertTrue(publish.auto_publishes(publish.CARD_NEWS))


class TestPublish(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.video = os.path.join(self.directory.name, "cardnews.mp4")
        with open(self.video, "wb") as handle:
            handle.write(b"video")

    def _publish(self, sent=None, title="어떤 도구", **overrides):
        sent = sent if sent is not None else {"success": True, "request_id": "r-1"}
        with _config(**overrides):
            with patch.object(
                publish.upload_post, "cross_post_video", return_value=sent
            ) as call:
                result = publish.publish(publish.CARD_NEWS, self.video, title)
        return result, call

    def test_the_video_goes_to_the_channel_profile(self):
        result, call = self._publish()
        self.assertTrue(result.ok)
        self.assertEqual(call.call_args.kwargs["username"], "cardnews-profile")
        self.assertEqual(
            call.call_args.kwargs["platforms"], ["youtube", "tiktok", "threads", "x"]
        )

    def test_the_same_video_is_not_published_twice(self):
        """버튼을 두 번 누르거나 봇을 다시 켜면 같은 영상이 또 올라간다."""
        self._publish()
        result, call = self._publish()
        self.assertTrue(result.ok)
        self.assertEqual(result.skipped, "already published")
        call.assert_not_called()

    def test_a_failed_upload_can_be_tried_again(self):
        """올라가지도 않았는데 올린 것으로 기록하면 그 영상은 영영 안 올라간다."""
        self._publish(sent={"success": False, "error": "rate limited"})
        result, call = self._publish()
        self.assertTrue(result.ok)
        call.assert_called_once()

    def test_ai_generated_content_is_declared_on_tiktok(self):
        """밝히지 않으면 나중에 계정 쪽에서 문제가 된다."""
        _, call = self._publish()
        self.assertEqual(call.call_args.kwargs["extra_fields"]["is_aigc"], "true")

    def test_youtube_gets_a_title_and_privacy_setting(self):
        _, call = self._publish()
        extra = call.call_args.kwargs["youtube_extra"]
        self.assertEqual(extra["youtube_title"], "어떤 도구")
        self.assertEqual(extra["privacyStatus"], "public")

    def test_a_channel_without_youtube_sends_no_youtube_fields(self):
        _, call = self._publish(upload_post_cardnews_platforms=["tiktok"])
        self.assertIsNone(call.call_args.kwargs["youtube_extra"])

    def test_a_title_that_is_too_long_is_cut(self):
        """YouTube 제목은 100자를 넘길 수 없다."""
        result, call = self._publish(title="가" * 500)
        self.assertTrue(result.ok)
        self.assertLessEqual(
            len(call.call_args.kwargs["title"]), publish.MAX_TITLE_LENGTH
        )

    def test_a_video_with_no_title_is_not_sent(self):
        """YouTube 는 제목이 없으면 받지 않는다."""
        result, call = self._publish(title="   ")
        self.assertFalse(result.ok)
        call.assert_not_called()

    def test_a_missing_file_is_not_sent(self):
        os.remove(self.video)
        result, call = self._publish()
        self.assertFalse(result.ok)
        call.assert_not_called()

    def test_an_unconfigured_channel_says_so_instead_of_failing(self):
        result, call = self._publish(upload_post_cardnews_username="")
        self.assertEqual(result.skipped, "not configured")
        call.assert_not_called()

    def test_a_transport_that_raises_does_not_escape(self):
        """
        전송 계층은 실패를 반환값으로 알리기로 되어 있지만 그것까지 보장하지는
        않는다. 여기서 새면 영상을 보내는 자리에서 봇이 죽는다.
        """
        with _config():
            with patch.object(
                publish.upload_post,
                "cross_post_video",
                side_effect=RuntimeError("boom"),
            ):
                result = publish.publish(publish.CARD_NEWS, self.video, "제목")
        self.assertFalse(result.ok)
        # 못 올렸으니 다시 시도할 수 있어야 한다.
        self.assertFalse(publish.already_published(self.video))

    def test_a_response_that_is_not_a_dictionary_is_a_failure(self):
        result, _ = self._publish(sent="nope")
        self.assertFalse(result.ok)

    def test_a_provider_error_is_sanitized_before_it_is_shown(self):
        """
        오류 문구는 남의 서버가 적어 보낸 값이고, 그대로 텔레그램으로 나간다.
        자격 증명이 붙은 주소가 섞여 있으면 그게 화면과 기록에 남는다.
        """
        leak = "failed to reach https://user:hunter2@upload.test/api?api_key=SECRET42"
        result, _ = self._publish(sent={"success": False, "error": leak})

        self.assertFalse(result.ok)
        self.assertNotIn("hunter2", result.error)
        self.assertNotIn("SECRET42", result.error)

    def test_a_long_provider_error_is_cut(self):
        result, _ = self._publish(sent={"success": False, "error": "x" * 5000})
        self.assertLessEqual(len(result.error), publish.MAX_ERROR_LENGTH)

    def test_only_the_request_id_is_kept_from_the_response(self):
        """응답을 통째로 담으면 그 안에 무엇이 들었는지 모른 채 흘러다닌다."""
        result, _ = self._publish(
            sent={"success": True, "request_id": "r" * 5000, "secret": "shh"}
        )
        self.assertLessEqual(len(result.request_id), publish.MAX_REQUEST_ID_LENGTH)
        self.assertNotIn("shh", str(result))


class TestNotTwice(unittest.TestCase):
    """
    같은 영상을 두 번 올리면 되돌릴 수 없다. 지우는 것보다 손으로 한 번 더 올리는
    편이 쉬우므로, 애매하면 올리지 않는 쪽으로 틀린다.
    """

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.video = os.path.join(self.directory.name, "cardnews.mp4")
        with open(self.video, "wb") as handle:
            handle.write(b"video")

    def test_the_claim_is_taken_before_the_upload_starts(self):
        """
        보내고 나서 기록하면 늦다. 그 사이에 다시 부르면 둘 다 아직 안 올렸다고
        보고 같은 영상을 두 번 올린다.
        """
        seen = {}

        def _send(**_kwargs):
            seen["claimed_during_upload"] = publish.already_published(self.video)
            return {"success": True, "request_id": "r-1"}

        with _config():
            with patch.object(publish.upload_post, "cross_post_video", side_effect=_send):
                publish.publish(publish.CARD_NEWS, self.video, "제목")

        self.assertTrue(seen["claimed_during_upload"])

    def test_dying_mid_upload_does_not_let_it_go_out_again(self):
        """올라갔는지 모르는 영상은 안 올리는 쪽이 낫다."""
        with _config():
            with patch.object(
                publish.upload_post,
                "cross_post_video",
                side_effect=KeyboardInterrupt(),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    publish.publish(publish.CARD_NEWS, self.video, "제목")

            with patch.object(publish.upload_post, "cross_post_video") as call:
                result = publish.publish(publish.CARD_NEWS, self.video, "제목")

        self.assertEqual(result.skipped, "already published")
        call.assert_not_called()

    def test_a_receipt_that_could_not_be_written_still_blocks_a_second_upload(self):
        """내용을 못 적어도 자리는 이미 잡혀 있어야 한다."""
        with _config():
            with patch.object(
                publish.upload_post,
                "cross_post_video",
                return_value={"success": True, "request_id": "r-1"},
            ):
                with patch.object(publish, "_write_receipt"):
                    publish.publish(publish.CARD_NEWS, self.video, "제목")

            with patch.object(publish.upload_post, "cross_post_video") as call:
                publish.publish(publish.CARD_NEWS, self.video, "제목")

        call.assert_not_called()

    def test_the_receipt_says_where_it_went(self):
        with _config():
            with patch.object(
                publish.upload_post,
                "cross_post_video",
                return_value={"success": True, "request_id": "r-1"},
            ):
                publish.publish(publish.CARD_NEWS, self.video, "제목")

        with open(f"{self.video}{publish.RECEIPT_SUFFIX}", encoding="utf-8") as handle:
            receipt = json.load(handle)
        self.assertEqual(receipt["profile"], "cardnews-profile")
        self.assertEqual(receipt["request_id"], "r-1")


if __name__ == "__main__":
    unittest.main()
