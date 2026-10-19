import asyncio

from app.loops import LoopGroup, run_periodic


async def test_failing_step_does_not_kill_the_loop() -> None:
    calls = 0

    async def step() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")

    group = LoopGroup()
    group.start("t", run_periodic("t", 0.01, step))
    await asyncio.sleep(0.1)
    await group.stop()
    assert calls >= 3


async def test_wake_event_runs_step_early() -> None:
    calls = 0
    wake = asyncio.Event()

    async def step() -> None:
        nonlocal calls
        calls += 1

    group = LoopGroup()
    group.start("t", run_periodic("t", 60, step, wake=wake))
    await asyncio.sleep(0.02)
    assert calls == 1
    wake.set()
    await asyncio.sleep(0.02)
    await group.stop()
    assert calls == 2
