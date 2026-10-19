from fastapi import FastAPI
from sqlalchemy import func, select

from app.dispatcher import dispatch_pending, enqueue_comment, parse_comments
from app.matching import KeywordMatcher
from app.models import (
    Delivery,
    Job,
    JobType,
    Origin,
    WebhookEvent,
    WebhookStatus,
)
from tests.factories import (
    FAN_ID,
    OWNER_ID,
    POST_ID,
    add_campaign,
    add_webhook_event,
    comment_payload,
)

matcher = KeywordMatcher()


async def counts(app: FastAPI) -> tuple[int, int]:
    async with app.state.session_factory() as session:
        deliveries = (await session.execute(select(func.count(Delivery.id)))).scalar_one()
        jobs = (await session.execute(select(func.count(Job.id)))).scalar_one()
    return deliveries, jobs


async def test_matching_comment_creates_delivery_and_private_job(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory)
    await add_webhook_event(factory, comment_payload(text="Quero o LINK 🔥"))

    assert await dispatch_pending(factory, matcher) == 1

    async with factory() as session:
        delivery = (await session.execute(select(Delivery))).scalar_one()
        job = (await session.execute(select(Job))).scalar_one()
        event = (await session.execute(select(WebhookEvent))).scalar_one()
    assert delivery.source_id == "c_1"
    assert delivery.actor_id == FAN_ID
    assert delivery.matched_keyword == "link"
    assert delivery.origin == Origin.WEBHOOK
    assert job.type == JobType.PRIVATE_REPLY
    assert event.status == WebhookStatus.PROCESSED


async def test_duplicate_webhook_creates_one_delivery(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory)
    await add_webhook_event(factory, comment_payload())
    await add_webhook_event(factory, comment_payload())

    assert await dispatch_pending(factory, matcher) == 1
    assert await counts(app) == (1, 1)


async def test_webhook_then_polling_creates_one_delivery(app: FastAPI) -> None:
    factory = app.state.session_factory
    campaign = await add_campaign(factory)
    await add_webhook_event(factory, comment_payload())
    await dispatch_pending(factory, matcher)

    (comment,) = parse_comments(comment_payload())
    async with factory() as session, session.begin():
        outcome = await enqueue_comment(
            session,
            comment,
            origin=Origin.POLLING,
            campaigns=[campaign],
            matcher=matcher,
            account_ig_id=OWNER_ID,
        )
    assert outcome == "DUPLICATE"
    assert await counts(app) == (1, 1)


async def test_one_delivery_per_comment_even_with_two_matching_campaigns(app: FastAPI) -> None:
    factory = app.state.session_factory
    any_post = await add_campaign(factory, name="geral", post_id=None, match_any_post=True)
    specific = await add_campaign(factory, name="este post")
    await add_webhook_event(factory, comment_payload())

    await dispatch_pending(factory, matcher)

    async with factory() as session:
        delivery = (await session.execute(select(Delivery))).scalar_one()
    assert delivery.campaign_id == specific.id != any_post.id


async def test_own_comment_is_ignored(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory)
    await add_webhook_event(factory, comment_payload(from_id=OWNER_ID))

    assert await dispatch_pending(factory, matcher) == 0
    assert await counts(app) == (0, 0)


async def test_comment_on_other_post_or_without_keyword_is_ignored(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory)
    await add_webhook_event(factory, comment_payload("c_1", media_id="999"))
    await add_webhook_event(factory, comment_payload("c_2", text="que lindo"))

    assert await dispatch_pending(factory, matcher) == 0
    assert await counts(app) == (0, 0)


async def test_ad_comment_matches_through_original_media_id(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory)
    await add_webhook_event(
        factory, comment_payload(media_id="ad_media_1", original_media_id=POST_ID)
    )
    assert await dispatch_pending(factory, matcher) == 1


async def test_public_reply_gets_its_own_job(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory, public_reply_enabled=True, public_reply_messages=["Te mandei!"])
    await add_webhook_event(factory, comment_payload())

    await dispatch_pending(factory, matcher)

    async with factory() as session:
        jobs = (await session.execute(select(Job).order_by(Job.run_at))).scalars().all()
    assert [j.type for j in jobs] == [JobType.PRIVATE_REPLY, JobType.PUBLIC_REPLY]
    assert jobs[1].run_at > jobs[0].run_at


async def test_paused_or_deleted_campaigns_do_not_fire(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory, is_active=False)
    await add_webhook_event(factory, comment_payload())
    assert await dispatch_pending(factory, matcher) == 0


async def test_broken_event_is_marked_failed_without_user_text(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory)
    payload = comment_payload(text="segredo-do-usuario")
    payload["entry"][0]["changes"][0]["value"]["from"] = "not-a-dict"
    await add_webhook_event(factory, payload)

    await dispatch_pending(factory, matcher)

    async with factory() as session:
        event = (await session.execute(select(WebhookEvent))).scalar_one()
    assert event.status == WebhookStatus.FAILED
    assert event.error is not None
    assert "segredo-do-usuario" not in event.error
