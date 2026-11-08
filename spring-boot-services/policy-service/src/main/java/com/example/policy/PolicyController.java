package com.example.policy;

import com.example.common.security.CallerHolder;
import com.example.policy.PolicyRecords.*;
import com.example.policy.ingest.IngestionJobService;
import com.example.policy.ingest.PolicyStore;
import com.example.policy.cache.SafeCache;
import com.example.policy.retrieval.RetrievalService;
import com.example.common.web.ApiException;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/policies")
public class PolicyController {

    private final RetrievalService retrieval;
    private final ValidationService validation;
    private final IngestionJobService jobs;
    private final PolicyStore store;
    private final SafeCache cache;

    public PolicyController(RetrievalService retrieval, ValidationService validation, IngestionJobService jobs,
                            PolicyStore store, SafeCache cache) {
        this.retrieval = retrieval;
        this.validation = validation;
        this.jobs = jobs;
        this.store = store;
        this.cache = cache;
    }

    @PostMapping("/search")
    public RagResponse search(@Valid @RequestBody SearchRequest request) {
        return retrieval.search(request);
    }

    @PostMapping("/validate")
    public ValidationResult validate(@Valid @RequestBody ValidateRequest request) {
        return validation.validate(request);
    }

    /** Admin-only: adding policy text changes what agents can retrieve. Async by default (202 + job id); wait=true runs inline. */
    @PostMapping("/ingest")
    public ResponseEntity<IngestJob> ingest(@Valid @RequestBody IngestRequest request, @RequestParam(defaultValue = "false") boolean wait) {
        CallerHolder.requireRole("ADMIN");
        return wait ? ResponseEntity.ok(jobs.runNow(request)) : ResponseEntity.status(HttpStatus.ACCEPTED).body(jobs.submit(request));
    }

    @GetMapping("/jobs/{jobId}")
    public IngestJob job(@PathVariable String jobId) {
        CallerHolder.requireRole("ADMIN");
        return jobs.get(jobId);
    }

    @GetMapping
    public List<PolicySummary> list() {
        return store.list();
    }

    /** Removes a policy from retrieval (kept for audit). Cached answers are invalidated. */
    @PostMapping("/{policyId}/archive")
    public PolicyDoc archive(@PathVariable long policyId) {
        CallerHolder.requireRole("ADMIN");
        if (!store.archive(policyId)) {
            throw new ApiException(HttpStatus.CONFLICT, "ALREADY_ARCHIVED", "Policy is already archived");
        }
        cache.bumpCorpusVersion();
        return validation.byId(policyId);
    }

    @GetMapping("/{policyId}")
    public PolicyDoc byId(@PathVariable long policyId) {
        return validation.byId(policyId);
    }

    @GetMapping("/{policyKey}/{version}")
    public PolicyDoc byVersion(@PathVariable String policyKey, @PathVariable String version) {
        return validation.byKeyVersion(policyKey, version);
    }
}
