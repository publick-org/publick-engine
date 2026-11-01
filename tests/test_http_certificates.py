"""A host whose server leaves an intermediate out of its certificate chain (pipeline/http.py,
EXTRA_CERTIFICATES): verified with that intermediate added, for that host alone.

The chain is made here, the shape of SAU 10's: a root the trust store has (as ISRG Root X1),
a second root cross-signed by it (as ISRG Root YR), an intermediate issued by the second root
(as YR1), and the server's certificate. The server sends its certificate and the intermediate
only, as SAU 10's does."""

import datetime
import http.server
import ssl
import threading
from pathlib import Path

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from pipeline import http as engine_http

NOW = datetime.datetime.now(datetime.timezone.utc)


def name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def certificate(subject: str, key, issuer: str, issuer_key, ca: bool, host: str | None = None) -> x509.Certificate:
    builder = (x509.CertificateBuilder().subject_name(name(subject)).issuer_name(name(issuer))
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(NOW - datetime.timedelta(days=1)).not_valid_after(NOW + datetime.timedelta(days=30))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False))
    if ca:
        builder = builder.add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                                      content_commitment=False, key_encipherment=False,
                                                      data_encipherment=False, key_agreement=False,
                                                      encipher_only=False, decipher_only=False), critical=True)
    if issuer_key is not key:
        builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
                                        critical=False)
    if host:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


def pem(*certs) -> str:
    return "".join(c.public_bytes(serialization.Encoding.PEM).decode() for c in certs)


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    """A server for "localhost" sending its certificate and its issuer, and the files a client needs."""
    folder = tmp_path_factory.mktemp("certs")
    keys = {k: ec.generate_private_key(ec.SECP256R1()) for k in ("x1", "yr", "yr1", "leaf")}
    x1 = certificate("Test Root X1", keys["x1"], "Test Root X1", keys["x1"], ca=True)
    yr_by_x1 = certificate("Test Root YR", keys["yr"], "Test Root X1", keys["x1"], ca=True)
    yr1 = certificate("Test YR1", keys["yr1"], "Test Root YR", keys["yr"], ca=True)
    leaf = certificate("localhost", keys["leaf"], "Test YR1", keys["yr1"], ca=False, host="localhost")
    (folder / "server.pem").write_text(pem(leaf, yr1))
    (folder / "server.key").write_bytes(keys["leaf"].private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (folder / "store.pem").write_text(pem(x1))
    other = ec.generate_private_key(ec.SECP256R1())
    stranger = certificate("Test Root YR", keys["yr"], "Another Root", other, ca=True)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(folder / "server.pem", folder / "server.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"url": f"https://localhost:{server.server_port}/", "store": str(folder / "store.pem"),
           "cross_signed": pem(yr_by_x1), "stranger": pem(stranger)}
    server.shutdown()


def session(chain, extra: str | None = None) -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # no proxy, and the trust store given here, not the environment's
    s.verify = chain["store"]
    if extra:
        s.mount(chain["url"], engine_http.ExtraCertificate(extra))
    return s


def test_a_chain_missing_its_cross_signed_root_doesnt_verify(chain):
    with pytest.raises(requests.exceptions.SSLError):
        session(chain).get(chain["url"], timeout=10)


def test_the_cross_signed_root_completes_it(chain):
    assert session(chain, chain["cross_signed"]).get(chain["url"], timeout=10).text == "ok"


def test_verification_stays_on_with_an_extra_certificate(chain):
    """The extra certificate must itself chain to the trust store: a certificate for the same
    root signed by a root the store hasn't is no help."""
    with pytest.raises(requests.exceptions.SSLError):
        session(chain, chain["stranger"]).get(chain["url"], timeout=10)


def test_only_the_listed_hosts_get_their_certificate():
    s = requests.Session()
    engine_http.mount_extra_certificates(s)
    assert isinstance(s.get_adapter("https://www.sau10.nh.gov/school-board/meetings-agendas"), engine_http.ExtraCertificate)
    assert not isinstance(s.get_adapter("https://www.derrynh.gov/AgendaCenter"), engine_http.ExtraCertificate)
    assert not isinstance(s.get_adapter("https://www.sau10.nh.gov.example.com/"), engine_http.ExtraCertificate)
    client = engine_http.PoliteClient("test")
    assert isinstance(client.session.get_adapter("https://www.sau10.nh.gov/"), engine_http.ExtraCertificate)


def test_the_saved_certificate_is_lets_encrypts_root_yr_cross_signed_by_isrg_root_x1():
    """The file is the one Let's Encrypt publishes (letsencrypt.org/certs/gen-y/root-yr-by-x1.pem),
    and ISRG Root X1, in certifi's store, signed it."""
    import certifi

    saved = x509.load_pem_x509_certificate((engine_http.CERTS_DIR / "isrg-root-yr-by-x1.pem").read_bytes())
    assert saved.subject.rfc4514_string() == "CN=Root YR,O=ISRG,C=US"
    assert saved.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "ISRG Root X1"
    assert saved.fingerprint(hashes.SHA256()).hex(":").upper() == (
        "07:26:39:D0:B1:40:D5:BF:FA:E1:6A:D9:C3:F6:CC:60:86:04:06:21:F5:1E:E6:1A:6D:46:A8:91:5C:07:CF:76")
    store = Path(certifi.where()).read_text()
    block = store[store.index('# Label: "ISRG Root X1"'):]
    x1 = x509.load_pem_x509_certificate(block[:block.index("-----END CERTIFICATE-----") + 25].encode())
    assert x1.subject == saved.issuer
    saved.verify_directly_issued_by(x1)
