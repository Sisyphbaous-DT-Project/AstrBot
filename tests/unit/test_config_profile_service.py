import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from astrbot.core.config.default import (
    CONFIG_METADATA_2,
    CONFIG_METADATA_3,
    DEFAULT_CONFIG,
)
from astrbot.core.config.i18n_utils import ConfigMetadataI18n
from astrbot.dashboard.services.config_service import (
    ConfigProfileService,
    validate_config,
)


def test_send_wake_prefix_to_llm_defaults_and_metadata() -> None:
    assert DEFAULT_CONFIG["platform_settings"]["send_wake_prefix_to_llm"] is False
    validation_items = CONFIG_METADATA_2["platform_group"]["metadata"][
        "platform_settings"
    ]["items"]
    assert validation_items["send_wake_prefix_to_llm"]["type"] == "bool"

    display_items = CONFIG_METADATA_3["platform_group"]["metadata"]["general"]["items"]
    field = "platform_settings.send_wake_prefix_to_llm"
    assert display_items[field]["type"] == "bool"
    display_order = list(display_items)
    assert display_order[display_order.index("wake_prefix") + 1] == field


@pytest.mark.parametrize("value", [True, False])
def test_send_wake_prefix_to_llm_accepts_boolean(value: bool) -> None:
    errors, config = validate_config(
        {"platform_settings": {"send_wake_prefix_to_llm": value}},
        CONFIG_METADATA_2,
        is_core=True,
    )

    assert errors == []
    assert config["platform_settings"]["send_wake_prefix_to_llm"] is value


def test_send_wake_prefix_to_llm_rejects_string_false() -> None:
    errors, _ = validate_config(
        {"platform_settings": {"send_wake_prefix_to_llm": "false"}},
        CONFIG_METADATA_2,
        is_core=True,
    )

    assert len(errors) == 1
    assert "platform_settings.send_wake_prefix_to_llm" in errors[0]
    assert "bool" in errors[0]


def test_send_wake_prefix_to_llm_missing_in_legacy_config() -> None:
    errors, config = validate_config(
        {"platform_settings": {}},
        CONFIG_METADATA_2,
        is_core=True,
    )

    assert errors == []
    assert "send_wake_prefix_to_llm" not in config["platform_settings"]
    assert config["platform_settings"].get("send_wake_prefix_to_llm", False) is False


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ru-RU", "ja-JP"])
def test_send_wake_prefix_to_llm_translations(locale: str) -> None:
    locale_path = (
        Path(__file__).resolve().parents[2]
        / "dashboard"
        / "src"
        / "i18n"
        / "locales"
        / locale
        / "features"
        / "config-metadata.json"
    )
    translations = json.loads(locale_path.read_text(encoding="utf-8"))
    metadata = ConfigMetadataI18n.convert_to_i18n_keys(CONFIG_METADATA_3)
    item = metadata["platform_group"]["metadata"]["general"]["items"][
        "platform_settings.send_wake_prefix_to_llm"
    ]

    for attr in ("description", "hint"):
        assert item[attr] == (
            f"platform_group.general.platform_settings.send_wake_prefix_to_llm.{attr}"
        )
        translated = translations
        for part in item[attr].split("."):
            translated = translated[part]
        assert isinstance(translated, str)
        assert translated.strip()


def test_get_system_config_includes_effective_server_time() -> None:
    """Verify that the response includes server UTC time and configured offset."""
    fixed_time = datetime(2026, 8, 7, 2, 31, tzinfo=timezone.utc)
    service = ConfigProfileService(
        SimpleNamespace(
            astrbot_config_mgr=SimpleNamespace(
                confs={"default": {"timezone": "Asia/Shanghai"}}
            )
        ),
        runtime={"os": "windows", "sandbox": {"status": "unsupported"}},
    )

    with patch("astrbot.dashboard.services.config_service.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_time
        result = service.get_system_config()

    assert result["server_utc_time"] == "2026-08-07T02:31:00+00:00"
    assert result["server_utc_offset_minutes"] == 480
    assert result["config"]["timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_profile_mutations_await_config_manager() -> None:
    """Verify profile mutations use the config manager's async methods."""
    config_manager = SimpleNamespace(
        create_conf=AsyncMock(return_value="profile-id"),
        update_conf_info=AsyncMock(return_value=True),
        delete_conf=AsyncMock(return_value=True),
    )
    lifecycle = SimpleNamespace(
        astrbot_config_mgr=config_manager,
        reload_pipeline_scheduler=AsyncMock(),
        pipeline_scheduler_mapping={"profile-id": object()},
    )
    service = ConfigProfileService(
        lifecycle, runtime={"os": "windows", "sandbox": {"status": "unsupported"}}
    )

    with pytest.raises(ValueError, match="Invalid local filesystem scope for member") as exc:
        await service.create_profile(
            "Invalid",
            {
                "provider_settings": {
                    "computer_use_runtime": "local",
                    "computer_use_local_permissions": {
                        role: {"filesystem_scope": "invalid"}
                        for role in ("member", "admin")
                    },
                }
            },
        )
    assert "Invalid local filesystem scope for admin" in str(exc.value)
    config_manager.create_conf.assert_not_awaited()
    lifecycle.reload_pipeline_scheduler.assert_not_awaited()

    result = await service.create_profile(
        "Profile", {"provider_settings": {"computer_use_runtime": "local"}}
    )
    await service.rename_profile("profile-id", "Renamed")
    await service.delete_profile("profile-id")

    assert result == {"conf_id": "profile-id"}
    config_manager.create_conf.assert_awaited_once_with(
        name="Profile",
        config={"provider_settings": {"computer_use_runtime": "local"}},
    )
    lifecycle.reload_pipeline_scheduler.assert_awaited_once_with("profile-id")
    config_manager.update_conf_info.assert_awaited_once_with(
        "profile-id",
        name="Renamed",
    )
    config_manager.delete_conf.assert_awaited_once_with("profile-id")
    assert "profile-id" not in lifecycle.pipeline_scheduler_mapping
