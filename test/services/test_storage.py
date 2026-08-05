"""영상을 잠깐 올려 두고 주소를 내주는 곳."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.services import storage

SETTINGS = {
    "r2_account_id": "abc123",
    "r2_access_key_id": "key",
    "r2_secret_access_key": "secret",
    "r2_bucket": "shipcast-videos",
}


def _config(**overrides):
    values = dict(SETTINGS)
    values.update(overrides)
    return patch.object(storage.config, "app", values)


class TestConfiguration(unittest.TestCase):
    def test_every_setting_is_needed(self):
        """
        하나라도 비면 올릴 수 없다. 반쯤 채운 설정으로 시도하면 자격 증명 오류가
        게시 실패처럼 보인다.
        """
        for key in SETTINGS:
            with self.subTest(missing=key):
                with _config(**{key: ""}):
                    self.assertFalse(storage.is_configured())

    def test_a_full_setting_is_ready(self):
        with _config():
            self.assertTrue(storage.is_configured())


class TestKeyNames(unittest.TestCase):
    def test_the_key_says_which_task_it_came_from(self):
        self.assertEqual(
            storage.key_for("storage/tasks/e9e4fa32-afa4/final-1.mp4"),
            "e9e4fa32-afa4/final-1.mp4",
        )

    def test_a_path_cannot_climb_out(self):
        """경로에서 오는 값이다. 그대로 쓰면 의도한 것과 다른 자리에 올라간다."""
        for path in ("../../etc/passwd", "../secrets/key.pem", "/etc/hosts"):
            with self.subTest(path=path):
                key = storage.key_for(path)
                self.assertNotIn("..", key)
                self.assertFalse(key.startswith("/"))

    def test_unusual_characters_do_not_survive(self):
        key = storage.key_for("storage/tasks/한글 폴더/영상 파일.mp4")
        self.assertRegex(key, r"^[A-Za-z0-9._/-]*$")


class TestUpload(unittest.TestCase):
    def setUp(self):
        # 올릴 수 있는 곳은 작업 디렉터리 안뿐이다.
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        tasks = os.path.join(self.directory.name, "tasks", "e9e4fa32")
        os.makedirs(tasks)
        patcher = patch.object(
            storage.utils, "task_dir", return_value=os.path.join(self.directory.name, "tasks")
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.video = os.path.join(tasks, "final-1.mp4")
        with open(self.video, "wb") as handle:
            handle.write(b"video bytes")

    def _put(self, client=None, **overrides):
        client = client or MagicMock()
        client.generate_presigned_url.return_value = "https://r2.test/signed?x=1"
        with _config(**overrides):
            with patch.object(storage, "_client", return_value=client):
                return storage.put(self.video, "task/final-1.mp4"), client

    def test_the_file_goes_up_and_a_url_comes_back(self):
        stored, client = self._put()

        self.assertEqual(stored.url, "https://r2.test/signed?x=1")
        self.assertEqual(stored.key, "task/final-1.mp4")
        self.assertEqual(client.upload_file.call_args.args[1], "shipcast-videos")

    def test_the_url_expires(self):
        """
        열어 두면 그 주소를 아는 누구나, 언제까지나 가져갈 수 있다.
        """
        _, client = self._put()

        self.assertEqual(
            client.generate_presigned_url.call_args.kwargs["ExpiresIn"],
            storage.URL_LIFETIME_SECONDS,
        )
        self.assertLessEqual(storage.URL_LIFETIME_SECONDS, 60 * 60 * 24)

    def test_the_video_is_marked_as_a_video(self):
        """종류를 밝히지 않으면 받는 쪽이 내려받기로 다루고 영상으로 안 읽는다."""
        _, client = self._put()
        self.assertEqual(
            client.upload_file.call_args.kwargs["ExtraArgs"]["ContentType"], "video/mp4"
        )

    def test_nothing_is_uploaded_without_settings(self):
        with _config(r2_bucket=""):
            with patch.object(storage, "_client") as client:
                self.assertIsNone(storage.put(self.video, "k"))
        client.assert_not_called()

    def test_a_missing_file_is_not_uploaded(self):
        os.remove(self.video)
        stored, client = self._put()
        self.assertIsNone(stored)
        client.upload_file.assert_not_called()

    def test_a_file_outside_the_task_directory_is_refused(self):
        """
        경로를 그대로 믿으면 부르는 쪽의 실수 하나로 이 기계의 아무 파일이나 남의
        서버에 올라간다.
        """
        outside = os.path.join(self.directory.name, "secret.pem")
        with open(outside, "wb") as handle:
            handle.write(b"private key")

        client = MagicMock()
        with _config():
            with patch.object(storage, "_client", return_value=client):
                for path in (outside, "/etc/hosts", "../secret.pem"):
                    with self.subTest(path=path):
                        self.assertIsNone(storage.put(path, "k"))
        client.upload_file.assert_not_called()

    def test_a_symlink_pointing_outside_is_refused(self):
        """링크를 따라가면 검사가 있으나 마나다."""
        outside = os.path.join(self.directory.name, "secret.pem")
        with open(outside, "wb") as handle:
            handle.write(b"private key")
        link = os.path.join(os.path.dirname(self.video), "innocent.mp4")
        os.symlink(outside, link)

        client = MagicMock()
        with _config():
            with patch.object(storage, "_client", return_value=client):
                self.assertIsNone(storage.put(link, "k"))
        client.upload_file.assert_not_called()

    def test_a_directory_is_not_a_video(self):
        client = MagicMock()
        with _config():
            with patch.object(storage, "_client", return_value=client):
                self.assertIsNone(storage.put(os.path.dirname(self.video), "k"))
        client.upload_file.assert_not_called()

    def test_an_oversized_file_is_refused(self):
        """쇼츠 한 편은 십수 MB 다. 이보다 큰 것은 무언가 잘못된 것이다."""
        with open(self.video, "wb") as handle:
            handle.write(b"x" * (storage.MAX_UPLOAD_BYTES + 1))

        stored, client = self._put()

        self.assertIsNone(stored)
        client.upload_file.assert_not_called()

    def test_a_failure_does_not_raise(self):
        """올리지 못했다고 이미 만들어 둔 영상까지 버릴 이유가 없다."""
        client = MagicMock()
        client.upload_file.side_effect = RuntimeError("network gone")

        stored, _ = self._put(client=client)

        self.assertIsNone(stored)

    def test_a_url_that_could_not_be_signed_is_not_a_success(self):
        """주소가 없으면 올린 것이 소용없다."""
        client = MagicMock()
        client.generate_presigned_url.side_effect = RuntimeError("no signer")

        stored, _ = self._put(client=client)

        self.assertIsNone(stored)


class TestRemove(unittest.TestCase):
    def test_the_file_is_deleted_after_use(self):
        client = MagicMock()
        with _config():
            with patch.object(storage, "_client", return_value=client):
                self.assertTrue(storage.remove("task/final-1.mp4"))

        self.assertEqual(
            client.delete_object.call_args.kwargs,
            {"Bucket": "shipcast-videos", "Key": "task/final-1.mp4"},
        )

    def test_a_failure_to_delete_is_not_fatal(self):
        """
        게시는 이미 끝난 일이다. 주소도 시간이 지나면 죽으므로 남아 있어도 위험이
        커지지는 않는다.
        """
        client = MagicMock()
        client.delete_object.side_effect = RuntimeError("gone")
        with _config():
            with patch.object(storage, "_client", return_value=client):
                self.assertFalse(storage.remove("task/final-1.mp4"))

    def test_an_empty_key_deletes_nothing(self):
        with _config():
            with patch.object(storage, "_client") as client:
                self.assertFalse(storage.remove(""))
        client.assert_not_called()


class TestOptionalDependency(unittest.TestCase):
    def test_a_missing_client_library_is_explained(self):
        """
        올리지 않는 사람에게까지 무거운 의존성을 지울 이유가 없다. 없을 때 무엇을
        설치해야 하는지 알려야 한다.
        """
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name.startswith("boto"):
                raise ImportError("no boto3")
            return real_import(name, *args, **kwargs)

        with _config():
            with patch.object(builtins, "__import__", side_effect=refuse):
                with patch.object(storage.logger, "warning") as warned:
                    self.assertIsNone(storage._client())

        self.assertIn("--extra storage", " ".join(str(c.args[0]) for c in warned.call_args_list))


if __name__ == "__main__":
    unittest.main()
