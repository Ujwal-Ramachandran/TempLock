"""AST-based structural analysis for content-gated-emit patterns.

Parses a Jinja2 chat template into an AST and walks conditional nodes
to identify the structural signature of the known backdoor pattern:
a branch that tests user/message content against a string literal
and emits a string literal or sets a system-role context.

Known limitations:
- Does not detect obfuscated triggers (split strings, encoded values,
  variable indirection).
- Does not detect triggers embedded in macros or imported templates.
- May produce false positives on templates that legitimately test
  message content (rare but possible).
"""

from __future__ import annotations

from dataclasses import dataclass

import jinja2
from jinja2 import nodes

from ghost_in_the_template import AnalysisError

# Field names that indicate user/message content (not structural properties).
# Structural properties like 'role', 'tool_calls', 'tool_call_id' are excluded
# because benign templates routinely branch on them.
CONTENT_FIELD_NAMES = frozenset({
    "content",
})

# Fields that are structural (role, tool metadata) - NOT suspicious
STRUCTURAL_FIELD_NAMES = frozenset({
    "role",
    "tool_calls",
    "tool_call_id",
    "name",
    "function",
    "function_call",
    "tools",
    "type",
})


@dataclass(frozen=True)
class Finding:
    """A suspicious structural pattern found in a template.

    Attributes:
        line: Line number in the template where the pattern starts.
        pattern: Name of the pattern detected.
        trigger_expression: Human-readable description of the trigger test.
        payload_snippet: First 200 chars of the emitted payload.
        confidence: Confidence level (high, medium, low).
    """

    line: int
    pattern: str
    trigger_expression: str
    payload_snippet: str
    confidence: str = "high"


@dataclass(frozen=True)
class StructuralResult:
    """Result of structural analysis.

    Attributes:
        clean: True if no suspicious patterns were found.
        target_path: Path or label of the analyzed template.
        findings: List of suspicious patterns found.
    """

    clean: bool
    target_path: str
    findings: list[Finding]


def analyze_template(
    template_string: str,
    source_label: str = "template",
) -> StructuralResult:
    """Analyze a Jinja2 template string for content-gated-emit patterns.

    Parses the template to an AST, walks all If nodes, and flags any
    branch whose test references message/user content compared against
    a literal and whose body emits a string literal or sets a system role.

    Args:
        template_string: The raw Jinja2 template string.
        source_label: Label for reporting (file path or identifier).

    Returns:
        A StructuralResult with findings.

    Raises:
        AnalysisError: If the template cannot be parsed.
    """
    env = jinja2.Environment()

    try:
        ast = env.parse(template_string)
    except jinja2.TemplateSyntaxError as exc:
        raise AnalysisError(
            f"Failed to parse Jinja2 template ({source_label}): {exc}"
        ) from exc

    findings: list[Finding] = []

    # Walk all If nodes in the AST
    for if_node in ast.find_all(nodes.If):
        _check_if_node(if_node, findings)

    return StructuralResult(
        clean=len(findings) == 0,
        target_path=source_label,
        findings=findings,
    )


def _check_if_node(if_node: nodes.If, findings: list[Finding]) -> None:
    """Check a single If node for the content-gated-emit pattern.

    The pattern has two parts:
    1. The test expression references message content AND compares it
       against a string literal (the trigger).
    2. The body contains an Output node that emits a string literal
       (the payload).
    """
    # Part 1: Does the test reference message content with a literal comparison?
    content_refs = _find_content_literal_comparisons(if_node.test)

    if not content_refs:
        return

    # Part 2: Does the body emit a string literal?
    payload_snippets = _find_string_emissions(if_node.body)

    if not payload_snippets:
        return

    # Both conditions met - this is a content-gated-emit pattern
    for trigger_desc, payload in zip(content_refs, payload_snippets or [payload_snippets[0]]):
        findings.append(Finding(
            line=if_node.lineno,
            pattern="content-gated-emit",
            trigger_expression=trigger_desc,
            payload_snippet=payload[:200],
            confidence="high",
        ))

    # If we found more payloads than triggers, report them too
    if len(payload_snippets) > len(content_refs):
        for payload in payload_snippets[len(content_refs):]:
            findings.append(Finding(
                line=if_node.lineno,
                pattern="content-gated-emit",
                trigger_expression=content_refs[0],
                payload_snippet=payload[:200],
                confidence="high",
            ))


def _find_content_literal_comparisons(test_node: nodes.Expr) -> list[str]:
    """Find comparisons of message content against string literals.

    Returns a list of human-readable descriptions of each comparison found.

    Recognized patterns:
    - 'literal' in message.content
    - 'literal' in message['content']
    - message.content == 'literal'
    - message['content'] == 'literal'
    - Combined with And/Or operators
    """
    results: list[str] = []

    if isinstance(test_node, nodes.And):
        results.extend(_find_content_literal_comparisons(test_node.left))
        results.extend(_find_content_literal_comparisons(test_node.right))
        return results

    if isinstance(test_node, nodes.Or):
        results.extend(_find_content_literal_comparisons(test_node.left))
        results.extend(_find_content_literal_comparisons(test_node.right))
        return results

    if isinstance(test_node, nodes.Not):
        results.extend(_find_content_literal_comparisons(test_node.node))
        return results

    if isinstance(test_node, nodes.Compare):
        return _check_compare_node(test_node)

    return results


def _check_compare_node(compare: nodes.Compare) -> list[str]:
    """Check a Compare node for content-vs-literal patterns."""
    results: list[str] = []

    expr = compare.expr
    for operand in compare.ops:
        op = operand.op
        other = operand.expr

        # Pattern: 'literal' in message.content
        # AST: Compare(Const('literal'), [Operand('in', <content_ref>)])
        if op == "in":
            if isinstance(expr, nodes.Const) and isinstance(expr.value, str):
                if _is_content_reference(other):
                    results.append(
                        f"'{expr.value}' in {_describe_node(other)}"
                    )

        # Pattern: message.content == 'literal'  or  'literal' == message.content
        elif op in ("eq", "ne"):
            if isinstance(other, nodes.Const) and isinstance(other.value, str):
                if _is_content_reference(expr):
                    results.append(
                        f"{_describe_node(expr)} {op} '{other.value}'"
                    )
            elif isinstance(expr, nodes.Const) and isinstance(expr.value, str):
                if _is_content_reference(other):
                    results.append(
                        f"'{expr.value}' {op} {_describe_node(other)}"
                    )

    return results


def _is_content_reference(node: nodes.Expr) -> bool:
    """Check if a node references message/user content.

    Returns True for patterns like:
    - message.content
    - message['content']
    - messages[-1].content
    - messages|last.content
    """
    # Direct attribute access: message.content
    if isinstance(node, nodes.Getattr):
        if node.attr in CONTENT_FIELD_NAMES:
            return True

    # Item access: message['content']
    if isinstance(node, nodes.Getitem):
        if isinstance(node.arg, nodes.Const) and node.arg.value in CONTENT_FIELD_NAMES:
            return True

    # Filtered access: message.content|lower
    if isinstance(node, nodes.Filter):
        if _is_content_reference(node.node):
            return True

    return False


def _find_string_emissions(body: list[nodes.Node]) -> list[str]:
    """Find string literal emissions in an If body.

    Looks for Output nodes containing TemplateData (literal strings)
    or Const nodes that emit string content.
    """
    emissions: list[str] = []

    for node in body:
        if isinstance(node, nodes.Output):
            for child in node.nodes:
                if isinstance(child, nodes.TemplateData):
                    text = child.data.strip()
                    # Skip pure whitespace or empty strings
                    if text and len(text) > 5:
                        emissions.append(text)
                elif isinstance(child, nodes.Const) and isinstance(child.value, str):
                    text = child.value.strip()
                    if text and len(text) > 5:
                        emissions.append(text)

        # Also check for Assign nodes that set variables
        # (e.g., {% set system_message = 'malicious instruction' %})
        elif isinstance(node, nodes.Assign):
            if isinstance(node.node, nodes.Const) and isinstance(node.node.value, str):
                text = node.node.value.strip()
                if text and len(text) > 5:
                    target_name = ""
                    if isinstance(node.target, nodes.Name):
                        target_name = node.target.name
                    emissions.append(f"[assign {target_name}] {text}")

    return emissions


def _describe_node(node: nodes.Expr) -> str:
    """Produce a human-readable description of an AST node."""
    if isinstance(node, nodes.Getattr):
        return f"{_describe_node(node.node)}.{node.attr}"
    if isinstance(node, nodes.Getitem):
        if isinstance(node.arg, nodes.Const):
            return f"{_describe_node(node.node)}['{node.arg.value}']"
        return f"{_describe_node(node.node)}[...]"
    if isinstance(node, nodes.Name):
        return node.name
    if isinstance(node, nodes.Const):
        return repr(node.value)
    if isinstance(node, nodes.Filter):
        return f"{_describe_node(node.node)}|{node.name}"
    return f"<{type(node).__name__}>"
