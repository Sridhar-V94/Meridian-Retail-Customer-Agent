#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

DEFAULT_MODEL = ""  # Blank means use OpenCode's configured default model
ALLOWED_REVIEW_STATUSES = {"READY_FOR_HUMAN_REVIEW", "NEEDS_INFORMATION", "BLOCKED"}
MANUAL_FALLBACK_MESSAGE = (
    "Manual review required. Draft unavailable; please review the supplied facts and policy "
    "and prepare a response for human review."
)
NO_MODEL_API_LINE = "No model API was called (offline demo using prerecorded synthetic result)."


def base_dir() -> Path:
    return Path(__file__).resolve().parent


def load_policy(path: Optional[Path] = None) -> str:
    policy_path = path or (base_dir() / "policy.md")
    return policy_path.read_text(encoding="utf-8")


def load_cases(path: Optional[Path] = None) -> Dict[str, Any]:
    cases_path = path or (base_dir() / "cases.json")
    with cases_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_case(cases: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    if case_id not in cases:
        raise ValueError(f"Unknown case: {case_id}")
    return cases[case_id]


def unavailable_result() -> Dict[str, Any]:
    return {
        "draft_reply": "",
        "evidence_refs": [],
        "missing_information": [],
        "review_status": "UNAVAILABLE",
    }


def validate_model_result(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Model result must be a JSON object")
    expected_keys = {"draft_reply", "evidence_refs", "missing_information", "review_status"}
    if set(data.keys()) != expected_keys:
        raise ValueError("Model result must contain exactly the required keys")

    draft_reply = data["draft_reply"]
    evidence_refs = data["evidence_refs"]
    missing_information = data["missing_information"]
    review_status = data["review_status"]

    if not isinstance(draft_reply, str):
        raise ValueError("draft_reply must be text")
    if not isinstance(evidence_refs, list) or not all(isinstance(x, str) for x in evidence_refs):
        raise ValueError("evidence_refs must be a list of policy IDs (strings)")
    if not isinstance(missing_information, list) or not all(
        isinstance(x, str) for x in missing_information
    ):
        raise ValueError("missing_information must be a list of field names (strings)")
    if review_status not in ALLOWED_REVIEW_STATUSES:
        raise ValueError("review_status is not allowed")

    return {
        "draft_reply": draft_reply,
        "evidence_refs": evidence_refs,
        "missing_information": missing_information,
        "review_status": review_status,
    }


def _extract_json_objects(text: str) -> List[Any]:
    candidates: List[Any] = []
    # fenced blocks like ```json { ... } ```
    for m in re.finditer(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE):
        block = m.group(1).strip()
        if not block:
            continue
        try:
            candidates.append(json.loads(block))
            continue
        except Exception:
            pass
        # try to find objects inside fenced block
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(block):
            if block[idx] == "{":
                try:
                    obj, end = decoder.raw_decode(block, idx)
                    candidates.append(obj)
                    idx = end
                    continue
                except Exception:
                    pass
            idx += 1
    # balanced JSON objects in full text
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        if text[idx] == "{":
            try:
                obj, end = decoder.raw_decode(text, idx)
                candidates.append(obj)
                idx = end
                continue
            except Exception:
                pass
        idx += 1
    return candidates


def _select_result_object(candidates: List[Any]) -> Optional[Any]:
    expected_keys = {"draft_reply", "evidence_refs", "missing_information", "review_status"}
    for obj in candidates:
        if isinstance(obj, dict) and set(obj.keys()) == expected_keys:
            return obj
    for obj in candidates:
        if isinstance(obj, dict) and expected_keys.issubset(set(obj.keys())):
            return obj
    return None


def extract_json_text(raw_text: str) -> Any:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Empty text")
    try:
        parsed = json.loads(text)
        return parsed
    except Exception:
        pass
    candidates = _extract_json_objects(text)
    selected = _select_result_object(candidates)
    if selected is not None:
        return selected
    if candidates:
        # return first dict candidate or first candidate
        for obj in candidates:
            if isinstance(obj, dict):
                return obj
        return candidates[0]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("No JSON object found")


def build_prompt(case_data: Dict[str, Any], policy_text: str) -> str:
    facts_json = json.dumps(case_data, indent=2, ensure_ascii=False)
    return (
        "You are drafting a support reply for human review only.\n"
        "Use only the supplied facts and the supplied policy for this case.\n"
        "Do not send messages, approve refunds, or change records.\n"
        "Do not ask questions and do not mention other cases.\n"
        "Return exactly one JSON object and no other text, with exactly these keys:\n"
        "- draft_reply: text\n"
        "- evidence_refs: list of policy IDs\n"
        "- missing_information: list of field names\n"
        "- review_status: one of READY_FOR_HUMAN_REVIEW, NEEDS_INFORMATION, BLOCKED\n\n"
        "SUPPLIED FACTS (JSON):\n"
        f"{facts_json}\n\n"
        "SUPPLIED POLICY:\n"
        f"{policy_text}\n"
    )


def _find_opencode_command() -> str:
    for name in ("opencode", "opencode.cmd", "opencode.exe"):
        path = shutil.which(name)
        if path:
            return path
    return "opencode"


def _extract_text_from_opencode_json(data: Any) -> str:
    # Try to extract assistant text from OpenCode JSON output; fall back to raw dump
    if isinstance(data, dict):
        # OpenCode run --format json event stream item: {"type":"text","part":{"type":"text","text":"..."}}
        if isinstance(data.get("part"), dict):
            part = data["part"]
            t = part.get("text")
            if isinstance(t, str) and t.strip():
                return t
            content = part.get("content")
            if isinstance(content, str) and content.strip():
                return content

        for key in ("result", "text", "output", "content", "message"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val
        # common message/content shapes
        msg = data.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                joined = "\n".join([p for p in parts if p])
                if joined.strip():
                    return joined
        messages = data.get("messages")
        if isinstance(messages, list):
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") in (None, "assistant"):
                    content = m.get("content")
                    if isinstance(content, str) and content.strip():
                        return content
                    if isinstance(content, list):
                        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                        joined = "\n".join([p for p in parts if p])
                        if joined.strip():
                            return joined
        # parts/events arrays
        for key in ("parts", "events", "items"):
            items = data.get(key)
            if isinstance(items, list):
                texts: List[str] = []
                for it in items:
                    if isinstance(it, dict):
                        t = it.get("text") or it.get("content")
                        if isinstance(t, str):
                            texts.append(t)
                if texts:
                    return "\n".join(texts)
    if isinstance(data, list):
        texts = []
        for item in data:
            if isinstance(item, dict):
                t = item.get("text") or item.get("content")
                if isinstance(t, str):
                    texts.append(t)
        if texts:
            return "\n".join(texts)
    return json.dumps(data, ensure_ascii=False)


def parse_opencode_output(raw_output: str) -> Any:
    text = (raw_output or "").strip()
    if not text:
        raise ValueError("Empty model output")

    # Handle OpenCode NDJSON event stream (one JSON object per line)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    ndjson_texts: List[str] = []
    ndjson_objs = 0
    for ln in lines:
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if isinstance(obj, dict):
            ndjson_objs += 1
            # Prefer explicit text events/parts
            if obj.get("type") == "text":
                part = obj.get("part")
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    ndjson_texts.append(part["text"])
                    continue
            if isinstance(obj.get("part"), dict) and obj["part"].get("type") == "text":
                t = obj["part"].get("text")
                if isinstance(t, str):
                    ndjson_texts.append(t)
                    continue
    if ndjson_objs >= 1 and ndjson_texts:
        # Try from last to first to find the complete/final JSON payload
        for candidate in reversed(ndjson_texts):
            try:
                parsed = extract_json_text(candidate)
                if isinstance(parsed, dict) and set(parsed.keys()) >= {
                    "draft_reply",
                    "evidence_refs",
                    "missing_information",
                    "review_status",
                }:
                    return parsed
            except Exception:
                continue
        return extract_json_text(ndjson_texts[-1])

    # Try direct JSON first
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and set(parsed.keys()) >= {
            "draft_reply",
            "evidence_refs",
            "missing_information",
            "review_status",
        }:
            return parsed
        # If it's a wrapper JSON, extract text then parse
        extracted = _extract_text_from_opencode_json(parsed)
        return extract_json_text(extracted)
    except Exception:
        pass
    # Try to find JSON in mixed output (logs + content)
    return extract_json_text(text)


def call_opencode_model(case_data: Dict[str, Any], policy_text: str, model: str) -> str:
    opencode_cmd = _find_opencode_command()
    prompt = build_prompt(case_data, policy_text)

    fd, temp_path = tempfile.mkstemp(prefix="meridian_case_", suffix=".txt")
    os.close(fd)
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(prompt)

        short_message = (
            "Use the attached supplied facts and policy. "
            "Return exactly one JSON object and no other text with exactly these keys: "
            "draft_reply (text), evidence_refs (list of policy IDs), "
            "missing_information (list of field names), "
            "review_status (READY_FOR_HUMAN_REVIEW, NEEDS_INFORMATION, or BLOCKED)."
        )
        cmd = [opencode_cmd, "run", "--format", "json", "--auto"]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["--file", temp_path, short_message])

        # On Windows, .cmd/.bat shims are more reliably executed via cmd /c
        if os.name == "nt":
            cmd = ["cmd", "/c"] + cmd

        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60.0,
            cwd=str(base_dir()),
            check=False,
        )
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    if completed.returncode != 0:
        # include stderr context in exception message for debugging paths, but app will not show traceback
        err = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"OpenCode call failed: {err[:500]}")
    out = (completed.stdout or "").strip()
    if not out:
        raise RuntimeError("OpenCode returned empty output")
    return out


def generate_live_result(
    case_data: Dict[str, Any],
    policy_text: str,
    model: str,
    model_call_fn: Optional[Callable[[Dict[str, Any], str, str], str]] = None,
) -> Dict[str, Any]:
    try:
        fn = model_call_fn or call_opencode_model
        raw_text = fn(case_data, policy_text, model)
        parsed = parse_opencode_output(raw_text)
        return validate_model_result(parsed)
    except Exception:
        return unavailable_result()


def load_offline_demo_result(case_id: str, path: Optional[Path] = None) -> Dict[str, Any]:
    demo_path = path or (base_dir() / "demo_outputs.json")
    with demo_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Support either {"cases": {"C1": {"result": {...}, "label": ...}}} or {"C1": {...}}
    if "cases" in data and isinstance(data["cases"], dict):
        entry = data["cases"].get(case_id)
    else:
        entry = data.get(case_id)
    if not isinstance(entry, dict):
        raise ValueError(f"No offline demo result for case {case_id}")
    if "result" in entry and isinstance(entry["result"], dict):
        result = entry["result"]
    else:
        # allow flat structure
        result = {k: v for k, v in entry.items() if k in {
            "draft_reply", "evidence_refs", "missing_information", "review_status"
        }}
    return validate_model_result(result)


def format_facts(case_data: Dict[str, Any]) -> str:
    return json.dumps(case_data, indent=2, ensure_ascii=False)


def print_report(
    case_data: Dict[str, Any],
    policy_text: str,
    result: Dict[str, Any],
    offline_demo: bool = False,
    unavailable_reason: Optional[str] = None,
) -> None:
    print("SUPPLIED FACTS")
    print(format_facts(case_data))
    print()
    print("SUPPLIED POLICY")
    print(policy_text.rstrip())
    print()
    print("DRAFT RESULT FOR HUMAN REVIEW")
    if offline_demo:
        print(NO_MODEL_API_LINE)
    print(f"review_status: {result.get('review_status')}")
    print(f"evidence_refs: {json.dumps(result.get('evidence_refs', []), ensure_ascii=False)}")
    print(
        f"missing_information: {json.dumps(result.get('missing_information', []), ensure_ascii=False)}"
    )
    draft_reply = result.get("draft_reply", "")
    if result.get("review_status") == "UNAVAILABLE" or unavailable_reason:
        print("draft_reply:")
        print(f"MANUAL FALLBACK: {MANUAL_FALLBACK_MESSAGE}")
    else:
        print("draft_reply:")
        print(draft_reply)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Meridian Retail support-draft review tool")
    parser.add_argument("--case", required=True, choices=["C1", "C2", "C3"], help="Case ID")
    parser.add_argument(
        "--simulate-timeout", action="store_true", help="Simulate timeout with no API call"
    )
    parser.add_argument(
        "--offline-demo",
        action="store_true",
        help="Use prerecorded synthetic demo output with no API call",
    )
    return parser.parse_args(argv)


def run(argv: Optional[List[str]] = None, model_call_fn: Optional[Callable[..., str]] = None) -> int:
    args = parse_args(argv)
    load_dotenv(base_dir() / ".env")

    try:
        policy_text = load_policy()
        cases = load_cases()
        case_data = get_case(cases, args.case)
    except Exception:
        # If core input files/case cannot be loaded, show safe unavailable state
        print("SUPPLIED FACTS")
        print("{}")
        print()
        print("SUPPLIED POLICY")
        print("")
        print()
        print("DRAFT RESULT FOR HUMAN REVIEW")
        result = unavailable_result()
        print(f"review_status: {result['review_status']}")
        print(f"evidence_refs: {json.dumps(result['evidence_refs'])}")
        print(f"missing_information: {json.dumps(result['missing_information'])}")
        print("draft_reply:")
        print(f"MANUAL FALLBACK: {MANUAL_FALLBACK_MESSAGE}")
        return 0

    offline_demo = False
    if args.simulate_timeout:
        result = unavailable_result()
        unavailable_reason = "timeout"
    elif args.offline_demo:
        offline_demo = True
        unavailable_reason = None
        try:
            result = load_offline_demo_result(args.case)
        except Exception:
            result = unavailable_result()
            unavailable_reason = "offline_load_failed"
    else:
        model = os.getenv("APP_OPENCODE_MODEL", DEFAULT_MODEL).strip()
        result = generate_live_result(case_data, policy_text, model, model_call_fn=model_call_fn)
        unavailable_reason = "unavailable" if result.get("review_status") == "UNAVAILABLE" else None

    print_report(
        case_data,
        policy_text,
        result,
        offline_demo=offline_demo and result.get("review_status") != "UNAVAILABLE",
        unavailable_reason=unavailable_reason,
    )
    return 0


def main() -> None:
    try:
        sys.exit(run())
    except Exception:
        # Final safety net: never show traceback/stale draft
        print("DRAFT RESULT FOR HUMAN REVIEW")
        result = unavailable_result()
        print(f"review_status: {result['review_status']}")
        print(f"evidence_refs: {json.dumps(result['evidence_refs'])}")
        print(f"missing_information: {json.dumps(result['missing_information'])}")
        print("draft_reply:")
        print(f"MANUAL FALLBACK: {MANUAL_FALLBACK_MESSAGE}")
        sys.exit(0)


if __name__ == "__main__":
    main()
