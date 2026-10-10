"""Exact decimal arithmetic for the model: it writes the expressions, this evaluates them without floating point."""
import ast
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, getcontext

getcontext().prec = 50


def round_half_up(x, places=2):
    return Decimal(x).quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)


FUNCTIONS = {
    "round_half_up": round_half_up,
    "ceil": lambda x: Decimal(x).to_integral_value(rounding=ROUND_CEILING),
    "floor": lambda x: Decimal(x).to_integral_value(rounding=ROUND_FLOOR),
    "min": min, "max": max, "abs": abs,
    "sum": lambda items: sum((Decimal(i) for i in items), Decimal(0)),
}


def _eval(node, names):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        raise ValueError(f"unknown name {node.id}")
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, names), _eval(node.right, names)
        ops = {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b, ast.Div: lambda: a / b,
               ast.FloorDiv: lambda: a // b, ast.Mod: lambda: a % b, ast.Pow: lambda: a ** b}
        return ops[type(node.op)]()
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _eval(node.operand, names)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, names) for e in node.elts]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS:
        return FUNCTIONS[node.func.id](*[_eval(a, names) for a in node.args])
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        a, b = _eval(node.left, names), _eval(node.comparators[0], names)
        ops = {ast.Lt: a < b, ast.LtE: a <= b, ast.Gt: a > b, ast.GtE: a >= b, ast.Eq: a == b, ast.NotEq: a != b}
        return ops[type(node.ops[0])]
    raise ValueError(f"unsupported expression: {ast.dump(node)[:80]}")


def calculate(expression: str) -> str:
    """Evaluates lines like `a = 1.5 * 3` or bare expressions; returns each result. German decimal commas must be
    converted by the caller (1.234,56 -> 1234.56)."""
    names, out = {}, []
    for line in expression.replace(";", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            tree = ast.parse(line)
            stmt = tree.body[0]
            if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
                value = _eval(stmt.value, names)
                names[stmt.targets[0].id] = value
                out.append(f"{stmt.targets[0].id} = {_fmt(value)}")
            else:
                out.append(f"{line} = {_fmt(_eval(stmt.value, names))}")
        except Exception as error:  # noqa: BLE001
            out.append(f"{line} -> error: {error}")
    return "\n".join(out)


def _fmt(value):
    if isinstance(value, list):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f") if value == value.to_integral_value() else format(value, "f").rstrip("0")
    return str(value)
