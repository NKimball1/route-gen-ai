"""Domain validation after structured LLM output, before side effects."""
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequestPart(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RideRequest(RequestPart):
    distance_miles: float = Field(gt=0)
    max_climb_ft: float | None = Field(default=None, ge=0)
    maximize_climb: bool = False
    minimize_climb: bool = False
    shape: Literal["loop", "outback", "both"] = "loop"
    avoid_places: list[str] = Field(default_factory=list)
    via_places: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_goal(self) -> Self:
        if self.maximize_climb and self.minimize_climb:
            raise ValueError("Choose either more climbing or less climbing.")
        return self


class IntervalRequest(RequestPart):
    reps: int = Field(gt=0)
    rep_minutes: float = Field(gt=0)
    kind: Literal["flat", "incline", "any"]
    max_travel_minutes: float = Field(gt=0)
    watts: float | None = Field(default=None, gt=0)
    total_kg: float | None = Field(default=None, gt=0)
    max_stops: int | None = Field(default=None, ge=0)


class EditRequest(RequestPart):
    mode: Literal["avoid", "via", "extend", "shorten", "move_start", "move_end", "anchor", "connect"]
    place: str | None = None
    places: list[str] | None = None
    radius_m: float = Field(gt=0)
    miles_delta: float | None = Field(default=None, ge=0)
    target_miles: float | None = Field(default=None, gt=0)
    connect_return: bool = False
    revert_first: bool = False


class ParsedRequest(RequestPart):
    request_type: Literal["route", "interval_spot", "edit_route", "undo", "clarify"]
    address: str | None = None
    route: RideRequest | None = None
    interval: IntervalRequest | None = None
    edit: EditRequest | None = None
    notes: str = ""

    @model_validator(mode="after")
    def matching_branch(self) -> Self:
        expected = {"route": "route", "interval_spot": "interval", "edit_route": "edit"}.get(self.request_type)
        for name in ("route", "interval", "edit"):
            if (getattr(self, name) is not None) != (name == expected):
                raise ValueError("The interpretation contains inconsistent request types. Please rephrase.")
        return self
