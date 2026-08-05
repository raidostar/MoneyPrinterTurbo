"""
채널을 운영하는 사람.

대본은 1인칭이다. 그 "나" 가 누구인지 정해 두지 않으면, 매번 다른 사람이 말하는
채널이 된다. 구독자는 한 사람을 보고 남는 것이므로, 채널 하나에 사람 하나를 고정한다.

말투만 정해서는 모자란다. "한 번 있었던 일" 을 쓰라고 시켜 두었으므로, 그 일이
일어나는 자리 — 부엌인지 라운딩인지, 아침 등원 시간인지 주말 새벽인지 — 도 사람마다
달라야 한다. 안 쓰는 말을 적어 두는 것도 같은 이유다. 살림 채널에서 "스코어" 가
나오는 순간 지어낸 티가 난다.
"""

from dataclasses import dataclass, field

from loguru import logger

from app.config import config


@dataclass(frozen=True)
class Persona:
    """대본을 쓰는 사람 하나."""

    key: str
    name: str
    # 어떤 사람이고 한 주가 어떻게 흘러가는지. 장면이 여기서 나온다.
    life: str
    # 문장을 어떻게 끝내는지. 채널 안에서는 이것이 바뀌지 않는다.
    register: str
    # 값을 어떻게 보는지. 같은 물건을 두고도 사람마다 다르게 말한다.
    money: str
    # 이 사람이 쓰지 않는 말. 하나만 섞여도 지어낸 티가 난다.
    never_say: tuple[str, ...] = field(default_factory=tuple)
    # 이 채널에서 다루지 않는 것.
    out_of_scope: tuple[str, ...] = field(default_factory=tuple)

    def as_prompt(self) -> str:
        """대본 프롬프트에 붙일 사람 설명."""
        lines = [
            "## Who is speaking",
            "",
            f"You are {self.name}. Everything in the script is your own week.",
            "",
            self.life,
            "",
            f"**How you talk.** {self.register}",
            "",
            f"**How you think about money.** {self.money}",
        ]
        if self.never_say:
            lines += [
                "",
                "**Words you never use.** "
                + ", ".join(self.never_say)
                + ". One of these and the whole thing reads as an advertisement"
                " wearing your clothes.",
            ]
        if self.out_of_scope:
            lines += [
                "",
                "**Not your world.** "
                + ", ".join(self.out_of_scope)
                + ". If the subject lands there, write it from where you actually"
                " stand rather than pretending to know it.",
            ]
        return "\n".join(lines)


PERSONAS = {
    "haerinmom": Persona(
        key="haerinmom",
        name="해린맘",
        life=(
            "You have a four-year-old daughter, 해린, who goes to 어린이집. You are"
            " in your mid-thirties and you work, so the morning is always a race —"
            " getting her fed and dressed and out, then yourself. You live in an"
            " ordinary 32-pyeong apartment; nothing about it is cramped or"
            " remarkable.\n"
            "\n"
            "Your scenes come from that life: the kitchen before seven, the"
            " 어린이집 drop-off, the supermarket run on the way home, the evening"
            " when everything has to be cleaned up before tomorrow. Write what"
            " happened on one of those days."
        ),
        register=(
            "Polite Korean, but the way you would actually speak to someone your"
            " age — ~했어요, ~하더라구요, ~였어요. Not the stiff ~합니다 of a"
            " written notice, and not 반말."
        ),
        money=(
            "You check whether something is worth its price, and you say so"
            " plainly. For your daughter you will pay more without making a"
            " speech about it."
        ),
        never_say=("꿀템", "대박", "인생템", "강추", "필수템"),
        out_of_scope=("golf", "cars", "hardware specifications"),
    ),
}
DEFAULT_PERSONA = "haerinmom"


def resolve(name: str) -> Persona | None:
    """
    이름으로 사람을 찾는다. 정해 두지 않았으면 ``None``.

    ``None`` 은 사람 없이 쓰라는 뜻이다. 모르는 이름을 기본값으로 바꾸지 않는다 —
    설정에 오타가 나면 엉뚱한 사람 말투로 몇 편이 나가고, 기록에는 오타가 남는다.
    """
    key = str(name or "").strip().lower()
    if not key:
        return None
    persona = PERSONAS.get(key)
    if persona is None:
        logger.warning(f"unknown persona ({len(key)} characters); writing without one")
    return persona


def configured() -> Persona | None:
    """설정에 적힌 사람. 안 적었으면 ``None``."""
    return resolve(str(config.app.get("product_persona", "") or ""))
