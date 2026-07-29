import ssl

from utils.tls import create_outbound_ssl_context


def test_outbound_ssl_context_keeps_peer_and_hostname_verification_enabled():
    context = create_outbound_ssl_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
