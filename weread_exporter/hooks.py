"""Patch browser responses without executing or reimplementing the site's crypto in Python."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from tree_sitter import Language, Node, Parser
from tree_sitter_javascript import language

INITIAL_STATE_REF = "__INITIAL_STATE__REF__"
DECRYPTION = "__DECRYPTION__"
_LANGUAGE = Language(language())
_SCRIPT = re.compile(r"(<script\b[^>]*>)([\s\S]*?)(</script\s*>)", re.IGNORECASE)


class HookError(RuntimeError):
    pass


def _walk(node: Node):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.named_children))


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8") if node is not None else ""


def _property(node: Node) -> str:
    part = node.child_by_field_name("property") or node.child_by_field_name("index")
    return _text(part).strip("\"'")


def is_site_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "weread.qq.com"


def is_script_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"weread.qq.com", "cdn.weread.qq.com"}
        and "/wrwebnjlogic/js/" in parsed.path
        and parsed.path.endswith(".js")
    )


def override_document(url: str, html: str) -> str:
    if not is_site_url(url):
        return html

    def patch(match: re.Match) -> str:
        script = match[2]
        if "__INITIAL_STATE__" not in script or INITIAL_STATE_REF in script:
            return match[0]
        source = script.encode("utf-8")
        tree = Parser(_LANGUAGE).parse(source)
        if tree.root_node.has_error:
            raise HookError("无法解析页面初始化脚本，网站可能已更新")
        for node in _walk(tree.root_node):
            if node.type != "assignment_expression":
                continue
            left = node.child_by_field_name("left")
            if left is None or _property(left) != "__INITIAL_STATE__":
                continue
            if _text(left.child_by_field_name("object")) != "window":
                continue
            # Wrap the actual assignment, preserving its value and surrounding expressions.
            original = source[node.start_byte : node.end_byte]
            wrapped = (
                b"("
                + original
                + b",window."
                + INITIAL_STATE_REF.encode()
                + b"=Object.assign({},window.__INITIAL_STATE__),window.__INITIAL_STATE__)"
            )
            source = source[: node.start_byte] + wrapped + source[node.end_byte :]
            return match[1] + source.decode("utf-8") + match[3]
        return match[0]

    return _SCRIPT.sub(patch, html)


def override_script(url: str, script: str) -> str:
    if not is_script_url(url) or "decrypt" not in script or "DES" not in script:
        return script
    if DECRYPTION in script:
        return script
    source = script.encode("utf-8")
    tree = Parser(_LANGUAGE).parse(source)
    if tree.root_node.has_error:
        raise HookError("无法解析阅读器脚本，网站可能已更新")
    candidates: dict[int, Node] = {}
    for node in _walk(tree.root_node):
        if node.type not in {"member_expression", "subscript_expression"}:
            continue
        obj = node.child_by_field_name("object")
        if _property(node) != "decrypt" or obj is None or _property(obj) != "DES":
            continue
        parent = node.parent
        while parent is not None:
            if parent.type in {"function_expression", "arrow_function"}:
                candidates[parent.start_byte] = parent
                break
            parent = parent.parent
    if not candidates:
        return script
    if len(candidates) > 1:
        raise HookError("发现多个 DES 解密入口，无法可靠确定正文解密函数")
    node = next(iter(candidates.values()))
    source = (
        source[: node.start_byte]
        + b"(window."
        + DECRYPTION.encode()
        + b"="
        + source[node.start_byte : node.end_byte]
        + b")"
        + source[node.end_byte :]
    )
    if Parser(_LANGUAGE).parse(source).root_node.has_error:
        raise HookError("脚本注入后语法校验失败")
    return source.decode("utf-8")
