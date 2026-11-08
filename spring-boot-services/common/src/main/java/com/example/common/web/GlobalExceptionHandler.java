package com.example.common.web;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientResponseException;

/** Turns exceptions into RFC 7807 problem responses with a "code" property. */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(ApiException.class)
    ResponseEntity<ProblemDetail> handleApi(ApiException ex) {
        return problem(ex.status(), ex.code(), ex.getMessage());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<ProblemDetail> handleValidation(MethodArgumentNotValidException ex) {
        String msg = ex.getBindingResult().getFieldErrors().stream()
                .map(e -> e.getField() + " " + e.getDefaultMessage()).reduce((a, b) -> a + "; " + b).orElse("invalid request");
        return problem(HttpStatus.BAD_REQUEST, "VALIDATION_FAILED", msg);
    }

    /** An upstream platform service answered with an error: pass its status through. */
    @ExceptionHandler(RestClientResponseException.class)
    ResponseEntity<ProblemDetail> handleUpstream(RestClientResponseException ex) {
        HttpStatusCode status = ex.getStatusCode();
        return problem(HttpStatus.valueOf(status.value()), "UPSTREAM_" + status.value(),
                "Upstream call failed: " + ex.getResponseBodyAsString());
    }

    @ExceptionHandler(ResourceAccessException.class)
    ResponseEntity<ProblemDetail> handleUnreachable(ResourceAccessException ex) {
        log.warn("Upstream unreachable: {}", ex.getMessage());
        return problem(HttpStatus.SERVICE_UNAVAILABLE, "UPSTREAM_UNAVAILABLE", "A dependent service is unavailable");
    }

    @ExceptionHandler(DuplicateKeyException.class)
    ResponseEntity<ProblemDetail> handleDuplicate(DuplicateKeyException ex) {
        return problem(HttpStatus.CONFLICT, "DUPLICATE", "Resource already exists");
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<ProblemDetail> handleOther(Exception ex) {
        log.error("Unhandled error", ex);
        return problem(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "Unexpected error");
    }

    private ResponseEntity<ProblemDetail> problem(HttpStatus status, String code, String detail) {
        ProblemDetail pd = ProblemDetail.forStatusAndDetail(status, detail);
        pd.setProperty("code", code);
        return ResponseEntity.status(status).contentType(org.springframework.http.MediaType.APPLICATION_PROBLEM_JSON).body(pd);
    }
}
