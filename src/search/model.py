from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(
        min_length=1,
        max_length=200,
        description="검색할 문자열. 앞뒤 및 연속 공백은 정규화됩니다.",
        examples=["한국외국어대학교 날씨"],
    )

    @field_validator("query")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("검색어에는 제어 문자를 사용할 수 없습니다.")
        return value


class SearchAcceptedResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    magic_code: str = Field(
        alias="magicCode",
        description="검색 상태와 결과를 조회할 때 사용하는 일회성 권한 증표",
        examples=["Y6hQTcAkFjC4vAGscR5J0bnYKtD-_osRYVQ97tL7u5I"],
    )
    status: str = Field(default="PENDING", description="접수 시점의 검색 상태")


class SearchPendingResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    magic_code: str = Field(alias="magicCode")
    status: str = Field(default="PENDING")


class SearchResultResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    magic_code: str = Field(alias="magicCode")
    status: str = Field(default="COMPLETED")
    result: Any = Field(description="검색 작업자가 Redis에 JSON으로 저장한 검색 결과")
