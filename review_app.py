"""Small Streamlit UI for reviewing an initialized FAIRagro DFFP review workspace.

Initialize the workspace with run_review.py first. This UI never changes the
machine baseline; every button records an event in review_manifest.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from review_workflow import (
    ReviewWorkflowError,
    derive_reviewed_matrix,
    load_review_workspace,
    publication_gate,
    record_review_decision,
    review_audit_issues,
    review_conflicts,
    review_status,
)


st.set_page_config(page_title="FAIRagro DFFP Human Review", layout="wide")
st.title("FAIRagro DFFP Human Review")
st.caption("Review decisions are stored separately; the machine-generated baseline remains immutable.")

review_dir = st.sidebar.text_input("Review workspace", value=st.session_state.get("review_dir", "review_63"))
reviewer = st.sidebar.text_input("Reviewer", value=st.session_state.get("reviewer", ""))
st.session_state["review_dir"] = review_dir
st.session_state["reviewer"] = reviewer

try:
    paths, _, queue, manifest = load_review_workspace(review_dir)
except Exception as exc:
    st.info("Initialize a workspace first, for example with `python run_review.py init ...`.")
    st.code(str(exc))
    st.stop()

status = review_status(review_dir)
summary = status["summary"]
conflicts = review_conflicts(review_dir)
audit_issues = review_audit_issues(review_dir)
if conflicts:
    st.error(
        f"{len(conflicts)} review-decision conflict(s) must be resolved before a reviewed matrix can be derived."
    )
    with st.expander("Show review conflicts", expanded=True):
        for conflict in conflicts:
            st.markdown(f"**{conflict['code']} — {conflict['review_id']}**")
            st.write(conflict["message"])
            for parent in conflict.get("parents", []):
                st.write(f"Affected parent: {parent['parent_title']} — `{parent['parent_review_id']}`")
            st.caption(
                "To resolve: include Rejected in the Status filter, select the rejected evidence item, "
                "then Verify/Correct/Defer it; or reject the affected parent record first if that parent is unsupported."
            )
if audit_issues:
    st.warning(
        f"{len(audit_issues)} review provenance-quality issue(s) remain. They do not prevent draft derivation, "
        "but the publication gate will stay closed until they are resolved."
    )
    with st.expander("Show review audit issues", expanded=False):
        for issue in audit_issues:
            st.markdown(f"**{issue['code']} — {issue.get('review_id', '')}**")
            st.write(issue.get("message"))

cols = st.columns(6)
for col, (label, key) in zip(cols, [
    ("Remaining", "required_remaining"),
    ("Verified", "verified"),
    ("Corrected", "corrected"),
    ("Rejected", "rejected"),
    ("Deferred", "deferred"),
    ("N/A", "not_applicable"),
]):
    col.metric(label, summary.get(key, 0))

priority = st.sidebar.multiselect("Priority", ["critical", "high", "normal"], default=["critical", "high", "normal"])
statuses = st.sidebar.multiselect(
    "Status", ["pending", "verified", "corrected", "rejected", "deferred", "not_applicable"], default=["pending", "deferred"]
)
kinds = st.sidebar.multiselect(
    "Kind", ["validation_metric", "related_resource", "tuning_parameter", "evidence"],
    default=["validation_metric", "related_resource", "tuning_parameter", "evidence"],
)

items = [x for x in queue.get("items", []) if x.get("priority") in priority and x.get("review_status") in statuses and x.get("kind") in kinds]
if not items:
    st.success("No review items match the current filters.")
else:
    options = {f"[{x['priority']}] {x['kind']}: {x['title']} — {x['review_id']}": x for x in items}
    label = st.selectbox("Review item", list(options))
    item = options[label]
    st.subheader(item["title"])
    st.code(item["review_id"])
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Extracted record**")
        st.json(item.get("snapshot"))
    with c2:
        st.markdown("**Source locator / review metadata**")
        st.json({
            "kind": item.get("kind"),
            "priority": item.get("priority"),
            "status": item.get("review_status"),
            "locator": item.get("locator"),
            "json_pointer": item.get("json_pointer"),
            "duplicate_json_pointers": item.get("duplicate_json_pointers"),
            "latest_decision": item.get("latest_decision"),
        })

    reason = st.text_area("Review note / reason", help="Required for Correct, Reject, and Defer; optional for Verify.", key=f"reason_{item['review_id']}")

    def save_simple_decision(action: str) -> None:
        try:
            record_review_decision(
                review_dir, review_id=item["review_id"], action=action,
                reviewer=reviewer or None, reason=reason or None,
            )
            st.rerun()
        except ReviewWorkflowError as exc:
            st.error(str(exc))

    b1, b2, b3 = st.columns(3)
    if b1.button("✓ Verify", use_container_width=True):
        save_simple_decision("verify")
    if b2.button("✕ Reject", use_container_width=True):
        save_simple_decision("reject")
    if b3.button("? Defer", use_container_width=True):
        save_simple_decision("defer")

    st.markdown("### Correct")
    st.caption("Provide only fields to change. Evidence lists on parent metric/resource/parameter records must be reviewed separately.")
    patch_text = st.text_area("JSON patch", value="{}", height=150, key=f"patch_{item['review_id']}")
    if st.button("✎ Save correction"):
        try:
            patch = json.loads(patch_text)
            if not isinstance(patch, dict):
                raise ValueError("Patch must be a JSON object")
            record_review_decision(
                review_dir, review_id=item["review_id"], action="correct", reviewer=reviewer or None,
                reason=reason or None, patch=patch,
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

st.divider()
st.subheader("Derived reviewed matrix")
if st.button("Derive reviewed matrix"):
    try:
        path, info = derive_reviewed_matrix(review_dir)
        st.success(f"Derived {path}")
        st.json(info)
    except ReviewWorkflowError as exc:
        st.error(str(exc))

st.subheader("Publication gate")
if st.button("Check publication gate"):
    st.json(publication_gate(review_dir))
