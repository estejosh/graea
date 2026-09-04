"""Tests for graea.client.serialize.message_to_observed.

Builds raw telethon.tl.types.Message objects directly (no client / network
needed) since telethon patches tl.types.Message with all the custom.Message
convenience properties (chat_id, sender_id, out, ...) that serialize.py
relies on.
"""
from __future__ import annotations

from telethon.tl import types as tt

from graea.client.serialize import message_to_observed

BOT_ID = 999
USER_ID = 111


def _msg(**kwargs) -> tt.Message:
    defaults = dict(
        id=1,
        peer_id=tt.PeerUser(user_id=BOT_ID),
        from_id=tt.PeerUser(user_id=BOT_ID),
        message="hi",
        out=False,
    )
    defaults.update(kwargs)
    return tt.Message(**defaults)


def test_text_with_bold_entity_rendered_without_markup():
    m = _msg(message="bold text", entities=[tt.MessageEntityBold(offset=0, length=4)])
    obs = message_to_observed(m, BOT_ID)
    assert obs.text == "bold text"
    assert obs.rendered_text == "bold text"
    assert obs.entities == [{"type": "bold", "offset": 0, "length": 4}]
    assert obs.from_bot is True
    assert obs.sender_id == BOT_ID
    assert obs.message_id == 1


def test_degrade_on_raw_markdown_bug():
    # bot sent literal "*bold*" with no matching entity (wrong parse_mode bug)
    m = _msg(message="*bold* text", entities=[])
    obs = message_to_observed(m, BOT_ID)
    assert obs.rendered_text == "*bold* text"
    assert "*" in obs.rendered_text  # stray markup visible, as a human would see it


def test_text_url_entity_carries_url():
    m = _msg(message="click here", entities=[
        tt.MessageEntityTextUrl(offset=0, length=5, url="https://example.com"),
    ])
    obs = message_to_observed(m, BOT_ID)
    assert obs.entities[0]["type"] == "textUrl"
    assert obs.entities[0]["url"] == "https://example.com"


def test_inline_keyboard_rows_and_callback_decode():
    markup = tt.ReplyInlineMarkup(rows=[
        tt.KeyboardButtonRow(buttons=[
            tt.KeyboardButtonCallback(text="OK", data=b"ok"),
            tt.KeyboardButtonUrl(text="Visit", url="https://example.com"),
        ]),
        tt.KeyboardButtonRow(buttons=[
            tt.KeyboardButtonCallback(text="Cancel", data=b"cancel"),
        ]),
    ])
    m = _msg(message="choose", reply_markup=markup)
    obs = message_to_observed(m, BOT_ID)
    assert obs.keyboard is not None
    assert obs.keyboard.kind == "inline"
    assert obs.keyboard.shape == [2, 1]

    b0 = obs.keyboard.rows[0][0]
    assert b0.text == "OK"
    assert b0.kind == "callback"
    assert b0.data == "ok"
    assert b0.row == 0 and b0.col == 0

    b1 = obs.keyboard.rows[0][1]
    assert b1.kind == "url"
    assert b1.data == "https://example.com"
    assert b1.row == 0 and b1.col == 1

    b2 = obs.keyboard.rows[1][0]
    assert b2.text == "Cancel"
    assert b2.data == "cancel"
    assert b2.row == 1 and b2.col == 0

    flat = obs.keyboard.flat()
    assert [b.text for b in flat] == ["OK", "Visit", "Cancel"]


def test_callback_data_decodes_invalid_utf8_with_replace():
    markup = tt.ReplyInlineMarkup(rows=[
        tt.KeyboardButtonRow(buttons=[
            tt.KeyboardButtonCallback(text="Bad", data=b"\xff\xfe\x00bad"),
        ]),
    ])
    m = _msg(message="x", reply_markup=markup)
    obs = message_to_observed(m, BOT_ID)
    assert obs.keyboard.rows[0][0].data is not None
    assert "�" in obs.keyboard.rows[0][0].data


def test_reply_keyboard():
    markup = tt.ReplyKeyboardMarkup(
        rows=[tt.KeyboardButtonRow(buttons=[tt.KeyboardButton(text="Menu")])],
        resize=True,
        single_use=True,
    )
    m = _msg(message="pick one", reply_markup=markup)
    obs = message_to_observed(m, BOT_ID)
    assert obs.keyboard.kind == "reply"
    assert obs.keyboard.resize is True
    assert obs.keyboard.one_time is True
    assert obs.keyboard.rows[0][0].kind == "reply"
    assert obs.keyboard.rows[0][0].text == "Menu"


def test_photo_media():
    photo = tt.Photo(
        id=1, access_hash=1, file_reference=b"", date=None,
        sizes=[tt.PhotoSize(type="x", w=800, h=600, size=1234)],
        dc_id=1,
    )
    media = tt.MessageMediaPhoto(photo=photo)
    m = _msg(message="a caption", media=media)
    obs = message_to_observed(m, BOT_ID)
    assert obs.media is not None
    assert obs.media.kind == "photo"
    assert obs.media.width == 800
    assert obs.media.height == 600
    assert obs.caption == "a caption"


def test_document_media_classified_as_video():
    doc = tt.Document(
        id=1, access_hash=1, file_reference=b"", date=None,
        mime_type="video/mp4", size=5000, dc_id=1,
        attributes=[
            tt.DocumentAttributeVideo(duration=12.5, w=640, h=480),
            tt.DocumentAttributeFilename(file_name="clip.mp4"),
        ],
    )
    m = _msg(message="", media=tt.MessageMediaDocument(document=doc))
    obs = message_to_observed(m, BOT_ID)
    assert obs.media.kind == "video"
    assert obs.media.file_name == "clip.mp4"
    assert obs.media.duration_s == 12.5
    assert obs.media.mime_type == "video/mp4"


def test_reply_to_and_edit_date():
    m = _msg(
        message="edited",
        reply_to=tt.MessageReplyHeader(reply_to_msg_id=42),
    )
    obs = message_to_observed(m, BOT_ID)
    assert obs.reply_to_message_id == 42


def test_raw_trimmed_no_binary():
    m = _msg(message="hello")
    obs = message_to_observed(m, BOT_ID)
    assert obs.raw["id"] == 1
    assert obs.raw["text"] == "hello"
    assert "date" in obs.raw
    assert all(not isinstance(v, (bytes, bytearray)) for v in obs.raw.values())


def test_degrade_on_garbage_never_raises():
    class NotAMessage:
        id = "not-an-int-that-breaks-nothing"

        def __getattr__(self, item):
            raise RuntimeError("boom")

    obs = message_to_observed(NotAMessage(), BOT_ID)
    assert obs.raw.get("serialize_error")
    assert obs.message_id == 0


def test_degrade_on_none_like_object():
    class Weird:
        pass

    obs = message_to_observed(Weird(), BOT_ID)
    # id missing entirely -> should still degrade, not raise
    assert isinstance(obs.raw.get("serialize_error"), str) or obs.message_id == 0
