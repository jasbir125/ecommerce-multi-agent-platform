package com.example.policy;

import com.example.common.security.CallerHolder;
import com.example.policy.PolicyRecords.*;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/policies")
public class PolicyController {

    private final RetrievalService retrieval;
    private final ValidationService validation;
    private final IngestionService ingestion;

    public PolicyController(RetrievalService retrieval, ValidationService validation, IngestionService ingestion) {
        this.retrieval = retrieval;
        this.validation = validation;
        this.ingestion = ingestion;
    }

    @PostMapping("/search")
    public RagResponse search(@Valid @RequestBody SearchRequest request) {
        return retrieval.search(request);
    }

    @PostMapping("/validate")
    public ValidationResult validate(@Valid @RequestBody ValidateRequest request) {
        return validation.validate(request);
    }

    /** Admin-only: adding policy text changes what agents can retrieve. */
    @PostMapping("/ingest")
    public IngestResult ingest(@Valid @RequestBody IngestRequest request) {
        CallerHolder.requireRole("ADMIN");
        return ingestion.ingest(request);
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
