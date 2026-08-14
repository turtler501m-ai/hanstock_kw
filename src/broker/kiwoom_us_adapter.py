"""Read-only Kiwoom US-stock adapter for the legacy mistock service."""

from __future__ import annotations

from typing import Any, Mapping

from src.broker.kiwoom_client import KiwoomRestClient


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


class KiwoomUSStockAdapter:
    """Expose Kiwoom US account data in the KIS-shaped format mistock uses.

    This adapter deliberately has no order methods.  Activating it cannot make
    an existing mistock order path submit a Kiwoom order accidentally.
    """

    broker_name = "kiwoom"

    def __init__(self, client: KiwoomRestClient, *, account_no: str = "") -> None:
        self.client = client
        self.account_no = account_no.strip()

    def get_overseas_balance(self) -> dict[str, Any]:
        pages = self.client.post_all_pages(
            "/api/us/acnt",
            api_id="ust21070",
            body={"stex_tp": "", "stk_cd": ""},
        )
        rows: list[dict[str, Any]] = []
        summary: Mapping[str, Any] = {}
        for page in pages:
            data = page.data
            if not summary:
                summary = data
            result_list = data.get("result_list") or []
            if isinstance(result_list, Mapping):
                result_list = [result_list]
            for item in result_list:
                if isinstance(item, Mapping):
                    rows.append(self._holding(item))
        return {
            "output1": rows,
            "output2": {
                "crcy_cd": _text(summary.get("crnc_code")) or "USD",
                "frcr_evlu_tota": _text(summary.get("tot_evlt_amt")),
                "tot_evlu_amt": _text(summary.get("tot_evlt_amt")),
                "tot_pfls_amt": _text(summary.get("tot_pl_amt")),
            },
            "output3": {},
            "_broker": "kiwoom",
            "_account_configured": bool(self.account_no),
        }

    @staticmethod
    def _holding(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "pdno": _text(item.get("stk_cd")),
            "prdt_name": _text(item.get("frgn_stk_nm")) or _text(item.get("stk_cd")),
            "cblc_qty13": _text(item.get("poss_qty") or item.get("qty")),
            "avg_unpr3": _text(item.get("frgn_stk_book_uv")),
            "ovrs_now_pric1": _text(item.get("now_pric")),
            "frcr_evlu_amt2": _text(item.get("evlt_amt")),
            "evlu_pfls_amt2": _text(item.get("pl_amt")),
            "evlu_pfls_rt1": _text(item.get("pl_rt")),
            "ovrs_excg_cd": _text(item.get("stex_nm")),
        }

