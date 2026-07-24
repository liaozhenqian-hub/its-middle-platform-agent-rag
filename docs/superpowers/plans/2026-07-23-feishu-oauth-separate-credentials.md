# Feishu OAuth Separate Credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the formal-enterprise Feishu bot credentials while allowing optional web OAuth to use a separate test-enterprise application.

**Architecture:** Add a dedicated optional OAuth credential pair to `Settings`. OAuth resolves the dedicated pair first and falls back to the existing bot pair only when both dedicated values are absent; the bot continues to read only the existing pair.

**Tech Stack:** Python 3.11, Pydantic Settings, pytest, FastAPI application lifecycle.

---

### Task 1: Define Credential Resolution and Validation

**Files:**
- Modify: `tests/test_settings.py`
- Modify: `knowledge/config/settings.py`

- [ ] **Step 1: Write failing settings tests**

Add tests that express dedicated precedence, fallback, and pair validation:

```python
def test_settings_prefers_dedicated_feishu_oauth_credentials():
    settings = Settings(
        _env_file=None,
        FEISHU_APP_ID="cli_bot",
        FEISHU_APP_SECRET="bot-secret",
        FEISHU_OAUTH_APP_ID="cli_oauth",
        FEISHU_OAUTH_APP_SECRET="oauth-secret",
    )
    assert settings.resolved_feishu_app_id == "cli_bot"
    assert settings.resolved_feishu_oauth_app_id == "cli_oauth"
    assert settings.resolved_feishu_oauth_app_secret == "oauth-secret"


def test_settings_falls_back_to_shared_feishu_credentials_for_oauth():
    settings = Settings(
        _env_file=None,
        FEISHU_APP_ID="cli_shared",
        FEISHU_APP_SECRET="shared-secret",
    )
    assert settings.resolved_feishu_oauth_app_id == "cli_shared"
    assert settings.resolved_feishu_oauth_app_secret == "shared-secret"


@pytest.mark.parametrize(
    "values",
    [
        {"FEISHU_OAUTH_APP_ID": "cli_oauth"},
        {"FEISHU_OAUTH_APP_SECRET": "oauth-secret"},
    ],
)
def test_settings_rejects_partial_dedicated_feishu_oauth_credentials(values):
    with pytest.raises(
        ValidationError,
        match="FEISHU_OAUTH_APP_ID and FEISHU_OAUTH_APP_SECRET",
    ):
        Settings(_env_file=None, **values)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
python -m pytest tests/test_settings.py -k "dedicated_feishu_oauth or falls_back_to_shared" -q
```

Expected: failures because the dedicated fields and resolved OAuth properties do not exist.

- [ ] **Step 3: Add dedicated settings and deterministic resolution**

Add fields next to the current Feishu OAuth settings:

```python
feishu_oauth_app_id: str = Field(default="", alias="FEISHU_OAUTH_APP_ID")
feishu_oauth_app_secret: str = Field(
    default="", alias="FEISHU_OAUTH_APP_SECRET"
)
```

Validate that the dedicated pair is all-or-none, then retain the existing shared-pair validation only when OAuth would use the fallback pair:

```python
oauth_id = bool(_usable(self.feishu_oauth_app_id))
oauth_secret = bool(_usable(self.feishu_oauth_app_secret))
if oauth_id != oauth_secret:
    raise ValueError(
        "FEISHU_OAUTH_APP_ID and FEISHU_OAUTH_APP_SECRET must be configured together"
    )
if (
    self.feishu_oauth_enabled
    and not (oauth_id and oauth_secret)
    and bool(_usable(self.feishu_app_id))
    != bool(_usable(self.feishu_app_secret))
):
    raise ValueError(
        "Feishu OAuth fallback requires both FEISHU_APP_ID and FEISHU_APP_SECRET"
    )
```

Add resolved properties and update availability:

```python
@property
def resolved_feishu_oauth_app_id(self) -> str:
    return _usable(self.feishu_oauth_app_id) or self.resolved_feishu_app_id

@property
def resolved_feishu_oauth_app_secret(self) -> str:
    return _usable(self.feishu_oauth_app_secret) or self.resolved_feishu_app_secret

@property
def feishu_oauth_available(self) -> bool:
    return bool(
        self.user_auth_enabled
        and self.feishu_oauth_enabled
        and self.resolved_feishu_oauth_app_id
        and self.resolved_feishu_oauth_app_secret
    )
```

- [ ] **Step 4: Run settings tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_settings.py -q
```

Expected: all settings tests pass.

### Task 2: Route OAuth Through Its Dedicated Pair

**Files:**
- Modify: `tests/test_user_auth_api.py`
- Modify: `knowledge/auth/service.py`

- [ ] **Step 1: Write a failing service construction test**

Add a test that constructs the real service with fake credentials and checks which application is selected:

```python
def test_user_auth_service_uses_dedicated_feishu_oauth_application(tmp_path):
    settings = Settings(
        _env_file=None,
        USER_AUTH_DB=tmp_path / "auth.db",
        FEISHU_APP_ID="cli_bot",
        FEISHU_APP_SECRET="bot-secret",
        FEISHU_OAUTH_APP_ID="cli_oauth",
        FEISHU_OAUTH_APP_SECRET="oauth-secret",
    )
    repository = UserAuthRepository(settings.resolved_user_auth_db)
    service = UserAuthService(settings, repository)
    assert service.oauth_client is not None
    assert service.oauth_client.app_id == "cli_oauth"
```

- [ ] **Step 2: Run the service test and verify RED**

Run:

```powershell
python -m pytest tests/test_user_auth_api.py -k dedicated_feishu_oauth_application -q
```

Expected: failure because `UserAuthService` still selects the shared bot application.

- [ ] **Step 3: Use resolved OAuth credentials in the service**

Change only the OAuth client construction:

```python
self.oauth_client = FeishuOAuthClient(
    app_id=settings.resolved_feishu_oauth_app_id,
    app_secret=settings.resolved_feishu_oauth_app_secret,
    callback_url=settings.feishu_oauth_callback_url,
    tenant_key=settings.feishu_tenant_key,
)
```

- [ ] **Step 4: Run focused auth tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_user_auth_api.py tests/test_feishu_oauth.py -q
```

Expected: all OAuth and user-auth tests pass.

### Task 3: Document the Separate Deployment Values

**Files:**
- Modify: `.env.example`
- Modify: `docs/optional-feishu-identity.md`

- [ ] **Step 1: Add safe placeholders to `.env.example`**

Keep the existing bot fields and add the dedicated OAuth pair under optional web identity:

```dotenv
FEISHU_OAUTH_APP_ID=<fill-locally>
FEISHU_OAUTH_APP_SECRET=<fill-locally>
```

- [ ] **Step 2: Update deployment documentation**

Document that the bot always uses `FEISHU_APP_ID/SECRET`, OAuth prefers `FEISHU_OAUTH_APP_ID/SECRET`, and OAuth falls back to the shared pair only for backward compatibility. State that real values remain local and must not be pasted into chat or committed.

- [ ] **Step 3: Run the configuration regression tests**

Run:

```powershell
python -m pytest tests/test_settings.py tests/test_app_lifespan.py tests/test_user_auth_api.py tests/test_feishu_oauth.py -q
```

Expected: all selected tests pass.

### Task 4: Full Verification and Local Handoff

**Files:**
- Verify only: `knowledge/`, `tests/`, `web/`

- [ ] **Step 1: Run the full non-live Python suite**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass; live integration tests remain skipped unless explicitly enabled.

- [ ] **Step 2: Run frontend regressions**

Run:

```powershell
npm test -- --run
npm run build
```

from `web/`.

Expected: Vitest and production build succeed.

- [ ] **Step 3: Configure local credentials without exposing them**

The operator adds the test-enterprise pair directly to `.env` while preserving the formal bot pair:

```dotenv
FEISHU_BOT_ENABLED=true
FEISHU_OAUTH_ENABLED=true
FEISHU_OAUTH_APP_ID=<test-application-id>
FEISHU_OAUTH_APP_SECRET=<test-application-secret>
FEISHU_TENANT_KEY=
```

Do not print or read back credential values. Check only whether each required key is non-empty.

- [ ] **Step 4: Restart and verify both integrations**

After confirming `sync_jobs`, `eval_runs`, and `memory_extraction_jobs` have no queued or running work, restart with one Uvicorn worker. Verify readiness, confirm the bot component remains available, request `/api/v1/auth/feishu/start` without following redirects, and confirm only that its decoded `redirect_uri` equals:

```text
http://172.18.26.1:8000/api/v1/auth/feishu/callback
```

Complete one browser login and verify `/api/v1/auth/me` reports `identity_kind=feishu` without logging OAuth state, code, cookies, or tokens.
