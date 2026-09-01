"""OpenDART API 클라이언트.

금융감독원 전자공시(OpenDART) Open API를 감싼 얇은 래퍼.
API 키는 https://opendart.fss.or.kr 에서 발급받아 환경변수 DART_API_KEY 로 지정한다.
"""

from __future__ import annotations

import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests

BASE_URL = "https://opendart.fss.or.kr/api"

# 보고서 코드: 1분기 / 반기 / 3분기 / 사업보고서
REPRT_CODE = {
    "Q1": "11013",
    "H1": "11012",
    "Q3": "11014",
    "FY": "11011",
}

# OpenDART 공통 응답 상태코드
STATUS_MESSAGE = {
    "000": "정상",
    "010": "등록되지 않은 키입니다",
    "011": "사용할 수 없는 키입니다 (오픈API에 등록되지 않은 개발자)",
    "012": "접근할 수 없는 IP입니다",
    "013": "조회된 데이터가 없습니다",
    "014": "파일이 존재하지 않습니다",
    "020": "요청 제한을 초과하였습니다 (일 20,000건)",
    "021": "조회 가능한 회사 개수가 초과하였습니다",
    "100": "필드의 부적절한 값입니다",
    "101": "부적절한 접근입니다",
    "800": "시스템 점검으로 서비스가 중단 중입니다",
    "900": "정의되지 않은 오류가 발생하였습니다",
    "901": "사용자 계정의 개인정보 보유기간이 만료되었습니다",
}


class DartError(RuntimeError):
    """OpenDART 호출 실패."""


class DartNoData(DartError):
    """상태코드 013 - 해당 조건에 공시 데이터가 없음."""


class DartClient:
    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: str | Path = "data/raw",
        timeout: int = 30,
        max_retries: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ.get("DART_API_KEY", "").strip()
        if not self.api_key:
            raise DartError(
                "DART_API_KEY 가 설정되지 않았습니다. "
                "https://opendart.fss.or.kr 에서 키를 발급받은 뒤 "
                "`export DART_API_KEY=...` 로 지정하세요."
            )
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    # 저수준 호출
    # ------------------------------------------------------------------ #
    def _request(self, path: str, params: dict[str, str]) -> requests.Response:
        url = f"{BASE_URL}/{path}"
        payload = {"crtfc_key": self.api_key, **params}

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=payload, timeout=self.timeout)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:  # 네트워크/HTTP 오류만 재시도
                last_exc = exc
                if attempt == self.max_retries - 1:
                    break
                time.sleep(2 ** (attempt + 1))

        raise DartError(
            f"OpenDART 호출 실패: {url}\n"
            f"  마지막 오류: {last_exc}\n"
            "  사내망/프록시 환경에서는 opendart.fss.or.kr 로의 아웃바운드가 "
            "차단되어 있는지 확인하세요."
        ) from last_exc

    def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        data = self._request(path, params).json()
        status = str(data.get("status", ""))
        if status == "013":
            raise DartNoData(
                f"조회된 데이터 없음 ({path}, {params})"
            )
        if status != "000":
            raise DartError(
                f"OpenDART 오류 status={status} "
                f"({STATUS_MESSAGE.get(status, data.get('message', '알 수 없음'))}) "
                f"- {path}, {params}"
            )
        return data

    # ------------------------------------------------------------------ #
    # 고유번호(corp_code) 조회
    # ------------------------------------------------------------------ #
    def corp_code_table(self, refresh: bool = False) -> list[dict[str, str]]:
        """전체 공시대상 회사의 고유번호 목록.

        약 10만 건 규모라 로컬에 캐시한다.
        """
        cache = self.cache_dir / "corp_codes.json"
        if cache.exists() and not refresh:
            return json.loads(cache.read_text(encoding="utf-8"))

        resp = self._request("corpCode.xml", {})
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])

        root = ElementTree.fromstring(xml_bytes)
        rows = [
            {
                "corp_code": (el.findtext("corp_code") or "").strip(),
                "corp_name": (el.findtext("corp_name") or "").strip(),
                "stock_code": (el.findtext("stock_code") or "").strip(),
                "modify_date": (el.findtext("modify_date") or "").strip(),
            }
            for el in root.iter("list")
        ]
        cache.write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return rows

    def find_corp_code(
        self, *, stock_code: str | None = None, corp_name: str | None = None
    ) -> str:
        """종목코드 또는 회사명으로 고유번호를 찾는다. 종목코드가 더 정확하다."""
        table = self.corp_code_table()

        if stock_code:
            wanted = stock_code.strip().zfill(6)
            hits = [r for r in table if r["stock_code"] == wanted]
            if hits:
                return hits[0]["corp_code"]
            raise DartError(f"종목코드 {wanted} 에 해당하는 회사를 찾지 못했습니다.")

        if corp_name:
            exact = [r for r in table if r["corp_name"] == corp_name]
            if len(exact) == 1:
                return exact[0]["corp_code"]
            if len(exact) > 1:
                listed = [r for r in exact if r["stock_code"]]
                if len(listed) == 1:
                    return listed[0]["corp_code"]
                raise DartError(
                    f"회사명 '{corp_name}' 이 중복됩니다: "
                    + ", ".join(f"{r['corp_name']}({r['corp_code']})" for r in exact)
                    + " — stock_code 로 지정하세요."
                )
            raise DartError(f"회사명 '{corp_name}' 을 찾지 못했습니다.")

        raise DartError("stock_code 또는 corp_name 중 하나는 필요합니다.")

    # ------------------------------------------------------------------ #
    # 재무제표
    # ------------------------------------------------------------------ #
    def financial_statements(
        self,
        corp_code: str,
        year: int,
        period: str,
        fs_div: str = "CFS",
    ) -> list[dict[str, Any]]:
        """단일회사 전체 재무제표 (fnlttSinglAcntAll).

        period: Q1 / H1 / Q3 / FY
        fs_div: CFS(연결) / OFS(별도)
        """
        if period not in REPRT_CODE:
            raise DartError(f"알 수 없는 기간 '{period}'. {list(REPRT_CODE)} 중 하나여야 합니다.")

        data = self._get_json(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": REPRT_CODE[period],
                "fs_div": fs_div,
            },
        )
        rows = data.get("list", [])
        for row in rows:
            row["_year"] = year
            row["_period"] = period
            row["_fs_div"] = fs_div
        return rows

    def company(self, corp_code: str) -> dict[str, Any]:
        """기업개황 (company)."""
        return self._get_json("company.json", {"corp_code": corp_code})

    def filings(
        self, corp_code: str, bgn_de: str, end_de: str, pblntf_ty: str = "A"
    ) -> list[dict[str, Any]]:
        """공시검색 (list). pblntf_ty='A' 는 정기공시."""
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get_json(
                "list.json",
                {
                    "corp_code": corp_code,
                    "bgn_de": bgn_de,
                    "end_de": end_de,
                    "pblntf_ty": pblntf_ty,
                    "page_no": str(page),
                    "page_count": "100",
                },
            )
            out.extend(data.get("list", []))
            if page >= int(data.get("total_page", 1)):
                break
            page += 1
        return out
