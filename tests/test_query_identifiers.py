from knowledge.services.query_identifiers import extract_exact_identifiers


def test_extracts_api_paths_symbols_fields_and_normalized_gateway_variant():
    identifiers = extract_exact_identifiers(
        "查询 gateway/gateway/sys/flow/process/instance/getInstanceDetail 的 "
        "ProcessInstanceService.getDetail 和 isApprovalAdmin 字段"
    )

    assert "/gateway/gateway/sys/flow/process/instance/getInstanceDetail" in identifiers
    assert "/gateway/sys/flow/process/instance/getInstanceDetail" in identifiers
    assert "ProcessInstanceService.getDetail" in identifiers
    assert "isApprovalAdmin" in identifiers


def test_does_not_promote_bearer_tokens_or_unbounded_values():
    token = "vHSjlhjXhpTZr_" + "x" * 180
    identifiers = extract_exact_identifiers(f"Authorization: Bearer {token}")

    assert token not in identifiers
    assert all(len(value) <= 160 for value in identifiers)
