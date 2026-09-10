"""Connector registry (T046): ``connector_type`` -> ``SourceConnector`` factory.

The only place that knows the concrete connector classes. Decision / domain / observation
code never imports these modules.
"""

from __future__ import annotations

from typing import Any

from app.integration.connectors.base import ConnectorError, SourceConnector
from app.integration.connectors.file import FileSourceConnector
from app.integration.connectors.rest import RestSourceConnector
from app.integration.connectors.sql import SqlSourceConnector

_FACTORIES: dict[str, Any] = {
    "file": FileSourceConnector,
    "rest": RestSourceConnector,
    "sql": SqlSourceConnector,
}


def build_connector(
    connector_type: str,
    config: dict[str, Any],
    credential: dict[str, Any] | None = None,
) -> SourceConnector:
    factory = _FACTORIES.get(connector_type)
    if factory is None:
        raise ConnectorError(f"unknown connector_type {connector_type!r}")
    connector: SourceConnector = factory(config, credential)
    return connector
