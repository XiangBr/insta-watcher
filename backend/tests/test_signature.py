from app.meta.signature import tokens_equal, verify_signature
from tests.conftest import sign

BODY = b'{"object":"instagram","entry":[]}'
SECRET_A = "a1" * 16
SECRET_B = "b2" * 16
SECRETS = (SECRET_A.encode(), SECRET_B.encode())


def test_valid_signature_with_first_secret() -> None:
    assert verify_signature(BODY, sign(BODY, SECRET_A), SECRETS)


def test_valid_signature_with_second_secret() -> None:
    assert verify_signature(BODY, sign(BODY, SECRET_B), SECRETS)


def test_uppercase_hex_is_accepted() -> None:
    header = sign(BODY, SECRET_A)
    assert verify_signature(BODY, "sha256=" + header.removeprefix("sha256=").upper(), SECRETS)


def test_invalid_signature() -> None:
    assert not verify_signature(BODY, sign(BODY, "c3" * 16), SECRETS)


def test_missing_header() -> None:
    assert not verify_signature(BODY, None, SECRETS)
    assert not verify_signature(BODY, "", SECRETS)


def test_wrong_prefix() -> None:
    digest = sign(BODY, SECRET_A).removeprefix("sha256=")
    assert not verify_signature(BODY, "sha1=" + digest, SECRETS)
    assert not verify_signature(BODY, digest, SECRETS)


def test_altered_body() -> None:
    header = sign(BODY, SECRET_A)
    assert not verify_signature(BODY + b" ", header, SECRETS)
    assert not verify_signature(BODY.replace(b"instagram", b"instagram!"), header, SECRETS)


def test_empty_secret_never_matches() -> None:
    assert not verify_signature(BODY, sign(BODY, ""), (b"",))


def test_tokens_equal() -> None:
    assert tokens_equal("abc", "abc")
    assert not tokens_equal("abd", "abc")
    assert not tokens_equal(None, "abc")
    assert not tokens_equal("", "abc")
