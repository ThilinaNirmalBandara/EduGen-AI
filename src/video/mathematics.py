"""Restricted mathematical typesetting and deterministic numerical demonstrations."""

from __future__ import annotations

import math
import ast
import re
import shutil
import os
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass


class MathRenderingError(ValueError):
    pass


LATEX_INSTALL = "sudo apt install texlive-latex-base texlive-latex-recommended dvisvgm"
COMMANDS = set("alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi rho sigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Phi Psi Omega nabla partial frac dfrac tfrac sqrt sum prod int lim log ln exp sin cos tan min max argmin argmax cdot times div pm mp le leq ge geq neq approx equiv infty in notin to rightarrow leftarrow Rightarrow mapsto forall exists left right big Big bigg Bigg lvert rvert vert lVert rVert Vert langle rangle mathrm mathbf mathit mathcal mathbb text operatorname overline underline hat bar vec dot ddot underbrace overbrace quad qquad sin cos tan top perp ell varepsilon percent".split())
COMMANDS.add("prime")


def sanitize_math(expression: str) -> str:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 1000:
        raise MathRenderingError("Provide a non-empty mathematical expression of at most 1000 characters.")
    value = expression.strip()
    for left, right in (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]")):
        if value.startswith(left) and value.endswith(right):
            value = value[len(left):-len(right)].strip()
            break
    # Older JSON producers sometimes double-escape command backslashes.
    value = re.sub(r"\\{2,}(?=[A-Za-z])", lambda _: "\\", value)
    subscripts = str.maketrans('₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎', '0123456789+-=()')
    value = re.sub(r'[₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎]+',
                   lambda match: '_{' + match.group().translate(subscripts) + '}', value)
    replacements = {"θ": r"\theta ", "α": r"\alpha ", "η": r"\eta ", "∇": r"\nabla ",
                    "∂": r"\partial ", "β": r"\beta ", "λ": r"\lambda ", "π": r"\pi ",
                    "²": "^{2}", "³": "^{3}", "−": "-", "×": r"\times ", "·": r"\cdot ",
                    "≤": r"\leq ", "≥": r"\geq ", "∞": r"\infty ", "′": "'"}
    for symbol, replacement in replacements.items():
        value = value.replace(symbol, replacement)
    value = re.sub(r"(?<![\\A-Za-z])(?:theta|alpha|eta|nabla|beta|lambda|delta|epsilon|gamma|sigma|mu|pi)(?![A-Za-z])",
                   lambda match: "\\" + match.group(), value)
    value = value.replace("*", r"\cdot ")
    value = re.sub(r"_([A-Za-z][A-Za-z0-9]+)", r"_{\1}", value)
    if re.search(r"[^\x20-\x7e]", value) or any(char in value for char in ("$", "%", "#", "&", "~", "`")):
        raise MathRenderingError(f"Unsupported character in equation {expression!r}. Use plain mathematical LaTeX.")
    if "^^" in value or "\\\\" in value or re.search(r"\\[^A-Za-z{},!;: _]", value):
        raise MathRenderingError("Unsupported LaTeX escape; use a single mathematical expression.")
    unknown = set(re.findall(r"\\([A-Za-z]+)", value)) - COMMANDS
    if unknown:
        raise MathRenderingError(f"Unsupported LaTeX command(s): {', '.join(sorted(unknown))}. Only mathematical commands are allowed.")
    depth = 0
    for char in value:
        depth += (char == "{") - (char == "}")
        if depth < 0:
            raise MathRenderingError("Unbalanced braces in equation. Correct the LaTeX before rendering.")
    if depth or not value:
        raise MathRenderingError("Unbalanced or empty mathematical expression.")
    if re.search(r'[=+\-*/^_]\s*$', value) or re.search(r'=\s*=', value):
        raise MathRenderingError(f"Incomplete mathematical expression {expression!r}; supply the missing operand, not a guessed replacement.")
    if re.search(r'\\(?:dfrac|tfrac|frac)\s*\{\s*\}|\^\s*\{\s*\}|_\s*\{\s*\}', value):
        raise MathRenderingError('Empty mathematical argument. Complete the equation before rendering.')
    for match in re.finditer(r'\\(?:dfrac|tfrac|frac)\b', value):
        position = match.end()
        for _ in range(2):
            while position < len(value) and value[position].isspace(): position += 1
            if position >= len(value) or value[position] != '{':
                raise MathRenderingError('A fraction requires a complete numerator and denominator: use \\frac{numerator}{denominator}.')
            start, level = position+1, 1
            position += 1
            while position < len(value) and level:
                level += (value[position] == '{') - (value[position] == '}')
                position += 1
            if not value[start:position-1].strip():
                raise MathRenderingError('Empty fraction numerator or denominator. Complete the calculation.')
    return value


def numerical_equalities(text: str) -> list[str]:
    """Extract complete numeric chains, not suffixes inside longer calculations.

    For example, do not misread `1.2 - 0.2*2.4 = 0.72` as an assertion that
    `0.2*2.4 = 0.72`. Symbolic powers/subscripts are not numeric left operands.
    The extracted data is checked by the bounded arithmetic parser below.
    """
    number_token = r'(?:\d+(?:\.\d+)?|\.\d+)'
    atom = rf'(?:{number_token}|\([\d.\s()+*/^\-]+\))'
    arithmetic = rf'[+-]?{atom}(?:\s*(?:\*\*|[+*/^\-]|(?=\())\s*[+-]?{atom})*'
    pattern = rf'(?<![\w.]){arithmetic}\s*=\s*{arithmetic}(?:\s*=\s*{arithmetic})*'
    expressions = []
    for match in re.finditer(pattern, text):
        before = text[:match.start()].rstrip()
        after = text[match.end():].lstrip()
        # An unsupported symbolic expression must not be reduced to a plausible
        # numeric suffix. Dedicated equation fields still receive full checks.
        if before and before[-1] in '+-*/^_':
            continue
        if (after and after[0] in '+-*/^_') or after.startswith('='):
            continue
        expressions.append(match.group())
    return expressions


def validate_math_in_text(text: str) -> None:
    """Check embedded arithmetic without treating English prose as a TeX operand."""
    value = sanitize_math(text)
    value = value.replace(r'\cdot', '*').replace(r'\times', '*').replace(r'\div', '/')
    value = re.sub(r'\\(?:dfrac|tfrac|frac)\{([^{}]+)\}\{([^{}]+)\}', r'((\1)/(\2))', value)
    for expression in numerical_equalities(value):
        validate_calculation(expression)


def validate_calculation(expression: str) -> None:
    """Check complete expressions and constant arithmetic; do not claim symbolic proofs.

    Only an explicit, bounded arithmetic AST is interpreted. Symbolic equations
    still receive syntax/typesetting checks, but require authored/domain templates
    for semantic guarantees. No CAS guessing or generated code execution occurs.
    """
    value = sanitize_math(expression)
    if '=' not in value:
        return
    parts = value.split('=')
    if any(not part.strip() for part in parts):
        raise MathRenderingError(f'Incomplete equation: {expression!r}.')

    def constant(source):
        source = source.strip().replace(r'\cdot', '*').replace(r'\times', '*').replace(r'\div', '/')
        source = re.sub(r'\\(?:dfrac|tfrac|frac)\{([^{}]+)\}\{([^{}]+)\}', r'((\1)/(\2))', source)
        source = source.replace('{', '(').replace('}', ')').replace('^', '**')
        source = re.sub(r'(?<=[\d)])\s*(?=\()', '*', source)
        if not re.fullmatch(r'[\d.\s()+*/\-]+', source):
            return None
        try:
            tree = ast.parse(source, mode='eval')
            def visit(node):
                if isinstance(node, ast.Expression):
                    return visit(node.body)
                if isinstance(node, ast.Constant) and type(node.value) in (int, float) and abs(node.value) <= 1e6:
                    return float(node.value)
                if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                    return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
                if isinstance(node, ast.BinOp):
                    left, right = visit(node.left), visit(node.right)
                    if isinstance(node.op, ast.Add): return left + right
                    if isinstance(node.op, ast.Sub): return left - right
                    if isinstance(node.op, ast.Mult): return left * right
                    if isinstance(node.op, ast.Div): return left / right
                    if isinstance(node.op, ast.Pow) and abs(right) <= 6: return left ** right
                raise ValueError('Use bounded arithmetic.')
            result = visit(tree)
            if not math.isfinite(result) or abs(result) > 1e12:
                raise ValueError('Calculation exceeds supported bounds.')
            return result
        except (SyntaxError, ArithmeticError, ValueError) as exc:
            raise MathRenderingError(f'Invalid numerical calculation {expression!r}: {exc}') from exc
    previous = None
    for part in parts:
        current = constant(part)
        if current is not None and previous is not None and not math.isclose(current, previous, rel_tol=1e-4, abs_tol=0.00011):
            raise MathRenderingError(f'Incorrect numerical equality: {expression!r}. Recalculate it; values will not be silently replaced.')
        previous = current


def check_latex_dependencies() -> None:
    missing = [name for name in ("latex", "dvisvgm") if shutil.which(name) is None]
    if missing:
        raise MathRenderingError(f"MathTex requires {', '.join(missing)}. Install with: {LATEX_INSTALL}")


@contextmanager
def _tex_cache_lock(directory: Path):
    """Serialize compilers sharing SVG/DVI cache files, including parallel runs."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.compile.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def mathtex(expression: str, *, font_size: float = 38, color=None):
    value = sanitize_math(expression)
    check_latex_dependencies()
    from manim import MathTex, TexTemplate, config
    # A small, fixed preamble avoids requiring standalone.cls and prevents model
    # content from choosing packages, compilers, environments, or document options.
    template = TexTemplate(documentclass=r"\documentclass{article}",
                           preamble=r"\usepackage{amsmath}\usepackage{amssymb}",
                           post_doc_commands=r"\pagestyle{empty}")
    try:
        options = {"font_size": font_size, "tex_template": template}
        if color is not None:
            options["color"] = color
        with _tex_cache_lock(Path(config.get_dir('tex_dir'))):
            cleanup = config.no_latex_cleanup
            config.no_latex_cleanup = True
            try:
                return MathTex(value, **options)
            finally:
                config.no_latex_cleanup = cleanup
    except Exception as exc:
        raise MathRenderingError(
            f"MathTex failed for {value!r}: {exc}. Check the equation syntax and LaTeX log. "
            f"Required tools: {LATEX_INSTALL}"
        ) from exc


@dataclass(frozen=True)
class GradientUpdate:
    iteration: int
    x: float
    gradient: float
    learning_rate: float
    next_x: float
    loss: float
    next_loss: float


def gradient_updates(initial_x: float = 3.0, learning_rate: float = 0.2,
                     iterations: int = 4, offset: int = 0) -> list[GradientUpdate]:
    if not math.isfinite(initial_x) or abs(initial_x) > 3:
        raise ValueError("The quadratic demonstration requires a finite initial x between -3 and 3.")
    if not math.isfinite(learning_rate) or not 0 < learning_rate < 1:
        raise ValueError("Learning rate must be between 0 and 1 for this quadratic demonstration.")
    if not 1 <= iterations <= 12 or not 0 <= offset <= 100:
        raise ValueError("Use 1-12 iterations and an offset between 0 and 100.")
    x = initial_x * (1 - 2 * learning_rate) ** offset
    result = []
    for index in range(offset, offset + iterations):
        gradient = 2 * x
        next_x = x - learning_rate * gradient
        result.append(GradientUpdate(index, x, gradient, learning_rate, next_x, x*x, next_x*next_x))
        x = next_x
    return result


def number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def quadratic_loss_history(learning_rate: float, *, iterations: int = 6) -> list[float]:
    """Include stable and deliberately unstable rates in a bounded comparison."""
    if not math.isfinite(learning_rate) or not 0 < learning_rate <= 1.1 or not 1 <= iterations <= 12:
        raise ValueError('Use a finite learning rate in (0, 1.1] and 1-12 quadratic updates.')
    x, losses = 3.0, [9.0]
    for _ in range(iterations):
        x -= learning_rate * 2*x
        losses.append(x*x)
    return losses


def polynomial_function(expression: str):
    """Parse a small arithmetic grammar; never eval/exec model-provided code."""
    source = expression.split("=", 1)[-1].strip().replace("²", "^2").replace("³", "^3").replace("^", "**")
    if len(source) > 180:
        raise MathRenderingError("Graph expression is too long. Use a short polynomial in x.")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise MathRenderingError("Use an explicit polynomial such as x^2 or (x-2)^2; multiplication must use *.") from exc
    def calculate(node, x):
        if isinstance(node, ast.Expression):
            return calculate(node.body, x)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float) and abs(node.value) <= 1000:
            return float(node.value)
        if isinstance(node, ast.Name) and node.id == "x":
            return x
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return calculate(node.operand, x) * (-1 if isinstance(node.op, ast.USub) else 1)
        if isinstance(node, ast.BinOp):
            a, b = calculate(node.left, x), calculate(node.right, x)
            if isinstance(node.op, ast.Add):
                return a+b
            if isinstance(node.op, ast.Sub):
                return a-b
            if isinstance(node.op, ast.Mult):
                return a*b
            if isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant) and b.is_integer() and 0 <= b <= 6:
                return a**int(b)
        raise MathRenderingError("Unsupported graph expression. Only x, numbers, +, -, *, and integer powers 0-6 are supported.")
    def function(x):
        result = calculate(tree, float(x))
        if not math.isfinite(result) or abs(result) > 1e6:
            raise MathRenderingError("Graph values exceed the supported range. Rescale the polynomial.")
        return result
    function(0)
    return function
