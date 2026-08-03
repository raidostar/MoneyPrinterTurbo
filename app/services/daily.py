"""
매일 소재를 골라 온다.

수집기는 조건에 맞는 글을 전부 돌려준다. 여기서는 그중 아직 다루지 않은 것만
남긴다. 어제 만든 것을 오늘 또 만들면 채널이 같은 말을 반복한다.
"""

import json
import os
import tempfile
import time
from dataclasses import dataclass

from loguru import logger

from app.services.sources import hackernews
from app.services.sources.base import SourceItem
from app.utils import utils

SEEN_FILE = "daily_seen.json"
# 다룬 소재를 기억하는 기간. 이보다 오래된 것은 지운다. 반년 전에 한 번 나왔던
# 글이 다시 화제가 되면 그건 다시 다룰 만하다.
SEEN_TTL_DAYS = 45
# 기록이 무한정 자라지 않게 한다. 하루 몇 건 규모에서는 넉넉하다.
MAX_SEEN_ENTRIES = 2000
MAX_SEEN_BYTES = 512 * 1024


@dataclass(frozen=True)
class DailyPick:
    """오늘 다룰 후보 하나."""

    item: SourceItem
    reason: str = ""


def _seen_path() -> str:
    return os.path.join(utils.storage_dir(create=True), SEEN_FILE)


def _key(item: SourceItem) -> str:
    return f"{item.source}:{item.item_id}"


def load_seen() -> dict[str, float]:
    """
    다룬 소재의 기록을 읽는다. 읽을 수 없으면 빈 기록으로 시작한다.

    기록이 깨졌다고 오늘 작업을 멈출 이유는 없다. 최악의 결과는 어제 것을 한 번
    더 다루는 것이고, 그건 작업이 아예 안 도는 것보다 낫다.
    """
    path = _seen_path()
    try:
        if os.path.getsize(path) > MAX_SEEN_BYTES:
            logger.warning("the seen-items record is too large, starting over")
            return {}
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}

    if not isinstance(data, dict):
        logger.warning("the seen-items record is not an object, starting over")
        return {}

    cutoff = time.time() - SEEN_TTL_DAYS * 86400
    fresh = {}
    for key, when in data.items():
        if not isinstance(key, str):
            continue
        try:
            stamp = float(when)
        except (TypeError, ValueError):
            continue
        if stamp >= cutoff:
            fresh[key] = stamp
    return fresh


def save_seen(seen: dict[str, float]) -> None:
    """
    기록을 저장한다. 실패해도 예외를 올리지 않는다.

    임시 파일에 쓰고 바꿔치기한다. 그러지 않으면 쓰는 도중에 멈췄을 때 반쯤 쓰인
    파일이 남고, 다음 실행이 그걸 읽지 못해 기록을 통째로 잃는다.
    """
    # 오래된 것부터 버려 개수를 맞춘다.
    if len(seen) > MAX_SEEN_ENTRIES:
        newest = sorted(seen.items(), key=lambda pair: pair[1], reverse=True)
        seen = dict(newest[:MAX_SEEN_ENTRIES])

    path = _seen_path()
    handle = None
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".daily-seen-", suffix=".json", dir=os.path.dirname(path)
        )
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        json.dump(seen, handle)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temporary, path)
        temporary = ""
    except OSError as exc:
        logger.warning(f"could not save the seen-items record: {type(exc).__name__}")
    finally:
        if handle is not None:
            handle.close()
        if temporary and os.path.exists(temporary):
            os.remove(temporary)


def pick_items(
    limit: int = 3,
    min_points: int = 100,
    within_hours: int = 24,
    tags: str = "show_hn",
) -> list[DailyPick]:
    """
    오늘 다룰 후보를 고른다. 없으면 빈 목록.

    기록에 남기지는 않는다. 후보를 보여 준 것과 실제로 영상을 만든 것은 다르고,
    보기만 하고 넘어간 소재는 내일 다시 후보가 되어야 한다.
    """
    items = hackernews.fetch_items(
        min_points=min_points, within_hours=within_hours, limit=50, tags=tags
    )
    if not items:
        return []

    seen = load_seen()
    picks = []
    for item in items:
        if _key(item) in seen:
            continue
        picks.append(DailyPick(item=item, reason=f"{item.points} points"))
        if len(picks) >= max(1, int(limit or 1)):
            break

    logger.info(f"picked {len(picks)} of {len(items)} items for today")
    return picks


def mark_used(item: SourceItem) -> None:
    """영상까지 만든 소재를 기록한다."""
    seen = load_seen()
    seen[_key(item)] = time.time()
    save_seen(seen)
