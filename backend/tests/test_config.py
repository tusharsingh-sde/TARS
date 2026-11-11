from pathlib import Path

import pytest

from app.config import Settings, get_settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TARS_DATABASE_URL", raising=False)
    monkeypatch.delenv("TARS_GEMINI_MODEL", raising=False)
    monkeypatch.delenv("TARS_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TARS_OUTLET_MATCH_THRESHOLD", raising=False)
    monkeypatch.delenv("TARS_SKU_MATCH_THRESHOLD", raising=False)

    settings = Settings()

    assert settings.gemini_api_key == ""
    assert settings.gemini_model == "gemini-3.8-live"
    assert settings.database_url == "sqlite:///./tars.db"
    assert settings.outlet_match_threshold == pytest.approx(0.85)
    assert settings.sku_match_threshold == pytest.approx(0.80)


def test_tars_database_url_overrides_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TARS_DATABASE_URL", "sqlite:///./custom_override.db")
    get_settings.cache_clear()

    try:
        settings = get_settings()
        assert settings.database_url == "sqlite:///./custom_override.db"
    finally:
        get_settings.cache_clear()


def test_settings_ignores_non_tars_keys_in_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "TARS_GEMINI_MODEL=gemini-3.8-live\nVITE_API_URL=http://localhost:8000\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TARS_GEMINI_MODEL", raising=False)

    settings = Settings()

    assert settings.gemini_model == "gemini-3.8-live"

