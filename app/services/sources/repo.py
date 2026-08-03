"""
GitHub 저장소에서 관찰할 수 있는 것들을 모은다.

README 는 만든 사람이 쓴 글이라 "잘 만들었다" 는 말이 그대로 들어 있다. 그 말을
그대로 옮기면 소개가 아니라 광고가 된다. 테스트가 있는지, CI 가 도는지, 언제
시작해서 언제까지 손댔는지는 저장소를 보면 확인할 수 있는 사실이다.

이 값들로 완성도 점수를 계산한다. 모델에게 "완성도를 매겨 봐" 라고 물으면 무엇을
보든 4점이 나온다. 셀 수 있는 것은 세고, 모델에게는 셀 수 없는 것만 맡긴다.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger

from app.services.sources import enrich

REPO_API = "https://api.github.com/repos/{owner}/{repo}"
# 맨 위 칸만 본다. `recursive=1` 은 파일이 많은 저장소에서 수 MB 가 되고, 상한에
# 걸려 잘리면 파싱이 실패해 "아무것도 없다" 로 읽힌다. 못 본 것을 없는 것으로
# 세면 점수가 그대로 틀린다.
TREE_API = "https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD"
WORKFLOWS_API = (
    "https://api.github.com/repos/{owner}/{repo}/contents/.github/workflows"
)
GITHUB_ACCEPT = "application/vnd.github+json"

MAX_TREE_BYTES = 1024 * 1024
MAX_TREE_ENTRIES = 5_000

TEST_DIR = re.compile(r"^(tests?|spec|__tests__)$", re.I)
# 맨 위에 테스트 파일을 늘어놓는 저장소도 있다. `_ut` 는 unit test 의 줄임말로
# C++ 쪽에서 흔하다.
TEST_FILE = re.compile(r"^test_|_test\.|_ut\.|\.spec\.|\.test\.", re.I)
DOC_DIR = re.compile(r"^(docs?|book|website)$", re.I)
# 이게 있으면 소스를 내려받아 직접 빌드하지 않아도 된다.
PACKAGED = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "cargo.toml",
        "package.json",
        "go.mod",
        "dockerfile",
        "flake.nix",
        "formula.rb",
    }
)

# 점수 범위. 카드에 막대로 그린다.
MIN_SCORE = 1
MAX_SCORE = 5


@dataclass(frozen=True)
class RepoSignals:
    """저장소에서 확인한 사실들. 못 본 것은 ``None`` 이 아니라 기본값이다."""

    owner: str = ""
    name: str = ""
    stars: int = 0
    language: str = ""
    license_name: str = ""
    description: str = ""
    open_issues: int = 0
    archived: bool = False
    age_days: int = 0
    idle_days: int = 0
    has_tests: bool = False
    has_ci: bool = False
    has_docs: bool = False
    is_packaged: bool = False
    file_count: int = 0
    # 저장소를 못 읽었으면 여기 있는 값은 전부 기본값이다. 점수를 매기기 전에
    # 이걸 봐야, 못 본 것을 "없다" 로 읽지 않는다.
    seen: bool = False
    # 파일 목록까지 봤는지. 저장소 정보는 읽혔는데 목록만 못 읽는 경우가 있고,
    # 그때 "테스트 없음" 이라고 하면 통신 사정이 점수가 된다.
    tree_seen: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}" if self.owner else ""


def _days_since(stamp: str) -> int:
    """ISO 시각에서 지금까지의 일수. 읽을 수 없으면 ``0``."""
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - moment).days)


def _count(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _read_tree(owner: str, repo: str) -> dict:
    """
    저장소 맨 위 칸에서 볼 것만 추린다. 못 읽으면 빈 딕셔너리.

    ``tree_seen`` 이 빠져 있으면 아무것도 확인하지 못한 것이다. 이걸 안 두면
    읽기에 실패한 저장소와 정말 테스트가 없는 저장소가 같은 값이 되고, 점수가
    통신 사정에 따라 달라진다.
    """
    body = enrich.get_json(
        TREE_API.format(owner=owner, repo=repo),
        accept=GITHUB_ACCEPT,
        limit=MAX_TREE_BYTES,
    )
    entries = body.get("tree") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        return {}

    directories = []
    files = []
    for entry in entries[:MAX_TREE_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("path", ""))
        if not name:
            continue
        (directories if entry.get("type") == "tree" else files).append(name)

    has_ci = ".github" in directories and bool(
        # 폴더만 있고 안이 비어 있을 수 있다. 그건 CI 가 도는 것이 아니다.
        enrich.get_json(
            WORKFLOWS_API.format(owner=owner, repo=repo),
            accept=GITHUB_ACCEPT,
            limit=MAX_TREE_BYTES,
        )
    )

    return {
        "has_tests": any(TEST_DIR.match(name) for name in directories)
        or any(TEST_FILE.search(name) for name in files),
        "has_ci": has_ci,
        "has_docs": any(DOC_DIR.match(name) for name in directories),
        "is_packaged": any(name.lower() in PACKAGED for name in files),
        "file_count": len(files) + len(directories),
        "tree_seen": True,
    }


def fetch_signals(url: str) -> RepoSignals:
    """
    주소가 GitHub 저장소면 그 저장소를 살펴본다. 아니거나 못 읽으면 빈 값.

    예외를 올리지 않는다. 저장소를 못 봤다고 그 소재로 영상을 못 만들 이유는
    없고, 그때는 셀 수 있는 것이 없으니 점수를 매기지 않으면 된다.
    """
    repository = enrich.github_repo(url)
    if not repository:
        return RepoSignals()

    owner, repo = repository
    body = enrich.get_json(REPO_API.format(owner=owner, repo=repo), accept=GITHUB_ACCEPT)
    if not isinstance(body, dict) or not body.get("full_name"):
        logger.info(f"could not read the repository: {owner}/{repo}")
        return RepoSignals()

    license_info = body.get("license")
    license_name = ""
    if isinstance(license_info, dict):
        spdx = str(license_info.get("spdx_id", "") or "")
        # NOASSERTION 은 라이선스 파일은 있는데 무엇인지 알아보지 못한 경우다.
        # 정해진 라이선스가 붙은 것과 같이 볼 수 없다.
        license_name = "" if spdx in {"NOASSERTION", "NONE"} else spdx

    return RepoSignals(
        owner=owner,
        name=repo,
        stars=_count(body.get("stargazers_count")),
        language=str(body.get("language") or ""),
        license_name=license_name,
        description=str(body.get("description") or "")[:300],
        open_issues=_count(body.get("open_issues_count")),
        archived=bool(body.get("archived")),
        age_days=_days_since(body.get("created_at", "")),
        idle_days=_days_since(body.get("pushed_at", "")),
        seen=True,
        **_read_tree(owner, repo),
    )


def maturity(signals: RepoSignals) -> tuple[int, str] | None:
    """
    완성도 점수와 그 근거. 저장소를 못 봤으면 ``None``.

    모델에게 묻지 않고 여기서 센다. "완성도를 매겨 봐" 라고 물으면 테스트가 없는
    3주차 프로젝트에도 4점이 나오고, 그러면 매 영상이 똑같이 끝난다.

    점수가 낮다고 나쁜 프로젝트라는 뜻이 아니다. 지금 남에게 넘길 수 있는
    상태냐를 본다 — 주말에 만든 것이 3점이면 그건 정확한 설명이다.
    """
    if not signals.seen or not signals.tree_seen:
        # 파일 목록을 못 봤으면 셀 수 있는 것이 절반뿐이다. 그 절반으로 매긴
        # 점수는 통신 사정을 점수로 바꾼 것이다.
        return None

    score = MIN_SCORE
    # 점수를 깎은 것과 올린 것을 따로 모은다. 자리가 모자랄 때는 깎은 쪽을
    # 남긴다 — 시청자가 알아야 할 것은 왜 이 점수가 더 높지 않은가다.
    limits = []
    haves = []

    if signals.has_tests:
        score += 1
        haves.append("테스트 있음")
    else:
        limits.append("테스트 없음")

    if signals.has_ci:
        score += 1
        haves.append("CI 돎")
    else:
        limits.append("CI 없음")

    if signals.license_name:
        score += 1
        haves.append(signals.license_name)
    else:
        # 라이선스가 없으면 갖다 쓸 수 없다. 코드가 아무리 좋아도 그렇다.
        limits.append("라이선스 불명")

    # 한 달 넘게 손을 안 댔으면 지금 상태가 마지막 상태다.
    if signals.archived:
        limits.append("보관됨")
    elif signals.idle_days <= 30:
        score += 1
    else:
        limits.append(f"{signals.idle_days}일째 조용")

    # 만든 지 얼마 안 됐으면 위의 신호가 다 있어도 아직 검증된 물건은 아니다.
    if signals.age_days < 30:
        score = min(score, MAX_SCORE - 1)
        limits.append(f"{max(1, signals.age_days // 7)}주차")

    # 신호를 더 넣으면 여기서 MAX_SCORE 를 넘는다. 범위는 시험이 지킨다.
    return score, " · ".join((limits + haves)[:3])


def summary_line(signals: RepoSignals) -> str:
    """후보 목록에 붙이는 한 줄. 못 본 저장소면 빈 문자열."""
    if not signals.seen:
        return ""
    parts = [f"★{signals.stars}"]
    if signals.language:
        parts.append(signals.language)
    if signals.idle_days <= 1:
        parts.append("어제도 커밋")
    elif signals.idle_days <= 30:
        parts.append(f"{signals.idle_days}일 전 커밋")
    return " · ".join(parts)
