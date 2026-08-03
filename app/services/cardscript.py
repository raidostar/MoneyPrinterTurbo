"""
소재 하나를 카드뉴스 한 편으로 바꾼다.

수집기(`sources`)와 렌더러(`cardnews`) 사이를 잇는다. 어느 쪽도 상대를 모르게 두고,
여기서만 둘의 모양을 안다.
"""

from dataclasses import dataclass

from loguru import logger

from app.services import llm
from app.services import cardnews
from app.services.cardnews import Card, Score
from app.services.sources import enrich, repo
from app.services.sources.base import SourceItem

# 점수판 나레이션. 막대를 눈으로 읽는 동안 귀로 들을 말이다.
SCORE_NARRATION_LEAD = "정리하면"
MIN_SCORES_TO_SHOW = 2
SCORE_CARD_TITLE = "점수"
# 점수판은 한국어로만 만든다. 항목 이름과 숫자 읽기가 한국어라, 다른 언어 대본에
# 붙이면 마지막 장만 한국어로 나온다.
SCORE_LANGUAGE_PREFIX = "ko"


def _is_korean(language: str) -> bool:
    return str(language or "").strip().lower().startswith(SCORE_LANGUAGE_PREFIX)


@dataclass(frozen=True)
class CardScript:
    """
    카드와 카드별 나레이션. 둘의 길이는 항상 같다.

    말로만 두면 어긋난 값이 들어와도 렌더링 직전까지 드러나지 않는다. 카드 하나가
    남거나 모자라면 그 지점부터 화면과 소리가 밀리고, 만들어진 영상과 기록된
    카드 수도 달라진다. 만들 때 짧은 쪽에 맞추고 렌더러의 상한도 여기서 건다.
    """

    cards: tuple[Card, ...]
    narrations: tuple[str, ...]

    def __post_init__(self):
        paired = list(zip(self.cards, self.narrations))[: cardnews.MAX_CARDS]
        object.__setattr__(self, "cards", tuple(card for card, _ in paired))
        object.__setattr__(self, "narrations", tuple(text for _, text in paired))

    @property
    def narration_text(self) -> str:
        """전체 나레이션. 길이 가늠이나 미리듣기에 쓴다."""
        return " ".join(self.narrations)


def _footer(item: SourceItem) -> str:
    """
    출처 줄. 어디서 왔고 반응이 어땠는지를 남긴다.

    남의 프로젝트를 소개하는 채널이라 출처 표기는 예의가 아니라 최소 조건이다.
    """
    parts = [_SOURCE_LABELS.get(item.source, item.source)]
    if item.points:
        parts.append(f"{item.points} points")
    link = item.url or item.discussion_url
    if link:
        parts.append(link.split("://", 1)[-1])
    return " · ".join(part for part in parts if part)


_SOURCE_LABELS = {"hackernews": "Hacker News"}


def _score_card(item: SourceItem, footer: str, language: str) -> tuple[Card, str] | None:
    """
    마무리 점수 카드와 그 나레이션. 잴 것이 모자라면 ``None``.

    완성도는 저장소를 세서 넣고 나머지는 모델이 매긴다. 완성도까지 모델에게
    물으면 무엇을 보든 4점이 나와, 매 영상이 똑같이 끝난다.

    한 칸만 남으면 점수판을 만들지 않는다. 비교할 것이 없는 막대 하나는 판정이
    아니라 장식이다.

    한국어 대본에만 붙인다. 항목 이름과 숫자 읽기가 한국어로 박혀 있어, 다른
    언어 대본에 붙이면 마지막 장만 한국어로 나온다. 확인할 수 없는 번역을 지어
    붙이느니 그 언어에서는 점수판 없이 끝내는 편이 낫다.
    """
    if not _is_korean(language):
        return None

    judged = llm.judge_project(
        title=item.title, url=item.url, body_text=item.text, language=language
    )

    scores = []
    measured = repo.maturity(repo.fetch_signals(item.url))
    if measured:
        scores.append(
            Score(label=llm.JUDGEMENT_LABELS["maturity"], value=measured[0], reason=measured[1])
        )
    for key in llm.JUDGEMENT_KEYS:
        if key in judged:
            value, reason = judged[key]
            scores.append(Score(label=llm.JUDGEMENT_LABELS[key], value=value, reason=reason))

    if len(scores) < MIN_SCORES_TO_SHOW:
        logger.info(f"not enough to score: {item.source}:{item.item_id}")
        return None

    spoken = ", ".join(f"{score.label} {_spoken(score.value)}점" for score in scores)
    card = Card(title=SCORE_CARD_TITLE, scores=tuple(scores), footer=footer)
    return card, f"{SCORE_NARRATION_LEAD} {spoken}입니다."


_SPOKEN_NUMBERS = {1: "일", 2: "이", 3: "삼", 4: "사", 5: "오"}


def _spoken(value: int) -> str:
    """숫자를 한글로. 합성기는 숫자를 밋밋하게 읽는다."""
    return _SPOKEN_NUMBERS.get(value, str(value))


def build_card_script(item: SourceItem, language: str = "ko-KR") -> CardScript | None:
    """
    소재에서 카드 대본을 만든다. 쓸 만한 게 안 나오면 ``None``.

    실패를 예외로 올리지 않는 이유는 위와 같다. 하루치 소재 중 하나가 카드가 되지
    않았다고 나머지까지 멈출 이유가 없다.

    본문은 여기서 채운다. 후보를 훑을 때 미리 채우면 보여 주기만 하고 넘어간
    소재까지 매번 남의 서버에 요청을 보내게 된다. 실제로 카드를 만들 때만 읽는다.
    """
    item = enrich.with_body(item)
    entries = llm.generate_card_script(
        title=item.title,
        url=item.url,
        source=item.source,
        points=item.points,
        body_text=item.text,
        language=language,
    )
    if not entries:
        logger.warning(f"no card script for {item.source}:{item.item_id}")
        return None

    footer = _footer(item)
    # 점수판은 대본을 다 쓴 뒤에 붙인다. 모델에게 점수까지 맡기면 무엇을 보든
    # 4점이 나오고, 그러면 매 영상이 똑같이 끝난다.
    scored = _score_card(item, footer, language)

    cards = []
    narrations = []
    for index, entry in enumerate(entries, start=1):
        cards.append(
            Card(
                index_label=f"{index:02d}",
                title=entry["title"],
                body=tuple(entry.get("bullets") or ()),
                # 출처는 첫 장과 마지막 장에만 둔다. 매 장에 반복하면 읽는 데
                # 방해가 되고, 없으면 어디서 온 이야기인지 알 수 없다.
                footer=footer if index == 1 or (index == len(entries) and not scored) else "",
            )
        )
        narrations.append(entry["narration"])

    if scored:
        card, narration = scored
        cards.append(
            Card(
                index_label=f"{len(cards) + 1:02d}",
                title=card.title,
                scores=card.scores,
                footer=card.footer,
            )
        )
        narrations.append(narration)

    return CardScript(cards=tuple(cards), narrations=tuple(narrations))
