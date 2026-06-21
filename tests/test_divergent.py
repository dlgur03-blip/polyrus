"""발산/창의 모드(4.2) — 폭·반클리셰('뻔한 답 거부')·스프레드 보존."""
from __future__ import annotations

from polyrus.divergent import DivergentGenerator, Option


class FixedModel:
    def __init__(self, out: str = "어떤 카피") -> None:
        self.out = out
        self.calls: list[float | None] = []

    def complete(self, prompt, *, system=None, model=None, temperature=None) -> str:
        self.calls.append(temperature)
        return self.out


def test_generate_options_spreads_angles() -> None:
    gen = DivergentGenerator(FixedModel())
    opts = gen.generate_options("새 앱 이름", k=4)
    assert len(opts) == 4
    assert len({o.angle for o in opts}) == 4  # 서로 다른 각도(탈상관)


def test_generate_uses_high_temperature() -> None:
    model = FixedModel()
    DivergentGenerator(model, temperature=0.95).generate_options("brief", k=2)
    assert all(t == 0.95 for t in model.calls)  # 발산은 높은 온도


def test_curate_rejects_cliche_preserves_spread() -> None:
    # 반클리셰: 뻔한 것만 거부하고 *나머지 스프레드는 보존*(단일 수렴 금지).
    gen = DivergentGenerator(FixedModel(), critic=lambda text, brief: "뻔한" in text)
    opts = [Option("참신한 안", "은유적"), Option("뻔한 안", "미니멀"), Option("또 참신", "역발상")]
    survivors = gen.curate(opts, "brief")
    assert len(survivors) == 2 and all("뻔한" not in o.text for o in survivors)


def test_diverge_end_to_end() -> None:
    # 생성 → 반클리셰 큐레이션. 살아남은 스프레드(여러 개)를 돌려준다.
    gen = DivergentGenerator(FixedModel("참신"), critic=lambda t, b: False)
    out = gen.diverge("브리프", k=3)
    assert len(out) == 3  # 아무도 안 잘리면 스프레드 전체 보존


def test_llm_critic_parses_verdict() -> None:
    # 기본 critic은 모델이 '뻔함/참신'으로 판정.
    gen = DivergentGenerator(FixedModel("뻔함"))
    assert gen.critic("안전한 답", "brief") is True
