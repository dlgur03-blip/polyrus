from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

from polyrus.sandbox import Sandbox, Workspace
from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult
from polyrus.verifiers.base import BaseVerifier

_CMP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE, ast.LtE: ast.Gt}
_BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Add}
_BOOL = {ast.And: ast.Or, ast.Or: ast.And}


def _count_sites(source: str) -> int:
    n = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare):
            n += sum(1 for op in node.ops if type(op) in _CMP)
        elif isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            n += 1
        elif isinstance(node, ast.BoolOp) and type(node.op) in _BOOL:
            n += 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            n += 1
    return n


def _mutate(source: str, target: int) -> str | None:
    """target번째 변이 지점 하나만 뒤집은 소스(지점이 없으면 None)."""
    tree = ast.parse(source)
    state = {"i": 0, "done": False}

    def hit() -> bool:
        if state["i"] == target and not state["done"]:
            state["done"] = True
            state["i"] += 1
            return True
        state["i"] += 1
        return False

    class M(ast.NodeTransformer):
        def visit_Compare(self, node: ast.Compare) -> ast.AST:
            self.generic_visit(node)
            node.ops = [_CMP[type(op)]() if (type(op) in _CMP and hit()) else op for op in node.ops]
            return node

        def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
            self.generic_visit(node)
            if type(node.op) in _BIN and hit():
                node.op = _BIN[type(node.op)]()
            return node

        def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
            self.generic_visit(node)
            if type(node.op) in _BOOL and hit():
                node.op = _BOOL[type(node.op)]()
            return node

        def visit_Constant(self, node: ast.Constant) -> ast.AST:
            if isinstance(node.value, int) and not isinstance(node.value, bool) and hit():
                return ast.copy_location(ast.Constant(value=node.value + 1), node)
            return node

    new = M().visit(tree)
    if not state["done"]:
        return None
    ast.fix_missing_locations(new)
    return ast.unparse(new)


class MutationVerifier(BaseVerifier):
    """T1 메타-검증기: 테스트의 *강도*를 검증한다(굿하트 차단).

    코드를 변이시켜도 수용 테스트가 통과하면 그 테스트는 빈껍데기다. 변이 중 테스트가 잡아낸
    비율(뮤테이션 점수)이 임계 미만이면 그 테스트 묶음은 T1 증거로 약하다고 본다.
    경량 AST 변이(비교/이항/불리언 연산자 뒤집기, 정수 상수 ±1) — mutmut CLI 의존 없이 자체 구현.
    """

    tier = Tier.T1_EXECUTION
    name = "code.t1.mutation"
    locality = Locality.LOCAL

    def __init__(self, sandbox: Sandbox | None = None, *, min_score: float = 0.5, cap: int = 8) -> None:
        self.sandbox = sandbox or Sandbox()
        self.min_score = min_score
        self.cap = cap

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        if not dod.acceptance_tests:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "수용 테스트 없음 — 강도 측정 불가")
        try:
            total = _count_sites(claim.content)
        except SyntaxError:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "구문 오류는 T1 실행의 영역")
        if total == 0:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "변이 지점 없음")

        module = str(claim.meta.get("module", "solution.py"))
        killed = 0
        ran = 0
        with self.sandbox.workspace() as ws:
            test_files = self._tests(dod, ws)
            if not test_files:
                return self._r(Verdict.INCONCLUSIVE, 0.0, "수용 테스트 미존재")
            for t in range(min(total, self.cap)):
                mutant = _mutate(claim.content, t)
                if mutant is None or mutant == claim.content:
                    continue
                ran += 1
                ws.write(module, mutant)
                res = self.sandbox.run(
                    [sys.executable, "-m", "pytest", "-q", *test_files],
                    cwd=ws.path, env={"PYTHONPATH": ws.path},
                )
                if res.returncode != 0:  # 변이가 테스트에 잡힘(killed)
                    killed += 1

        if ran == 0:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "유효 변이 없음")
        score = killed / ran
        if score >= self.min_score:
            return self._r(Verdict.PASS, 0.9, f"뮤테이션 점수 {score:.2f} ({killed}/{ran}) ≥ {self.min_score}")
        return self._r(Verdict.FAIL, 0.9, f"테스트 강도 약함: 뮤테이션 점수 {score:.2f} ({killed}/{ran})")

    def _tests(self, dod: DoD, ws: Workspace) -> list[str]:
        out = []
        for i, e in enumerate(dod.acceptance_tests):
            if "def test" in e:
                out.append(ws.write(f"test_acc_{i}.py", e))
            elif os.path.exists(e):
                out.append(ws.write(f"test_acc_{i}.py", Path(e).read_text(encoding="utf-8")))
        return out

    def _r(self, verdict: Verdict, reliability: float, detail: str) -> VerifierResult:
        return VerifierResult(tier=self.tier, verdict=verdict, reliability=reliability, detail=detail,
                              locality=self.locality)
