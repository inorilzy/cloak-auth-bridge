from cloak_auth_bridge.crypto import create_proof, verify_proof


def test_client_and_server_proofs_are_domain_separated() -> None:
    token = "0123456789abcdef"
    challenge = "abcdefghijklmnop"

    client = create_proof(token, "client", challenge)
    server = create_proof(token, "server", challenge)

    assert client != server
    assert verify_proof(token, "client", challenge, client)
    assert not verify_proof(token, "server", challenge, client)


def test_unknown_hmac_role_is_rejected() -> None:
    try:
        create_proof("0123456789abcdef", "peer", "abcdefghijklmnop")
    except ValueError as error:
        assert "role" in str(error)
    else:
        raise AssertionError("unknown role was accepted")
