---
{ "id": "topic.permissionResolution", "title": "How a Permission Is Decided", "parent": "window.permissions", "order": 1, "keywords": ["precedence", "inheritance", "resolve", "why denied"], "covers": ["custom_components/phoenix_mcp/policy_engine.py", "custom_components/phoenix_mcp/device_semantics.py"] }
---
Phoenix decides every request the same way, whichever app or feature sends it.

## The order {#order}

1. An entity that doesn't exist is NOT FOUND.
2. Phoenix's own entities are never reachable.
3. A Home Companion key reaches only its household devices.
4. A pass-through key reaches everything else, or only Assist-exposed entities if you limited it.
5. Otherwise Phoenix reads the tree: the entity, its device, the main device it's part of (if any), then its domain.

## Deny wins {#deny}

A D anywhere in that chain denies, even when the entity itself is set to W.

## The nearest grant decides {#nearest}

Otherwise the nearest R or W decides. An entity's own setting beats its device's, and a device's beats its domain's. If every level is N, the answer is NO ACCESS.

## Assist and the Voice Agent {#household}

When someone speaks to Assist or the Voice Agent, Phoenix also limits the result to what that Home Assistant user may see and control.

## Disabled entities {#registry-only}

An entity that's disabled, or has no current state, is visible only through a grant on its device or domain.

## What the tree shows {#tree-label}

The result after each name in the tree follows the tree alone. It doesn't take Home Companion limits or the person speaking into account.
