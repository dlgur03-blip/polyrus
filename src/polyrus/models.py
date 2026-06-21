from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol


class ModelClient(Protocol):
    """모델 비종속 클라이언트. 경쟁자처럼 OpenAI 호환 + 어댑터 + 프로바이더 폴백.

    temperature는 문어(병렬 팔) 탈상관에 쓴다 — 팔마다 다른 온도로 분포를 벌린다.
    TODO(phase2): OpenAI/OpenRouter 어댑터 + 폴백 체인.
    """

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = ...,
        model: str | None = ...,
        temperature: float | None = ...,
    ) -> str: ...


class EchoModel:
    """테스트용 더미. 실제 구현 전 스캐폴드가 import 가능하도록."""

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        return f"[echo] {prompt[:80]}"


class AnthropicModel:
    """Anthropic(Claude) 어댑터. 자격증명은 도구 경계에서 env로만(6.3) — 프롬프트엔 안 들어간다.

    client 주입 가능(테스트는 네트워크 0). 실제 호출은 키가 있을 때만 일어난다.
    """

    def __init__(
        self,
        *,
        model: str = "claude-opus-4-8",
        api_key: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        client: object | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = client

    def _ensure_client(self) -> object:
        if self._client is None:
            import anthropic  # 지연 import (패키지 import 시 SDK 강제 안 함)

            key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        client = self._ensure_client()
        kwargs: dict[str, object] = {
            "model": model or self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        msg = client.messages.create(**kwargs)  # type: ignore[attr-defined]
        return "".join(getattr(b, "text", "") for b in msg.content)


class OpenAIModel:
    """OpenAI(및 OpenAI-호환 엔드포인트) 어댑터. base_url로 아무 호환 프로바이더나 가리킨다
    (OpenRouter·로컬 vLLM 등) — '싸우지 말고 감싸라'의 모델층."""

    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        client: object | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = client

    def _ensure_client(self) -> object:
        if self._client is None:
            import openai  # 지연 import

            key = self.api_key or os.environ.get("OPENAI_API_KEY")
            self._client = openai.OpenAI(api_key=key, base_url=self.base_url)
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        client = self._ensure_client()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(  # type: ignore[attr-defined]
            model=model or self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature if temperature is None else temperature,
        )
        return resp.choices[0].message.content or ""


class GeminiModel:
    """Google Gemini 어댑터(google-genai SDK). §6 멀티API: 영상·이미지 인식 등엔 Gemini가 강하다.

    자격증명은 도구 경계에서 env로만(GEMINI_API_KEY/GOOGLE_API_KEY) — 프롬프트엔 안 들어간다.
    config를 *dict*로 넘겨 테스트 시 SDK 타입 import 없이 client 주입 가능(네트워크 0).
    """

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash",
        api_key: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        client: object | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = client

    def _ensure_client(self) -> object:
        if self._client is None:
            from google import genai  # 지연 import (패키지 import 시 SDK 강제 안 함)

            key = self.api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            self._client = genai.Client(api_key=key)
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        client = self._ensure_client()
        config: dict[str, object] = {
            "temperature": self.temperature if temperature is None else temperature,
            "max_output_tokens": self.max_tokens,
        }
        if system:
            config["system_instruction"] = system
        resp = client.models.generate_content(  # type: ignore[attr-defined]
            model=model or self.model, contents=prompt, config=config
        )
        return getattr(resp, "text", "") or ""


class FallbackModel:
    """프로바이더 폴백 체인(7.2). 모델 비종속 = 흡수방지의 핵심 — 한 프로바이더가 죽어도 다음으로.
    순서대로 시도하고 예외 시 폴백. 전부 실패해야 예외."""

    def __init__(self, providers: list[ModelClient]) -> None:
        if not providers:
            raise ValueError("최소 하나의 프로바이더가 필요하다")
        self.providers = providers
        self.last_used: ModelClient | None = None

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        errors: list[str] = []
        for p in self.providers:
            try:
                out = p.complete(prompt, system=system, model=model, temperature=temperature)
                self.last_used = p
                return out
            except Exception as e:  # noqa: BLE001 - 폴백 체인은 모든 실패를 삼키고 다음으로
                errors.append(f"{type(p).__name__}: {e}")
        raise RuntimeError("모든 프로바이더 실패 — " + "; ".join(errors))


class CliModelError(RuntimeError):
    """로컬 에이전트 CLI 구동 실패."""


# (cmd, stdin) -> (returncode, stdout, stderr). 실제 subprocess를 가린다(테스트 주입용).
CliRunner = Callable[[list[str], "str | None"], "tuple[int, str, str]"]


class CliModel:
    """로컬 에이전트 CLI(`claude`·`codex`)를 *계정 로그인 그대로* 구동하는 모델 백엔드.

    API 키가 아니라 사용자의 구독(Claude Max·ChatGPT)으로 돈다 — Polyrus가 CLI를 서브루틴으로
    호출(owns-loop)하고 자기 검증을 입힌다. runner 주입식이라 테스트는 서브프로세스 0.

    주의: 정확한 플래그는 설치된 CLI 버전에 따라 다를 수 있다(command로 조정). temperature는
    CLI가 노출하지 않으면 무시된다. 구독 CLI의 프로그램 구동은 각 도구의 레이트리밋·ToS 적용.
    """

    def __init__(
        self,
        command: list[str],
        *,
        model_flag: str | None = None,
        timeout: float = 600.0,
        runner: CliRunner | None = None,
        prompt_via_stdin: bool = False,
        parse: Callable[[str], str] | None = None,
    ) -> None:
        self.command = list(command)
        self.model_flag = model_flag
        self.timeout = timeout
        self._runner = runner or self._subprocess
        self.prompt_via_stdin = prompt_via_stdin
        self._parse = parse or (lambda s: s.strip())

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        full = f"{system}\n\n{prompt}" if system else prompt
        cmd = list(self.command)
        if model and self.model_flag:
            cmd += [self.model_flag, model]
        if self.prompt_via_stdin:
            rc, out, err = self._runner(cmd, full)
        else:
            rc, out, err = self._runner([*cmd, full], None)
        if rc != 0:
            raise CliModelError(f"{self.command[0]} 실패(rc={rc}): {(err or out).strip()[:300]}")
        return self._parse(out)

    def _subprocess(self, cmd: list[str], stdin: str | None) -> tuple[int, str, str]:
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=self.timeout)
        return p.returncode, p.stdout, p.stderr


# ── 작업별 모델 추천(§6 멀티API 라우팅) — 이유 + 대안을 항상 함께 ─────────────────────
# 사용자 셋업(Claude Max + Codex 계정)에 맞춤. claude/codex는 계정 CLI(키 불요), openai만 키.
# task_kind → (선호 provider, 이유, 대안)
_STRENGTHS: dict[str, tuple[str, str, str]] = {
    "code": ("claude", "코드 정확도가 높아요", "codex"),
    "long_reasoning": ("claude", "긴 추론에 강해요", "codex"),
    "cheap_divergence": ("codex", "발산(다양화)을 다른 모델로 탈상관시켜요", "claude"),
    "default": ("claude", "범용으로 안정적이에요", "codex"),
}


@dataclass
class ModelRecommendation:
    provider: str
    reason: str
    alternative: str
    needs_key: str = ""  # 그 provider에 필요한 env 키(계정 CLI면 빈 값)


def recommend_provider(task_kind: str) -> ModelRecommendation:
    """작업 종류 → 선호 provider 추천(이유·대안 동반). 키 필요하면 호출자가 요청 UX로."""
    provider, reason, alt = _STRENGTHS.get(task_kind, _STRENGTHS["default"])
    key = {"openai": "OPENAI_API_KEY"}.get(provider, "")  # claude/codex=계정 CLI(키 불요)
    return ModelRecommendation(provider, reason, alt, key)


def claude_cli(**kw: object) -> CliModel:
    """Claude Code를 print 모드(`claude -p`)로 — 당신 Claude 구독 로그인 사용(API 키 불요)."""
    return CliModel(["claude", "-p"], model_flag="--model", **kw)  # type: ignore[arg-type]


def codex_cli(**kw: object) -> CliModel:
    """OpenAI Codex CLI를 비대화(`codex exec`)로 — 당신 ChatGPT 계정 로그인 사용(API 키 불요)."""
    return CliModel(["codex", "exec"], model_flag="-m", **kw)  # type: ignore[arg-type]
