"""telethon Message -> ObservedMessage.

Public API: `message_to_observed(msg, bot_id) -> ObservedMessage`.

Accepts either a live `telethon.tl.custom.Message` (as delivered by event
handlers, backed by a client) or a bare `telethon.tl.types.Message` built by
hand (as tests do, and as anything reconstructed without a client would be).
Telethon actually patches `telethon.tl.types.Message` itself to carry all the
`custom.Message` convenience properties (chat_id, sender_id, out, ...), so
both cases are the same class hierarchy; the difference this module cares
about is only whether a `_client` is attached, and this module never depends
on that being the case (`.text`/`.buttons` need a client and are not used
here — we compute rendered_text and Keyboard ourselves).

Never raises: any exception while pulling a field is caught and degrades the
whole message to a minimal ObservedMessage with `raw['serialize_error']` set,
per docs/AGENT-RULES.md ("failures are never silent").
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from graea.models import Button, Keyboard, MediaInfo, ObservedMessage

# --------------------------------------------------------------------------
# entities
# --------------------------------------------------------------------------

# Telegram entities are ranges into the already-plain message text; they never
# add characters of their own (unlike Markdown/HTML source). So "applying"
# them to get human-rendered text is the identity transform on the text
# itself — the value of computing entities separately is (a) surfacing them
# as structured data for assertions like `markdown_rendered`, and (b) making
# it obvious when a bot sent literal markup characters (e.g. "*bold*") with
# no matching entity at all: rendered_text will show the literal asterisks,
# which is exactly the bug that assertion is meant to catch.


def _entity_type_name(ent: Any) -> str:
    name = type(ent).__name__
    if name.startswith("MessageEntity"):
        name = name[len("MessageEntity"):]
    return name[:1].lower() + name[1:] if name else "other"


def _entities_to_dicts(entities: Optional[list]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ent in entities or []:
        try:
            d: dict[str, Any] = {
                "type": _entity_type_name(ent),
                "offset": getattr(ent, "offset", 0),
                "length": getattr(ent, "length", 0),
            }
            url = getattr(ent, "url", None)
            if url:
                d["url"] = url
            user_id = getattr(ent, "user_id", None)
            if user_id:
                d["user_id"] = user_id
            language = getattr(ent, "language", None)
            if language:
                d["language"] = language
            out.append(d)
        except Exception:
            continue
    return out


def _rendered_text(text: str, entities: Optional[list]) -> str:
    # See note above: Telegram entities describe formatting ranges over the
    # existing plain text, they do not insert markup — so the human-rendered
    # text is the message text itself.
    return text


# --------------------------------------------------------------------------
# keyboards
# --------------------------------------------------------------------------

_URL_BUTTON_TYPES = ("KeyboardButtonUrl", "KeyboardButtonUrlAuth")
_CALLBACK_BUTTON_TYPES = ("KeyboardButtonCallback",)
_SWITCH_INLINE_TYPES = ("KeyboardButtonSwitchInline", "KeyboardButtonSwitchInlineQueryCurrentChat")
_WEB_APP_TYPES = ("KeyboardButtonWebView", "KeyboardButtonSimpleWebView")
_REQUEST_CONTACT_TYPES = ("KeyboardButtonRequestPhone",)
_REQUEST_LOCATION_TYPES = ("KeyboardButtonRequestGeoLocation",)


def _classify_button_kind(btn: Any, *, inline: bool) -> str:
    name = type(btn).__name__
    if name in _CALLBACK_BUTTON_TYPES:
        return "callback"
    if name in _URL_BUTTON_TYPES:
        return "url"
    if name in _SWITCH_INLINE_TYPES:
        return "switch_inline"
    if name in _WEB_APP_TYPES:
        return "web_app"
    if name in _REQUEST_CONTACT_TYPES:
        return "request_contact"
    if name in _REQUEST_LOCATION_TYPES:
        return "request_location"
    if not inline:
        return "reply"
    return "other"


def _button_data(btn: Any) -> Optional[str]:
    data = getattr(btn, "data", None)
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            return bytes(data).decode("utf-8", errors="replace")
        return str(data)
    url = getattr(btn, "url", None)
    if url:
        return url
    query = getattr(btn, "query", None)
    if query is not None:
        return query
    return None


def _keyboard_from_markup(markup: Any) -> Optional[Keyboard]:
    if markup is None:
        return None
    type_name = type(markup).__name__
    if type_name in ("ReplyKeyboardHide", "ReplyKeyboardForceReply"):
        return None
    rows_src = getattr(markup, "rows", None)
    if rows_src is None:
        return None
    inline = type_name == "ReplyInlineMarkup"
    rows: list[list[Button]] = []
    for r_idx, row in enumerate(rows_src):
        buttons_src = getattr(row, "buttons", None) or []
        row_buttons: list[Button] = []
        for c_idx, btn in enumerate(buttons_src):
            try:
                row_buttons.append(Button(
                    text=getattr(btn, "text", "") or "",
                    kind=_classify_button_kind(btn, inline=inline),
                    data=_button_data(btn),
                    row=r_idx,
                    col=c_idx,
                ))
            except Exception:
                continue
        rows.append(row_buttons)
    return Keyboard(
        kind="inline" if inline else "reply",
        rows=rows,
        resize=getattr(markup, "resize", None),
        one_time=getattr(markup, "single_use", None),
    )


# --------------------------------------------------------------------------
# media
# --------------------------------------------------------------------------


def _document_kind(doc: Any) -> tuple[str, dict[str, Any]]:
    """Inspect a Document's attributes to decide voice/video/audio/sticker/animation/document."""
    attrs = list(getattr(doc, "attributes", None) or [])
    attr_names = {type(a).__name__ for a in attrs}
    extra: dict[str, Any] = {}
    kind = "document"
    if "DocumentAttributeSticker" in attr_names:
        kind = "sticker"
    elif "DocumentAttributeAnimated" in attr_names:
        kind = "animation"
    elif "DocumentAttributeVideo" in attr_names:
        video_attr = next(a for a in attrs if type(a).__name__ == "DocumentAttributeVideo")
        if getattr(video_attr, "round_message", False):
            kind = "voice"  # video note behaves like a voice bubble
        else:
            kind = "video"
    elif "DocumentAttributeAudio" in attr_names:
        audio_attr = next(a for a in attrs if type(a).__name__ == "DocumentAttributeAudio")
        kind = "voice" if getattr(audio_attr, "voice", False) else "audio"
        title = getattr(audio_attr, "title", None)
        performer = getattr(audio_attr, "performer", None)
        if title:
            extra["title"] = title
        if performer:
            extra["performer"] = performer
    return kind, extra


def _media_info(media: Any) -> Optional[MediaInfo]:
    if media is None:
        return None
    type_name = type(media).__name__
    if type_name == "MessageMediaEmpty":
        return None

    try:
        if type_name == "MessageMediaPhoto":
            photo = getattr(media, "photo", None)
            sizes = getattr(photo, "sizes", None) or []
            width = height = None
            for s in sizes:
                w, h = getattr(s, "w", None), getattr(s, "h", None)
                if w and h:
                    width, height = w, h
            return MediaInfo(kind="photo", width=width, height=height)

        if type_name == "MessageMediaDocument":
            doc = getattr(media, "document", None)
            if doc is None:
                return MediaInfo(kind="document")
            kind, extra = _document_kind(doc)
            file_name = None
            duration = None
            width = height = None
            for attr in getattr(doc, "attributes", None) or []:
                aname = type(attr).__name__
                if aname == "DocumentAttributeFilename":
                    file_name = getattr(attr, "file_name", None)
                elif aname == "DocumentAttributeVideo":
                    duration = getattr(attr, "duration", None)
                    width, height = getattr(attr, "w", None), getattr(attr, "h", None)
                elif aname == "DocumentAttributeAudio":
                    duration = getattr(attr, "duration", None)
            return MediaInfo(
                kind=kind,
                file_name=file_name,
                mime_type=getattr(doc, "mime_type", None),
                size_bytes=getattr(doc, "size", None),
                width=width,
                height=height,
                duration_s=float(duration) if duration is not None else None,
                extra=extra,
            )

        if type_name == "MessageMediaGeo":
            geo = getattr(media, "geo", None)
            return MediaInfo(
                kind="location",
                extra={"lat": getattr(geo, "lat", None), "long": getattr(geo, "long", None)},
            )

        if type_name == "MessageMediaContact":
            return MediaInfo(
                kind="contact",
                extra={
                    "phone_number": getattr(media, "phone_number", None),
                    "first_name": getattr(media, "first_name", None),
                    "last_name": getattr(media, "last_name", None),
                },
            )

        if type_name == "MessageMediaPoll":
            poll = getattr(media, "poll", None)
            question = getattr(poll, "question", None)
            # newer telethon: TextWithEntities; older: plain str
            question_text = getattr(question, "text", question)
            answers = getattr(poll, "answers", None) or []
            answer_texts = []
            for a in answers:
                a_text = getattr(a, "text", None)
                answer_texts.append(getattr(a_text, "text", a_text))
            return MediaInfo(
                kind="poll",
                extra={"question": question_text, "answers": answer_texts},
            )

        # anything else we don't specifically model (game, invoice, webpage...)
        return MediaInfo(kind="other", extra={"telethon_type": type_name})
    except Exception as exc:
        return MediaInfo(kind="other", extra={"telethon_type": type_name, "media_parse_error": str(exc)})


# --------------------------------------------------------------------------
# core
# --------------------------------------------------------------------------


def _reply_to_id(msg: Any) -> Optional[int]:
    reply_to = getattr(msg, "reply_to", None)
    if reply_to is None:
        return None
    return getattr(reply_to, "reply_to_msg_id", None)


def _peer_id_of(peer: Any) -> Optional[int]:
    if peer is None:
        return None
    for attr in ("user_id", "chat_id", "channel_id"):
        val = getattr(peer, attr, None)
        if val is not None:
            return val
    return None


def _build_raw(msg: Any) -> dict[str, Any]:
    date = getattr(msg, "date", None)
    return {
        "id": getattr(msg, "id", None),
        "date": date.isoformat() if isinstance(date, datetime) else None,
        "out": bool(getattr(msg, "out", False)),
        "text": getattr(msg, "message", "") or "",
        "has_media": getattr(msg, "media", None) is not None,
        "has_keyboard": getattr(msg, "reply_markup", None) is not None,
        "has_entities": bool(getattr(msg, "entities", None)),
    }


def message_to_observed(msg: Any, bot_id: int) -> ObservedMessage:
    """Serialize a telethon Message into an ObservedMessage for the LLM/store.

    Accepts a live `telethon.tl.custom.Message` (from event handlers, backed
    by a client) or a bare `telethon.tl.types.Message` (constructed by hand,
    e.g. in tests). Returns fully-populated text/rendered_text/entities/
    keyboard/media/caption/reply_to/edit_date, plus a small trimmed `raw`
    dict (ids/flags only, no binary payloads).

    Never raises. On any error while reading fields, returns a minimal
    ObservedMessage with `raw['serialize_error']` set so the caller can see
    the failure instead of losing the message silently.
    """
    try:
        message_id = int(getattr(msg, "id"))
        chat_id = getattr(msg, "chat_id", None)
        if chat_id is None:
            chat_id = _peer_id_of(getattr(msg, "peer_id", None)) or 0

        sender_id = getattr(msg, "sender_id", None)
        if sender_id is None:
            sender_id = _peer_id_of(getattr(msg, "from_id", None))

        out = getattr(msg, "out", None)
        if sender_id is not None:
            from_bot = sender_id == bot_id
        elif out is not None:
            from_bot = not out
        else:
            from_bot = True

        date = getattr(msg, "date", None)
        if not isinstance(date, datetime):
            date = datetime.now(timezone.utc)

        edit_date = getattr(msg, "edit_date", None)
        if edit_date is not None and not isinstance(edit_date, datetime):
            edit_date = None

        text = getattr(msg, "message", None)
        if text is None:
            text = getattr(msg, "text", None) or ""

        raw_entities = getattr(msg, "entities", None)
        entities = _entities_to_dicts(raw_entities)
        rendered_text = _rendered_text(text, raw_entities)

        keyboard = _keyboard_from_markup(getattr(msg, "reply_markup", None))
        media = _media_info(getattr(msg, "media", None))

        # Telethon doesn't split text/caption: a media message's `.message`
        # field *is* its caption. Surface it as `caption` too when there is
        # media, and leave `text` mirroring the same string either way so
        # both fields are always populated.
        caption = text if (media is not None and text) else None

        return ObservedMessage(
            message_id=message_id,
            chat_id=chat_id,
            from_bot=from_bot,
            sender_id=sender_id,
            date=date,
            edit_date=edit_date,
            text=text,
            rendered_text=rendered_text,
            entities=entities,
            keyboard=keyboard,
            media=media,
            caption=caption,
            reply_to_message_id=_reply_to_id(msg),
            raw=_build_raw(msg),
        )
    except Exception as exc:
        # Degrade gracefully — never raise, never silently drop the message.
        message_id = 0
        try:
            message_id = int(getattr(msg, "id", 0) or 0)
        except Exception:
            pass
        return ObservedMessage(
            message_id=message_id,
            chat_id=0,
            from_bot=True,
            date=datetime.now(timezone.utc),
            raw={"serialize_error": str(exc)},
        )
