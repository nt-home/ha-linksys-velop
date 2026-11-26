"""Use case: validate a hub and list its mesh nodes for the setup flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..domain.models import HubIdentity, Node
    from ..domain.ports import HubPort


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    """What the setup flow learns from a reachable, authenticated hub."""

    identity: HubIdentity
    nodes: tuple[Node, ...]


async def async_probe_hub(client: HubPort) -> OnboardingResult:
    """Check identity, credentials, then read the mesh nodes.

    Raises:
        VelopError: Any domain error describing why the hub cannot be used.
    """
    identity = await client.async_get_identity()
    await client.async_validate_credentials()
    fast = await client.async_fetch_fast(identity)
    return OnboardingResult(identity=identity, nodes=fast.nodes)
