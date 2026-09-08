import pytest

from weread_exporter.hooks import (
    DECRYPTION,
    INITIAL_STATE_REF,
    HookError,
    override_document,
    override_script,
)

URL = "https://cdn.weread.qq.com/web/wrwebnjlogic/js/app.abc.js"


def test_initial_state_actual_assignment_and_nonce_preserved():
    source = (
        '<script nonce="123">"use strict";const x="中文";'
        "window.__INITIAL_STATE__={reader:{}};</script>"
    )
    result = override_document("https://weread.qq.com/web/reader/123", source)
    assert 'nonce="123"' in result
    assert f"window.{INITIAL_STATE_REF}=Object.assign" in result
    assert result.index('const x="中文"') < result.index(INITIAL_STATE_REF)
    assert override_document("https://weread.qq.com/web/reader/123", result) == result


@pytest.mark.parametrize("access", ["x.DES.decrypt", "x['DES']['decrypt']", 'x["DES"].decrypt'])
def test_decryption_ast_supports_accessor_forms(access):
    source = f'const 中文="test";const fn=function(a,b,c,d){{return {access}(a,b)}};'
    result = override_script(URL, source)
    assert f"(window.{DECRYPTION}=function" in result
    assert result.startswith('const 中文="test";')
    assert override_script(URL, result) == result


def test_no_false_match_in_string():
    source = 'const text="x.DES.decrypt"; const f=function(){return text};'
    assert override_script(URL, source) == source


def test_ambiguous_crypto_fails_explicitly():
    source = "let f=function(){return x.DES.decrypt(a)},g=function(){return x.DES.decrypt(b)}"
    with pytest.raises(HookError, match="多个"):
        override_script(URL, source)


def test_other_domains_are_untouched():
    source = "window.__INITIAL_STATE__={reader:{}}"
    assert override_document("https://weread.qq.com.example.org/", source) == source
    source = "let f=function(){return x.DES.decrypt(a)}"
    assert override_script("https://example.org/wrwebnjlogic/js/a.js", source) == source
