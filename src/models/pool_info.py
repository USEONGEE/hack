from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from bson import ObjectId


def validate_object_id(v):
    if v is None:
        return ObjectId()
    if isinstance(v, ObjectId):
        return v
    if isinstance(v, str) and ObjectId.is_valid(v):
        return ObjectId(v)
    raise ValueError(f"Invalid ObjectId: {v}")


class PoolInfoModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
    )

    id: ObjectId = Field(
        default_factory=ObjectId,
        alias="_id",
        description="MongoDB document ID",
    )
    dex_type: str
    can_hedge: bool = False
    token0_address: str
    token1_address: str
    fee: Optional[int] = None
    pool_address: str
    version: Optional[str] = None

    @field_validator("id", mode="before")
    @classmethod
    def _validate_id(cls, v):
        return validate_object_id(v)
