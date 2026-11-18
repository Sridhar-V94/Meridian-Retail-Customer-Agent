import json
from pathlib import Path

import app


def run_app(args, monkeypatch=None):
    # Capture stdout
    import io
    import sys

    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        app.run(args)
    finally:
        sys.stdout = old
    return buf.getvalue()


def test_all_input_files_exist_and_cases_loadable():
    base = Path(__file__).resolve().parent
    assert (base / "policy.md").exists()
    assert (base / "cases.json").exists()
    assert (base / "demo_outputs.json").exists()

    policy = app.load_policy(base / "policy.md")
    assert "Meridian Retail Support Drafting Policy" in policy
    assert "P1." in policy

    cases = app.load_cases(base / "cases.json")
    assert set(cases.keys()) == {"C1", "C2", "C3"}
    for cid in ["C1", "C2", "C3"]:
        case = app.get_case(cases, cid)
        assert case["case_id"] == cid


def test_successful_c1_result_with_fake_model():
    policy = app.load_policy()
    case = app.get_case(app.load_cases(), "C1")

    def fake_model(case_data, policy_text, model):
        assert case_data["case_id"] == "C1"
        assert "P1." in policy_text
        assert model == "opencode/test-model"
        return json.dumps(
            {
                "draft_reply": "Draft for C1: refer for human return review.",
                "evidence_refs": ["P1", "P4"],
                "missing_information": [],
                "review_status": "READY_FOR_HUMAN_REVIEW",
            }
        )

    result = app.generate_live_result(
        case, policy, "opencode/test-model", model_call_fn=fake_model
    )
    assert result["review_status"] == "READY_FOR_HUMAN_REVIEW"
    assert result["draft_reply"] != ""
    assert result["evidence_refs"] == ["P1", "P4"]
    assert result["missing_information"] == []


def test_invalid_model_output_results_in_unavailable_state():
    policy = app.load_policy()
    case = app.get_case(app.load_cases(), "C1")

    def bad_model(case_data, policy_text, model):
        return "not json at all"

    result = app.generate_live_result(case, policy, "", model_call_fn=bad_model)
    assert result == app.unavailable_result()

    # also wrong structure/types
    def bad_shape(case_data, policy_text, model):
        return json.dumps({"draft_reply": "hi"})

    result2 = app.generate_live_result(case, policy, "", model_call_fn=bad_shape)
    assert result2 == app.unavailable_result()


def test_timeout_behaviour_no_api_call_and_unavailable_output():
    output = run_app(["--case", "C2", "--simulate-timeout"])
    assert "SUPPLIED FACTS" in output
    assert "SUPPLIED POLICY" in output
    assert "DRAFT RESULT FOR HUMAN REVIEW" in output
    assert "review_status: UNAVAILABLE" in output
    assert "MANUAL FALLBACK:" in output
    # draft cleared (no separate user draft content line other than fallback context)
    assert app.NO_MODEL_API_LINE not in output


def test_offline_outputs_no_api_call_and_labelled_synthetic_result():
    output_c1 = run_app(["--case", "C1", "--offline-demo"])
    assert "SUPPLIED FACTS" in output_c1
    assert "SUPPLIED POLICY" in output_c1
    assert "DRAFT RESULT FOR HUMAN REVIEW" in output_c1
    assert app.NO_MODEL_API_LINE in output_c1
    assert "review_status: READY_FOR_HUMAN_REVIEW" in output_c1
    assert "MR-1042" in output_c1

    output_c2 = run_app(["--case", "C2", "--offline-demo"])
    assert app.NO_MODEL_API_LINE in output_c2
    assert "review_status: NEEDS_INFORMATION" in output_c2
    assert "delivery_date" in output_c2

    output_c3 = run_app(["--case", "C3", "--offline-demo"])
    assert app.NO_MODEL_API_LINE in output_c3
    assert "review_status: BLOCKED" in output_c3
    assert "MR-1126" in output_c3
