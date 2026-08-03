import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.upload_post import UploadPostService


_CONFIG_BASE = {
    "upload_post_enabled": True,
    "upload_post_api_key": "test-key",
    "upload_post_username": "testuser",
    "upload_post_platforms": ["tiktok", "instagram", "youtube"],
    "upload_post_auto_upload": True,
    "upload_post_youtube_privacy_status": "unlisted",
}


def _mock_response(success=True, body=None):
    r = MagicMock()
    if body is None:
        body = json.dumps({"success": success, "request_id": "abc123"}).encode("utf-8")
    r.raw.read.return_value = body
    r.raise_for_status = MagicMock()
    return r


def _get(data, key):
    for k, v in data:
        if k == key:
            return v
    return None


def _get_all(data, key):
    return [v for k, v in data if k == key]


def _has_key(data, key):
    return any(k == key for k, v in data)


class TestChannelProfile(unittest.TestCase):
    """
    채널마다 올라가는 계정이 다르다. 프로필을 정해 보냈는데 설정에 적힌 기본
    프로필로 나가면, 카드뉴스가 쇼츠 계정에 올라간다.
    """

    @patch("app.services.upload_post.config.app", _CONFIG_BASE)
    @patch("app.services.upload_post.os.path.exists", return_value=True)
    @patch("builtins.open", mock_open(read_data=b"fake"))
    @patch("app.services.upload_post.requests.post")
    def test_a_given_profile_is_the_one_used(self, mock_post, _exists):
        mock_post.return_value = _mock_response()

        UploadPostService().upload_video("/fake/v.mp4", "T", username="cardnews")

        self.assertEqual(_get(mock_post.call_args[1]["data"], "user"), "cardnews")

    @patch("app.services.upload_post.config.app", _CONFIG_BASE)
    @patch("app.services.upload_post.os.path.exists", return_value=True)
    @patch("builtins.open", mock_open(read_data=b"fake"))
    @patch("app.services.upload_post.requests.post")
    def test_without_one_the_configured_profile_is_used(self, mock_post, _exists):
        mock_post.return_value = _mock_response()

        UploadPostService().upload_video("/fake/v.mp4", "T")

        self.assertEqual(_get(mock_post.call_args[1]["data"], "user"), "testuser")

    @patch(
        "app.services.upload_post.config.app",
        {**_CONFIG_BASE, "upload_post_username": ""},
    )
    @patch("app.services.upload_post.os.path.exists", return_value=True)
    @patch("builtins.open", mock_open(read_data=b"fake"))
    @patch("app.services.upload_post.requests.post")
    def test_a_channel_profile_works_without_a_configured_one(self, mock_post, _exists):
        """기본 프로필을 비워 둔 채로 카드뉴스만 올리는 설정이 가능해야 한다."""
        mock_post.return_value = _mock_response()

        result = UploadPostService().upload_video("/fake/v.mp4", "T", username="cardnews")

        self.assertTrue(result["success"])
        self.assertEqual(_get(mock_post.call_args[1]["data"], "user"), "cardnews")

    @patch("app.services.upload_post.config.app", _CONFIG_BASE)
    @patch("app.services.upload_post.os.path.exists", return_value=True)
    @patch("builtins.open", mock_open(read_data=b"fake"))
    @patch("app.services.upload_post.requests.post")
    def test_extra_fields_reach_the_request(self, mock_post, _exists):
        """
        AI 로 만들었다는 고지가 이 길로 나간다. 조용히 버려지면 밝히지 않은 채
        올라간다.
        """
        mock_post.return_value = _mock_response()

        UploadPostService().upload_video(
            "/fake/v.mp4", "T", extra_fields={"is_aigc": "true"}
        )

        self.assertEqual(_get(mock_post.call_args[1]["data"], "is_aigc"), "true")


class TestResponseIsExternalInput(unittest.TestCase):
    """
    응답은 밖에서 온다. 이 함수는 실패를 예외가 아니라 반환값으로 알리기로 되어
    있는데, 본문 파싱에서 나는 예외는 아래 `RequestException` 에 걸리지 않는다.
    """

    def _upload(self, body):
        with (
            patch("app.services.upload_post.config.app", _CONFIG_BASE),
            patch("app.services.upload_post.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=b"fake")),
            patch("app.services.upload_post.requests.post") as post,
        ):
            post.return_value = _mock_response(body=body)
            return UploadPostService().upload_video("/fake/v.mp4", "T")

    def test_a_body_that_is_not_json_is_a_failure(self):
        result = self._upload(b"<html>gateway timeout</html>")
        self.assertFalse(result["success"])

    def test_a_body_that_is_not_an_object_is_a_failure(self):
        result = self._upload(b'["not", "an", "object"]')
        self.assertFalse(result["success"])

    def test_an_oversized_body_is_not_parsed(self):
        """통째로 올려 파싱하면 거대한 본문 하나에 흔들린다."""
        from app.services.upload_post import MAX_RESPONSE_BYTES

        filler = b" " * (MAX_RESPONSE_BYTES + 10)
        result = self._upload(b'{"success": true, "note": "' + filler + b'"}')
        self.assertFalse(result["success"])

    def test_the_body_is_read_up_to_a_limit(self):
        from app.services.upload_post import MAX_RESPONSE_BYTES

        with (
            patch("app.services.upload_post.config.app", _CONFIG_BASE),
            patch("app.services.upload_post.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=b"fake")),
            patch("app.services.upload_post.requests.post") as post,
        ):
            response = _mock_response()
            post.return_value = response
            UploadPostService().upload_video("/fake/v.mp4", "T")

        response.raw.read.assert_called_once_with(
            MAX_RESPONSE_BYTES + 1, decode_content=True
        )


class TestUploadPostService(unittest.TestCase):
    @patch(
        "app.services.upload_post.config.app",
        {**_CONFIG_BASE, "upload_post_enabled": False},
    )
    @patch("app.services.upload_post.requests.post")
    def test_unconfigured_service_skips_request(self, mock_post):
        """기능이 꺼져 있으면 파일을 실수로 올리거나 외부 API 크레딧을 소모해서는 안 된다."""
        result = UploadPostService().upload_video("/fake/v.mp4", "Title")

        self.assertFalse(result["success"])
        self.assertIn("not configured", result["error"])
        mock_post.assert_not_called()

    @patch("app.services.upload_post.config.app", _CONFIG_BASE)
    @patch("app.services.upload_post.os.path.exists", return_value=False)
    @patch("app.services.upload_post.requests.post")
    def test_missing_video_skips_request(self, mock_post, _exists):
        """로컬 결과물이 없으면 네트워크 요청을 보내기 전에 명확한 오류를 반환해야 한다."""
        result = UploadPostService().upload_video("/missing/v.mp4", "Title")

        self.assertFalse(result["success"])
        self.assertIn("Video file not found", result["error"])
        mock_post.assert_not_called()

    @patch("app.services.upload_post.config.app", _CONFIG_BASE)
    @patch("app.services.upload_post.os.path.exists", return_value=True)
    @patch("builtins.open", mock_open(read_data=b"fake"))
    @patch("app.services.upload_post.requests.post")
    def test_upload_request_error_returns_failure(self, mock_post, _exists):
        """네트워크 예외는 안정적인 결과로 변환해야 하며, 업로드 실패가 영상 생성 작업을 끊어서는 안 된다."""
        mock_post.side_effect = requests.exceptions.Timeout("upload timed out")

        result = UploadPostService().upload_video("/fake/v.mp4", "Title")

        self.assertFalse(result["success"])
        self.assertIn("upload timed out", result["error"])

    @patch("app.services.upload_post.config.app", _CONFIG_BASE)
    @patch("app.services.upload_post.requests.get")
    def test_check_status_returns_payload_or_network_failure(self, mock_get):
        """상태 조회의 성공과 실패는 업로드 엔드포인트와 같은 반환 규약을 써야 한다."""
        response = _mock_response()
        response.json.return_value = {"success": True, "status": "processing"}
        mock_get.return_value = response
        service = UploadPostService()

        self.assertEqual(
            service.check_status("request-123"),
            {"success": True, "status": "processing"},
        )

        mock_get.side_effect = requests.exceptions.ConnectionError("offline")
        failed = service.check_status("request-123")
        self.assertFalse(failed["success"])
        self.assertIn("offline", failed["error"])


class TestUploadPostYouTubePayload(unittest.TestCase):
    @patch("app.services.upload_post.config.app", _CONFIG_BASE)
    @patch("app.services.upload_post.os.path.exists", return_value=True)
    @patch("builtins.open", mock_open(read_data=b"fake"))
    @patch("app.services.upload_post.requests.post")
    def test_youtube_fields_en_payload(self, mock_post, _exists):
        mock_post.return_value = _mock_response()
        svc = UploadPostService()

        svc.upload_video("/fake/v.mp4", "Título", youtube_extra={
            "youtube_title": "Mi Short",
            "youtube_description": "Descripción",
            "tags": ["ia", "shorts"],
            "privacyStatus": "unlisted",
        })

        data = mock_post.call_args[1]["data"]
        self.assertEqual(_get(data, "youtube_title"), "Mi Short")
        self.assertEqual(_get(data, "youtube_description"), "Descripción")
        self.assertEqual(_get_all(data, "tags[]"), ["ia", "shorts"])
        self.assertEqual(_get(data, "privacyStatus"), "unlisted")
        self.assertEqual(_get(data, "containsSyntheticMedia"), "true")

    @patch("app.services.upload_post.config.app", _CONFIG_BASE)
    @patch("app.services.upload_post.os.path.exists", return_value=True)
    @patch("builtins.open", mock_open(read_data=b"fake"))
    @patch("app.services.upload_post.requests.post")
    def test_contains_synthetic_media_siempre_true(self, mock_post, _exists):
        mock_post.return_value = _mock_response()
        svc = UploadPostService()

        svc.upload_video("/fake/v.mp4", "T", youtube_extra={"containsSyntheticMedia": False})

        data = mock_post.call_args[1]["data"]
        self.assertEqual(_get(data, "containsSyntheticMedia"), "true")

    @patch("app.services.upload_post.config.app", {
        **_CONFIG_BASE,
        "upload_post_platforms": ["tiktok", "instagram"],
    })
    @patch("app.services.upload_post.os.path.exists", return_value=True)
    @patch("builtins.open", mock_open(read_data=b"fake"))
    @patch("app.services.upload_post.requests.post")
    def test_tiktok_instagram_sin_youtube_fields(self, mock_post, _exists):
        mock_post.return_value = _mock_response()
        svc = UploadPostService()
        svc.upload_video("/fake/v.mp4", "T")

        data = mock_post.call_args[1]["data"]
        self.assertFalse(_has_key(data, "youtube_title"))
        self.assertFalse(_has_key(data, "containsSyntheticMedia"))
        self.assertFalse(_has_key(data, "privacyStatus"))

    @patch("app.services.upload_post.config.app", {
        **_CONFIG_BASE,
        "upload_post_platforms": ["tiktok"],
    })
    @patch("app.services.upload_post.os.path.exists", return_value=True)
    @patch("builtins.open", mock_open(read_data=b"fake"))
    @patch("app.services.upload_post.requests.post")
    def test_youtube_extra_ignorado_si_youtube_no_en_platforms(self, mock_post, _exists):
        mock_post.return_value = _mock_response()
        svc = UploadPostService()
        svc.upload_video("/fake/v.mp4", "T", youtube_extra={"youtube_title": "irrelevante"})

        data = mock_post.call_args[1]["data"]
        self.assertFalse(_has_key(data, "youtube_title"))

    @patch("app.services.upload_post.config.app", _CONFIG_BASE)
    @patch("app.services.upload_post.os.path.exists", return_value=True)
    @patch("builtins.open", mock_open(read_data=b"fake"))
    @patch("app.services.upload_post.requests.post")
    def test_endpoint_y_platform_format_correcto(self, mock_post, _exists):
        mock_post.return_value = _mock_response()
        svc = UploadPostService()
        svc.upload_video("/fake/v.mp4", "T")

        call_url = mock_post.call_args[0][0]
        self.assertTrue(call_url.endswith("/api/upload"), f"Endpoint incorrecto: {call_url}")

        data = mock_post.call_args[1]["data"]
        platforms = _get_all(data, "platform[]")
        self.assertIn("tiktok", platforms)
        self.assertIn("instagram", platforms)
        self.assertIn("youtube", platforms)


if __name__ == "__main__":
    unittest.main()
