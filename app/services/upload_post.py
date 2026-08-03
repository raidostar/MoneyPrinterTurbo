"""
Upload-Post API integration for cross-posting videos to TikTok, Instagram and YouTube Shorts.

Docs: https://docs.upload-post.com
"""
import json
import os
from typing import Optional

import requests
from loguru import logger
from app.config import config

# 응답도 외부 입력이다. 통째로 메모리에 올리기 전에 크기를 끊는다.
MAX_RESPONSE_BYTES = 1024 * 1024
# 응답에서 꺼내 쓰는 문자열의 상한. 이 값들이 기록과 화면으로 흘러간다.
MAX_FIELD_LENGTH = 300


def _clean(value) -> str:
    """
    저쪽이 적어 보낸 문자열을 기록과 화면에 실을 수 있게 만든다.

    제어문자가 섞이면 로그를 보는 화면이 조작되고, 길이 제한이 없으면 응답 하나로
    기록이 채워진다. 자격 증명이 붙은 주소가 섞일 수 있어 공용 정리기도 거친다.
    """
    from app.services.llm import sanitize_error_message

    text = sanitize_error_message(value if value is not None else "")
    text = "".join(char for char in text if char.isprintable())
    return " ".join(text.split())[:MAX_FIELD_LENGTH]


def _status_of(response) -> int:
    try:
        return int(response.status_code)
    except (AttributeError, TypeError, ValueError):
        return 0


class UploadPostService:
    API_BASE = "https://api.upload-post.com"

    def __init__(self):
        self.api_key = config.app.get("upload_post_api_key", "")
        self.username = config.app.get("upload_post_username", "")
        self.enabled = config.app.get("upload_post_enabled", False)
        self.platforms = config.app.get("upload_post_platforms", ["tiktok", "instagram"])
        self.auto_upload = config.app.get("upload_post_auto_upload", False)
        self.youtube_privacy_status = config.app.get("upload_post_youtube_privacy_status", "public")

    def is_configured(self) -> bool:
        return self._can_upload(self.username)

    def _can_upload(self, username: str) -> bool:
        """키와 올릴 프로필이 있고 켜져 있는지. 프로필은 채널마다 다를 수 있다."""
        return bool(self.api_key and username and self.enabled)

    def _read_result(self, response) -> dict:
        """
        응답을 읽어 이 모듈이 쓰는 모양으로 바꾼다.

        응답은 외부 입력이다. 통째로 올려 파싱하면 거대한 본문 하나에 흔들리고,
        JSON 이 아닐 때 나는 예외는 아래 `RequestException` 에 걸리지 않아 부르는
        쪽으로 그대로 새어 나간다. 받은 값을 그대로 돌려주지도 않는다 — 저쪽이
        적어 보낸 문자열이 기록과 화면으로 흘러가므로 길이와 글자를 여기서 자른다.

        `indeterminate` 는 "보냈는데 받았는지 모르겠다" 는 뜻이다. 이 표시가 붙으면
        부르는 쪽이 다시 보내지 않는다. 4xx 는 저쪽이 분명히 거절한 것이라 붙이지
        않는다 — 제목이나 키를 고치고 다시 보낼 수 있어야 한다.
        """
        status = _status_of(response)
        # 본문을 다 읽었든 못 읽었든 연결은 돌려준다. 안 닫으면 응답이 쌓일수록
        # 열린 소켓이 남는다.
        with response:
            try:
                raw = response.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
            except Exception as exc:
                # 압축이 도중에 끊기면 requests 가 아니라 urllib3 의 예외가 난다.
                # 그건 아래 `RequestException` 에 안 걸려 부르는 쪽으로 샌다.
                logger.warning(f"could not read the upload response: {_clean(exc)}")
                return self._unreadable("a body we could not finish reading", status)

        if len(raw) > MAX_RESPONSE_BYTES:
            return self._unreadable("an oversized body", status)
        try:
            body = json.loads(raw)
        except ValueError:
            return self._unreadable("a body we cannot read", status)
        if not isinstance(body, dict):
            return self._unreadable("an unexpected body", status)

        # `success` 는 참/거짓으로 오기로 되어 있다. 문자열 "false" 를 그대로 믿으면
        # 비어 있지 않다는 이유로 성공이 된다.
        if status >= 400 or body.get("success") is not True:
            reason = body.get("error") or body.get("message") or f"HTTP {status}"
            return {
                "success": False,
                "error": _clean(reason),
                "indeterminate": status >= 500,
            }

        return {
            "success": True,
            "request_id": _clean(body.get("request_id", "")),
            "message": _clean(body.get("message", "")),
        }

    @staticmethod
    def _unreadable(what: str, status: int) -> dict:
        return {
            "success": False,
            "error": f"Upload-Post returned {what} (HTTP {status})",
            # 4xx 로 거절하면서 본문이 깨진 경우까지 "모르겠다" 로 두면, 고치고
            # 다시 보낼 수 있는 영상이 영영 막힌다.
            "indeterminate": status < 400 or status >= 500,
        }

    def upload_video(
        self,
        video_path: str,
        title: str,
        platforms: Optional[list] = None,
        privacy_level: str = "PUBLIC_TO_EVERYONE",
        youtube_extra: Optional[dict] = None,
        username: str = "",
        extra_fields: Optional[dict] = None,
    ) -> dict:
        # 채널마다 올라가는 계정이 다르다. 프로필을 받으면 그것으로 보내고, 없으면
        # 설정에 적힌 기본 프로필을 쓴다.
        username = str(username or "").strip() or self.username
        if not self._can_upload(username):
            logger.warning("Upload-Post is not configured. Skipping cross-post.")
            return {"success": False, "error": "Upload-Post not configured"}

        if platforms is None:
            platforms = self.platforms

        if not os.path.exists(video_path):
            logger.error(f"Video file not found: {video_path}")
            return {"success": False, "error": f"Video file not found: {video_path}"}

        logger.info(f"Cross-posting video to {', '.join(platforms)} via Upload-Post...")

        try:
            with open(video_path, 'rb') as video_file:
                files = {'video': video_file}

                data = [
                    ('user', username),
                    ('title', title[:2200]),
                    ('privacy_level', privacy_level),
                ]

                for platform in platforms:
                    data.append(('platform[]', platform))

                if youtube_extra and any(p.startswith("youtube") for p in platforms):
                    if "youtube_title" in youtube_extra:
                        data.append(('youtube_title', youtube_extra["youtube_title"][:100]))
                    if "youtube_description" in youtube_extra:
                        data.append(('youtube_description', youtube_extra["youtube_description"]))
                    for tag in youtube_extra.get("tags", []):
                        data.append(('tags[]', tag))
                    data.append(('privacyStatus', youtube_extra.get("privacyStatus", "public")))
                    data.append(('containsSyntheticMedia', "true"))

                # 플랫폼별 추가 항목. AI 로 만들었다는 고지가 여기로 들어온다.
                for key, value in (extra_fields or {}).items():
                    data.append((str(key), str(value)))

                headers = {'Authorization': f'Apikey {self.api_key}'}

                response = requests.post(
                    f"{self.API_BASE}/api/upload",
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=300,
                    stream=True,
                )

                # 상태 코드로 예외를 올리지 않는다. 400 으로 거절당한 것과 보내다
                # 끊긴 것은 다르게 다뤄야 하는데, 예외로 만들면 둘이 같아진다.
                result = self._read_result(response)

                if result.get('success'):
                    logger.info(f"✅ Video cross-posted successfully! Request ID: {result.get('request_id')}")
                else:
                    logger.warning(f"Cross-post failed: {result.get('error', 'Unknown error')}")

                return result

        except requests.exceptions.RequestException as e:
            # 예외 문구에 자격 증명이 붙은 주소가 섞일 수 있다. 이 값은 기록과
            # 화면으로 그대로 흘러간다.
            message = _clean(e)
            logger.error(f"Failed to cross-post video: {message}")
            # 보내는 도중에 끊긴 것이라, 저쪽이 받았는지 여기서는 알 수 없다.
            return {"success": False, "error": message, "indeterminate": True}

    def check_status(self, request_id: str) -> dict:
        """
        Check the status of an upload request.

        Args:
            request_id (str): The request ID from upload

        Returns:
            dict: Status information
        """
        try:
            headers = {
                'Authorization': f'Apikey {self.api_key}'
            }

            response = requests.get(
                f"{self.API_BASE}/api/uploadposts/status",
                params={'request_id': request_id},
                headers=headers,
                timeout=30
            )
            
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to check status: {str(e)}")
            return {"success": False, "error": str(e)}


# Singleton instance
upload_post_service = UploadPostService()


def cross_post_video(
    video_path: str,
    title: str,
    platforms: Optional[list] = None,
    youtube_extra: Optional[dict] = None,
    username: str = "",
    extra_fields: Optional[dict] = None,
) -> dict:
    return upload_post_service.upload_video(
        video_path,
        title,
        platforms,
        youtube_extra=youtube_extra,
        username=username,
        extra_fields=extra_fields,
    )
