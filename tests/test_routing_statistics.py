"""Prove routing accounting, persistence, reset isolation and missing usage semantics."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.phoenix_mcp.routing_statistics import (
    RoutingStatistics, TurnStatistics, decision_usage, local_result, provider_events, tracked_turn, voice_result,
)


def data(stats=None):
    return SimpleNamespace(routing_statistics=stats or RoutingStatistics())


async def events(*items):
    for item in items:
        yield item


@pytest.mark.parametrize('route', ['local', 'decision'])
async def test_completed_local_reply_and_preview_exclusion(route):
    d=data()
    @tracked_turn('chat')
    async def turn(*,data,emit):
        decision_usage('preview', {'usage':{'input_tokens':999,'output_tokens':999}})
        local_result(SimpleNamespace(pending=None,statistics_completed=True,outcome='ok'),route)
        await emit('done',{'stop_reason':'end_turn'})
    await turn(data=d,emit=AsyncMock())
    r=d.routing_statistics.summary()['chat']
    assert r['outcomes'][route]==1 and r['provider_turns']==0
    assert r['decision_usage']['requests']==0
    assert sum(r['outcomes'].values())==1


@pytest.mark.parametrize('pending,complete,outcome,expected', [
    ({'approval_id':'private'},False,'ok','pending'),
    (None,False,'ok','failed'),
    (None,False,'cancelled','cancelled'),
    (None,False,'outcome_unknown','failed'),
])
async def test_failed_or_queued_local_reply_is_not_completed(pending,complete,outcome,expected):
    d=data()
    @tracked_turn('chat')
    async def turn(*,data,emit):
        local_result(SimpleNamespace(pending=pending,statistics_completed=complete,outcome=outcome),'decision')
        await emit('done',{'stop_reason':'end_turn'})
    await turn(data=d,emit=AsyncMock())
    assert d.routing_statistics.summary()['chat']['outcomes'][expected]==1
    assert 'private' not in str(d.routing_statistics.summary())


async def test_cumulative_usage_replaced_per_request_and_unknown_not_zero_report():
    d=data()
    @tracked_turn('chat')
    async def turn(*,data,emit):
        for stream in (events({'type':'usage_update','input_tokens':10,'output_tokens':1},
                              {'type':'usage_update','input_tokens':10,'output_tokens':7}),
                       events({'type':'usage_update','input_tokens':5,'output_tokens':3}), events()):
            async for _ in provider_events(stream): pass
        decision_usage('chat',{'usage':{'input_tokens':9,'output_tokens':2}})
        decision_usage('chat',{})
        decision_usage('voice_shadow',{'usage':{'input_tokens':999,'output_tokens':999}})
        await emit('done',{'stop_reason':'end_turn'})
    await turn(data=d,emit=AsyncMock())
    r=d.routing_statistics.summary()['chat']
    assert r['provider_usage']=={'requests':3,'reports':2,'input_tokens':15,'output_tokens':10}
    assert r['decision_usage']=={'requests':2,'reports':1,'input_tokens':9,'output_tokens':2}
    assert r['provider_turns']==1 and r['outcomes']['provider']==1


async def test_untracked_external_or_ai_task_stream_is_excluded():
    d=data()
    async for _ in provider_events(events({'type':'usage_update','input_tokens':5,'output_tokens':3})): pass
    decision_usage('chat',{'usage':{'input_tokens':5,'output_tokens':3}})
    assert all(sum(r['outcomes'].values())==0 for r in d.routing_statistics.summary().values())


async def test_cancel_and_failed_delivery_still_account_usage():
    d=data()
    @tracked_turn('chat')
    async def turn(*,data,emit):
        async for _ in provider_events(events({'type':'usage_update','input_tokens':10,'output_tokens':7})): pass
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):await turn(data=d,emit=AsyncMock())
    assert d.routing_statistics.summary()['chat']['outcomes']['cancelled']==1
    assert d.routing_statistics.summary()['chat']['provider_usage']['input_tokens']==10


async def test_voice_distinguishes_provider_error_pending_and_completed():
    d=data()
    @tracked_turn('voice')
    async def turn(hass,data,outcome,pending):
        async for _ in provider_events(events()): pass
        voice_result(outcome,pending,True)
        return 'voice reply'
    for outcome,pending in [('ok',False),('ok',True),('provider_error',False)]:
        await turn(None,d,outcome,pending)
    r=d.routing_statistics.summary()['voice']
    assert r['outcomes']['provider']==r['outcomes']['pending']==r['outcomes']['failed']==1
    assert r['provider_turns']==3


async def test_restart_reset_and_wipe_preserve_only_aggregates():
    store=SimpleNamespace(async_load=AsyncMock(return_value=None),async_save=AsyncMock(),async_remove=AsyncMock())
    s=RoutingStatistics(store)
    old=TurnStatistics('chat',s.surfaces['chat']['generation'],route='local',completed=True)
    s.finish(old);await s.async_save()
    saved=store.async_save.call_args.args[0]
    assert 'generation' not in str(saved)
    store.async_load.return_value=saved
    restored=RoutingStatistics(store);await restored.async_load()
    assert restored.summary()==s.summary()
    voice=restored.summary()['voice']
    await restored.async_reset('chat')
    restored.finish(old)
    assert restored.summary()['chat']['outcomes']['local']==0
    assert restored.summary()['voice']==voice
    await restored.async_wipe();store.async_remove.assert_awaited_once()
    assert all(sum(r['outcomes'].values())==0 for r in restored.summary().values())


async def test_reset_failure_preserves_counters_and_other_surface_finishing_during_save():
    store=SimpleNamespace(async_save=AsyncMock(side_effect=OSError))
    s=RoutingStatistics(store);before=s.summary()
    with pytest.raises(OSError):await s.async_reset('chat')
    assert s.summary()==before
    async def save(_):
        s.finish(TurnStatistics('voice',s.surfaces['voice']['generation'],route='local',completed=True))
    store.async_save=AsyncMock(side_effect=save)
    await s.async_reset('chat')
    assert s.summary()['voice']['outcomes']['local']==1
    assert s.saved_revision!=s.revision


async def test_late_decision_after_timeout_stays_missing_not_zero_and_nested_ai_task_excluded():
    from custom_components.phoenix_mcp.routing_statistics import begin_decision, end_decision, untracked_turn
    d=data(); handle=None
    @untracked_turn
    async def ai_task():
        async for _ in provider_events(events({'type':'usage_update','input_tokens':500,'output_tokens':10})): pass
    @tracked_turn('chat')
    async def turn(*,data,emit):
        nonlocal handle
        handle=begin_decision('chat')
        await ai_task()
        local_result(SimpleNamespace(pending=None,statistics_completed=True,outcome='ok'),'local')
    await turn(data=d,emit=AsyncMock())
    end_decision(handle,{'usage':{'input_tokens':5,'output_tokens':1}})
    r=d.routing_statistics.summary()['chat']
    assert r['decision_usage']['requests']==1 and r['decision_usage']['reports']==0
    assert r['provider_turns']==0
