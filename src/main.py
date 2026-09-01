"""핑거(163730) FDD 재무데이터 파이프라인 CLI.

사용 예:
    export DART_API_KEY=발급받은키
    python -m src.main fetch   --stock-code 163730 --years 2023 2024 2025
    python -m src.main build   --fs-div CFS
    python -m src.main report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from src import fdd_metrics, quarterly
from src.dart_client import DartClient, DartError, DartNoData

RAW_DIR = Path("data/raw")
OUT_DIR = Path("outputs")
RAW_FILE = RAW_DIR / "financials.json"

DEFAULT_STOCK_CODE = "163730"  # 핑거


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def cmd_fetch(args: argparse.Namespace) -> int:
    client = DartClient(cache_dir=RAW_DIR)

    corp_code = args.corp_code or client.find_corp_code(stock_code=args.stock_code)
    print(f"[fetch] corp_code = {corp_code} (stock_code={args.stock_code})")

    profile = client.company(corp_code)
    (RAW_DIR / "company.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[fetch] 기업개황 저장: {profile.get('corp_name')} / {profile.get('ceo_nm')}")

    collected: list[dict] = []
    missing: list[str] = []

    for year in args.years:
        for period in quarterly.PERIOD_ORDER:
            for fs_div in args.fs_div:
                tag = f"{year}-{period}-{fs_div}"
                try:
                    rows = client.financial_statements(corp_code, year, period, fs_div)
                except DartNoData:
                    missing.append(tag)
                    print(f"[fetch] {tag}: 데이터 없음 (미제출 또는 미공시)")
                    continue
                collected.extend(rows)
                print(f"[fetch] {tag}: {len(rows)}개 계정")

    if not collected:
        print("[fetch] 수집된 데이터가 없습니다.", file=sys.stderr)
        return 1

    RAW_FILE.write_text(
        json.dumps(collected, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[fetch] 저장 완료: {RAW_FILE} ({len(collected)}행)")
    if missing:
        print(f"[fetch] 누락 보고서: {', '.join(missing)}")

    filings = client.filings(
        corp_code, f"{min(args.years)}0101", f"{max(args.years)}1231"
    )
    (RAW_DIR / "filings.json").write_text(
        json.dumps(filings, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[fetch] 정기공시 목록 {len(filings)}건 저장")
    return 0


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def cmd_build(args: argparse.Namespace) -> int:
    if not RAW_FILE.exists():
        print(
            f"[build] {RAW_FILE} 가 없습니다. 먼저 `fetch` 를 실행하세요.",
            file=sys.stderr,
        )
        return 1

    rows = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    frame = quarterly.to_dataframe(rows)
    print(f"[build] 원자료 {len(frame)}행")

    qframe = quarterly.build_quarterly(frame, fs_div=args.fs_div)
    lines, mapping = fdd_metrics.extract_lines(qframe, trace=True)
    metrics = fdd_metrics.build_metrics(lines)
    recon = quarterly.reconciliation(frame, fs_div=args.fs_div)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    excel_path = OUT_DIR / f"핑거_분기재무제표_{args.fs_div}.xlsx"

    statements = {"BS": "재무상태표", "IS": "손익계산서", "CIS": "포괄손익계산서", "CF": "현금흐름표"}
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        for sj_div, sheet in statements.items():
            block = qframe[qframe.index.get_level_values("sj_div") == sj_div]
            if block.empty:
                continue
            block.reset_index().to_excel(writer, sheet_name=sheet, index=False)
        if not lines.empty:
            lines.to_excel(writer, sheet_name="FDD표준라인")
        if not metrics.empty:
            metrics.to_excel(writer, sheet_name="FDD지표")
        if not recon.empty:
            recon.to_excel(writer, sheet_name="차분대사", index=False)
        if not mapping.empty:
            mapping.to_excel(writer, sheet_name="계정매핑", index=False)

    print(f"[build] 엑셀 저장: {excel_path}")

    metrics_md = OUT_DIR / "재무분석표.md"
    _write_markdown(metrics_md, lines, metrics, recon, args.fs_div)
    print(f"[build] 마크다운 표 저장: {metrics_md}")

    if not recon.empty:
        print(
            f"[build] 경고: 차분값과 보고서 표기 3개월 수치가 어긋난 계정 {len(recon)}건. "
            "소급재작성/연결범위 변동 가능성을 확인하세요."
        )

    print()
    print("=" * 72)
    print("아래 표를 그대로 복사해 전달하면 분석·해석을 이어서 진행할 수 있습니다.")
    print("=" * 72)
    print()
    print(metrics.to_markdown() if not metrics.empty else "(지표 없음)")
    print()
    return 0


def _write_markdown(
    path: Path,
    lines: pd.DataFrame,
    metrics: pd.DataFrame,
    recon: pd.DataFrame,
    fs_div: str,
) -> None:
    basis = "연결" if fs_div == "CFS" else "별도"
    parts = [
        f"# 핑거(163730) 분기 재무 분석표 ({basis}기준)",
        "",
        "> 출처: 금융감독원 전자공시(DART) OpenAPI `fnlttSinglAcntAll`.",
        "> 손익·현금흐름은 보고서상 누적치를 차분한 **분기 단독** 수치입니다.",
        "> 금액 단위: 억원.",
        "",
        "## 1. FDD 표준 라인 (원화, 원 단위)",
        "",
        lines.to_markdown() if not lines.empty else "_데이터 없음_",
        "",
        "## 2. FDD 지표 요약",
        "",
        metrics.to_markdown() if not metrics.empty else "_데이터 없음_",
        "",
        "## 3. 차분 대사 (분기 단독 계산치 vs 보고서 표기 3개월)",
        "",
        recon.to_markdown(index=False)
        if not recon.empty
        else "_불일치 항목 없음 — 차분 결과가 보고서 표기치와 일치합니다._",
        "",
    ]
    path.write_text("\n".join(parts), encoding="utf-8")


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def cmd_doctor(args: argparse.Namespace) -> int:
    """실행 전 환경 점검. 무엇이 막혔는지 먼저 알려준다."""
    ok = True

    key = __import__("os").environ.get("DART_API_KEY", "").strip()
    if key:
        print(f"[doctor] DART_API_KEY: 설정됨 (길이 {len(key)})")
        if len(key) != 40:
            print("[doctor]   주의: OpenDART 인증키는 보통 40자입니다. 값을 확인하세요.")
    else:
        ok = False
        print("[doctor] DART_API_KEY: 없음")
        print("[doctor]   https://opendart.fss.or.kr 에서 발급 후 아래처럼 지정하세요.")
        print("[doctor]   export DART_API_KEY=발급받은키")

    try:
        import requests

        resp = requests.get("https://opendart.fss.or.kr/", timeout=15)
        print(f"[doctor] opendart.fss.or.kr 연결: OK (HTTP {resp.status_code})")
    except Exception as exc:  # noqa: BLE001 - 진단 목적이라 폭넓게 잡는다
        ok = False
        print(f"[doctor] opendart.fss.or.kr 연결: 실패 - {exc}")
        print("[doctor]   방화벽/프록시가 DART 도메인을 차단하고 있는지 확인하세요.")

    if key and ok:
        try:
            client = DartClient(cache_dir=RAW_DIR)
            corp_code = client.find_corp_code(stock_code=args.stock_code)
            profile = client.company(corp_code)
            print(
                f"[doctor] API 인증 및 조회: OK - "
                f"{profile.get('corp_name')} (corp_code={corp_code})"
            )
        except DartError as exc:
            ok = False
            print(f"[doctor] API 조회 실패: {exc}")

    print()
    print("[doctor] 결과:", "정상 - fetch 를 실행하세요." if ok else "문제 있음 (위 항목 확인)")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="finger-fdd", description="핑거(163730) FDD 재무데이터 파이프라인"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="DART 에서 재무제표 원자료 수집")
    fetch.add_argument("--stock-code", default=DEFAULT_STOCK_CODE)
    fetch.add_argument("--corp-code", default=None, help="DART 고유번호 직접 지정")
    fetch.add_argument(
        "--years", nargs="+", type=int, default=[2023, 2024, 2025],
        help="수집 사업연도 (기본: 최근 3개년)",
    )
    fetch.add_argument(
        "--fs-div", nargs="+", default=["CFS", "OFS"],
        choices=["CFS", "OFS"], help="CFS=연결, OFS=별도",
    )
    fetch.set_defaults(func=cmd_fetch)

    doctor = sub.add_parser("doctor", help="API 키 및 DART 연결 상태 점검")
    doctor.add_argument("--stock-code", default=DEFAULT_STOCK_CODE)
    doctor.set_defaults(func=cmd_doctor)

    build = sub.add_parser("build", help="분기 시계열 및 FDD 지표 산출")
    build.add_argument("--fs-div", default="CFS", choices=["CFS", "OFS"])
    build.set_defaults(func=cmd_build)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DartError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
