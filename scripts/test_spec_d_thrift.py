from __future__ import annotations
"""Spec D mock tests T1–T3."""


from chip.thrift import ThriftState, classify_message, gate_tool_class


def test_t1_deploy_workflow_class_gha_refuse_expensive() -> None:
    msg = "please run the deploy workflow on main"
    assert classify_message(msg) == "gha"
    token = gate_tool_class(
        msg,
        requested_tool_class="expensive_agent",
        state=ThriftState(budget_remaining=1.0, max_hops=3, hops=0),
    )
    assert token == "REFUSE:thrift_skip"


def test_t2_investigate_until_without_go() -> None:
    msg = "investigate until fixed"
    token = gate_tool_class(
        msg,
        requested_tool_class="cheap_agent",
        state=ThriftState(budget_remaining=1.0, max_hops=3, hops=0),
    )
    assert token == "REFUSE:need_operator_go"


def test_t3_max_hops_blocked() -> None:
    token = gate_tool_class(
        "hello",
        requested_tool_class="cheap_agent",
        state=ThriftState(budget_remaining=1.0, max_hops=3, hops=4),
    )
    assert token == "BLOCKED:max_hops"
