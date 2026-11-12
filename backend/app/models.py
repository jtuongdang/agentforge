from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1)
    default_branch: str = "main"
    validation_profile: str = Field(default="auto", pattern="^(auto|maven|node|python|go|custom)$")
    validation_command: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("validation_command")
    @classmethod
    def validate_command(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 300 or "\n" in value or "\r" in value for value in cleaned):
            raise ValueError("Each validation command argument must be a non-empty token of at most 300 characters")
        return cleaned

    @model_validator(mode="after")
    def require_custom_command(self):
        if self.validation_profile in {"custom", "node", "python", "go"} and not self.validation_command:
            raise ValueError("This validation profile requires an explicit argv command")
        if self.validation_profile in {"auto", "maven"} and self.validation_command:
            raise ValueError("This validation profile does not accept a custom command")
        return self


class RepositoryPreflight(BaseModel):
    path: str = Field(min_length=1)
    default_branch: str = "main"
    validation_profile: str = Field(default="auto", pattern="^(auto|maven|node|python|go|custom)$")
    validation_command: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("validation_command")
    @classmethod
    def validate_command(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 300 or "\n" in value or "\r" in value for value in cleaned):
            raise ValueError("Each validation command argument must be a non-empty token of at most 300 characters")
        return cleaned

    @model_validator(mode="after")
    def validate_profile_command(self):
        if self.validation_profile in {"custom", "node", "python", "go"} and not self.validation_command:
            raise ValueError("This validation profile requires an explicit argv command")
        if self.validation_profile in {"auto", "maven"} and self.validation_command:
            raise ValueError("This validation profile does not accept a custom command")
        return self


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    adapter: str = Field(pattern="^(codex|copilot|antigravity|exec|handoff)$")
    command: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    file_globs: list[str] = Field(default_factory=lambda: ["**/*"])
    max_parallel: int = Field(default=1, ge=1, le=8)


class ProviderConnectionCreate(BaseModel):
    provider: str = Field(pattern="^(openai|anthropic|gemini|ollama)$")
    name: str = Field(min_length=1, max_length=120)
    model: str = Field(default="", max_length=160)
    api_key: SecretStr | None = None
    endpoint: str | None = Field(default=None, max_length=300)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and (not value.get_secret_value().strip() or len(value.get_secret_value()) > 4096):
            raise ValueError("API key must contain 1 to 4096 characters")
        return value


class ProviderConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, max_length=160)
    api_key: SecretStr | None = None

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and (not value.get_secret_value().strip() or len(value.get_secret_value()) > 4096):
            raise ValueError("API key must contain 1 to 4096 characters")
        return value


class LicenseActivation(BaseModel):
    license_key: SecretStr = Field(min_length=1, max_length=256)


class ContractCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=10, max_length=30000)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    objective: str = Field(min_length=10, max_length=12000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    repository_id: str
    base_branch: str | None = None
    required_capabilities: list[str] = Field(default_factory=lambda: ["implementation"])
    file_globs: list[str] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    contract_id: str | None = None
    preferred_agent_id: str | None = None
    provider_data_consent: bool = False
    max_retries: int = Field(default=2, ge=0, le=2)

    @field_validator("file_globs")
    @classmethod
    def validate_globs(cls, values: list[str]) -> list[str]:
        cleaned = [p.strip().replace("\\", "/") for p in values if p.strip()]
        if not cleaned:
            raise ValueError("At least one allowed file glob is required")
        if any(p.startswith("/") or ".." in p.split("/") for p in cleaned):
            raise ValueError("File globs must stay relative to the repository")
        return cleaned

    @field_validator("base_branch")
    @classmethod
    def clean_base_branch(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()

    @field_validator("acceptance_criteria")
    @classmethod
    def clean_acceptance_criteria(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if any(len(value) > 500 for value in cleaned):
            raise ValueError("Each acceptance criterion must be 500 characters or fewer")
        return cleaned


class CompleteHandoff(BaseModel):
    report: str = Field(min_length=3, max_length=5000)


class RestartRecoveryRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=80)


class FleetSettingsUpdate(BaseModel):
    max_active_runs: int | None = Field(default=None, ge=1, le=32)
    max_parallel_builds: int | None = Field(default=None, ge=1, le=16)
    max_per_credential: int | None = Field(default=None, ge=1, le=16)
    min_free_disk_bytes: int | None = Field(default=None, ge=256 * 1024**2, le=1024**4)
    disk_reserve_per_run_bytes: int | None = Field(default=None, ge=64 * 1024**2, le=1024**4)


class ProjectCheckpointCreate(BaseModel):
    include_uncommitted: bool = False


class ProjectCheckpointRestore(BaseModel):
    checkpoint_path: str = Field(min_length=1, max_length=4096)
    destination_parent: str = Field(min_length=1, max_length=4096)


class LeadPairingCreate(BaseModel):
    scopes: list[str] = Field(default_factory=lambda: ["handover:read"])


class RemotePairingRequest(BaseModel):
    pairing_code: str = Field(min_length=1, max_length=256)
    client_id: str = Field(min_length=1, max_length=160)
    client_name: str = Field(min_length=1, max_length=120)


class RemotePairingPoll(BaseModel):
    poll_secret: str = Field(min_length=1, max_length=256)


class RemoteWsTicketRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=160)


class RemoteDiffGrant(BaseModel):
    revision_sha: str = Field(min_length=40, max_length=64)
    confirmation: str = Field(min_length=1, max_length=120)


class LeadHandshake(BaseModel):
    pairing_code: str = Field(min_length=20, max_length=128)
    client_id: str = Field(min_length=1, max_length=160)
    client_name: str = Field(min_length=1, max_length=160)
    requested_scopes: list[str] | None = None


class LeadReview(BaseModel):
    decision: str = Field(pattern="^(approve|request_changes|comment)$")
    comments: str = Field(default="", max_length=30000)

