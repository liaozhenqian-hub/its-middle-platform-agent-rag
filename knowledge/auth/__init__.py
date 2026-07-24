"""Optional browser and machine identity services."""

from knowledge.auth.models import ResolvedIdentity
from knowledge.auth.repository import UserAuthRepository

__all__ = ["ResolvedIdentity", "UserAuthRepository"]
