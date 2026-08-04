from pathlib import Path

from knowledge import run_api


def test_api_server_entrypoint_exists() -> None:
    project_root = Path(__file__).resolve().parents[1]

    assert (project_root / "knowledge" / "run_api.py").is_file()


def test_windows_server_uses_selector_event_loop_policy(monkeypatch) -> None:
    selector_policy = object()
    installed_policies: list[object] = []
    monkeypatch.setattr(run_api.sys, "platform", "win32")
    monkeypatch.setattr(
        run_api.asyncio,
        "WindowsSelectorEventLoopPolicy",
        lambda: selector_policy,
        raising=False,
    )
    monkeypatch.setattr(
        run_api.asyncio,
        "set_event_loop_policy",
        installed_policies.append,
    )

    assert run_api.configure_windows_event_loop_policy() is True
    assert installed_policies == [selector_policy]


def test_main_configures_policy_before_starting_uvicorn(monkeypatch) -> None:
    calls: list[str] = []
    uvicorn_options: dict[str, object] = {}
    monkeypatch.setattr(
        run_api,
        "configure_windows_event_loop_policy",
        lambda: calls.append("policy"),
    )
    monkeypatch.setattr(
        run_api.uvicorn,
        "run",
        lambda *args, **kwargs: (
            calls.append("uvicorn"),
            uvicorn_options.update(kwargs),
        ),
    )

    run_api.main()

    assert calls == ["policy", "uvicorn"]
    assert uvicorn_options["loop"] is run_api.api_loop_factory


def test_api_loop_factory_returns_selector_loop_on_windows(monkeypatch) -> None:
    selector_loop = object()
    monkeypatch.setattr(run_api.sys, "platform", "win32")
    monkeypatch.setattr(
        run_api.asyncio,
        "SelectorEventLoop",
        lambda: selector_loop,
    )

    assert run_api.api_loop_factory() is selector_loop
