import re
from typing import Optional

from pydantic import BaseModel, field_validator

LINKEDIN_PROFILE_URL_RE = re.compile(
    r"^https?://(www\.)?linkedin\.com/in/[a-zA-Z0-9\-_%]+/?(\?.*)?$"
)


class ProfileRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_linkedin_url(cls, value: str) -> str:
        value = value.strip()
        if not LINKEDIN_PROFILE_URL_RE.match(value):
            raise ValueError(
                "url must be a valid LinkedIn profile URL, e.g. "
                "https://www.linkedin.com/in/some-person/"
            )
        return value


class ExperienceItem(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    duration: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None


class EducationItem(BaseModel):
    school: Optional[str] = None
    degree: Optional[str] = None
    duration: Optional[str] = None


class ProfileResponse(BaseModel):
    name: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    about: Optional[str] = None
    experience: list[ExperienceItem] = []
    education: list[EducationItem] = []
    skills: list[str] = []
    certifications: list[str] = []
    languages: list[str] = []
    profile_image_url: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


class BootstrapRequest(BaseModel):
    cookie_header: str


class LoginRequest(BaseModel):
    username: str
    password: str
