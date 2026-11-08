package com.example.policy.retrieval;

import java.util.List;

/** The two candidate generators retrieval combines (implemented over Postgres; faked in tests). */
public interface CandidateSource {

    List<Candidate> vector(float[] query, String model, Filters filters, int limit);

    List<Candidate> keyword(String tsQuery, Filters filters, int limit);
}
