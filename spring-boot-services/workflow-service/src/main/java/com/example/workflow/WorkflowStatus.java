package com.example.workflow;

import java.util.Map;
import java.util.Set;

/** Allowed workflow states and the transitions between them. */
public final class WorkflowStatus {

    public static final String STARTED = "STARTED";
    public static final String INVESTIGATING = "INVESTIGATING";
    public static final String WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL";
    public static final String READY_FOR_EXECUTION = "READY_FOR_EXECUTION";
    public static final String COMPLETED = "COMPLETED";
    public static final String PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED";
    public static final String COMPENSATING = "COMPENSATING";
    public static final String ESCALATED = "ESCALATED";
    public static final String REJECTED = "REJECTED";
    public static final String FAILED = "FAILED";

    private static final Map<String, Set<String>> NEXT = Map.of(
            STARTED, Set.of(INVESTIGATING, ESCALATED, FAILED),
            INVESTIGATING, Set.of(WAITING_FOR_APPROVAL, ESCALATED, FAILED, COMPLETED),
            WAITING_FOR_APPROVAL, Set.of(READY_FOR_EXECUTION, ESCALATED, REJECTED, FAILED),
            READY_FOR_EXECUTION, Set.of(COMPLETED, COMPENSATING, PARTIALLY_COMPLETED, FAILED, ESCALATED),
            COMPENSATING, Set.of(PARTIALLY_COMPLETED, FAILED, ESCALATED));

    private WorkflowStatus() {
    }

    public static boolean canMove(String from, String to) {
        return NEXT.getOrDefault(from, Set.of()).contains(to);
    }

    public static boolean isKnown(String s) {
        return NEXT.containsKey(s) || Set.of(COMPLETED, PARTIALLY_COMPLETED, ESCALATED, REJECTED, FAILED).contains(s);
    }
}
