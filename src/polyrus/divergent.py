"""발산/창의 모드 (4.2) — 수렴 모드와 같은 부품, 극성만 뒤집어.

수렴(코드·사실): 검증기 왕, 낮은 온도, 하나의 검증된 답.
발산(네이밍·각도·카피): 폭 넓힘, 팔 더 많이·더 다르게, 클리셰 거부, 진실 오라클 대신
취향·적합성, *스프레드 보존*(단일 수렴 금지 → 사람이 큐레이션).

패스 금지의 창의 버전 = '뻔한 답 거부'. 첫 번째 떠오르는 안전한 답(영합 패스의 창의 쌍둥이)을
감지해 거부하고 덜 뻔한 영역을 강제한다. 정답 오라클이 없으므로 검증기로 게이팅하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from polyrus.models import ModelClient

# 서로 다른 각도로 팔을 강하게 탈상관(같은 뻔한 영역으로 수렴 못 하게).
_ANGLES = ["은유적", "도발적", "미니멀", "역발상", "감성적", "기술적", "유머러스"]


@dataclass
class Option:
    text: str
    angle: str


class DivergentGenerator:
    """발산 생성기. critic(반클리셰)을 주입 가능 — 기본은 LLM 비평가."""

    def __init__(
        self,
        model: ModelClient,
        *,
        critic: Callable[[str, str], bool] | None = None,
        temperature: float = 0.95,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.critic = critic or self._llm_critic

    def generate_options(self, brief: str, k: int = 5) -> list[Option]:
        """k개의 *서로 다른 각도* 후보. 높은 온도 + 각도 탈상관."""
        opts: list[Option] = []
        for i in range(max(1, k)):
            angle = _ANGLES[i % len(_ANGLES)]
            text = self.model.complete(
                f"# 브리프\n{brief}\n\n# 각도\n{angle} 관점에서, 한 줄로.",
                system="너는 창의 카피라이터다. 첫 번째로 떠오르는 안전하고 뻔한 답을 *거부*하고 덜 뻔한 영역을 노려라.",
                temperature=self.temperature,
            )
            opts.append(Option(text=text.strip(), angle=angle))
        return opts

    def curate(self, options: list[Option], brief: str) -> list[Option]:
        """반클리셰: 뻔한 것을 거부하되 *스프레드는 보존*(여러 개를 남겨 사람이 고르게)."""
        return [o for o in options if not self.critic(o.text, brief)]

    def diverge(self, brief: str, k: int = 5) -> list[Option]:
        """생성 → 반클리셰 큐레이션. 단일 답이 아니라 살아남은 스프레드를 돌려준다."""
        return self.curate(self.generate_options(brief, k=k), brief)

    def _llm_critic(self, text: str, brief: str) -> bool:
        v = self.model.complete(
            f"다음 안이 뻔한 클리셰인가? '뻔함' 또는 '참신' 한 단어로만.\n안: {text}\n브리프: {brief}",
            system="너는 까다로운 크리에이티브 디렉터다. 안전하고 예측 가능한 안을 가차없이 '뻔함'으로 친다.",
            temperature=0.0,
        )
        return "뻔함" in v
