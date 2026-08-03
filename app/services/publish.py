"""
만든 영상을 채널의 계정들에 올린다.

채널마다 올라가는 곳이 다르다. 카드뉴스와 쇼츠는 보는 사람이 달라, 한 계정에
섞으면 알고리즘이 채널 성격을 못 잡는다. upload-post 는 프로필 하나에 플랫폼
계정 묶음을 매달아 두므로, 채널마다 다른 프로필 이름을 쓴다.

설정이 비어 있으면 올리지 않는다. 다른 채널의 프로필로 대신 올리지 않는다 —
엉뚱한 계정에 올라간 영상은 지워도 이미 나간 뒤다.
"""

import json
import os
from dataclasses import dataclass, field

from loguru import logger

from app.config import config
from app.services import upload_post

CARD_NEWS = "cardnews"
SHORTS = "shorts"

# 채널마다 읽는 설정 키. 쇼츠는 원래 쓰던 키를 그대로 쓴다.
CHANNEL_KEYS = {
    CARD_NEWS: "upload_post_cardnews",
    SHORTS: "upload_post",
}

# upload-post 가 받는 이름. 여기 없는 값을 보내면 요청 전체가 거절된다.
KNOWN_PLATFORMS = frozenset(
    {
        "tiktok",
        "instagram",
        "linkedin",
        "youtube",
        "facebook",
        "x",
        "threads",
        "pinterest",
        "bluesky",
        "reddit",
        "google_business",
        "discord",
        "telegram",
    }
)

RECEIPT_SUFFIX = ".published.json"
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 2200


@dataclass(frozen=True)
class PublishTarget:
    """한 채널이 올라가는 곳."""

    channel: str
    profile: str
    platforms: tuple[str, ...]
    youtube_privacy: str = "public"


@dataclass(frozen=True)
class PublishResult:
    """올린 결과. ``platforms`` 는 실제로 보낸 곳이다."""

    ok: bool
    platforms: tuple[str, ...] = ()
    error: str = ""
    skipped: str = ""
    detail: dict = field(default_factory=dict)


def _setting(prefix: str, name: str, default=None):
    return config.app.get(f"{prefix}_{name}", default)


def resolve_target(channel: str) -> PublishTarget | None:
    """
    채널의 업로드 대상. 올릴 수 없으면 ``None``.

    프로필이 비어 있으면 그 채널은 아직 올릴 준비가 안 된 것이다. 기본 프로필로
    대신 보내지 않는다.
    """
    prefix = CHANNEL_KEYS.get(channel)
    if not prefix:
        logger.warning(f"unknown publish channel: {channel}")
        return None

    if not config.app.get("upload_post_enabled", False):
        return None
    if not config.app.get("upload_post_api_key", ""):
        return None

    profile = str(_setting(prefix, "username", "") or "").strip()
    if not profile:
        return None

    raw = _setting(prefix, "platforms", []) or []
    if isinstance(raw, str):
        raw = [raw]
    platforms = []
    for value in raw:
        name = str(value or "").strip().lower()
        if not name:
            continue
        if name not in KNOWN_PLATFORMS:
            # 모르는 이름 하나 때문에 요청 전체가 거절된다. 그 하나만 빼고 보낸다.
            logger.warning(f"dropping an unknown publish platform: {name[:40]}")
            continue
        if name not in platforms:
            platforms.append(name)
    if not platforms:
        return None

    return PublishTarget(
        channel=channel,
        profile=profile,
        platforms=tuple(platforms),
        youtube_privacy=str(
            config.app.get("upload_post_youtube_privacy_status", "public") or "public"
        ),
    )


def auto_publishes(channel: str) -> bool:
    """물어보지 않고 바로 올리는 채널인지."""
    prefix = CHANNEL_KEYS.get(channel)
    if not prefix:
        return False
    return bool(_setting(prefix, "auto_upload", False))


def _receipt_path(video_path: str) -> str:
    return f"{video_path}{RECEIPT_SUFFIX}"


def already_published(video_path: str) -> bool:
    """이미 올린 영상인지. 영수증 파일이 옆에 있으면 올린 것이다."""
    return os.path.exists(_receipt_path(video_path))


def _write_receipt(video_path: str, payload: dict) -> None:
    """
    올렸다는 사실을 영상 옆에 남긴다.

    남기지 못해도 올린 것 자체는 성공이다. 예외를 올리면 이미 나간 영상을
    실패로 보고하게 되고, 부르는 쪽이 다시 올린다.
    """
    try:
        with open(_receipt_path(video_path), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    except OSError as exc:
        logger.warning(f"could not record the upload receipt: {type(exc).__name__}")


def _disclosure(platforms) -> dict:
    """
    AI 로 만든 영상이라고 밝히는 값들.

    플랫폼마다 항목 이름이 다르고, 밝히지 않으면 나중에 계정 쪽에서 문제가 된다.
    여기서 만드는 영상은 전부 합성 음성과 생성된 문장이므로 끌 수 있게 두지 않는다.
    YouTube 쪽은 전송 계층이 언제나 붙이므로 여기서 다시 넣지 않는다.
    """
    return {"is_aigc": "true"} if "tiktok" in platforms else {}


def publish(
    channel: str,
    video_path: str,
    title: str,
    description: str = "",
    tags=(),
) -> PublishResult:
    """
    영상 하나를 채널의 계정들에 올린다.

    예외를 올리지 않는다. 업로드가 안 됐다고 이미 만들어 둔 영상까지 실패로
    만들 이유가 없고, 매일 도는 자동화가 거기서 멈춰서도 안 된다.
    """
    target = resolve_target(channel)
    if not target:
        return PublishResult(ok=False, skipped="not configured")

    if not os.path.exists(video_path):
        return PublishResult(ok=False, error="the video file is gone")

    if already_published(video_path):
        # 버튼을 두 번 누르거나 봇을 다시 켜면 같은 영상이 또 올라간다.
        logger.info(f"already published, skipping: {os.path.basename(video_path)}")
        return PublishResult(ok=True, skipped="already published")

    title = " ".join(str(title or "").split())[:MAX_TITLE_LENGTH]
    description = str(description or "").strip()[:MAX_DESCRIPTION_LENGTH]
    if not title:
        # YouTube 는 제목이 없으면 받지 않는다.
        return PublishResult(ok=False, error="a title is required")

    youtube_extra = None
    if "youtube" in target.platforms:
        youtube_extra = {
            "youtube_title": title,
            "youtube_description": description or title,
            "tags": [str(tag) for tag in tags][:10],
            "privacyStatus": target.youtube_privacy,
        }

    logger.info(
        f"publishing to {target.channel}: {', '.join(target.platforms)} "
        f"as {target.profile}"
    )
    response = upload_post.cross_post_video(
        video_path=video_path,
        title=title,
        platforms=list(target.platforms),
        youtube_extra=youtube_extra,
        username=target.profile,
        extra_fields=_disclosure(target.platforms),
    )
    if not isinstance(response, dict):
        response = {"success": False, "error": "upload-post returned nothing usable"}

    if not response.get("success"):
        error = str(
            response.get("error") or response.get("message") or "unknown upload error"
        )
        logger.warning(f"publish failed: {error[:200]}")
        return PublishResult(ok=False, platforms=target.platforms, error=error[:500])

    _write_receipt(
        video_path,
        {
            "channel": target.channel,
            "profile": target.profile,
            "platforms": list(target.platforms),
            "title": title,
            "request_id": str(response.get("request_id", "")),
        },
    )
    return PublishResult(ok=True, platforms=target.platforms, detail=response)
