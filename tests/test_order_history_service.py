import unittest

from src.dashboard.services.order_history_service import (
    _history_fill_price,
    _history_remaining_qty,
    _history_row_to_trade,
    _normalize_history_cancellations,
)


class OrderHistoryServiceTests(unittest.TestCase):
    def test_parses_kiwoom_execution_price_and_remaining_quantity(self):
        row = {
            "cntr_uv": "0000026900",
            "ord_remnq": "0000000056",
        }

        self.assertEqual(_history_fill_price(row), 26900)
        self.assertEqual(_history_remaining_qty(row), 56)

    def test_folds_separate_kiwoom_cancellation_into_original_order(self):
        original = {"ord_no": "0035136", "ord_qty": "5", "cncl_yn": "N"}
        cancellation = {
            "ord_no": "0065539", "ori_ord": "0035136", "mdfy_cncl": "취소",
        }

        result = _normalize_history_cancellations([original, cancellation])

        self.assertEqual(result, [{**original, "cncl_yn": "Y"}])

    def test_keeps_unrelated_history_rows(self):
        history = [{"ord_no": "100", "ord_qty": "1"}]
        self.assertIs(_normalize_history_cancellations(history), history)

    def test_expired_order_with_remainder_is_canceled(self):
        row = {
            "ord_no": "100",
            "pdno": "005930",
            "sll_buy_dvsn_cd": "02",
            "ord_dt": "20260818",
            "ord_qty": "10",
            "cntr_qty": "4",
            "ord_remnq": "6",
        }

        self.assertEqual(_history_row_to_trade(row)["order_status"], "canceled")


if __name__ == "__main__":
    unittest.main()
