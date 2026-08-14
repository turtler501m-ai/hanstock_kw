import unittest
from dataclasses import dataclass

from src.broker.kiwoom_adapter import KiwoomBrokerAdapter
from src.broker.models import CancelOrderRequest, OrderRequest, OrderSide, ReviseOrderRequest


@dataclass
class FakePage:
    data: dict


class FakeKiwoomClient:
    def __init__(self):
        self.daily_call = None
        self.rank_call = None

    def post(self, path, *, api_id, body=None, continuation=None, request_kind="query"):
        self.last_post = (path, api_id, body, request_kind)
        if api_id == "kt00018":
            return FakePage({
            "tot_evlt_amt": "1,210,000",
            "tot_asst_amt": "+1,710,000",
            "tot_evlt_pl": "-40,000",
            "acnt_evlt_remn_indv_tot": [{
                "stk_cd": "A005930", "stk_nm": "삼성전자", "rmnd_qty": "10",
                "trde_able_qty": "8", "pur_pric": "+75,000", "cur_prc": "-71,000",
                "evlt_amt": "710,000", "evlt_pl": "-40,000", "prft_rt": "-5.33",
            }],
            })
        if api_id == "kt00001":
            return FakePage({"entr": "500,000", "ord_alow_amt": "+480,000"})
        if api_id == "ka10001":
            symbol = body["stk_cd"]
            return FakePage({"stk_cd": f"A{symbol}", "cur_prc": "-71,000", "sel_fpr_bid": "+71,100", "buy_fpr_bid": "70,900", "mac": "424,000"})
        raise AssertionError(api_id)

    def post_all_pages(self, path, *, api_id, body=None, request_kind="query", max_pages=100):
        self.last_pages = (path, api_id, body, request_kind)
        if api_id == "ka10081":
            return [FakePage({"stk_dt_pole_chart_qry": [{"dt": "20260814", "open_pric": "+70,000", "high_pric": "72,000", "low_pric": "-69,500", "cur_prc": "+71,000", "trde_qty": "12,345"}]})]
        if api_id == "ka10030":
            return [FakePage({"trde_qty_upper": [{"stk_cd": "A005930"}]}), FakePage({"trde_qty_upper": [{"stk_cd": "000660"}, {"stk_cd": ""}]})]
        raise AssertionError(api_id)


class KiwoomBrokerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeKiwoomClient()
        self.adapter = KiwoomBrokerAdapter(self.client)

    def test_balance_combines_kt00018_and_kt00001(self):
        result = self.adapter.fetch_balance()
        self.assertEqual(result.cash, 480000)
        self.assertEqual(result.total_equity, 1710000)
        self.assertEqual(result.stock_value, 1210000)
        self.assertEqual(result.profit_loss, -40000)
        holding = result.holdings[0]
        self.assertEqual(holding.symbol, "005930")
        self.assertEqual(holding.quantity, 10)
        self.assertEqual(holding.sellable_quantity, 8)
        self.assertEqual(holding.current_price, 71000)
        self.assertEqual(holding.profit_loss_rate, -5.33)
        self.assertIn("kt00018", result.raw)
        self.assertIn("kt00001", result.raw)

    def test_balance_derives_total_equity_when_summary_field_is_absent(self):
        original = self.client.post
        self.client.post = lambda path, **kwargs: FakePage(
            {"tot_evlt_amt": "700", "acnt_evlt_remn_indv_tot": []}
            if kwargs["api_id"] == "kt00018" else {"entr": "300"}
        )
        result = self.adapter.fetch_balance()
        self.assertEqual(result.total_equity, 1000)
        self.client.post = original

    def test_quote_normalizes_signed_fields(self):
        result = self.adapter.fetch_quote("005930")
        self.assertEqual(result.symbol, "005930")
        self.assertEqual(result.current_price, 71000)
        self.assertEqual(result.ask_price, 71100)
        self.assertEqual(result.bid_price, 70900)

    def test_daily_bars_normalize_ka10081_rows(self):
        result = self.adapter.fetch_daily_bars("005930", count=1)
        self.assertEqual(self.client.last_pages[0:2], ("/api/dostk/chart", "ka10081"))
        self.assertEqual(self.client.last_pages[2]["stk_cd"], "005930")
        self.assertEqual(result[0].date, "20260814")
        self.assertEqual(result[0].low_price, 69500)
        self.assertEqual(result[0].volume, 12345)

    def test_volume_rank_normalizes_codes_and_limits_results(self):
        result = self.adapter.fetch_volume_rank(top_n=2)
        self.assertEqual(self.client.last_pages[0:2], ("/api/dostk/rkinfo", "ka10030"))
        self.assertEqual(self.client.last_pages[2]["stex_tp"], "1")
        self.assertEqual(result, ["005930", "000660"])

    def test_malformed_numbers_are_zero(self):
        self.client.post = lambda *args, **kwargs: FakePage({"cur_prc": "--", "sel_fpr_bid": None})
        result = self.adapter.fetch_quote("005930")
        self.assertEqual(result.current_price, 0)
        self.assertEqual(result.ask_price, 0)

    def test_dry_run_never_calls_order_transport(self):
        result = self.adapter.submit_order(OrderRequest("005930", OrderSide.BUY, 1, 70000))
        self.assertTrue(result.success)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.message, "DRY_RUN")

    def test_live_order_revision_and_cancellation_use_distinct_trs(self):
        calls = []

        def post(path, *, api_id, body=None, continuation=None, request_kind="query"):
            calls.append((path, api_id, body, request_kind))
            return FakePage({"return_code": 0, "return_msg": "", "ord_no": f"O-{api_id}"})

        self.client.post = post
        adapter = KiwoomBrokerAdapter(self.client, order_submission_enabled=True)
        order = adapter.submit_order(OrderRequest("005930", OrderSide.SELL, 2, 0))
        revision = adapter.submit_revision(ReviseOrderRequest("O1", "005930", 2, 71000))
        cancellation = adapter.submit_cancellation(CancelOrderRequest("O1", "005930", 2))
        self.assertEqual([row[1] for row in calls], ["kt10001", "kt10002", "kt10003"])
        self.assertTrue(all(row[3] == "order" for row in calls))
        self.assertEqual(order.broker_order_id, "O-kt10001")
        self.assertTrue(revision.success)
        self.assertEqual(cancellation.status.value, "canceled")

    def test_legacy_balance_and_order_shapes_remain_compatible(self):
        balance = self.adapter.get_balance()
        self.assertEqual(balance["rt_cd"], "0")
        self.assertEqual(balance["output1"][0]["pdno"], "005930")
        self.assertEqual(balance["output1"][0]["prpr"], "71000")
        self.assertEqual(balance["output2"][0]["tot_evlu_amt"], "1710000")
        self.assertNotIn(".", balance["output2"][0]["prvs_rcdl_excc_amt"])
        order = self.adapter.place_order("005930", "buy", 70000, 1)
        self.assertEqual(order["rt_cd"], "0")
        self.assertEqual(order["msg1"], "DRY_RUN")


if __name__ == "__main__":
    unittest.main()
