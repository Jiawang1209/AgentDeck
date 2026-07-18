from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest

from agentdeck.adapters.config import ConfigResolver


class HostileItems(Mapping[object, object]):
    def __getitem__(self, key: object) -> object:
        raise KeyError(key)

    def __iter__(self) -> Iterator[object]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def items(self) -> object:
        raise RuntimeError("token=hostile-items-marker")


class HostileIteration(HostileItems):
    def items(self) -> object:
        def generate() -> Iterator[tuple[object, object]]:
            yield "leader", "codex-cli"
            raise RuntimeError("token=hostile-iteration-marker")

        return generate()


class DuplicateItems(HostileItems):
    def items(self) -> object:
        return iter((("leader", "codex-cli"), ("leader", "claude-cli")))


class MalformedItems(HostileItems):
    def items(self) -> object:
        return iter((("leader",),))


class TooManyItems(HostileItems):
    def items(self) -> object:
        return ((f"key-{index}", "value") for index in range(257))


@pytest.mark.parametrize(
    "layer_name",
    ["discovered", "global_values", "project_values", "session_values"],
)
@pytest.mark.parametrize("hostile", [HostileItems(), HostileIteration()])
def test_all_config_layers_redact_hostile_mapping_failures(
    layer_name: str, hostile: Mapping[object, object]
) -> None:
    layers: dict[str, object] = {
        "discovered": {},
        "global_values": {},
        "project_values": {},
        "session_values": {},
    }
    layers[layer_name] = hostile

    with pytest.raises(TypeError) as error:
        ConfigResolver(**layers)  # type: ignore[arg-type]

    assert "hostile" not in str(error.value)
    assert "token" not in str(error.value)


@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        (DuplicateItems(), "configuration layer contains duplicate keys"),
        (MalformedItems(), "configuration layer items must be key-value pairs"),
        (TooManyItems(), "configuration layer has too many items"),
    ],
)
def test_config_mapping_structure_fails_closed(
    mapping: Mapping[object, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ConfigResolver(
            discovered=mapping,  # type: ignore[arg-type]
            global_values={},
            project_values={},
            session_values={},
        )
