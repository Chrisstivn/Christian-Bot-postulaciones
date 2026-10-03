"""ATS plugin registry."""

from __future__ import annotations

from .base import BaseATSHandler
from .greenhouse import GreenhouseHandler
from .lever import LeverHandler
from .workday import WorkdayHandler
from .ashby import AshbyHandler
from .smartrecruiters import SmartRecruitersHandler
from .icims import ICIMSHandler
from .personio import PersonioHandler
from .successfactors import SuccessFactorsHandler
from .teamtailor import TeamtailorHandler
from .join import JoinHandler
from .hibob import HiBobHandler
from .amazonjobs import AmazonJobsHandler
from .linkedin import LinkedInHandler

HANDLERS: dict[str, type[BaseATSHandler]] = {
    "greenhouse": GreenhouseHandler,
    "lever": LeverHandler,
    "workday": WorkdayHandler,
    "ashby": AshbyHandler,
    "smartrecruiters": SmartRecruitersHandler,
    "icims": ICIMSHandler,
    "personio": PersonioHandler,
    "successfactors": SuccessFactorsHandler,
    "teamtailor": TeamtailorHandler,
    "join": JoinHandler,
    "hibob": HiBobHandler,
    "amazonjobs": AmazonJobsHandler,
    "linkedin": LinkedInHandler,
}


def get_handler(ats: str) -> BaseATSHandler:
    cls = HANDLERS.get(ats, BaseATSHandler)
    return cls()
