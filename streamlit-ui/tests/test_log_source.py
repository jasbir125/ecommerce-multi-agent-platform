"""Rebuilding one request's journey from raw container logs."""
import json
import struct

import httpx

from log_source import LogSource, Row, frames, journey, related_ids, tidy

WF = "WF-AAAAAAAAAA"


def raw(ts, msg):
    return f"2026-11-09T06:20:{ts}Z {msg}"


def docker_frames(*lines):
    out = b""
    for line in lines:
        payload = (line + "\n").encode()
        out += b"\x01\x00\x00\x00" + struct.pack(">I", len(payload)) + payload
    return out


def source(logs_by_service):
    containers = [{"Id": s, "Names": [f"/platform-{s}"], "Labels": {"com.docker.compose.service": s}} for s in logs_by_service]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_ping":
            return httpx.Response(200, text="OK")
        if request.url.path == "/containers/json":
            return httpx.Response(200, json=containers)
        service = request.url.path.split("/")[2]
        return httpx.Response(200, content=docker_frames(*logs_by_service[service]))
    return LogSource(project="p", transport=httpx.MockTransport(handler))


SPRING_LINE = "2026-11-09T06:20:03.100Z INFO [workflow-service,trace=t1,corr=c-1110001,wf=" + WF + "] 1 --- [workflow-service] [exec-1] [t1-s1] http.access : -> POST /api/workflows caller=customer=CUST-1001 body={\"orderId\":\"ORD-8801\"}"


def test_frames_decodes_the_docker_stream_format():
    assert frames(docker_frames("one", "two")) == "one\ntwo\n"


def test_tidy_strips_spring_and_python_prefixes():
    lvl, text = tidy("2026-11-09T06:20:03.100Z INFO [wf,trace=t,corr=c,wf=W] 1 --- [wf] [exec-1] [t-s] http.access : -> POST /x")
    assert (lvl, text) == ("INFO", "http.access: -> POST /x")
    lvl, text = tidy("2026-11-09 06:20:03.816 INFO  [agent-host trace=t corr=c wf=W agent=order] agent.graph: AGENT > order received")
    assert (lvl, text) == ("INFO", "[order] agent.graph: AGENT > order received")


def test_journey_follows_the_workflow_id_and_every_correlation_id_used_on_that_case():
    rows = source({
        "workflow-service": [raw("03.100", f"2026-11-09 06:20:03.100 INFO  [wfs trace=t corr=c-1110001 wf={WF}] http.access: -> POST /api/workflows req={{}}"),
                             raw("03.900", f"2026-11-09 06:20:03.900 INFO  [wfs trace=t corr=c-1110001 wf={WF}] http.access: <- POST /api/workflows 201 40ms resp={{}}"),
                             raw("09.000", "2026-11-09 06:20:09.000 INFO  [wfs trace=u corr=other wf=WF-BBBBBBBBBB] unrelated case")],
        "approval-service": [raw("20.000", f"2026-11-09 06:20:20.000 INFO  [a trace=v corr=c-2220002 wf={WF}] http.access: -> POST /api/approvals/APR-1/decision"),
                             raw("20.500", "2026-11-09 06:20:20.500 INFO  [a trace=v corr=c-2220002 wf=] manager's click carries only its own corr id")],
        "postgres": [raw("01.000", f"{WF} infra lines are never read")],
    }).rows(0)
    hits, ids = journey(rows, WF)
    assert {"c-1110001", "c-2220002", WF} <= ids
    assert [h.service for h in hits] == ["workflow-service", "workflow-service", "approval-service", "approval-service"]
    assert all("unrelated" not in h.text and "infra" not in h.text for h in hits)
    assert hits == sorted(hits, key=lambda r: r.ts)


def test_the_uis_own_polling_is_left_out():
    poll = Row("2026-11-09T06:20:05Z", "streamlit", "INFO", f"ui.api: -> GET http://workflow-service:8088/api/workflows/{WF} user=x corr=poll-0001 body=-",
               f"ui.api: -> GET http://workflow-service:8088/api/workflows/{WF} user=x corr=poll-0001 body=-")
    real = Row("2026-11-09T06:20:03Z", "workflow-service", "INFO", "http.access: -> POST /api/workflows", f"corr=c-1 wf={WF} -> POST /api/workflows")
    hits, ids = journey([poll, real], WF)
    assert hits == [real] and "poll-0001" not in ids


def test_exchange_lines_are_recognised():
    assert Row("t", "s", "INFO", "http.access: -> POST /x", "").exchange
    assert Row("t", "s", "INFO", "[order] agent.graph: AGENT < order ok", "").exchange
    assert Row("t", "s", "INFO", "TOOL IN get_order_status args={}", "").exchange
    assert not Row("t", "s", "INFO", "Started Application in 9 seconds", "").exchange


def test_unavailable_when_the_socket_does_not_answer():
    def boom(request):
        raise httpx.ConnectError("no socket")
    src = LogSource(project="p", transport=httpx.MockTransport(boom))
    assert "ConnectError" in src.unavailable_reason()
    assert source({"workflow-service": []}).unavailable_reason() is None


def test_related_ids_ignores_rows_of_other_cases():
    rows = [Row("t", "s", "INFO", "x", f"corr=aaa111aa wf={WF}"), Row("t", "s", "INFO", "x", "corr=bbb222bb wf=WF-OTHER00000")]
    assert related_ids(rows, WF) == {WF, "aaa111aa"}
