"""Authentication and authorization of the agent host API, with real RSA keys and the real router."""
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.exceptions import PyJWKClientError

from app.api.routes import router
from app.config import Settings
from app.security import JwtVerifier, check_startup

ISS, AUD, SERVICE_TOKEN = "order-recovery-auth", "order-recovery-api", "local-demo-token"


def make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


PRIV = make_key()
OTHER = make_key()


class FakeKeys:
    """Stands in for PyJWKClient: knows one public key, by kid."""

    def get_signing_key_from_jwt(self, token):
        kid = jwt.get_unverified_header(token).get("kid")
        if kid != "k1":
            raise PyJWKClientError(f"unknown kid {kid}")
        return SimpleNamespace(key=PRIV.public_key())


def token(user="alice", customer="CUST-1001", roles=("CUSTOMER",), key=PRIV, kid="k1", aud=AUD, iss=ISS, ttl=900, alg="RS256"):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": user, "iat": now, "exp": now + ttl, "roles": list(roles)}
    if customer:
        claims["customer_id"] = customer
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


@pytest.fixture
def verifier():
    return JwtVerifier("http://unused", ISS, AUD, client=FakeKeys())


# ------------------------------------------------------------------- verifier
def test_a_valid_token_gives_identity_and_roles(verifier):
    p = verifier.verify(token(roles=("customer",)))
    assert p.customer == "CUST-1001" and p.roles == {"CUSTOMER"} and p.via_jwt and p.subject == "alice"


@pytest.mark.parametrize("bad", [
    lambda: token(ttl=-10),                                   # expired
    lambda: token(key=OTHER),                                 # signed by another key
    lambda: token(kid="other"),                               # unknown key id
    lambda: token(aud="some-other-api"),                      # wrong audience
    lambda: token(iss="evil-issuer"),                         # wrong issuer
    lambda: jwt.encode({"iss": ISS, "aud": AUD, "sub": "x", "exp": int(time.time()) + 60, "iat": int(time.time())}, "secret-secret-secret-secret-1234", algorithm="HS256", headers={"kid": "k1"}),
    lambda: jwt.encode({"iss": ISS, "aud": AUD, "sub": "x", "exp": int(time.time()) + 60, "iat": int(time.time())}, None, algorithm="none", headers={"kid": "k1"}),
    lambda: "not.a.jwt",
])
def test_forged_expired_and_malformed_tokens_are_rejected(verifier, bad):
    with pytest.raises(Exception):
        verifier.verify(bad())


def test_tampered_claims_break_the_signature(verifier):
    import base64
    h, p, s = token(roles=("CUSTOMER",)).split(".")
    evil = base64.urlsafe_b64encode(base64.urlsafe_b64decode(p + "==").replace(b"CUSTOMER", b"MANAGER")).rstrip(b"=").decode()
    with pytest.raises(Exception):
        verifier.verify(f"{h}.{evil}.{s}")


def test_missing_required_claims_are_rejected(verifier):
    now = int(time.time())
    no_sub = jwt.encode({"iss": ISS, "aud": AUD, "iat": now, "exp": now + 60}, PRIV, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(Exception):
        verifier.verify(no_sub)


# ---------------------------------------------------------------------- startup
def test_production_refuses_the_demo_token_and_a_missing_jwks():
    with pytest.raises(RuntimeError, match="demo value"):
        check_startup(Settings(platform_env="production", mcp_service_token=SERVICE_TOKEN, auth_jwks_url="http://x"))
    with pytest.raises(RuntimeError, match="AUTH_JWKS_URL"):
        check_startup(Settings(platform_env="production", mcp_service_token="x" * 40))
    check_startup(Settings(platform_env="production", mcp_service_token="x" * 40, auth_jwks_url="http://x"))   # fine
    check_startup(Settings(platform_env="dev"))                                                              # warns only


# ----------------------------------------------------------------------- routes
class FakeRunner:
    """Just enough of RunManager for the routes under test."""
    def __init__(self):
        from app.guardrails.input_guardrails import RunRateLimiter
        self.ask_limiter = RunRateLimiter(100)
        self.deps = SimpleNamespace()

    async def get(self, wf):
        return {"workflowId": wf, "customerId": "CUST-1001", "status": "COMPLETED"}

    async def trace(self, wf):
        return {"workflowId": wf, "trace": []}

    async def start(self, **kw):
        return {"workflowId": "WF-1", "customer": kw["customer_id"]}

    async def resume(self, wf):
        return {"workflowId": wf}


@pytest.fixture
def client(verifier):
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(mcp_service_token=SERVICE_TOKEN)
    app.state.runner = FakeRunner()
    app.state.jwt = verifier
    return TestClient(app)


def bearer(**kw):
    return {"Authorization": f"Bearer {token(**kw)}"}


def test_no_credentials_is_401(client):
    assert client.get("/api/agent/runs/WF-1").status_code == 401


def test_a_customer_reads_their_own_run_but_not_someone_elses(client):
    assert client.get("/api/agent/runs/WF-1", headers=bearer()).status_code == 200
    r = client.get("/api/agent/runs/WF-1", headers=bearer(user="bob", customer="CUST-2002"))
    assert r.status_code == 403
    assert client.get("/api/agent/runs/WF-1/trace", headers=bearer(user="bob", customer="CUST-2002")).status_code == 403


def test_an_operator_can_read_any_run(client):
    assert client.get("/api/agent/runs/WF-1", headers=bearer(user="olivia", customer=None, roles=("OPERATOR",))).status_code == 200


def test_forged_identity_headers_are_ignored_when_a_user_token_is_used(client):
    h = {**bearer(user="bob", customer="CUST-2002"), "X-Customer-Id": "CUST-1001", "X-Roles": "ADMIN,OPERATOR"}
    assert client.get("/api/agent/runs/WF-1", headers=h).status_code == 403            # still bob: no access to CUST-1001's run


def test_a_bad_bearer_token_is_refused_even_with_a_valid_service_token(client):
    h = {"Authorization": "Bearer garbage", "X-Service-Token": SERVICE_TOKEN, "X-Roles": "ADMIN"}
    r = client.get("/api/agent/runs/WF-1", headers=h)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "INVALID_TOKEN"


def test_trusted_components_act_for_users_with_the_service_token(client):
    h = {"X-Service-Token": SERVICE_TOKEN, "X-Customer-Id": "CUST-1001"}
    assert client.get("/api/agent/runs/WF-1", headers=h).status_code == 200
    assert client.get("/api/agent/runs/WF-1", headers={**h, "X-Customer-Id": "CUST-2002"}).status_code == 403
    assert client.get("/api/agent/runs/WF-1", headers={"X-Service-Token": "wrong"}).status_code == 401


def test_a_customer_cannot_start_a_run_for_someone_else(client):
    body = {"customerId": "CUST-2002", "orderId": "ORD-1", "message": "hi"}
    assert client.post("/api/agent/runs", json=body, headers=bearer()).status_code == 403
    ok = client.post("/api/agent/runs", json={**body, "customerId": "CUST-1001"}, headers=bearer())
    assert ok.status_code == 200 and ok.json()["customer"] == "CUST-1001"


def test_staff_tokens_cannot_start_recoveries_on_a_customers_behalf(client):
    body = {"customerId": "CUST-1001", "orderId": "ORD-1", "message": "hi"}
    ops = bearer(user="olivia", customer=None, roles=("OPERATOR",))
    assert client.post("/api/agent/runs", json=body, headers=ops).status_code == 403


def test_admin_metrics_need_a_staff_role(client):
    assert client.get("/api/agent/admin/metrics", headers=bearer()).status_code == 403
    assert client.get("/api/agent/admin/metrics", headers={"X-Service-Token": SERVICE_TOKEN}).status_code == 403
    # an operator passes the role check (the database is not available in this test, so it is a 503, not a 403)
    r = client.get("/api/agent/admin/metrics", headers=bearer(user="olivia", customer=None, roles=("OPERATOR",)))
    assert r.status_code in (200, 503)


def test_oversized_bodies_and_security_headers():
    from app.main import app
    c = TestClient(app)                                                   # no lifespan: only the middleware runs
    big = c.post("/api/agent/runs", content=b"x", headers={"Content-Length": "1000000", "Content-Type": "application/json"})
    assert big.status_code == 413 and big.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"
    h = c.get("/health")
    assert h.headers["x-content-type-options"] == "nosniff" and h.headers["cache-control"] == "no-store"
