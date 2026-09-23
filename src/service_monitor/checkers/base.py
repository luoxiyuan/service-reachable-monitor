"""Checker interface shared by HTTP and Dubbo probes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..config import EnvironmentConfig
from ..models import CheckResult


class BaseChecker(ABC):
    """Probes every relevant target of an environment."""

    #: value matched against ``Target.kind``.
    kind: str = "base"

    @abstractmethod
    def check_environment(self, environment: EnvironmentConfig) -> List[CheckResult]:
        """Return one result per target; never raise for a single bad target."""
        raise NotImplementedError