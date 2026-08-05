"""
만든 영상을 잠깐 올려 두고 주소를 내준다.

인스타그램과 스레드는 파일을 받지 않는다. 공개 주소를 주면 그쪽 서버가 그 주소로
가져간다. 영상은 이 기계에만 있고 이 기계는 공유기 뒤에 있어서, 그대로는 닿을 수
없다.

버킷은 열어 두지 않는다. 시간이 지나면 죽는 주소를 그때그때 만들어 넘긴다 —
열어 두면 그 주소를 아는 누구나, 언제까지나 가져갈 수 있다. 올리고 게시가 끝나면
지운다.
"""

import mimetypes
import os
import re
from dataclasses import dataclass

from loguru import logger

from app.config import config
from app.utils import file_security, utils

# 주소가 살아 있는 시간. 인스타그램은 컨테이너를 만들 때 영상을 가져가고, 긴
# 영상은 그 처리에 몇 분이 걸린다. 넉넉하게 두되 하루씩 열어 두지는 않는다.
URL_LIFETIME_SECONDS = 60 * 60
# 올릴 수 있는 최대 크기. 쇼츠 한 편은 십수 MB 다. 이보다 큰 것이 올라오면 무언가
# 잘못된 것이고, 전송비와 시간만 쓴다.
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
R2_ENDPOINT = "https://{account_id}.r2.cloudflarestorage.com"
# R2 는 지역을 쓰지 않지만 S3 클라이언트가 값을 요구한다.
R2_REGION = "auto"


@dataclass(frozen=True)
class StoredFile:
    """올려 둔 파일 하나. ``url`` 은 시간이 지나면 죽는다."""

    key: str
    url: str


def _settings() -> dict:
    return {
        "account_id": str(config.app.get("r2_account_id", "") or "").strip(),
        "access_key_id": str(config.app.get("r2_access_key_id", "") or "").strip(),
        "secret_access_key": str(
            config.app.get("r2_secret_access_key", "") or ""
        ).strip(),
        "bucket": str(config.app.get("r2_bucket", "") or "").strip(),
    }


def is_configured() -> bool:
    """올릴 곳이 정해져 있는지."""
    return all(_settings().values())


def _client():
    """
    S3 클라이언트. 만들지 못하면 ``None``.

    boto3 는 선택 의존성이다. 올리지 않는 사람에게까지 지울 이유가 없어, 없으면
    무엇을 설치해야 하는지 알리고 넘어간다.
    """
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        logger.warning(
            "boto3 is not installed; run `uv sync --extra storage` to publish videos"
        )
        return None

    values = _settings()
    try:
        return boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT.format(account_id=values["account_id"]),
            aws_access_key_id=values["access_key_id"],
            aws_secret_access_key=values["secret_access_key"],
            region_name=R2_REGION,
            # R2 는 SigV4 만 받는다. 재시도는 여기서 정해 둔다 — 기본값은 느린
            # 회선에서 큰 파일을 여러 번 다시 올린다.
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )
    except Exception as exc:
        logger.warning(f"could not create the storage client: {type(exc).__name__}")
        return None


def key_for(local_path: str) -> str:
    """
    올릴 때 쓸 이름. 작업 폴더와 파일 이름을 이어 붙인다.

    작업마다 폴더가 다르므로 이것만으로 겹치지 않고, 나중에 버킷을 열어 봤을 때
    어느 작업의 것인지 알 수 있다.

    이름에 쓸 수 있는 글자만 남긴다. 경로에서 오는 값이라 `..` 이나 슬래시가 섞이면
    의도한 것과 다른 자리에 올라간다.
    """
    parent = os.path.basename(os.path.dirname(local_path))
    name = os.path.basename(local_path)
    parts = [re.sub(r"[^A-Za-z0-9._-]", "", part) for part in (parent, name)]
    return "/".join(part for part in parts if part and part not in {".", ".."})


def put(local_path: str, key: str) -> StoredFile | None:
    """
    파일 하나를 올리고 가져갈 수 있는 주소를 돌려준다. 못 올리면 ``None``.

    예외를 올리지 않는다. 올리지 못했다고 이미 만들어 둔 영상까지 버릴 이유가
    없고, 매일 도는 자동화가 거기서 멈춰서도 안 된다.
    """
    if not is_configured():
        logger.info("storage is not configured; skipping the upload")
        return None

    # 올릴 것은 우리가 만든 영상뿐이다. 경로를 그대로 믿으면 부르는 쪽의 실수 하나로
    # 이 기계의 아무 파일이나 남의 서버에 올라간다. 심볼릭 링크로 밖을 가리키는
    # 경우까지 공용 검사기가 잡는다.
    try:
        local_path = file_security.resolve_path_within_directory(
            utils.task_dir(), local_path
        )
    except (ValueError, OSError) as exc:
        logger.warning(f"refusing to upload from outside the task directory: {exc}")
        return None

    size = os.path.getsize(local_path)
    if size > MAX_UPLOAD_BYTES:
        logger.warning(f"refusing to upload {size} bytes; over the limit")
        return None

    client = _client()
    if client is None:
        return None

    bucket = _settings()["bucket"]
    # 종류를 밝히지 않으면 받는 쪽이 내려받기로 다루고, 그러면 영상으로 안 읽는다.
    content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    try:
        client.upload_file(
            local_path, bucket, key, ExtraArgs={"ContentType": content_type}
        )
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=URL_LIFETIME_SECONDS,
        )
    except Exception as exc:
        logger.warning(f"could not upload the video: {type(exc).__name__}")
        return None

    logger.info(f"uploaded {size} bytes as {key}")
    return StoredFile(key=key, url=url)


def remove(key: str) -> bool:
    """
    올려 둔 파일을 지운다. 지웠으면 ``True``.

    게시가 끝나면 남겨 둘 이유가 없다. 지우지 못해도 게시 자체는 끝난 일이므로
    실패로 만들지 않는다 — 주소는 어차피 시간이 지나면 죽는다.
    """
    if not key or not is_configured():
        return False

    client = _client()
    if client is None:
        return False

    try:
        client.delete_object(Bucket=_settings()["bucket"], Key=key)
    except Exception as exc:
        logger.warning(f"could not remove the uploaded video: {type(exc).__name__}")
        return False

    logger.info(f"removed {key} from storage")
    return True
