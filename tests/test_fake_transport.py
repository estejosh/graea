"""Tests for graea.client.fake.FakeTransport."""
from __future__ import annotations

import pytest

from graea.client.fake import FakeTransport
from graea.models import Button, Keyboard


async def _connected(script=None) -> FakeTransport:
    t = FakeTransport(script=script)
    await t.connect()
    await t.open_chat("@some_bot")
    return t


@pytest.mark.asyncio
async def test_script_round_trip_plain_string():
    t = await _connected({"/start": ["Welcome!"]})
    await t.send_text("/start")
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert len(events) == 1
    assert events[0].kind == "new"
    assert events[0].message.text == "Welcome!"
    assert events[0].message.from_bot is True


@pytest.mark.asyncio
async def test_timeout_returns_empty_list_when_no_script_entry():
    t = await _connected({})
    await t.send_text("/unscripted")
    events = await t.wait_for_events(timeout_ms=50, quiet_ms=10)
    assert events == []


@pytest.mark.asyncio
async def test_multiple_replies_scripted_for_one_action():
    t = await _connected({"/help": ["first", "second"]})
    await t.send_text("/help")
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert [e.message.text for e in events] == ["first", "second"]


@pytest.mark.asyncio
async def test_press_inline_by_text_exact_case_insensitive():
    kb = Keyboard(kind="inline", rows=[[Button(text="OK", kind="callback", data="ok")]])
    t = await _connected({"OK": ["you pressed ok"]})
    msg = t.make_bot_message("choose", keyboard=kb)
    answer = await t.press_inline(msg, text="ok")
    assert answer is None  # no callback_answer scripted
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert events[0].message.text == "you pressed ok"


@pytest.mark.asyncio
async def test_press_inline_by_substring():
    kb = Keyboard(kind="inline", rows=[[Button(text="Confirm Order", kind="callback", data="c")]])
    t = await _connected({"Confirm Order": ["confirmed"]})
    msg = t.make_bot_message("choose", keyboard=kb)
    await t.press_inline(msg, text="confirm")
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert events[0].message.text == "confirmed"


@pytest.mark.asyncio
async def test_press_inline_by_index():
    kb = Keyboard(kind="inline", rows=[
        [Button(text="A", data="a"), Button(text="B", data="b")],
        [Button(text="C", data="c")],
    ])
    t = await _connected({"C": ["chose C"]})
    msg = t.make_bot_message("choose", keyboard=kb)
    await t.press_inline(msg, index=2)
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert events[0].message.text == "chose C"


@pytest.mark.asyncio
async def test_press_inline_by_row_col():
    kb = Keyboard(kind="inline", rows=[
        [Button(text="A", data="a"), Button(text="B", data="b")],
        [Button(text="C", data="c")],
    ])
    t = await _connected({"B": ["chose B"]})
    msg = t.make_bot_message("choose", keyboard=kb)
    await t.press_inline(msg, row=0, col=1)
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert events[0].message.text == "chose B"


@pytest.mark.asyncio
async def test_press_inline_no_match_raises_value_error():
    kb = Keyboard(kind="inline", rows=[[Button(text="A", data="a")]])
    t = await _connected({})
    msg = t.make_bot_message("choose", keyboard=kb)
    with pytest.raises(ValueError):
        await t.press_inline(msg, text="nonexistent")


@pytest.mark.asyncio
async def test_press_inline_returns_callback_answer():
    kb = Keyboard(kind="inline", rows=[[Button(text="Buy", data="buy")]])
    t = await _connected({"Buy": [{"kind": "callback_answer", "detail": "Purchased!"}]})
    msg = t.make_bot_message("shop", keyboard=kb)
    answer = await t.press_inline(msg, text="Buy")
    assert answer == "Purchased!"


@pytest.mark.asyncio
async def test_press_inline_by_message_id_lookup():
    # Simulates the engine holding an older ObservedMessage snapshot: pressing
    # still works because FakeTransport re-resolves by message_id internally.
    kb = Keyboard(kind="inline", rows=[[Button(text="OK", data="ok")]])
    t = await _connected({"OK": ["done"]})
    msg = t.make_bot_message("choose", keyboard=kb, message_id=55)
    stale_copy = msg.model_copy()
    answer = await t.press_inline(stale_copy, text="OK")
    assert answer is None
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert events[0].message.text == "done"


@pytest.mark.asyncio
async def test_edit_event():
    t = await _connected({
        "/edit": [{"kind": "edit", "message": {"message_id": 10, "text": "edited text"}}],
    })
    await t.send_text("/edit")
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert len(events) == 1
    assert events[0].kind == "edit"
    assert events[0].message.message_id == 10
    assert events[0].message.text == "edited text"


@pytest.mark.asyncio
async def test_delete_event():
    t = await _connected({
        "/gone": [{"kind": "delete", "message_ids": [1, 2, 3]}],
    })
    await t.send_text("/gone")
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert len(events) == 1
    assert events[0].kind == "delete"
    assert events[0].message_ids == [1, 2, 3]


@pytest.mark.asyncio
async def test_delete_event_single_message_id_shorthand():
    t = await _connected({"/gone": [{"kind": "delete", "message_id": 7}]})
    await t.send_text("/gone")
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert events[0].message_ids == [7]


@pytest.mark.asyncio
async def test_drain_events_independent_of_wait_for_events():
    t = await _connected({"/start": ["Welcome!"]})
    await t.send_text("/start")
    drained = await t.drain_events()
    assert len(drained) == 1
    assert drained[0].message.text == "Welcome!"
    # wait_for_events has its own independent view of "the last action's
    # events" and still reports them once, even though drain_events already
    # cleared its own backlog.
    waited = await t.wait_for_events(timeout_ms=10, quiet_ms=10)
    assert len(waited) == 1
    assert waited[0].message.text == "Welcome!"
    # calling again with no new action -> nothing left to report
    waited_again = await t.wait_for_events(timeout_ms=10, quiet_ms=10)
    assert waited_again == []


@pytest.mark.asyncio
async def test_drain_events_accumulates_across_actions():
    t = await _connected({"/a": ["A reply"], "/b": ["B reply"]})
    await t.send_text("/a")
    await t.send_text("/b")
    drained = await t.drain_events()
    assert [e.message.text for e in drained] == ["A reply", "B reply"]
    assert await t.drain_events() == []


@pytest.mark.asyncio
async def test_last_bot_messages():
    t = await _connected({})
    t.make_bot_message("one")
    t.make_bot_message("two")
    t.make_bot_message("three")
    msgs = await t.last_bot_messages(2)
    assert [m.text for m in msgs] == ["two", "three"]


@pytest.mark.asyncio
async def test_send_file_lookup_by_caption_then_path():
    t = await _connected({"a caption": ["got file with caption"]})
    await t.send_file("/tmp/x.png", caption="a caption")
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert events[0].message.text == "got file with caption"

    t2 = await _connected({"/tmp/y.png": ["got file by path"]})
    await t2.send_file("/tmp/y.png")
    events2 = await t2.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert events2[0].message.text == "got file by path"


@pytest.mark.asyncio
async def test_press_reply_button():
    t = await _connected({"Menu": ["here is the menu"]})
    await t.press_reply_button("Menu")
    events = await t.wait_for_events(timeout_ms=100, quiet_ms=10)
    assert events[0].message.text == "here is the menu"


@pytest.mark.asyncio
async def test_connect_disconnect_me_status():
    t = FakeTransport()
    assert await t.is_connected() is False
    await t.connect()
    assert await t.is_connected() is True
    assert isinstance(await t.me(), str)
    await t.disconnect()
    assert await t.is_connected() is False
