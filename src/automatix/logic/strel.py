import itertools
import math
import types
from abc import ABC
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Self

from lark import Lark, Token, Transformer, ast_utils, v_args
from typing_extensions import override

STREL_GRAMMAR_FILE = Path(__file__).parent / "strel.lark"


class _Ast(ast_utils.Ast):
    pass


class Expr(_Ast, ABC):

    def __invert__(self) -> "Expr":
        match self:
            case NotOp(arg):
                return arg
            case phi:
                return NotOp(phi)

    def __and__(self, other: Self) -> "AndOp":
        return AndOp(self, other)

    def __or__(self, other: Self) -> "OrOp":
        return OrOp(self, other)

    def expand_intervals(self) -> "Expr":
        """Expand the formula to eliminate intervals and Gloablly operators."""
        # By default, just return self, temporal operators will override
        return self


@dataclass(eq=True, frozen=True, slots=True)
class TimeInterval(_Ast):
    start: Optional[int]
    end: Optional[int]

    def __str__(self) -> str:
        return f"[{self.start or ''}, {self.end or ''}]"

    def is_unbounded(self) -> bool:
        return self.end is None or math.isinf(self.end)

    def is_untimed(self) -> bool:
        """If the interval is [0, inf]"""
        return (self.start is None or self.start == 0.0) and (self.end is None or math.isinf(self.end))

    def __post_init__(self) -> None:
        match (self.start, self.end):
            case (int(t1), int(t2)) if t1 == t2:
                raise ValueError("Time intervals cannot be point values [a,a]")
            case (int(t1), int(t2)) if t1 > t2:
                raise ValueError("Time interval [a,b] cannot have a > b")
            case (int(t1), int(t2)) if t1 < 0 or t2 < 0:
                raise ValueError("Time interval cannot have negative bounds")

    def __iter__(self) -> Iterator[int]:
        """Return an iterator over the discrete range of the time interval

        !!! note

            If the time interval is unbounded, this will return a generator that goes on forever
        """
        match (self.start, self.end):
            case (int(start), None):
                return itertools.count(start)
            case (None, int(end)):
                return iter(range(0, end + 1))
            case (int(start), int(end)):
                return iter(range(start, end + 1))
        return iter([])


@dataclass(eq=True, frozen=True, slots=True)
class DistanceInterval(_Ast):
    start: Optional[float]
    end: Optional[float]

    def __str__(self) -> str:
        return f"[{self.start or ''}, {self.end}]"

    def __post_init__(self) -> None:
        if self.start is None:
            object.__setattr__(self, "start", 0.0)
        if self.end is None:
            object.__setattr__(self, "end", math.inf)
        match (self.start, self.end):
            case (float(start), float(end)) if start < 0 or end < 0:
                raise ValueError("Distane cannot be less than 0")
            case (float(start), float(end)) if start >= end:
                raise ValueError(f"Distance interval cannot have `start` >= `end` ({start} >= {end})")


@dataclass(eq=True, frozen=True, slots=True)
class Constant(Expr):
    value: bool


true = Constant(True)
false = Constant(False)


@dataclass(eq=True, frozen=True, slots=True)
class Identifier(Expr):
    name: str

    def __post_init__(self) -> None:
        assert len(self.name) > 0, "Identifier has to have a non-empty value"
        assert not self.name.isspace(), "Identifier cannot have only whitespace characters"

    def __str__(self) -> str:
        if self.name.isalnum():
            return self.name
        else:
            return f'"{self.name}"'


@dataclass(eq=True, frozen=True, slots=True)
class NotOp(Expr):
    arg: Expr

    def __str__(self) -> str:
        return f"! {self.arg}"

    @override
    def expand_intervals(self) -> "Expr":
        arg = self.arg.expand_intervals()
        if isinstance(arg, NotOp):
            return arg
        return NotOp(arg)


@dataclass(eq=True, frozen=True, slots=True)
class AndOp(Expr):
    lhs: Expr
    rhs: Expr

    def __str__(self) -> str:
        return f"({self.lhs} & {self.rhs})"


@dataclass(eq=True, frozen=True, slots=True)
class OrOp(Expr):
    lhs: Expr
    rhs: Expr

    def __str__(self) -> str:
        return f"({self.lhs} | {self.rhs})"

    @override
    def expand_intervals(self) -> "Expr":
        return OrOp(self.lhs.expand_intervals(), self.rhs.expand_intervals())


@dataclass(eq=True, frozen=True, slots=True)
class EverywhereOp(Expr):
    interval: DistanceInterval
    arg: Expr

    def __str__(self) -> str:
        return f"(everywhere{self.interval} {self.arg})"

    @override
    def expand_intervals(self) -> "Expr":
        return EverywhereOp(self.interval, self.arg.expand_intervals())


@dataclass(eq=True, frozen=True, slots=True)
class SomewhereOp(Expr):
    interval: DistanceInterval
    arg: Expr

    def __str__(self) -> str:
        return f"(somewhere{self.interval} {self.arg})"

    @override
    def expand_intervals(self) -> "Expr":
        return SomewhereOp(self.interval, self.arg.expand_intervals())


@dataclass(eq=True, frozen=True, slots=True)
class EscapeOp(Expr):
    interval: DistanceInterval
    arg: Expr

    def __str__(self) -> str:
        return f"(escape{self.interval} {self.arg})"

    @override
    def expand_intervals(self) -> "Expr":
        return EscapeOp(self.interval, self.arg.expand_intervals())


@dataclass(eq=True, frozen=True, slots=True)
class ReachOp(Expr):
    lhs: Expr
    interval: DistanceInterval
    rhs: Expr

    def __str__(self) -> str:
        return f"({self.lhs} reach{self.interval} {self.rhs})"

    @override
    def expand_intervals(self) -> "Expr":
        return ReachOp(
            interval=self.interval,
            lhs=self.lhs.expand_intervals(),
            rhs=self.rhs.expand_intervals(),
        )


@dataclass(eq=True, frozen=True, slots=True)
class NextOp(Expr):
    steps: Optional[int]
    arg: Expr

    def __str__(self) -> str:
        match self.steps:
            case None | 1:
                step_str = ""
            case t:
                step_str = f"[{t}]"
        return f"(X{step_str} {self.arg})"

    def __post_init__(self) -> None:
        match self.steps:
            case int(t) if t <= 0:
                raise ValueError("Next operator cannot have non-positive steps")
            case 1:
                # Collapse X[1] to X
                object.__setattr__(self, "steps", None)

    @override
    def expand_intervals(self) -> "Expr":
        arg = self.arg.expand_intervals()
        match self.steps:
            case None:
                return NextOp(None, arg)
            case t:
                expr = arg
                for _ in range(t):
                    expr = NextOp(None, expr)
                return expr


@dataclass(eq=True, frozen=True, slots=True)
class GloballyOp(Expr):
    interval: Optional[TimeInterval]
    arg: Expr

    def __post_init__(self) -> None:
        match self.interval:
            case None | TimeInterval(None, None) | TimeInterval(0, None):
                # All unbounded, so collapse
                object.__setattr__(self, "interval", None)

    def __str__(self) -> str:
        return f"(G{self.interval or ''} {self.arg})"

    @override
    def expand_intervals(self) -> "Expr":
        return NotOp(EventuallyOp(self.interval, NotOp(self.arg))).expand_intervals()


@dataclass(eq=True, frozen=True, slots=True)
class EventuallyOp(Expr):
    interval: Optional[TimeInterval]
    arg: Expr

    def __post_init__(self) -> None:
        match self.interval:
            case None | TimeInterval(None, None) | TimeInterval(0, None):
                # All unbounded, so collapse
                object.__setattr__(self, "interval", None)

    def __str__(self) -> str:
        return f"(F{self.interval or ''} {self.arg})"

    @override
    def expand_intervals(self) -> "Expr":
        match self.interval:
            case None | TimeInterval(None, None) | TimeInterval(0, None):
                # Unbounded F
                return EventuallyOp(None, self.arg.expand_intervals())
            case TimeInterval(0, int(t2)) | TimeInterval(None, int(t2)):
                # F[0, t2]
                arg = self.arg.expand_intervals()
                expr = arg
                for _ in range(t2):
                    expr = OrOp(expr, NextOp(None, arg))
                return expr
            case TimeInterval(int(t1), None):
                # F[t1,inf]
                assert t1 > 0
                return NextOp(t1, EventuallyOp(None, self.arg)).expand_intervals()
            case TimeInterval(int(t1), int(t2)):
                # F[t1, t2]
                assert t1 > 0
                # F[t1, t2] = X[t1] F[0,t2-t1] arg
                # Nested nexts until t1
                return NextOp(t1, EventuallyOp(TimeInterval(0, t2 - t1), self.arg)).expand_intervals()
            case TimeInterval():
                raise RuntimeError(f"Unexpected time interval {self.interval}")


@dataclass(eq=True, frozen=True, slots=True)
class UntilOp(Expr):
    lhs: Expr
    interval: Optional[TimeInterval]
    rhs: Expr

    def __str__(self) -> str:
        return f"({self.lhs} U{self.interval or ''} {self.rhs})"

    def __post_init__(self) -> None:
        match self.interval:
            case None | TimeInterval(None, None) | TimeInterval(0, None):
                # All unbounded, so collapse
                object.__setattr__(self, "interval", None)

    @override
    def expand_intervals(self) -> Expr:
        new_lhs = self.lhs.expand_intervals()
        new_rhs = self.rhs.expand_intervals()
        match self.interval:
            case None | TimeInterval(None | 0, None):
                # Just make an unbounded one here
                return UntilOp(new_lhs, None, new_rhs)
            case TimeInterval(t1, None):  # Unbounded end
                return GloballyOp(
                    interval=TimeInterval(0, t1),
                    arg=UntilOp(interval=None, lhs=new_lhs, rhs=new_rhs),
                ).expand_intervals()
            case TimeInterval(t1, _):
                z1 = EventuallyOp(interval=self.interval, arg=new_lhs).expand_intervals()
                until_interval = TimeInterval(t1, None)
                z2 = UntilOp(interval=until_interval, lhs=new_lhs, rhs=new_rhs).expand_intervals()
                return AndOp(z1, z2)


class _TransformTerminals(Transformer):

    def CNAME(self, s: Token) -> str:  # noqa: N802
        return str(s)

    def ESCAPED_STRING(self, s: Token) -> str:  # noqa: N802
        # Remove quotation marks
        return s[1:-1]

    def INT(self, tok: Token) -> int:  # noqa: N802
        return int(tok)

    def NUMBER(self, tok: Token) -> float:  # noqa: N802
        return float(tok)

    @v_args(inline=True)
    def phi(self, x: Token) -> Token:
        return x


def get_parser() -> Lark:
    with open(STREL_GRAMMAR_FILE, "r") as grammar:
        return Lark(
            grammar,
            start="phi",
            strict=True,
        )


def _to_ast_transformer() -> Transformer:
    ast = types.ModuleType("ast")
    for c in itertools.chain(
        [TimeInterval, DistanceInterval],
        Expr.__subclasses__(),
    ):
        ast.__dict__[c.__name__] = c
    return ast_utils.create_transformer(ast, _TransformTerminals())


TO_AST_TRANSFORMER = _to_ast_transformer()


def parse(expr: str) -> Expr:
    tree = get_parser().parse(expr)

    return TO_AST_TRANSFORMER.transform(tree)
