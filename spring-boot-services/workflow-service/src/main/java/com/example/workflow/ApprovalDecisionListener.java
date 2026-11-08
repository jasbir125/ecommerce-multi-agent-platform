package com.example.workflow;

import com.example.common.events.EventEnvelope;
import com.example.common.events.InboxGuard;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Moves a workflow out of WAITING_FOR_APPROVAL when the approval is decided.
 * Idempotent: a duplicate delivery is ignored via the inbox table and the
 * transition key.
 */
@Component
public class ApprovalDecisionListener {

    private static final Logger log = LoggerFactory.getLogger(ApprovalDecisionListener.class);

    private final WorkflowService service;
    private final WorkflowRepository repo;
    private final InboxGuard inbox;
    private final ObjectMapper mapper;

    public ApprovalDecisionListener(WorkflowService service, WorkflowRepository repo, InboxGuard inbox, ObjectMapper mapper) {
        this.service = service;
        this.repo = repo;
        this.inbox = inbox;
        this.mapper = mapper;
    }

    @KafkaListener(topics = "workflow.approval.decided")
    public void onDecision(String message) throws Exception {
        EventEnvelope env = mapper.readValue(message, EventEnvelope.class);
        try {
            handle(env);
        } finally {
            com.example.common.events.EventLog.done();
        }
    }

    private void handle(EventEnvelope env) {
        com.example.common.events.EventLog.received(log, "workflow-service.approval", env, mapper);
        if (!inbox.firstTime("workflow-service.approval", env.eventId())) {
            com.example.common.events.EventLog.duplicate(log, "workflow-service.approval", env);
            return;
        }
        var wf = repo.findById(env.workflowId());
        if (wf.isEmpty() || !WorkflowStatus.WAITING_FOR_APPROVAL.equals(wf.get().status())) {
            log.info("Ignoring approval decision for workflow {} (not waiting)", env.workflowId());
            return;
        }
        Map<String, Object> p = env.payload();
        boolean approved = "APPROVED".equals(p.get("decision"));
        service.applyTransition(wf.get(), "approval-" + p.get("approvalId"),
                approved ? WorkflowStatus.READY_FOR_EXECUTION : WorkflowStatus.REJECTED,
                Map.of("approvalId", p.get("approvalId"), "approvalDecision", p.get("decision")),
                "approval " + p.get("decision"));
    }
}
