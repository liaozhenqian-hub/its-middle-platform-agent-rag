from knowledge.config.settings import Settings


def test_memory_settings_defaults_and_path_resolution(tmp_path):
    settings = Settings(_env_file=None, PROJECT_ROOT=tmp_path)

    assert settings.memory_enabled is True
    assert settings.memory_extraction_enabled is True
    assert settings.memory_max_recall == 5
    assert settings.memory_summary_max_chars == 2000
    assert settings.memory_incident_candidate_ttl_seconds == 7 * 24 * 3600
    assert settings.memory_auto_confirm_seconds == 24 * 3600
    assert settings.memory_maintenance_interval_seconds == 60
    assert settings.memory_entity_recall_limit == 5
    assert settings.memory_procedural_enabled is True
    assert settings.resolved_memory_db.name == "agent_memory.db"
