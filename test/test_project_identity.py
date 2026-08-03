"""프로젝트 이름."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_NAME = re.compile(r"MoneyPrinterTurbo", re.IGNORECASE)
# 바꾸면 안 되는 것들.
#  - harry0703/ : 갈라져 나온 원본. 출처 표기다.
#  - aff=, utm_term= : 원본의 제휴 코드. 우리 것으로 바꾸면 없는 코드를 쓰게 된다.
#  - raidostar/ : GitHub 저장소 주소. 저장소 이름을 바꾸면 기존 클론과 링크가
#    끊기므로 코드에서 결정할 일이 아니다. 저장소를 옮기면 이 예외를 지운다.
UPSTREAM = ("harry0703/", "raidostar/", "aff=", "utm_term=")
SEARCH_SUFFIXES = {".py", ".toml", ".yml", ".yaml", ".json", ".html"}
SKIP_DIRS = {".git", ".venv", "storage", ".redteam", "__pycache__", "node_modules", "docs"}


def _tracked_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in SEARCH_SUFFIXES or path.name.startswith("Dockerfile"):
            yield path


class TestTheOldNameIsGone(unittest.TestCase):
    def test_no_identity_string_still_says_the_old_name(self):
        """
        이름을 바꾸다 만 자리가 남으면, 화면·로그·컨테이너 경로가 서로 다른 이름을
        말한다. 업스트림을 가리키는 링크와 제휴 코드는 그대로 두어야 한다.
        """
        offenders = []
        for path in _tracked_files():
            if path.name == Path(__file__).name:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeError, OSError):
                continue
            for match in OLD_NAME.finditer(text):
                window = text[max(0, match.start() - 40) : match.end() + 20]
                if not any(marker in window for marker in UPSTREAM):
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(ROOT)}:{line}")

        self.assertEqual(offenders, [], f"옛 이름이 남아 있다: {offenders}")

    def test_the_upstream_credit_survives(self):
        """
        갈라져 나온 곳을 지우면 안 된다. 영상 파이프라인 대부분이 그쪽 코드다.
        """
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("harry0703/MoneyPrinterTurbo", readme)


if __name__ == "__main__":
    unittest.main()
