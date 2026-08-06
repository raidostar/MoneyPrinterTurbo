"""
첫 화면.

쇼츠는 피드에서 이미 재생된 채로 뜬다. 시청자가 하는 결정은 볼지 말지가 아니라
넘길지 말지고, 그 판단은 첫 한두 초에 끝난다. 그래서 맨 앞에 오는 클립만은 아무거나
와서는 안 된다.

검색어를 대본 순서에 맞춰도 이 자리는 해결되지 않는다. 검색어가 맞아도 받아 온 것이
다를 수 있어서다 — "baby exiting pool" 로 찾은 것이 사람 없는 빈 수영장이었고, 그날
영상의 첫 두 초는 초록색 물만 나왔다. 무엇을 찾았는지가 아니라 무엇을 받았는지로
골라야 하고, 받은 것이 무엇인지는 제공자가 붙여 둔 설명에 적혀 있다.
"""

import json
import re
from urllib.parse import urlparse

from loguru import logger

# 설명을 읽을 수 있는 제공자. 주소 안에 사람이 쓴 제목이 들어 있다:
# https://www.pexels.com/video/child-on-a-bed-only-wearing-a-diaper-8425000/
_DESCRIPTION_PATHS = {"pexels.com": "/video/"}
# 고를 후보 수. 더 늘려도 첫 칸은 하나뿐이라 판단만 길어진다.
MAX_CANDIDATES = 12
# 설명 길이 상한. 주소에서 오는 값이라 길이를 믿을 수 없고, 그대로 프롬프트에 넣으면
# 긴 주소 하나가 판단할 내용을 밀어낸다.
MAX_DESCRIPTION_LENGTH = 120

_PROMPT = """You are choosing the very first shot of a short vertical video.

The video opens with this line of narration:
{first_line}

These stock clips were downloaded for it. Each line is an index and what the
clip actually shows:
{candidates}

Pick the one that works best as the opening shot. What matters, in order:

1. Something is happening to someone. A clip with a person or an animal in it
   beats empty scenery, even when the scenery matches the words more closely.
   A viewer scrolls past an empty room; they stop for a face.
2. It fits the narration line above.

Answer with only the index number. No other text."""


def describe(source_page: str) -> str:
    """
    공개 페이지 주소에 적힌 클립 설명. 못 읽으면 빈 문자열.

    제공자가 붙인 제목이 주소에 그대로 들어 있다. 우리가 찾은 검색어가 아니라
    실제로 받아 온 것이 무엇인지를 말해 주는 유일한 값이다.
    """
    try:
        parsed = urlparse(str(source_page or ""))
    except ValueError:
        return ""

    host = parsed.netloc.lower().removeprefix("www.")
    prefix = _DESCRIPTION_PATHS.get(host)
    if not prefix or not parsed.path.startswith(prefix):
        return ""

    slug = parsed.path[len(prefix) :].strip("/")
    # 끝의 숫자는 자산 번호다. 설명이 아니라 식별자이므로 뺀다.
    slug = re.sub(r"-?\d+$", "", slug)
    words = [word for word in slug.split("-") if word]
    return " ".join(words)[:MAX_DESCRIPTION_LENGTH]


def _candidates(sources: list[dict]) -> list[tuple[str, str]]:
    """(파일 이름, 설명) 목록. 설명을 못 읽은 것은 뺀다."""
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        name = str(source.get("local_file", "") or "")
        description = describe(source.get("source_page", ""))
        if not name or not description or name in seen:
            continue
        seen.add(name)
        found.append((name, description))
        if len(found) >= MAX_CANDIDATES:
            break
    return found


def pick(first_line: str, sources: list[dict], ask) -> str:
    """
    맨 앞에 둘 클립의 파일 이름. 고르지 못하면 빈 문자열.

    고르지 못하는 것은 실패가 아니다. 그때는 부르는 쪽이 원래 순서를 그대로 쓰면
    되고, 첫 화면 하나 때문에 이미 만들어 둔 영상을 버릴 이유가 없다.
    """
    line = str(first_line or "").strip()
    candidates = _candidates(sources if isinstance(sources, list) else [])
    if not line or len(candidates) < 2:
        return ""

    listed = "\n".join(
        f"{index}. {description}" for index, (_, description) in enumerate(candidates)
    )
    try:
        answer = ask(_PROMPT.format(first_line=line, candidates=listed))
    except Exception as exc:
        logger.warning(f"could not choose an opening clip: {type(exc).__name__}")
        return ""

    chosen = _parse_index(answer, len(candidates))
    if chosen is None:
        logger.warning("no usable answer for the opening clip; keeping the order")
        return ""

    name, description = candidates[chosen]
    logger.info(f"opening clip: {description}")
    return name


def _parse_index(answer, limit: int) -> int | None:
    """
    답에서 고른 번호를 읽는다. 못 읽으면 ``None``.

    모델은 숫자만 답하라고 해도 문장을 붙이거나 JSON 으로 감싸 온다. 범위를 벗어난
    번호는 고르지 못한 것으로 본다 — 없는 자리를 0번으로 접으면 아무 근거 없이
    첫 클립이 정답이 된다.
    """
    text = str(answer or "").strip()
    if text.startswith("Error: "):
        logger.warning("could not choose an opening clip: provider error")
        return None
    try:
        text = str(json.loads(text))
    except (ValueError, TypeError):
        pass

    # 부호까지 읽는다. "-1" 에서 숫자만 떼면 1번을 고른 것이 되어, 못 골랐다는
    # 답이 멀쩡한 선택으로 바뀐다.
    match = re.search(r"-?\d+", text)
    if not match:
        return None
    index = int(match.group())
    return index if 0 <= index < limit else None
