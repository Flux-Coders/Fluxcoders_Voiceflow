"""VoiceFlow Strict Tool Registry Module.

Enforces registration, permission checks, and argument validation
for all tools that the LLM or agent may attempt to call.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Type
from pydantic import BaseModel, Field
from app.tools.train_search import (
    TRAIN_SEARCH_TOOL_SCHEMA,
    TrainSearchParams,
    search_trains,
)


class RegisteredTool(BaseModel):
    """Metadata and execution handler for a registered tool."""
    model_config = {"populate_by_name": True}

    name: str
    description: str
    schema_definition: Dict[str, Any] = Field(alias="schema")
    param_model: Any = Field(description="Pydantic model class for argument validation")
    handler: Any = Field(description="Async or sync callable")
    is_permitted: bool = True

    @property
    def schema(self) -> Dict[str, Any]:
        return self.schema_definition



class ToolRegistry:
    """Strict tool registry governing available tools and parameter schemas."""

    def __init__(self) -> None:
        self._tools: Dict[str, RegisteredTool] = {}

    def register(
        self,
        name: str,
        description: str,
        schema: Dict[str, Any],
        param_model: Type[BaseModel],
        handler: Callable[..., Any],
        is_permitted: bool = True,
    ) -> None:
        """Registers a tool with its schema, parameter validation model, and execution handler."""
        self._tools[name] = RegisteredTool(
            name=name,
            description=description,
            schema=schema,
            param_model=param_model,
            handler=handler,
            is_permitted=is_permitted,
        )

    def unregister(self, name: str) -> bool:
        """Removes a tool from the registry."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get_tool(self, name: str) -> Optional[RegisteredTool]:
        """Retrieves a registered tool by name."""
        return self._tools.get(name)

    def is_permitted(self, name: str) -> bool:
        """Checks if a tool is registered and currently permitted."""
        tool = self.get_tool(name)
        return tool is not None and tool.is_permitted

    def set_permitted(self, name: str, permitted: bool) -> None:
        """Dynamically enables or disables permission for a tool."""
        if name in self._tools:
            self._tools[name].is_permitted = permitted

    def get_schemas(self) -> List[Dict[str, Any]]:
        """Returns the list of function schemas for all permitted tools."""
        return [
            t.schema for t in self._tools.values()
            if t.is_permitted
        ]

    def validate_arguments(self, name: str, args: Dict[str, Any]) -> BaseModel:
        """Validates raw tool arguments against the tool's typed Pydantic model.
        
        Raises:
            KeyError: If tool is not registered.
            PermissionError: If tool is registered but not permitted.
            pydantic.ValidationError: If arguments do not conform to schema.
        """
        tool = self.get_tool(name)
        if not tool:
            raise KeyError(f"Tool '{name}' is not registered in ToolRegistry.")
        if not tool.is_permitted:
            raise PermissionError(f"Tool '{name}' is not permitted by policy.")

        model_cls: Type[BaseModel] = tool.param_model
        return model_cls(**args)


def create_default_registry() -> ToolRegistry:
    """Initializes and returns the standard default ToolRegistry."""
    registry = ToolRegistry()
    registry.register(
        name="search_trains",
        description="Search available train services between origin and destination with optional filters.",
        schema=TRAIN_SEARCH_TOOL_SCHEMA,
        param_model=TrainSearchParams,
        handler=search_trains,
        is_permitted=True,
    )
    return registry


# Global default singleton registry
default_tool_registry = create_default_registry()
