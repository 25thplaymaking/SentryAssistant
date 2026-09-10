"""Per-model runtime routing while preserving Sentry's single runtime contract."""

from __future__ import annotations

from .base import RuntimeExperience
from .codex_workstation import CodexWorkstationRuntime


class RoutedRuntime:
    """Use Hermes normally and native workstation Codex for subscription routes."""

    def __init__(self, primary) -> None:
        self.primary = primary
        self.codex = CodexWorkstationRuntime()

    def bind_pool(self, pool) -> None:
        self.codex.bind_pool(pool)

    async def capabilities(self, profile_id):
        return await self.primary.capabilities(profile_id)

    async def create_session(self, profile_id, scope):
        return await self.primary.create_session(profile_id, scope)

    async def available_models(self, profile_id):
        models = tuple(await self.primary.available_models(profile_id))
        return await self.codex.filter_models(profile_id, models)

    async def list_auth_providers(self, profile_id):
        payload = await self.primary.list_auth_providers(profile_id)
        return await self.codex.decorate_auth_providers(profile_id, payload)

    async def native_runtime_status(self, profile_id):
        return await self.codex.status(profile_id)

    def experience_for_model(self, model, requested):
        if self.codex.owns_model(model):
            return RuntimeExperience.WORK
        return requested

    def send_turn(self, request):
        if self.codex.owns_model(request.model):
            return self.codex.send_turn(request)
        return self.primary.send_turn(request)

    async def cancel(self, run_id):
        return await self.primary.cancel(run_id)

    async def search_sessions(self, query):
        return await self.primary.search_sessions(query)

    async def project_work_order(self, projection):
        return await self.primary.project_work_order(projection)

    async def read_work_board(self, profile_id):
        return await self.primary.read_work_board(profile_id)

    async def aclose(self):
        close = getattr(self.primary, "aclose", None)
        if close is not None:
            await close()

    def register(self, instance):
        return self.primary.register(instance)

    @property
    def endpoint_refresher(self):
        return getattr(self.primary, "endpoint_refresher", None)

    @endpoint_refresher.setter
    def endpoint_refresher(self, value):
        self.primary.endpoint_refresher = value
