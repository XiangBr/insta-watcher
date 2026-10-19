"""Builders for test data. Payload shapes follow Instagram webhook bodies."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Campaign, MatchMode, WebhookEvent

OWNER_ID = "17841400000000001"
FAN_ID = "28542923325365400"
POST_ID = "18446074984116754"


def comment_payload(
    comment_id: str = "c_1",
    text: str = "quero o link",
    *,
    from_id: str = FAN_ID,
    username: str = "fan",
    media_id: str = POST_ID,
    original_media_id: str | None = None,
    owner_id: str = OWNER_ID,
) -> dict[str, Any]:
    media: dict[str, Any] = {"id": media_id, "media_product_type": "FEED"}
    if original_media_id:
        media["original_media_id"] = original_media_id
    return {
        "object": "instagram",
        "entry": [
            {
                "id": owner_id,
                "time": 1790910591,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "from": {"id": from_id, "username": username},
                            "media": media,
                            "id": comment_id,
                            "text": text,
                        },
                    }
                ],
            }
        ],
    }


async def add_campaign(factory: async_sessionmaker[AsyncSession], **overrides: Any) -> Campaign:
    values: dict[str, Any] = {
        "name": "link",
        "post_id": POST_ID,
        "match_any_post": False,
        "keywords": ["link"],
        "match_mode": MatchMode.WHOLE_WORD,
        "dm_message": "Aqui está: https://exemplo.com",
        "public_reply_enabled": False,
        "public_reply_messages": [],
        **overrides,
    }
    async with factory() as session, session.begin():
        campaign = Campaign(**values)
        session.add(campaign)
    return campaign


async def add_webhook_event(
    factory: async_sessionmaker[AsyncSession], payload: dict[str, Any]
) -> None:
    async with factory() as session, session.begin():
        session.add(WebhookEvent(payload=payload))


def story_reply_payload(
    mid: str = "mid_1",
    text: str | None = "quero o link",
    *,
    sender_id: str = FAN_ID,
    story_id: str | None = "17900000000000001",
    owner_id: str = OWNER_ID,
    shape: str = "messaging",
    is_echo: bool = False,
    is_deleted: bool = False,
    is_self: bool = False,
) -> dict[str, Any]:
    """A messages webhook. Meta documents two shapes for it: entry[].messaging[] and
    entry[].changes[] with field "messages". A plain DM is a story reply with story_id=None."""
    message: dict[str, Any] = {"mid": mid}
    if text is not None:
        message["text"] = text
    if story_id is not None:
        message["reply_to"] = {"story": {"id": story_id, "url": "https://cdn.test/story"}}
    if is_echo:
        message["is_echo"] = True
    if is_deleted:
        message["is_deleted"] = True
    if is_self:
        message["is_self"] = True
    event = {
        "sender": {"id": sender_id},
        "recipient": {"id": owner_id},
        "timestamp": 1790910591,
        "message": message,
    }
    entry: dict[str, Any] = {"id": owner_id, "time": 1790910591}
    if shape == "messaging":
        entry["messaging"] = [event]
    else:
        entry["changes"] = [{"field": "messages", "value": event}]
    return {"object": "instagram", "entry": [entry]}


def postback_webhook(
    mid: str | None = "pb_1",
    payload: str | None = "bw:gate:1",
    *,
    sender_id: str = FAN_ID,
    owner_id: str = OWNER_ID,
    shape: str = "messaging",
) -> dict[str, Any]:
    """A messaging_postbacks webhook: someone tapped a button we sent. Two shapes, like
    story_reply_payload. mid or payload can be left out to build a malformed event."""
    postback: dict[str, Any] = {"title": "Já segui"}
    if mid is not None:
        postback["mid"] = mid
    if payload is not None:
        postback["payload"] = payload
    event = {
        "sender": {"id": sender_id},
        "recipient": {"id": owner_id},
        "timestamp": 1790910591,
        "postback": postback,
    }
    entry: dict[str, Any] = {"id": owner_id, "time": 1790910591}
    if shape == "messaging":
        entry["messaging"] = [event]
    else:
        entry["changes"] = [{"field": "messaging_postbacks", "value": event}]
    return {"object": "instagram", "entry": [entry]}
