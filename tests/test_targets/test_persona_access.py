"""Tests for the two-axis persona probe core (Q29 rule 3, step S1).

``http_probe`` GET, the general axis (``probe_general_access``), the model axis
(``probe_model_access``), and ``access_outcome`` — the ONE carrier of the verdict
table.  Rows and arms are numbered as in the Q29 rule-3 plan's verdict table.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from kanibako.targets.base import (
    AccessReport,
    AxisResult,
    GeneralAccess,
    ModelAccess,
    PersonaProbeOutcome,
    PersonaProbeVerdict,
    ProbeEvidence,
    ProbeResponse,
    access_outcome,
    http_probe,
    probe_general_access,
    probe_model_access,
    probe_outcome,
    probe_outcome_no_model,
)
from tests.test_targets.test_persona_settings import _ProbeServer

V = PersonaProbeVerdict
G = GeneralAccess
M = ModelAccess

_TOKEN = "sk-test-secret-0123456789"
_CRED = ("Authorization", f"Bearer {_TOKEN}")


@pytest.fixture
def server():
  srv = _ProbeServer()
  yield srv
  srv.close()


def _dead_endpoint() -> str:
  """An endpoint nothing listens on."""
  srv = _ProbeServer()
  srv.close()
  return srv.endpoint


def _sent(endpoint: str = "http://e.example", model: str | None = "m") -> ProbeEvidence:
  return ProbeEvidence(endpoint=endpoint, model=model, token_path=Path("/t/token"))


# ---------------------------------------------------------------------------
# http_probe: GET when there is no body
# ---------------------------------------------------------------------------


class TestHttpProbeGet:
  def test_no_body_is_a_GET_with_no_body_and_no_content_type(self, server):
    response = http_probe(server.endpoint + "/v1/models", headers={}, timeout=5.0)
    assert response.status == 200
    assert server.log == [("GET", "/v1/models", None, None, b"")]

  def test_a_body_is_still_a_JSON_POST(self, server):
    http_probe(server.endpoint + "/x", headers={}, body={"a": 1}, timeout=5.0)
    method, path, _, ctype, raw = server.log[0]
    assert (method, path, ctype, raw) == ("POST", "/x", "application/json", b'{"a": 1}')

  def test_a_redirect_is_refused(self, server):
    server.script = {("GET", False): 302}
    response = http_probe(server.endpoint + "/v1/models", headers={}, timeout=5.0)
    assert response.status == 302
    assert len(server.log) == 1

  def test_the_scrub_applies_to_a_GET(self, server):
    server.status = 403
    server.body = f'{{"error": "bad key {_TOKEN}"}}'.encode()
    response = http_probe(
      server.endpoint + "/v1/models", headers={_CRED[0]: _CRED[1]}, timeout=5.0,
    )
    assert response.status == 403
    assert _TOKEN not in response.body
    assert "bad key" in response.body

  def test_unreachable_is_status_None(self):
    assert http_probe(_dead_endpoint(), headers={}, timeout=2.0) == ProbeResponse(None)


# ---------------------------------------------------------------------------
# probe_general_access: every state, and what goes on the wire
# ---------------------------------------------------------------------------


def _general(server, *, credential=_CRED, timeout=5.0):
  return probe_general_access(
    server.endpoint + "/v1/models",
    headers={"anthropic-version": "2023-06-01"},
    credential=credential,
    sent=_sent(server.endpoint),
    timeout=timeout,
  )


class TestProbeGeneralAccess:
  @pytest.mark.parametrize(
    ("keyed", "bare", "state"),
    [
      (200, 401, G.VERIFIED),
      (200, 403, G.VERIFIED),
      (200, 200, G.OPEN),
      (200, 404, G.UNTESTED),
      (200, 500, G.UNTESTED),
    ],
  )
  def test_a_keyed_2xx_runs_the_control(self, server, keyed, bare, state):
    server.script = {("GET", True): keyed, ("GET", False): bare}
    result = _general(server)
    assert result.state is state
    assert result.control_status == bare
    assert [(m, p, a) for m, p, a, _, _ in server.log] == [
      ("GET", "/v1/models", _CRED[1]),
      ("GET", "/v1/models", None),
    ]

  @pytest.mark.parametrize(
    ("keyed", "state"),
    [(401, G.DENIED), (403, G.DENIED), (302, G.NO_ANSWER), (404, G.NO_ANSWER),
     (405, G.NO_ANSWER), (429, G.NO_ANSWER), (500, G.NO_ANSWER)],
  )
  def test_no_control_unless_the_keyed_GET_is_2xx(self, server, keyed, state):
    server.script = {("GET", True): keyed, ("GET", False): 401}
    result = _general(server)
    assert result.state is state
    assert result.control_status is None
    assert len(server.log) == 1

  @pytest.mark.parametrize(
    ("bare", "state"),
    [(200, G.OPEN), (401, G.DENIED), (403, G.DENIED), (404, G.NO_ANSWER), (500, G.NO_ANSWER)],
  )
  def test_keyless_sends_exactly_one_bare_GET(self, server, bare, state):
    server.script = {("GET", False): bare}
    result = _general(server, credential=None)
    assert result.state is state
    assert [(m, a) for m, _, a, _, _ in server.log] == [("GET", None)]

  def test_unreachable_is_NO_ANSWER(self):
    result = probe_general_access(
      _dead_endpoint() + "/v1/models", headers={}, credential=_CRED,
      sent=_sent(), timeout=2.0,
    )
    assert result.state is G.NO_ANSWER
    assert result.evidence.status is None

  def test_the_control_drops_a_credential_header_left_in_headers(self, server):
    server.script = {("GET", True): 200, ("GET", False): 401}
    result = probe_general_access(
      server.endpoint + "/v1/models", headers={"authorization": "Bearer stray-value"},
      credential=_CRED, sent=_sent(server.endpoint), timeout=5.0,
    )
    assert result.state is G.VERIFIED
    assert server.log[1][2] is None

  def test_the_evidence_is_the_first_GETs_and_names_no_model(self, server):
    server.script = {("GET", True): 403}
    result = _general(server)
    assert result.evidence == ProbeEvidence(
      endpoint=server.endpoint, model=None, token_path=Path("/t/token"),
      status=403, provider_text="{}", request="GET /v1/models",
    )

  def test_the_two_GETs_share_one_budget(self, server):
    """A keyed GET that spends most of the budget leaves the control too little time."""
    server.script = {("GET", True): 200, ("GET", False): 401}
    server.hangs = {"GET": 0.4}
    started = time.monotonic()
    result = _general(server, timeout=0.6)
    assert result.state is G.UNTESTED
    assert time.monotonic() - started < 1.0


# ---------------------------------------------------------------------------
# probe_model_access: today's POST, read as a state
# ---------------------------------------------------------------------------


class TestProbeModelAccess:
  @pytest.mark.parametrize(
    ("status", "model", "state"),
    [
      (200, "m", M.OK), (200, None, M.OK),
      (401, "m", M.DENIED), (403, None, M.DENIED),
      (400, None, M.MODEL_REQUIRED), (422, None, M.MODEL_REQUIRED),
      (400, "m", M.AMBIGUOUS), (422, "m", M.AMBIGUOUS),
      (404, None, M.AMBIGUOUS), (500, "m", M.AMBIGUOUS),
    ],
  )
  def test_states(self, server, status, model, state):
    server.status = status
    result = probe_model_access(
      server.endpoint + "/v1/messages", headers={}, body={"max_tokens": 1},
      sent=_sent(server.endpoint, model), timeout=5.0,
    )
    assert result.state is state
    assert result.evidence.status == status
    assert result.evidence.request == "POST /v1/messages"
    assert result.control_status is None
    assert server.log[0][0] == "POST"

  def test_unreachable(self):
    result = probe_model_access(
      _dead_endpoint() + "/v1/messages", headers={}, body={}, sent=_sent(), timeout=2.0,
    )
    assert result.state is M.UNREACHABLE


# ---------------------------------------------------------------------------
# access_outcome: the verdict table, one case per row and arm
# ---------------------------------------------------------------------------

_GENERAL_STATUS = {
  G.VERIFIED: 200, G.OPEN: 200, G.UNTESTED: 200, G.DENIED: 403, G.NO_ANSWER: 503,
}
_CONTROL_STATUS = {G.VERIFIED: 401, G.OPEN: 200, G.UNTESTED: 500}
_MODEL_STATUS = {
  M.OK: 200, M.DENIED: 403, M.MODEL_REQUIRED: 400, M.AMBIGUOUS: 500, M.UNREACHABLE: None,
}


def _report(
  general: GeneralAccess, model: ModelAccess, *, token: bool = True,
  model_id: str | None = "m", model_status: int | None = -1,
) -> AccessReport:
  token_path = Path("/t/token") if token else None
  base = ProbeEvidence(endpoint="http://e.example", token_path=token_path)
  status = _MODEL_STATUS[model] if model_status == -1 else model_status
  return AccessReport(
    general=AxisResult(
      general,
      ProbeEvidence(
        endpoint=base.endpoint, token_path=token_path,
        status=_GENERAL_STATUS[general], request="GET /v1/models",
      ),
      _CONTROL_STATUS.get(general),
    ),
    model=AxisResult(
      model,
      ProbeEvidence(
        endpoint=base.endpoint, model=model_id, token_path=token_path,
        status=status, provider_text="said no", request="POST /v1/messages",
      ),
    ),
  )


def _today(report: AccessReport) -> PersonaProbeOutcome:
  sent = report.model.evidence
  response = ProbeResponse(sent.status, sent.provider_text)
  if sent.model:
    return probe_outcome(response, sent)
  return probe_outcome_no_model(response, sent)


# (row, token, model id, GENERAL, MODEL, verdict)
_ROWS = [
  (1, True, "m", G.VERIFIED, M.OK, V.PASS),
  (2, True, "m", G.OPEN, M.OK, V.PASS),
  (3, True, "m", G.DENIED, M.OK, V.PASS),
  (4, True, "m", G.VERIFIED, M.DENIED, V.REJECTED),   # H1: model_exact; else INCONCLUSIVE
  (5, True, "m", G.OPEN, M.DENIED, V.REJECTED),
  (6, True, "m", G.DENIED, M.DENIED, V.REJECTED),
  (7, True, None, G.VERIFIED, M.OK, V.PASS),
  (8, True, None, G.OPEN, M.OK, V.PASS),
  (9, True, None, G.DENIED, M.OK, V.PASS),
  (10, True, None, G.VERIFIED, M.DENIED, V.REJECTED),  # H1: model_exact; else INCONCLUSIVE
  (11, True, None, G.OPEN, M.DENIED, V.REJECTED),
  (12, True, None, G.DENIED, M.DENIED, V.REJECTED),
  (13, False, "m", G.OPEN, M.OK, V.PASS),
  (14, False, "m", G.DENIED, M.OK, V.PASS),
  (15, False, "m", G.OPEN, M.DENIED, V.REJECTED),
  (16, False, "m", G.DENIED, M.DENIED, V.REJECTED),
  (17, False, None, G.OPEN, M.OK, V.PASS),
  (18, False, None, G.DENIED, M.OK, V.PASS),
  (19, False, None, G.OPEN, M.DENIED, V.REJECTED),
  (20, False, None, G.DENIED, M.DENIED, V.REJECTED),
]


class TestAccessOutcomeRows:
  @pytest.mark.parametrize("model_exact", [True, False])
  @pytest.mark.parametrize(
    ("row", "token", "model_id", "general", "model", "verdict"), _ROWS,
    ids=[f"row{r[0]}" for r in _ROWS],
  )
  def test_row(self, row, token, model_id, general, model, verdict, model_exact):
    report = _report(general, model, token=token, model_id=model_id)
    outcome = access_outcome(report, model_exact=model_exact)
    if row in (4, 10) and not model_exact:
      verdict = V.INCONCLUSIVE
    assert outcome.verdict is verdict
    assert outcome.access is report
    if verdict is V.REJECTED:
      # The deciding request is the model request: refusal_phrase reads its status.
      assert outcome.evidence == report.model.evidence
      assert outcome.refusal_phrase("http://e.example") == "refused the probe with HTTP 403"


class TestAccessOutcomeH1:
  """H1 = C [Jei:2026-10-11]: a verified credential whose model request is refused (rows 4, 10)
  REFUSES when the probe sent the box's own model id and WARNS when it could not.
  """

  @pytest.mark.parametrize("model_id", ["m", None], ids=["row4", "row10"])
  def test_an_exact_model_refuses(self, model_id):
    report = _report(G.VERIFIED, M.DENIED, model_id=model_id)
    outcome = access_outcome(report, model_exact=True)
    assert outcome == _today(report)._replace(access=report)
    assert outcome.verdict is V.REJECTED

  @pytest.mark.parametrize(("model_id", "refused"), [
    ("m", "refused the request for model 'm' with HTTP 403"),
    (None, "refused a request that named no model with HTTP 403"),
  ], ids=["row4", "row10"])
  def test_an_inexact_model_warns_and_launches(self, model_id, refused):
    report = _report(G.VERIFIED, M.DENIED, model_id=model_id)
    outcome = access_outcome(report, model_exact=False)
    assert outcome.verdict is V.INCONCLUSIVE
    assert "accepted this credential without a model" in outcome.reason
    assert refused in outcome.reason
    assert "token" not in outcome.reason  # never blames the token
    assert outcome.evidence == report.model.evidence
    assert outcome.access is report

  @pytest.mark.parametrize("general", [G.OPEN, G.UNTESTED, G.DENIED, G.NO_ANSWER])
  def test_an_inexact_model_still_refuses_without_a_verified_credential(self, general):
    report = _report(general, M.DENIED)
    assert access_outcome(report, model_exact=False).verdict is V.REJECTED


_MODEL_STATES = list(M)


class TestAccessOutcomeArms:
  @pytest.mark.parametrize("model", _MODEL_STATES)
  @pytest.mark.parametrize("model_id", ["m", None])
  def test_NO_ANSWER_is_todays_mapping_exactly(self, model, model_id):
    if model is M.MODEL_REQUIRED and model_id:
      pytest.skip("MODEL_REQUIRED exists only for an omitted model")
    report = _report(G.NO_ANSWER, model, model_id=model_id)
    assert access_outcome(report, model_exact=True) == _today(report)._replace(access=report)

  @pytest.mark.parametrize("model", _MODEL_STATES)
  def test_UNTESTED_is_as_OPEN(self, model):
    untested = access_outcome(_report(G.UNTESTED, model, model_id=None), model_exact=True)
    opened = access_outcome(_report(G.OPEN, model, model_id=None), model_exact=True)
    assert untested.verdict is opened.verdict

  def test_VERIFIED_and_MODEL_REQUIRED_passes(self):
    outcome = access_outcome(_report(G.VERIFIED, M.MODEL_REQUIRED, model_id=None),
                             model_exact=True)
    assert outcome.verdict is V.PASS

  @pytest.mark.parametrize("general", [G.OPEN, G.UNTESTED, G.NO_ANSWER])
  def test_no_verification_and_MODEL_REQUIRED_is_not_applicable(self, general):
    outcome = access_outcome(_report(general, M.MODEL_REQUIRED, model_id=None),
                             model_exact=True)
    assert outcome.verdict is V.NOT_APPLICABLE

  def test_DENIED_and_MODEL_REQUIRED_is_inconclusive(self):
    report = _report(G.DENIED, M.MODEL_REQUIRED, model_id=None)
    outcome = access_outcome(report, model_exact=True)
    assert outcome.verdict is V.INCONCLUSIVE
    assert "HTTP 403" in outcome.reason and "HTTP 400" in outcome.reason
    assert outcome.evidence == report.general.evidence

  @pytest.mark.parametrize("model", [M.AMBIGUOUS, M.UNREACHABLE])
  def test_an_undecided_model_axis_names_the_general_result(self, model):
    reasons = set()
    for general in (G.VERIFIED, G.OPEN, G.UNTESTED, G.DENIED):
      report = _report(general, model)
      outcome = access_outcome(report, model_exact=True)
      today = _today(report)
      assert outcome.verdict is V.INCONCLUSIVE
      assert outcome.reason.startswith(today.reason)
      assert outcome.reason != today.reason
      assert outcome.evidence == today.evidence
      reasons.add(outcome.reason)
    assert len(reasons) == 4


# ---------------------------------------------------------------------------
# No regression: outside the changed cells, the verdict is today's
# ---------------------------------------------------------------------------

_STATUSES = [200, 204, 400, 401, 403, 404, 422, 429, 500, None]


def _model_state(status: int | None, omitted: bool) -> ModelAccess:
  if status is None:
    return M.UNREACHABLE
  if 200 <= status < 300:
    return M.OK
  if status in (401, 403):
    return M.DENIED
  if omitted and status in (400, 422):
    return M.MODEL_REQUIRED
  return M.AMBIGUOUS


def _changed(general: GeneralAccess, model: ModelAccess, model_exact: bool) -> bool:
  """The cells whose verdict differs from today's (the H1 cells only for an inexact model)."""
  if model is M.DENIED and general is G.VERIFIED:
    return not model_exact
  return model is M.MODEL_REQUIRED and general in (G.VERIFIED, G.DENIED)


class TestNoRegression:
  @pytest.mark.parametrize("model_exact", [True, False])
  @pytest.mark.parametrize("model_id", ["m", None])
  @pytest.mark.parametrize("status", _STATUSES)
  @pytest.mark.parametrize("general", list(G))
  def test_every_other_cell_keeps_todays_verdict(self, general, status, model_id,
                                                 model_exact):
    model = _model_state(status, omitted=not model_id)
    if _changed(general, model, model_exact):
      pytest.skip("a changed cell")
    report = _report(general, model, model_id=model_id, model_status=status)
    outcome = access_outcome(report, model_exact=model_exact)
    today = _today(report)
    assert outcome.verdict is today.verdict
    assert outcome.evidence == today.evidence

  def test_the_model_axis_states_match_probe_model_access(self, server):
    """The table above reads MODEL states the way `probe_model_access` does."""
    for status in [s for s in _STATUSES if s is not None]:
      for model_id in ("m", None):
        server.status = status
        result = probe_model_access(
          server.endpoint + "/v1/messages", headers={}, body={},
          sent=_sent(server.endpoint, model_id), timeout=5.0,
        )
        assert result.state is _model_state(status, omitted=not model_id)


# ---------------------------------------------------------------------------
# PersonaProbeOutcome: the appended field changes nothing for existing callers
# ---------------------------------------------------------------------------


class TestOutcomeWithoutAccess:
  def test_built_without_access(self):
    assert PersonaProbeOutcome(V.PASS).access is None
    assert PersonaProbeOutcome.passed() == (V.PASS, None, None, None)

  def test_refusal_phrase_unchanged(self):
    evidence = ProbeEvidence(endpoint="http://e.example", status=401)
    outcome = PersonaProbeOutcome.rejected(evidence)
    assert outcome.access is None
    assert outcome.refusal_phrase("http://e.example") == "refused the probe with HTTP 401"
