package com.example.policy.retrieval;

import java.time.LocalDate;

/**
 * Metadata filters. Null region means global documents only and null tenant means "default";
 * null segment/category/policyType/version apply no filter.
 */
public record Filters(LocalDate asOf, String region, String policyType, String tenant, String segment,
                      String category, String version) {

    public static Filters of(LocalDate asOf, String region, String policyType, String tenant, String segment,
                             String category, String version) {
        return new Filters(asOf == null ? LocalDate.now() : asOf,
                blank(region) ? "ALL" : region.toUpperCase(), blank(policyType) ? null : policyType.toLowerCase(),
                blank(tenant) ? "default" : tenant, blank(segment) ? null : segment, blank(category) ? null : category,
                blank(version) ? null : version);
    }

    private static boolean blank(String s) {
        return s == null || s.isBlank();
    }

    public String cacheKey() {
        return asOf + "|" + region + "|" + policyType + "|" + tenant + "|" + segment + "|" + category + "|" + version;
    }
}
