"""Conservative factoring, checked by an independent rectangle-subtraction engine."""

from dataclasses import dataclass
from itertools import product
from time import monotonic

from policy_sets import union


@dataclass(frozen=True)
class Rule:
    # device, network context, source zone, destination zone, protocol
    context: tuple
    sources: tuple
    destinations: tuple
    ports: tuple
    refs: tuple = ()

    @property
    def axes(self):
        return (self.sources, self.destinations, self.ports)

    @property
    def key(self):
        return self.context, self.axes


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, limit=200_000, seconds=5):
        self.remaining = limit
        self.deadline = monotonic() + seconds

    def tick(self):
        self.remaining -= 1
        if self.remaining < 0 or monotonic() > self.deadline:
            raise BudgetExceeded("等价校验达到计算预算")


def subtract_box(box, cover):
    """A minus B, returned as disjoint boxes (at most 2 per dimension)."""
    intersection = tuple((max(a, c), min(b, d)) for (a, b), (c, d) in zip(box, cover))
    if any(a >= b for a, b in intersection):
        return [box]
    remaining = list(box)
    result = []
    for axis, (low, high) in enumerate(intersection):
        a, b = remaining[axis]
        if a < low:
            piece = remaining.copy()
            piece[axis] = (a, low)
            result.append(tuple(piece))
        if high < b:
            piece = remaining.copy()
            piece[axis] = (high, b)
            result.append(tuple(piece))
        remaining[axis] = (low, high)
    return result


def difference(left, right, budget):
    by_context = {}
    for rule in right:
        by_context.setdefault(rule.context, []).append(rule)
    for rule in left:
        for box in product(*rule.axes):
            budget.tick()
            pieces = [box]
            for other in by_context.get(rule.context, []):
                if not pieces:
                    break
                for cover in product(*other.axes):
                    budget.tick()
                    next_pieces = []
                    for piece in pieces:
                        budget.tick()
                        next_pieces.extend(subtract_box(piece, cover))
                    pieces = next_pieces
                    if not pieces:
                        break
            if pieces:
                return {"context": rule.context, "box": pieces[0]}
    return None


def verify(before, after, budget=None):
    budget = budget or Budget()
    added = difference(after, before, budget)
    removed = difference(before, after, budget)
    return {"equivalent": added is None and removed is None, "added": added, "removed": removed}


def factor(rules, budget):
    rules = sorted(rules, key=lambda r: (r.key, r.refs))
    steps = []
    while True:
        previous = len(rules)
        for axis in range(3):
            groups = {}
            for rule in rules:
                budget.tick()
                key = (rule.context, tuple(v for i, v in enumerate(rule.axes) if i != axis))
                groups.setdefault(key, []).append(rule)
            output = []
            for group in groups.values():
                axes = list(group[0].axes)
                axes[axis] = union(part for r in group for part in r.axes[axis])
                refs = tuple(sorted({ref for r in group for ref in r.refs}))
                output.append(Rule(group[0].context, *axes, refs))
                if len(group) > 1:
                    steps.append(
                        {
                            "dimension": ("sources", "destinations", "ports")[axis],
                            "input_count": len(group),
                            "refs": refs,
                        }
                    )
            rules = sorted(output, key=lambda r: (r.key, r.refs))
        if len(rules) == previous:
            return rules, steps


def optimize(original, enabled=True, budget=None):
    original = sorted(original, key=lambda r: (r.key, r.refs))
    if not enabled:
        return original, {
            "status": "disabled",
            "before": len(original),
            "after": len(original),
            "steps": [],
        }
    try:
        candidate, steps = factor(original, budget or Budget())
        # Separate budget and separate algorithm; no factoring assumptions here.
        check = (
            {"equivalent": True, "added": None, "removed": None}
            if [r.key for r in original] == [r.key for r in candidate]
            else verify(original, candidate, budget=budget)
        )
        if check["equivalent"]:
            return candidate, {
                "status": "verified",
                "before": len(original),
                "after": len(candidate),
                "steps": steps,
                **check,
            }
        reason = "优化结果不等价，已保留原始有效规则"
    except BudgetExceeded:
        check = {}
        reason = "优化或等价校验达到计算预算，已保留原始有效规则"
    return original, {
        "status": "unmerged",
        "before": len(original),
        "after": len(original),
        "reason": reason,
        "steps": [],
        "rejected_candidate": check,
    }
