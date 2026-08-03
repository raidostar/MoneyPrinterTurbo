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
        응답 본문을 상한까지만 읽어 딕셔너리로 만든다. 못 읽으면 실패로 본다.

        응답은 외부 입력이다. 통째로 올려 파싱하면 거대한 본문 하나에 흔들리고,
        JSON 이 아닐 때 나는 예외는 아래 `RequestException` 에 걸리지 않아 부르는
        쪽으로 그대로 새어 나간다.
        """
        raw = response.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
        if len(raw) > MAX_RESPONSE_BYTES:
            return {"success": False, "error": "Upload-Post returned an oversized body"}
        try:
            result = json.loads(raw)
        except ValueError:
            return {"success": False, "error": "Upload-Post returned a body we cannot read"}
        if not isinstance(result, dict):
            return {"success": False, "error": "Upload-Post returned an unexpected body"}
        return result

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

                response.raise_for_status()
                result = self._read_result(response)

                if result.get('success'):
                    logger.info(f"✅ Video cross-posted successfully! Request ID: {result.get('request_id')}")
                else:
                    logger.warning(f"Cross-post failed: {result.get('message', 'Unknown error')}")

                return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to cross-post video: {str(e)}")
            return {"success": False, "error": str(e)}

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
