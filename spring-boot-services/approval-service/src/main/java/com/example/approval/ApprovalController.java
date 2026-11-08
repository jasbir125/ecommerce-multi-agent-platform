package com.example.approval;

import com.example.approval.ApprovalRecords.*;
import com.example.common.security.CallerHolder;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/approvals")
public class ApprovalController {

    private final ApprovalService service;

    public ApprovalController(ApprovalService service) {
        this.service = service;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Approval create(@Valid @RequestBody CreateRequest request) {
        CallerHolder.requireInternal();
        return service.create(request);
    }

    @GetMapping
    public List<Approval> list(@RequestParam(required = false) String status) {
        CallerHolder.requireStaffIfUser("OPERATOR", "MANAGER", "APPROVER", "ADMIN");
        return service.list(status);
    }

    @GetMapping("/{approvalId}")
    public Approval get(@PathVariable String approvalId) {
        CallerHolder.requireStaffIfUser("OPERATOR", "MANAGER", "APPROVER", "ADMIN");
        return service.get(approvalId);
    }

    @PostMapping("/{approvalId}/decision")
    public Approval decide(@PathVariable String approvalId, @Valid @RequestBody DecisionRequest request) {
        return service.decide(approvalId, request);
    }
}
