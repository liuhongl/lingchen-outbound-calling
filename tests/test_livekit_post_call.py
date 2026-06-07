from __future__ import annotations

import pytest

from app.call_control import CallControlError
from app.livekit_post_call import LiveKitPostCallResultStore


def test_create_post_call_result_builds_queued_analysis_tasks():
    store = LiveKitPostCallResultStore(now_ms=lambda: 1780801000000)

    result = store.create_result(
        {
            "call_id": "call-001",
            "room": "sip-outbound-call-001",
            "source": "livekit-sip",
            "status": "completed",
            "turns": [
                {
                    "turn_index": 1,
                    "user_text": "你好，我想咨询物业费。",
                    "assistant_text": "您好，请问您想了解哪套房？",
                }
            ],
            "metadata": {"tenant_id": "tenant-a"},
        }
    )

    assert result["call_id"] == "call-001"
    assert result["room"] == "sip-outbound-call-001"
    assert result["source"] == "livekit-sip"
    assert result["status"] == "completed"
    assert result["turn_count"] == 1
    assert result["created_at_ms"] == 1780801000000
    assert result["updated_at_ms"] == 1780801000000
    assert result["metadata"] == {"tenant_id": "tenant-a"}
    assert [task["task_type"] for task in result["analysis_tasks"]] == [
        "summary",
        "tags",
        "quality",
        "promise_to_pay",
    ]
    assert {task["status"] for task in result["analysis_tasks"]} == {"queued"}
    assert all(task["call_id"] == "call-001" for task in result["analysis_tasks"])


def test_list_and_get_post_call_results_are_newest_first():
    clock = iter([1000, 2000])
    store = LiveKitPostCallResultStore(now_ms=lambda: next(clock))

    first = store.create_result({"call_id": "call-001", "turns": []})
    second = store.create_result({"call_id": "call-002", "turns": []})

    assert store.get_result("call-001") == first
    assert [result["call_id"] for result in store.list_results()] == [
        "call-002",
        "call-001",
    ]


def test_create_post_call_result_requires_call_id():
    store = LiveKitPostCallResultStore()

    with pytest.raises(CallControlError) as err:
        store.create_result({"turns": []})

    assert err.value.status_code == 400
    assert str(err.value) == "call_id is required"
