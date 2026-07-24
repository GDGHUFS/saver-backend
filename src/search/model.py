from typing import Literal

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


class KagiRelatedSearch(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    title: str = Field(min_length=1)


class KagiSearchImage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    url: str = Field(min_length=1)


class KagiSearchResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    snippet: str | None = None
    image: KagiSearchImage | None = None


class KagiSearchData(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    related_search: list[KagiRelatedSearch] = Field(default_factory=list)
    search: list[KagiSearchResult] = Field(default_factory=list)


class KagiSearchMeta(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    ms: int = Field(ge=0)


class KagiSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    data: KagiSearchData
    meta: KagiSearchMeta

    def to_result_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


class IntelligentSearchResponse(KagiSearchResponse):
    answer: str = Field(
        min_length=1,
        max_length=20_000,
        description="검색 결과를 근거로 intelligent worker가 생성한 최종 답변",
    )


class LegacySearchBranchResponse(BaseModel):
    status: Literal["PENDING", "COMPLETED", "FAILED"]
    result: KagiSearchResponse | None = None


class IntelligentSearchBranchResponse(BaseModel):
    status: Literal["PENDING", "COMPLETED", "FAILED"]
    result: IntelligentSearchResponse | None = None


class SearchResultsResponse(BaseModel):
    legacy: LegacySearchBranchResponse
    intelligent: IntelligentSearchBranchResponse


class SearchResultResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    magic_code: str = Field(alias="magicCode")
    status: Literal["PENDING", "COMPLETED", "PARTIAL"]
    results: SearchResultsResponse = Field(
        description="legacy 및 intelligent 검색 작업자의 독립적인 상태와 결과"
    )
