package com.example.policy.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class QueryProcessorTest {

    private final QueryProcessor p = new QueryProcessor();

    @Test
    void normalizesWhitespaceAndLength() {
        assertThat(p.normalize("  is   my\n order \t late?  ")).isEqualTo("is my order late?");
        assertThat(p.normalize("x".repeat(900))).hasSize(500);
    }

    @Test
    void expandsSynonymsForKeywordSearch() {
        assertThat(p.expansionTerms("my order is delayed, can I get a refund")).contains("late", "overdue", "reimbursement");
    }

    @Test
    void tsQueryContainsOnlySafeTokens() {
        String ts = p.tsQuery("refund'; DROP TABLE x; -- (delayed) & | !");
        assertThat(ts).matches("[a-z0-9 |]+");
        assertThat(ts).contains("refund").contains("delayed");
    }
}
