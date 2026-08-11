"""Config contract tests: validation, env substitution, fingerprints, loader."""
import json

import pytest

from server.api.config import (
    DatasetConfig,
    load_configs_from_dir,
    parse_config,
    resolve_env,
)


def _minimal(**updates):
    payload = {
        "version": 1,
        "dataset": {"id": "sensors", "label": "Sensors"},
        "source": {"type": "http", "base_url": "http://datasource:9000"},
        "endpoints": {
            "entities": {"path": "/entities"},
            "extent": {"path": "/extent", "start_path": "start", "end_path": "end"},
            "series": {"path": "/series"},
        },
        "mapping": {
            "entity_id": "id",
            "timestamp": "ts",
            "measures": {"value": {}},
        },
    }
    payload.update(updates)
    return payload


def test_minimal_config_validates():
    config = parse_config(_minimal())
    assert config.dataset.id == "sensors"
    assert config.fingerprint() == config.fingerprint()


def test_fingerprint_changes_with_schema():
    a = parse_config(_minimal())
    b = parse_config(_minimal(
        **{"mapping": {"entity_id": "key", "timestamp": "ts",
                      "measures": {"value": {}}}}))
    assert a.fingerprint() != b.fingerprint()


def test_unknown_top_level_keys_rejected():
    with pytest.raises(ValueError):
        parse_config(_minimal(**{"exec": "import os"}))


def test_measure_keys_must_match_ids():
    payload = _minimal()
    payload["mapping"]["measures"] = {
        "value": {"id": "renamed", "label": "V"},
    }
    with pytest.raises(ValueError, match="must equal its id"):
        parse_config(payload)


def test_measure_shorthand_defaults_id_to_key():
    config = parse_config(_minimal())
    assert config.mapping.measures["value"].id == "value"
    assert config.mapping.measures["value"].type == "number"


def test_series_parameters_require_entity_start_end():
    payload = _minimal()
    payload["endpoints"]["series"]["parameters"] = {"entity": "e"}
    with pytest.raises(ValueError, match="entity/start/end"):
        parse_config(payload)


def test_env_substitution_and_defaults(monkeypatch):
    monkeypatch.delenv("DCSVIZ_URL", raising=False)
    monkeypatch.setenv("DCSVIZ_PORT", "9000")
    payload = _minimal()
    payload["source"]["base_url"] = \
        "${DCSVIZ_URL:-http://localhost}:${DCSVIZ_PORT}"
    config = parse_config(payload)
    assert config.source.base_url == "http://localhost:9000"


def test_env_substitution_missing_var_raises(monkeypatch):
    monkeypatch.delenv("DCSVIZ_MISSING", raising=False)
    payload = _minimal()
    payload["source"]["base_url"] = "${DCSVIZ_MISSING}"
    with pytest.raises(ValueError, match="DCSVIZ_MISSING"):
        parse_config(payload)


def test_env_substitution_rejects_non_dcsviz_vars(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    payload = _minimal()
    payload["source"]["base_url"] = "${DATABASE_URL}"
    with pytest.raises(ValueError, match=r"DCSVIZ_\*"):
        parse_config(payload)


def test_resolve_env_nested_structures(monkeypatch):
    monkeypatch.setenv("DCSVIZ_TOKEN", "secret")
    assert resolve_env({"headers": {"X-Token": "${DCSVIZ_TOKEN}"}}) == \
        {"headers": {"X-Token": "secret"}}
    assert resolve_env(["${DCSVIZ_TOKEN}", 3]) == ["secret", 3]


def test_embedded_credentials_rejected():
    payload = _minimal()
    payload["source"]["base_url"] = "http://user:pass@host:9000"
    with pytest.raises(ValueError, match="embedded credentials"):
        parse_config(payload)


def test_configs_dir_loader_skips_bad_files(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps(_minimal()))
    (tmp_path / "bad.json").write_text("{not json")
    configs = load_configs_from_dir(tmp_path)
    assert len(configs) == 1
    assert configs[0].dataset.id == "sensors"
    assert load_configs_from_dir(tmp_path / "missing") == []


def test_row_cap_bounds():
    config = parse_config(_minimal(**{"row_cap": 5000}))
    assert config.row_cap == 5000
