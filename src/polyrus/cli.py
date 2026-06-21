"""polyrus CLI — Claude Code 훅 등록/해제/상태.

`polyrus wrap claude`  : settings.json에 Stop 훅을 1줄로 등록(멱등).
`polyrus unwrap claude`: 그 훅 제거.
`polyrus status`       : 등록 여부 + 텔레그램 env 상태.

설계: Polyrus는 *별도 에이전트가 아니라* Claude Code를 감싼다(wrap-first). 이 명령은
모델이 "끝" 선언 시 `polyrus-stop-hook`이 검증을 집행하도록 호스트 설정에 연결할 뿐이다.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path

HOOK_COMMAND = "polyrus-stop-hook"
AUTO_HOOK_COMMAND = "polyrus-auto-hook"
_HOOK_COMMANDS = (HOOK_COMMAND, AUTO_HOOK_COMMAND)


def _default_model() -> object:
    """LLM 합성용 모델 팩토리(테스트에서 monkeypatch 가능)."""
    from polyrus.models import AnthropicModel

    return AnthropicModel()


def _make_backend(backend: str) -> object:
    """CLI 백엔드 팩토리 — 계정 로그인 그대로 구동(테스트에서 monkeypatch 가능)."""
    from polyrus.models import claude_cli, codex_cli

    return {"claude": claude_cli, "codex": codex_cli}[backend]()


def cmd_run(args: argparse.Namespace) -> int:
    """Polyrus가 CLI 백엔드(claude/codex)를 구독 계정으로 구동 → 검증된 완료까지(owns-loop)."""
    from polyrus.adapters.claude_code.task_file import load_task_file
    from polyrus.dod import DoDGenerator
    from polyrus.session import Session
    from polyrus.types import LedgerItem, Task, Termination

    base = Path(getattr(args, "dir", None) or ".")
    model = _make_backend(args.backend)
    task_file = base / ".polyrus" / "task.json"

    if task_file.exists():
        task, _artifacts = load_task_file(task_file, artifact_base=base)
    elif getattr(args, "goal", None):
        gen = DoDGenerator(model=model if getattr(args, "llm_tests", False) else None)
        dod = gen.derive_dod(args.goal)
        task = Task(id="run", request=args.goal, items=[LedgerItem(id="i1", goal=args.goal, dod=dod)])
    else:
        print("목표 인자 또는 .polyrus/task.json 이 필요하다 (예: polyrus run \"X 구현\" --llm-tests)")
        return 2

    print(f"백엔드: {args.backend} (계정 로그인) · 작업: {task.request}")
    result = Session(model).run(task)
    print(f"종료: {result.termination.value} (확신 {result.weighted_confidence:.2f})")
    for item in result.items:
        status = "완료" if item.closed else ("에스컬레이션" if item.escalated else "미해결")
        line = f"  - {item.goal}: {status}"
        if item.escalated:
            line += f" — {item.escalation_reason}"
        print(line)
    return 0 if result.termination is Termination.VERIFIED_COMPLETE else 1


def cmd_plan(args: argparse.Namespace) -> int:
    """선제질문 기획 — '질문은 우리가, 사용자는 대답만'. 대화형으로 뼈대를 걸어 동결 기획 산출."""
    from polyrus.memory import SkillStore
    from polyrus.planner import InteractiveAnswers, ProactivePlanner
    from polyrus.skeleton import REGISTRY, get_skeleton
    from polyrus.skills_seed import ensure_skills_for

    domain = getattr(args, "domain", None) or "homepage"
    if domain not in REGISTRY:
        print(f"미등록 도메인: {domain} (있는 것: {', '.join(sorted(REGISTRY))})")
        return 2

    base = Path(getattr(args, "dir", None) or ".")
    pdir = base / ".polyrus"
    pdir.mkdir(parents=True, exist_ok=True)
    # 영속 검증 스킬 위키(자가발전) — 비면 도메인별 전문가 스킬 흡수(idempotent).
    wiki = SkillStore(str(pdir / "skills.db"))
    added = ensure_skills_for(wiki, domain)
    if added:
        print(f"📚 검증 스킬 위키에 전문가 스킬 {added}개 흡수")

    scope = float(getattr(args, "scope_min_weight", 0.0) or 0.0)
    researcher = searcher = None
    if getattr(args, "research", True):
        from polyrus.research import default_fetcher, default_searcher

        researcher = default_fetcher()    # Scrapling 우선, urllib 폴백 — 레퍼런스를 읽어 출처검증
        searcher = default_searcher()     # 업종만 줬을 때 후보를 찾아옴(막다른길 해소)
    planner = ProactivePlanner(
        get_skeleton(domain), wiki=wiki, researcher=researcher, searcher=searcher, scope_min_weight=scope
    )
    print(f"\n🧭 {domain} 기획 — 질문은 제가 합니다. 편하게 답해 주세요. (모르면 '예시'를 따라 골라도 됨)")
    result = planner.run(InteractiveAnswers())

    # 난이도 가드: 어려운 질문이 섞였으면 *우리 잘못*으로 표면화.
    if result.question_issues:
        print("\n⚠ 질문 난이도 경고(우리가 고쳐야 함):")
        for it in result.question_issues:
            print(f"  - {it}")

    print("\n" + result.brief)

    if result.blockers:
        # 회피/미응답을 조용히 통과시키지 않는다(No-Silent-Stop).
        print("\n⛔ 미해결(진행 전 답 필요):")
        for b in result.blockers:
            print(f"  - {b}")

    plan_md = pdir / "plan.md"
    plan_md.write_text(result.brief + "\n", encoding="utf-8")
    wiki.close()
    print(f"\n✅ 기획 저장 → {plan_md}")
    if result.blockers:
        print("  (미해결 항목을 채운 뒤 다시 실행하세요)")
        return 1

    if domain == "digest":
        # 실행 설정 저장 → `polyrus digest run`이 읽어서 실제로 조회·전송한다.
        cfg = result.digest_config()  # type: ignore[attr-defined]
        (pdir / "digest.json").write_text(
            json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"  # type: ignore[attr-defined]
        )
        print(f"  설정 저장 → {pdir / 'digest.json'}")
        print("  지금 한 번 보내보기: polyrus digest run --deliver console")
        print("  매일 자동: polyrus digest schedule")
        return 0

    if getattr(args, "build", False):
        return _build_homepage(result, args)
    print("  다음: polyrus plan --build 또는 polyrus run 으로 빌드 위임 → No-Pass 검증까지")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    """저장된 digest 설정으로 한 번 실행(조회→유망 선별→렌더→검증→전송) 또는 스케줄 등록."""
    from polyrus.digest import (
        ConsoleDeliverer,
        DigestConfig,
        FakeDeliverer,
        TelegramDeliverer,
        crontab_line,
        run_digest,
    )

    base = Path(getattr(args, "dir", None) or ".")
    cfg_file = base / ".polyrus" / "digest.json"
    if not cfg_file.exists():
        print(f"설정이 없어요 → 먼저 'polyrus plan digest' 로 만들어 주세요 ({cfg_file})")
        return 2
    config = DigestConfig.from_dict(json.loads(cfg_file.read_text(encoding="utf-8")))

    if getattr(args, "schedule", False):
        if not config.schedule_cron:
            print("스케줄(cron)이 비어 있어요 — 'polyrus plan digest'에서 '언제'를 다시 답해 주세요.")
            return 1
        cmd = f"cd {base.resolve()} && polyrus digest run --deliver {getattr(args, 'deliver', 'telegram')}"
        line = crontab_line(config.schedule_cron, cmd)
        print("아래 한 줄을 crontab에 추가하면 매일 자동 실행돼요 (터미널에 '! <명령>'):")
        print(f'  ( crontab -l 2>/dev/null; echo "{line}" ) | crontab -')
        print(f"\ncron: {config.schedule_cron}  → {cmd}")
        return 0

    # 한 번 실행.
    from polyrus.digest import GitHubSource

    source = GitHubSource()
    deliver = getattr(args, "deliver", "console")
    deliverer: object
    if deliver == "telegram":
        deliverer = TelegramDeliverer()
    elif deliver == "fake":
        deliverer = FakeDeliverer()
    else:
        deliverer = ConsoleDeliverer()

    print(f"🔎 GitHub 조회: {config.github_query()}")
    result = run_digest(config, source, deliverer)  # type: ignore[arg-type]
    if result.blocked:
        print(f"⛔ 전송 보류: {result.blocked}")  # 조용히 빈/슬롭 안 보냄
        return 1
    print(f"✅ {len(result.repos)}개 요약 · 전송={'성공' if result.delivered else '실패(채널 미설정?)'}")
    return 0 if result.delivered else 1


def _build_homepage(result: object, args: argparse.Namespace) -> int:
    """빌드 위임 실연 — 빌더가 히어로 카피를 쓰고 결정적 홈페이지 뱅크가 검증(마누스 회피)."""
    from polyrus.session import Session
    from polyrus.types import Termination

    model = _make_backend(getattr(args, "backend", "claude"))
    task = result.to_task("plan")  # type: ignore[attr-defined]
    print("\n🛠  빌드 위임 → 검증된 완료까지 (히어로 카피, 결정적 검증)")
    loop = Session.for_homepage_build(model).run(task)
    if loop.termination is Termination.ENV_BLOCKED:
        # 초보자 온보딩: 크래시 말고 친절 안내(스택트레이스 금지).
        print("\n" + (loop.items[0].escalation_reason if loop.items else "환경 미비"))
        print("\n  설치 후 다시 실행하세요: polyrus plan --build  (확인: polyrus doctor)")
        return 2
    print(f"종료: {loop.termination.value} (확신 {loop.weighted_confidence:.2f})")
    for item in loop.items:
        status = "완료" if item.closed else ("에스컬레이션" if item.escalated else "미해결")
        print(f"  - {item.goal}: {status}" + (f" — {item.escalation_reason}" if item.escalated else ""))
    return 0 if loop.termination is Termination.VERIFIED_COMPLETE else 1


def cmd_setup(args: argparse.Namespace) -> int:
    """한 방 설정 — 환경 점검 + Claude Code 자동검증 훅 등록 + 다음 단계 안내(개인도구 온보딩).

    에이전트가 'GitHub 주소 받아 설치·설정'할 때 실행하는 단일 명령.
    """
    print("🧭 Polyrus 설정 — 질문은 우리가, 당신은 대답만\n")

    # 1) 환경 점검(비차단) — 핵심 기능엔 Python만 있으면 됨(이미 충족).
    from polyrus.preflight import preflight_check

    rep = preflight_check(["python3"])
    print("1) 환경:", "✅ 준비됨" if rep.ok else "⚠ " + rep.popup)

    # 2) Claude Code 자동검증 훅 등록(무설정 auto 기본 — task.json 불요).
    if not getattr(args, "no_hook", False):
        args.auto = not getattr(args, "classic", False)
        args.target = "claude"
        print("2) Claude Code 훅 등록…")
        cmd_wrap(args)
    else:
        print("2) 훅 등록 건너뜀(--no-hook)")

    # 3) 다음 단계.
    print("\n✅ 설정 완료. 이제:")
    print("   • UI로 기획:   polyrus serve            (브라우저에서 선제질문)")
    print("   • CLI로 기획:   polyrus plan homepage    (또는 digest)")
    print("   • 자동 알림:    polyrus plan digest → polyrus digest schedule")
    print("   • Claude Code에서 코드 작업하면 Polyrus가 '끝' 선언을 자동 검증합니다.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """초보자 온보딩 — 도메인 빌드에 필요한 기본 프로그램(Python·Node 등)이 깔렸는지 점검."""
    from polyrus.preflight import preflight_check
    from polyrus.skeleton import REGISTRY, get_skeleton

    domain = getattr(args, "domain", None) or "homepage"
    tools = list(get_skeleton(domain).requires) if domain in REGISTRY else []
    report = preflight_check(tools)
    print(report.popup)
    if report.ok:
        return 0
    print("\n설치하려면 (터미널에 '! <명령>' 으로 실행하거나 직접):")
    for m in report.missing:
        if m.plan.tier == "gated":
            print(f"  {m.tool.name}: {m.plan.command}")
        else:
            print(f"  {m.tool.name}: {m.plan.manual_url or m.plan.command}")
    print("\n설치 후 다시 점검: polyrus doctor")
    return 1


def cmd_serve(args: argparse.Namespace) -> int:  # pragma: no cover - 실제 서버 루프
    """로컬 UI 실행 — 브라우저에서 선제질문 기획을 바로 사용(개인 도구, 의존성 0)."""
    from polyrus.server.app import serve

    serve(host=getattr(args, "host", "127.0.0.1"), port=int(getattr(args, "port", 8765)),
          open_browser=not getattr(args, "no_open", False))
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    """작업 종류 → 추천 모델(이유·대안) + 가용성. 미설정이면 친절 요청(§6 라우팅)."""
    from polyrus.routing import route

    d = route(getattr(args, "task", None) or "default")
    print(f"🧭 작업 '{d.task_kind}' → 추천: {d.provider} ({d.reason})")
    if d.available:
        print(f"  ✅ 바로 사용 가능 — {d.provider}")
    else:
        print(f"  ⚠ {d.request}")
        print(f"  → 지금은 {d.use}로 진행돼요.")
    return 0


def cmd_build_check(args: argparse.Namespace) -> int:
    """빌더가 만든 산출물이 *실제로 빌드되나* 검사(말 말고 실행). 도구 없으면 설치 안내."""
    import shlex

    from polyrus.types import Claim, DoD, Verdict
    from polyrus.verifiers.build import BuildVerifier

    base = Path(getattr(args, "dir", None) or ".")
    command = shlex.split(getattr(args, "cmd", None) or "npm run build")
    print(f"🔨 빌드 검사: {' '.join(command)}  (@ {base})")
    result = BuildVerifier(command=command).verify(
        Claim("b", "", kind="build", meta={"cwd": str(base)}), DoD(spec="build", frozen=True)
    )
    icon = {"pass": "✅", "fail": "❌", "inconclusive": "⚠"}.get(result.verdict.value, "·")
    print(f"{icon} {result.detail}")
    for e in result.evidence:
        print(f"   {e}")
    return 0 if result.verdict is Verdict.PASS else 1


def settings_path(args: argparse.Namespace) -> Path:
    if getattr(args, "settings", None):
        return Path(args.settings)
    if getattr(args, "project", False):
        return Path(".claude") / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def _load(path: Path) -> dict:
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else {}
    return {}


def _write(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def has_hook(settings: dict) -> bool:
    for group in settings.get("hooks", {}).get("Stop", []):
        for h in group.get("hooks", []):
            if any(c in str(h.get("command", "")) for c in _HOOK_COMMANDS):
                return True
    return False


def cmd_wrap(args: argparse.Namespace) -> int:
    path = settings_path(args)
    settings = _load(path)
    command = AUTO_HOOK_COMMAND if getattr(args, "auto", False) else HOOK_COMMAND
    if has_hook(settings):
        print(f"이미 등록됨 → {path}")
        return 0
    settings.setdefault("hooks", {}).setdefault("Stop", []).append(
        {"hooks": [{"type": "command", "command": command}]}
    )
    if getattr(args, "dry_run", False):
        print(f"# (dry-run) 아래를 {path} 에 쓸 예정:")
        print(json.dumps(settings, ensure_ascii=False, indent=2))
        return 0
    _write(path, settings)
    print(f"✅ Stop 훅 등록 → {path}")
    print("다음 단계:")
    print("  1) 프로젝트에 .polyrus/task.json (완료 원장: 목표 + 동결 수용 테스트) 작성")
    print("  2) (선택) export POLYRUS_TELEGRAM_TOKEN / POLYRUS_TELEGRAM_CHAT_ID")
    print("  → 이제 Claude가 '끝' 선언 시 Polyrus가 검증, 미통과면 이어서 고친다.")
    return 0


def cmd_unwrap(args: argparse.Namespace) -> int:
    path = settings_path(args)
    settings = _load(path)
    stop = settings.get("hooks", {}).get("Stop", [])
    kept = []
    for group in stop:
        group = dict(group)
        group["hooks"] = [
            h for h in group.get("hooks", [])
            if not any(c in str(h.get("command", "")) for c in _HOOK_COMMANDS)
        ]
        if group["hooks"]:
            kept.append(group)
    if "hooks" in settings:
        settings["hooks"]["Stop"] = kept
    _write(path, settings)
    print(f"🗑  Stop 훅 제거 → {path}")
    return 0


_TASK_SKELETON = {
    "_help": (
        "Polyrus 완료 원장(모델 밖). items의 각 항목 = goal + 동결 수용테스트(acceptance_tests) "
        "+ 현재 산출물 경로(artifact). acceptance_tests는 인라인 pytest 소스 또는 .py 경로. "
        "Claude가 '끝' 선언 시 Polyrus가 이 기준으로 검증하고, 미통과면 이어서 고치게 한다."
    ),
    "task": {
        "id": "my-task",
        "request": "<무엇을 만들지 한 줄로>",
        "items": [
            {
                "id": "i1",
                "goal": "<하위 목표 — 예: sum_even_squares 구현>",
                "module": "solution.py",
                "artifact": "solution.py",
                "acceptance_tests": [
                    "from solution import sum_even_squares\n"
                    "def test_example():\n"
                    "    assert sum_even_squares([1, 2, 3, 4]) == 20\n"
                ],
                "risk": "medium",
            }
        ],
    },
}


def cmd_init(args: argparse.Namespace) -> int:
    base = Path(getattr(args, "dir", None) or ".")
    pdir = base / ".polyrus"
    task_file = pdir / "task.json"
    goal = getattr(args, "goal", None)
    if task_file.exists() and not getattr(args, "force", False):
        print(f"이미 있음 → {task_file} (덮어쓰려면 --force)")
    else:
        skeleton = copy.deepcopy(_TASK_SKELETON)
        if goal:
            skeleton["task"]["request"] = goal
            skeleton["task"]["items"][0]["goal"] = goal
            if getattr(args, "llm", False):
                _synthesize_into(skeleton, goal)
        pdir.mkdir(parents=True, exist_ok=True)
        task_file.write_text(
            json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # 세션 카운터(*.count)·코퍼스 DB(*.db)는 커밋 대상 아님.
        (pdir / ".gitignore").write_text("*.count\n*.db\n", encoding="utf-8")
        print(f"✅ 완료 원장 골격 생성 → {task_file}")

    if getattr(args, "wrap", False):
        cmd_wrap(args)
    else:
        print("  훅 등록: polyrus wrap claude")
    if not (goal and getattr(args, "llm", False)):
        print(f"  편집: {task_file} (goal·acceptance_tests·artifact 채우기) → Claude Code에서 작업")
    return 0


def _synthesize_into(skeleton: dict, goal: str) -> None:
    """LLM으로 goal → 수용 테스트 합성, skeleton 첫 항목에 채운다. 실패해도 골격은 남는다."""
    from polyrus.dod import DoDGenerator

    try:
        tests = DoDGenerator(model=_default_model()).synthesize_acceptance_tests(goal)
        if tests:
            skeleton["task"]["items"][0]["acceptance_tests"] = tests
            print(f"  🤖 LLM이 수용 테스트 {len(tests)}개 합성 → 이제 검증 기준이 잡혔다")
        else:
            print("  ⚠ LLM이 빈 테스트 반환; 골격 유지")
    except Exception as e:  # noqa: BLE001 - 키 없음/네트워크 실패 시 골격으로 폴백
        print(f"  ⚠ LLM 합성 실패({type(e).__name__}); 골격 유지 — 수동 편집하거나 ANTHROPIC_API_KEY 설정")


def cmd_status(args: argparse.Namespace) -> int:
    path = settings_path(args)
    registered = has_hook(_load(path)) if path.exists() else False
    tg = bool(os.environ.get("POLYRUS_TELEGRAM_TOKEN") and os.environ.get("POLYRUS_TELEGRAM_CHAT_ID"))
    print(f"settings : {path} ({'존재' if path.exists() else '없음'})")
    print(f"Stop 훅  : {'등록됨 ✅' if registered else '미등록'}")
    print(f"텔레그램 : {'env 설정됨 ✅' if tg else 'env 미설정'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="polyrus", description="검증된 No-Pass 하니스 — Claude Code 래퍼")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_scope(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--project", action="store_true", help="~/.claude 대신 ./.claude/settings.json")
        sp.add_argument("--settings", help="명시적 settings.json 경로")

    i = sub.add_parser("init", help=".polyrus/task.json 골격 생성(+선택 LLM 합성·훅 등록)")
    i.add_argument("goal", nargs="?", help="목표 한 줄(예: \"sum_even_squares 구현\")")
    i.add_argument("--llm", action="store_true", help="goal에서 수용 테스트를 LLM으로 자동 합성(ANTHROPIC_API_KEY)")
    i.add_argument("--dir", help="프로젝트 디렉토리(기본 현재)")
    i.add_argument("--wrap", action="store_true", help="생성과 함께 Stop 훅도 등록")
    i.add_argument("--force", action="store_true", help="기존 task.json 덮어쓰기")
    add_scope(i)
    i.add_argument("--dry-run", action="store_true", help="(--wrap 시) 훅 미리보기")
    i.set_defaults(fn=cmd_init)

    w = sub.add_parser("wrap", help="Claude Code에 Stop 훅 등록")
    w.add_argument("target", choices=["claude"])
    add_scope(w)
    w.add_argument("--auto", action="store_true", help="무설정 자동 검증 훅(task.json 불요, 대화에서 자동)")
    w.add_argument("--dry-run", action="store_true", help="쓰지 않고 미리보기")
    w.set_defaults(fn=cmd_wrap)

    u = sub.add_parser("unwrap", help="Stop 훅 제거")
    u.add_argument("target", choices=["claude"])
    add_scope(u)
    u.set_defaults(fn=cmd_unwrap)

    s = sub.add_parser("status", help="등록/텔레그램 상태")
    add_scope(s)
    s.set_defaults(fn=cmd_status)

    rn = sub.add_parser("run", help="CLI 백엔드(claude/codex)를 계정으로 구동해 검증된 완료까지")
    rn.add_argument("goal", nargs="?", help="목표 한 줄(또는 .polyrus/task.json 사용)")
    rn.add_argument("--backend", choices=["claude", "codex"], default="claude")
    rn.add_argument("--llm-tests", action="store_true", help="목표에서 수용 테스트 합성")
    rn.add_argument("--dir", help="프로젝트 디렉토리(기본 현재)")
    rn.set_defaults(fn=cmd_run)

    pl = sub.add_parser("plan", help="선제질문 기획 — 대화형으로 뼈대를 걸어 기획 산출(비개발자)")
    pl.add_argument("domain", nargs="?", default="homepage", help="도메인 뼈대(기본 homepage)")
    pl.add_argument("--dir", help="프로젝트 디렉토리(기본 현재)")
    pl.add_argument(
        "--scope-min-weight", type=float, default=0.0, dest="scope_min_weight",
        help="비례 가지치기 — 가벼운 스코프(예 0.5)면 장식 질문 생략",
    )
    pl.add_argument("--no-research", action="store_false", dest="research",
                    help="레퍼런스 실조사(읽기·출처검증) 끄기 — 기본은 켬")
    pl.add_argument("--build", action="store_true", help="기획 후 빌드 위임 실연(히어로 카피 검증)까지")
    pl.add_argument("--backend", choices=["claude", "codex"], default="claude", help="(--build) 빌더 백엔드")
    pl.set_defaults(fn=cmd_plan)

    st = sub.add_parser("setup", help="한 방 설정 — 환경 점검 + Claude Code 자동검증 훅 + 안내")
    add_scope(st)
    st.add_argument("--classic", action="store_true", help="task.json 기반 stop-hook(기본은 무설정 auto)")
    st.add_argument("--no-hook", action="store_true", help="훅 등록 건너뛰기(UI만 쓸 때)")
    st.add_argument("--dry-run", action="store_true", help="훅을 쓰지 않고 미리보기")
    st.set_defaults(fn=cmd_setup)

    dr = sub.add_parser("doctor", help="초보자 온보딩 — 필요한 기본 프로그램(Python·Node 등) 점검·안내")
    dr.add_argument("domain", nargs="?", default="homepage", help="도메인(기본 homepage)")
    dr.set_defaults(fn=cmd_doctor)

    dg = sub.add_parser("digest", help="저장된 digest 설정으로 한 번 실행 또는 스케줄 등록")
    dg.add_argument("action", nargs="?", choices=["run", "schedule"], default="run")
    dg.add_argument("--dir", help="프로젝트 디렉토리(기본 현재)")
    dg.add_argument("--deliver", choices=["console", "telegram", "fake"], default="console",
                    help="전달 채널(기본 console — 화면 출력)")
    dg.set_defaults(fn=lambda a: cmd_digest(_with_schedule(a)))

    bc = sub.add_parser("build-check", help="산출물이 실제로 빌드되는지 검사(npm run build 등)")
    bc.add_argument("--dir", help="프로젝트 디렉토리(기본 현재)")
    bc.add_argument("--cmd", help="빌드 명령(기본 'npm run build')")
    bc.set_defaults(fn=cmd_build_check)

    rt = sub.add_parser("route", help="작업에 맞는 모델 추천(이유·대안) + 가용성·키 요청")
    rt.add_argument("task", nargs="?", default="default",
                    help="작업 종류(code / long_reasoning / cheap_divergence / default)")
    rt.set_defaults(fn=cmd_route)

    sv = sub.add_parser("serve", help="로컬 UI 실행 — 브라우저에서 선제질문 기획(개인 도구)")
    sv.add_argument("--port", type=int, default=8765, help="포트(기본 8765)")
    sv.add_argument("--host", default="127.0.0.1", help="호스트(기본 127.0.0.1)")
    sv.add_argument("--no-open", action="store_true", help="브라우저 자동 열기 끄기")
    sv.set_defaults(fn=cmd_serve)
    return p


def _with_schedule(args: argparse.Namespace) -> argparse.Namespace:
    args.schedule = getattr(args, "action", "run") == "schedule"
    return args


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))
