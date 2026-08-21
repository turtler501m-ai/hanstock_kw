import unittest

from src.dashboard.services.order_history_service import (
    _history_fill_price,
    _history_remaining_qty,
)


class OrderHistoryServiceTests(unittest.TestCase):
    def test_parses_kiwoom_execution_price_and_remaining_quantity(self):
        row = {
            "cntr_uv": "0000026900",
            "ord_remnq": "0000000056",
        }

        self.assertEqual(_history_fill_price(row), 26900)
        self.assertEqual(_history_remaining_qty(row), 56)


if __name__ == "__main__":
    unittest.main()
