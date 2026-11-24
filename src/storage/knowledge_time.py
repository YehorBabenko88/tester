"""Historical knowledge projection; transaction-start timestamps are not commit evidence."""


def project_knowledge(facts, failure=None):
    # No schema version currently persists a trusted commit/visibility upper bound.
    # Even a full list cannot timestamp negative facts (absence of corruption).
    reasons=['historical_visibility_not_proven','missing_or_unproven_fact_timing']
    if not facts: reasons.append('no_timed_facts')
    if failure: reasons.append(failure)
    return dict(version=1,status='UNAVAILABLE',quality_available_at_ms=None,
                timestamp_semantics='transaction_start_not_visibility',
                historical_as_of=False,reasons=reasons,facts=facts)
