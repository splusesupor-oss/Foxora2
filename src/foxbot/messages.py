from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter


class MissingMessageTemplate(KeyError):
    """Raised until the owner supplies the final Soroush Plus message templates."""


@dataclass(frozen=True, slots=True)
class MessageIntent:
    key: str
    values: Mapping[str, object]


class TemplateRenderer:
    """Render owner-supplied message strings; this package defines no user copy."""

    def __init__(self, templates: Mapping[str, str] | None = None) -> None:
        self._templates = dict(templates or {})

    def render(self, intent: MessageIntent) -> str:
        template = self._templates.get(intent.key)
        if template is None:
            raise MissingMessageTemplate(intent.key)
        required = {
            name
            for _, name, _, _ in Formatter().parse(template)
            if name is not None
        }
        missing = required.difference(intent.values)
        if missing:
            raise ValueError(f"Template {intent.key!r} is missing values: {sorted(missing)}")
        return template.format_map(dict(intent.values))
