"""DART 재무제표 원자료를 '분기 단독' 시계열로 변환한다.

DART 정기보고서의 손익계산서/현금흐름표는 기본이 '누적(YTD)' 기준이다.
FDD에서 분기별 추세를 보려면 누적치를 차분해 분기 단독 수치를 만들어야 한다.

    Q1 단독 = 1분기보고서 누적
    Q2 단독 = 반기 누적       - Q1 누적
    Q3 단독 = 3분기 누적      - 반기 누적
    Q4 단독 = 사업보고서 연간 - 3분기 누적

재무상태표(BS)는 시점 데이터이므로 차분하지 않고 각 보고서의 기말 잔액을 그대로 쓴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

PERIOD_ORDER = ["Q1", "H1", "Q3", "FY"]
QUARTER_OF = {"Q1": "Q1", "H1": "Q2", "Q3": "Q3", "FY": "Q4"}

# 시점(stock) 항목 - 차분 대상이 아님
POINT_IN_TIME_SJ = {"BS"}
# 기간(flow) 항목 - 누적이므로 차분 필요
FLOW_SJ = {"IS", "CIS", "CF"}


def _to_number(value: Any) -> float | None:
    """DART 금액 문자열을 숫자로. '-' 나 빈 값은 None."""
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "-", "—", "N/A"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[(),\s]", "", text)
    if text in {"", "-"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _account_key(row: dict[str, Any]) -> str:
    """계정 식별자. IFRS 표준 account_id 우선, 없으면 계정명."""
    account_id = (row.get("account_id") or "").strip()
    if account_id and not account_id.startswith("-"):
        return account_id
    detail = (row.get("account_detail") or "").strip()
    name = (row.get("account_nm") or "").strip()
    return f"NM:{name}" + (f"|{detail}" if detail and detail != "-" else "")


def to_dataframe(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """OpenDART fnlttSinglAcntAll 응답 행들을 정규화된 DataFrame 으로."""
    records = []
    for row in rows:
        sj_div = (row.get("sj_div") or "").strip()
        ytd = _to_number(row.get("thstrm_add_amount"))
        reported = _to_number(row.get("thstrm_amount"))

        # 사업보고서/1분기보고서는 add_amount 가 비어있고 amount 자체가 누적이다.
        if ytd is None:
            ytd = reported

        records.append(
            {
                "fs_div": row.get("_fs_div"),
                "year": int(row["_year"]),
                "period": row["_period"],
                "sj_div": sj_div,
                "sj_nm": (row.get("sj_nm") or "").strip(),
                "account_key": _account_key(row),
                "account_nm": (row.get("account_nm") or "").strip(),
                "account_detail": (row.get("account_detail") or "").strip(),
                "ord": _to_number(row.get("ord")) or 0,
                "currency": (row.get("currency") or "KRW").strip(),
                "ytd_amount": ytd,
                "reported_amount": reported,
                "rcept_no": (row.get("rcept_no") or "").strip(),
            }
        )

    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame

    # 같은 보고서 안에서 계정이 중복되면(주석 분해 등) 첫 행을 대표로 둔다.
    frame = frame.drop_duplicates(
        subset=["fs_div", "year", "period", "sj_div", "account_key"], keep="first"
    )
    return frame


@dataclass(frozen=True)
class QuarterLabel:
    year: int
    quarter: str

    def __str__(self) -> str:  # 2025Q3
        return f"{self.year}{self.quarter}"


def build_quarterly(frame: pd.DataFrame, fs_div: str = "CFS") -> pd.DataFrame:
    """누적 재무제표를 분기 단독 시계열로 변환.

    반환: index = (sj_div, account_key, account_nm), columns = '2025Q3' 형태.
    """
    if frame.empty:
        return frame

    scoped = frame[frame["fs_div"] == fs_div]
    if scoped.empty:
        raise ValueError(f"fs_div={fs_div} 데이터가 없습니다.")

    columns: dict[str, pd.Series] = {}
    meta: dict[str, dict[str, Any]] = {}

    for year in sorted(scoped["year"].unique()):
        by_period = {
            period: scoped[(scoped["year"] == year) & (scoped["period"] == period)]
            for period in PERIOD_ORDER
        }

        previous_ytd: pd.Series | None = None
        for period in PERIOD_ORDER:
            block = by_period[period]
            if block.empty:
                # 해당 보고서가 없으면 이후 분기 차분의 기준선이 끊긴다.
                previous_ytd = None
                continue

            indexed = block.set_index(["sj_div", "account_key"])
            for (sj_div, key), row in indexed.iterrows():
                meta.setdefault(
                    (sj_div, key),
                    {"account_nm": row["account_nm"], "ord": row["ord"], "sj_nm": row["sj_nm"]},
                )

            label = str(QuarterLabel(int(year), QUARTER_OF[period]))
            ytd = indexed["ytd_amount"]

            flow_mask = indexed.index.get_level_values(0).isin(FLOW_SJ)
            standalone = ytd.copy()

            if previous_ytd is not None:
                aligned_prev = previous_ytd.reindex(ytd.index)
                # flow 항목만 차분. 직전 누적이 없는 신규 계정은 누적치를 그대로 쓴다.
                diffed = ytd - aligned_prev.fillna(0.0)
                standalone = standalone.where(~flow_mask, diffed)

            columns[label] = standalone
            previous_ytd = ytd

    if not columns:
        raise ValueError("분기 시계열을 만들 수 있는 보고서가 없습니다.")

    result = pd.DataFrame(columns)
    result = result[sorted(result.columns)]

    meta_frame = pd.DataFrame.from_dict(meta, orient="index")
    meta_frame.index = pd.MultiIndex.from_tuples(
        meta_frame.index, names=["sj_div", "account_key"]
    )
    result = meta_frame.join(result, how="right")

    result = result.sort_values(["sj_div", "ord"]).drop(columns=["ord"])
    return result


def reconciliation(frame: pd.DataFrame, fs_div: str = "CFS") -> pd.DataFrame:
    """차분으로 만든 분기 단독치 vs 보고서에 직접 표기된 3개월 수치 대사.

    반기·3분기 보고서는 '3개월' 컬럼(thstrm_amount)을 따로 싣는 경우가 많다.
    두 값이 어긋나면 소급 재작성(restatement)이나 연결범위 변동을 의심해야 한다.
    """
    scoped = frame[(frame["fs_div"] == fs_div) & (frame["sj_div"].isin(FLOW_SJ))]
    rows = []

    for year in sorted(scoped["year"].unique()):
        previous: pd.Series | None = None
        for period in PERIOD_ORDER:
            block = scoped[(scoped["year"] == year) & (scoped["period"] == period)]
            if block.empty:
                previous = None
                continue

            indexed = block.set_index(["sj_div", "account_key"])
            ytd = indexed["ytd_amount"]
            derived = ytd if previous is None else ytd - previous.reindex(ytd.index).fillna(0.0)

            for key in indexed.index:
                reported = indexed.loc[key, "reported_amount"]
                calc = derived.loc[key]
                if period == "Q1" or reported is None or calc is None:
                    continue
                if pd.isna(reported) or pd.isna(calc):
                    continue
                # 사업보고서의 thstrm_amount 는 연간치라 3개월과 비교 대상이 아니다.
                if period == "FY":
                    continue
                diff = float(calc) - float(reported)
                if abs(diff) > 1.0:
                    rows.append(
                        {
                            "기간": f"{year}{QUARTER_OF[period]}",
                            "구분": key[0],
                            "계정": indexed.loc[key, "account_nm"],
                            "차분값": float(calc),
                            "보고서표기_3개월": float(reported),
                            "차이": diff,
                        }
                    )
            previous = ytd

    return pd.DataFrame(rows)
