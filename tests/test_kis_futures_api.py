import unittest
from unittest.mock import patch

from src.api import kis_futures_api
from src.api.kis_futures_api import KISFuturesAPI


class _FakeResponse:
    status_code = 200
    text = '{"rt_cd":"0"}'

    def json(self):
        return {"rt_cd": "0"}


class KISFuturesAPITest(unittest.TestCase):
    def _api(self, demo=True):
        """__init__을 건너뛰고 필요한 속성만 직접 설정한 인스턴스를 반환합니다."""
        api = KISFuturesAPI.__new__(KISFuturesAPI)
        api.demo = demo
        api.access_token = "token"
        api.base_url = "https://example.test"
        api._app_key = "app"
        api._app_secret = "secret"
        api._account = "12345678"
        api._env_label = "demo" if demo else "real"
        api._token_cache = KISFuturesAPI._DEMO_TOKEN_CACHE if demo else KISFuturesAPI._REAL_TOKEN_CACHE
        api._configured = True
        return api

    # ------------------------------------------------------------------
    # get_balance TR ID 검증
    # ------------------------------------------------------------------

    def test_balance_demo_uses_vtfo6501r(self):
        """demo=True 이면 get_balance()가 VTFO6501R TR ID를 사용해야 합니다."""
        api = self._api(demo=True)
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "get", return_value=_FakeResponse()) as http_get,
        ):
            api.get_balance()

        headers = http_get.call_args.kwargs["headers"]
        params = http_get.call_args.kwargs["params"]
        self.assertEqual(headers["tr_id"], "VTFO6501R")
        self.assertEqual(params["ACNT_PRDT_CD"], "08")

    def test_balance_real_uses_otfo6501r(self):
        """demo=False 이면 get_balance()가 OTFO6501R TR ID를 사용해야 합니다."""
        api = self._api(demo=False)
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "get", return_value=_FakeResponse()) as http_get,
        ):
            api.get_balance()

        headers = http_get.call_args.kwargs["headers"]
        self.assertEqual(headers["tr_id"], "OTFO6501R")

    # ------------------------------------------------------------------
    # get_positions TR ID 검증
    # ------------------------------------------------------------------

    def test_positions_demo_uses_vtfo6507r(self):
        api = self._api(demo=True)
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "get", return_value=_FakeResponse()) as http_get,
        ):
            api.get_positions()

        headers = http_get.call_args.kwargs["headers"]
        self.assertEqual(headers["tr_id"], "VTFO6507R")

    def test_positions_real_uses_otfo6507r(self):
        api = self._api(demo=False)
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "get", return_value=_FakeResponse()) as http_get,
        ):
            api.get_positions()

        headers = http_get.call_args.kwargs["headers"]
        self.assertEqual(headers["tr_id"], "OTFO6507R")

    # ------------------------------------------------------------------
    # place_order TR ID 및 응답 형식 검증
    # ------------------------------------------------------------------

    def test_order_demo_uses_vtfo1001u(self):
        api = self._api(demo=True)
        fake = _FakeResponse()
        fake.json = lambda: {"rt_cd": "0", "output": {"ODNO": "12345"}}
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "post", return_value=fake) as http_post,
        ):
            result = api.place_order("MNQM25", "buy", 1, 21000.0)

        headers = http_post.call_args.kwargs["headers"]
        self.assertEqual(headers["tr_id"], "VTFO1001U")
        self.assertEqual(result["status"], "submitted")
        self.assertTrue(result["demo"])
        self.assertEqual(result["order_id"], "12345")

    def test_order_real_uses_otfo1001u(self):
        api = self._api(demo=False)
        fake = _FakeResponse()
        fake.json = lambda: {"rt_cd": "0", "output": {"ODNO": "99999"}}
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "post", return_value=fake) as http_post,
        ):
            result = api.place_order("MNQM25", "sell", 2, 21000.0)

        headers = http_post.call_args.kwargs["headers"]
        self.assertEqual(headers["tr_id"], "OTFO1001U")
        self.assertFalse(result["demo"])

    # ------------------------------------------------------------------
    # get_executions TR ID 검증
    # ------------------------------------------------------------------

    def test_executions_demo_uses_vtfo6511r(self):
        api = self._api(demo=True)
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "get", return_value=_FakeResponse()) as http_get,
        ):
            api.get_executions()

        headers = http_get.call_args.kwargs["headers"]
        self.assertEqual(headers["tr_id"], "VTFO6511R")

    def test_executions_real_uses_otfo6511r(self):
        api = self._api(demo=False)
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "get", return_value=_FakeResponse()) as http_get,
        ):
            api.get_executions()

        headers = http_get.call_args.kwargs["headers"]
        self.assertEqual(headers["tr_id"], "OTFO6511R")

    # ------------------------------------------------------------------
    # not_configured 반환 검증
    # ------------------------------------------------------------------

    def test_not_configured_returns_standard_dict(self):
        """API 키 미설정 시 {"status": "not_configured", "demo": bool}을 반환해야 합니다."""
        api = KISFuturesAPI.__new__(KISFuturesAPI)
        api.demo = True
        api._configured = False

        result = api._not_configured()
        self.assertEqual(result["status"], "not_configured")
        self.assertTrue(result["demo"])

    def test_get_balance_not_configured(self):
        api = KISFuturesAPI.__new__(KISFuturesAPI)
        api.demo = True
        api._configured = False

        result = api.get_balance()
        self.assertEqual(result["status"], "not_configured")
        self.assertTrue(result["demo"])

    # ------------------------------------------------------------------
    # _resolve_tr_id: 기존 호환성 테스트
    # ------------------------------------------------------------------

    def test_resolve_tr_id_rewrites_stock_style_in_demo(self):
        api = self._api(demo=True)
        self.assertEqual(api._resolve_tr_id("TTTC8434R"), "VTTC8434R")

    def test_resolve_tr_id_keeps_futures_tr_id_in_demo(self):
        api = self._api(demo=True)
        self.assertEqual(api._resolve_tr_id("OTFM3116R"), "OTFM3116R")
        self.assertEqual(api._resolve_tr_id("HHDFC55010000"), "HHDFC55010000")

    def test_resolve_tr_id_no_rewrite_in_real(self):
        api = self._api(demo=False)
        self.assertEqual(api._resolve_tr_id("TTTC8434R"), "TTTC8434R")

    # ------------------------------------------------------------------
    # 계좌번호 파싱 테스트
    # ------------------------------------------------------------------

    def test_get_account_code_10digits(self):
        api = self._api()
        api._account = "1234567890"
        cano, prdt = api._get_account_code()
        self.assertEqual(cano, "12345678")
        self.assertEqual(prdt, "90")

    def test_get_account_code_8digits(self):
        api = self._api()
        api._account = "12345678"
        cano, prdt = api._get_account_code()
        self.assertEqual(cano, "12345678")
        self.assertEqual(prdt, "08")

    def test_parse_account_dash_format(self):
        """계좌번호 "12345678-08" 형식을 올바르게 파싱해야 합니다."""
        api = self._api()
        cano, prdt = api._parse_account("12345678-08")
        self.assertEqual(cano, "12345678")
        self.assertEqual(prdt, "08")

    def test_parse_account_dash_format_custom_product_code(self):
        """계좌번호 "12345678-09" 형식에서 상품코드를 올바르게 파싱해야 합니다."""
        api = self._api()
        cano, prdt = api._parse_account("12345678-09")
        self.assertEqual(cano, "12345678")
        self.assertEqual(prdt, "09")

    # ------------------------------------------------------------------
    # _check_configured 테스트
    # ------------------------------------------------------------------

    def test_check_configured_true_when_all_set(self):
        """app_key, app_secret, account가 모두 설정된 경우 True를 반환해야 합니다."""
        api = self._api()
        self.assertTrue(api._check_configured())

    def test_check_configured_false_when_missing(self):
        """필수 값 중 하나라도 없으면 False를 반환해야 합니다."""
        api = self._api()
        api._app_key = ""
        self.assertFalse(api._check_configured())

    # ------------------------------------------------------------------
    # get_positions 엔드포인트 검증
    # ------------------------------------------------------------------

    def test_positions_uses_inquire_balance_endpoint(self):
        """get_positions()는 /trading/inquire-balance 엔드포인트를 사용해야 합니다."""
        api = self._api(demo=True)
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "get", return_value=_FakeResponse()) as http_get,
        ):
            api.get_positions()

        url = http_get.call_args.args[0] if http_get.call_args.args else http_get.call_args[0][0]
        self.assertIn("inquire-balance", url)
        self.assertNotIn("inquire-ccld", url)

    def test_positions_params_include_ovrs_futr_fx_pdno(self):
        """get_positions() 파라미터에 OVRS_FUTR_FX_PDNO가 포함되어야 합니다."""
        api = self._api(demo=True)
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "get", return_value=_FakeResponse()) as http_get,
        ):
            api.get_positions()

        params = http_get.call_args.kwargs["params"]
        self.assertIn("OVRS_FUTR_FX_PDNO", params)

    # ------------------------------------------------------------------
    # get_current_price 구조화된 응답 검증
    # ------------------------------------------------------------------

    def test_current_price_returns_structured_response(self):
        """get_current_price()가 성공 시 price, symbol, status 키를 포함한 dict를 반환해야 합니다."""
        api = self._api(demo=True)
        fake = _FakeResponse()
        fake.json = lambda: {"rt_cd": "0", "output": {"last": "21000", "diff": "100", "acml_vol": "500"}}
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "get", return_value=fake),
        ):
            result = api.get_current_price("MNQM25")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["symbol"], "MNQM25")
        self.assertEqual(result["price"], 21000.0)
        self.assertEqual(result["change"], "100")
        self.assertEqual(result["volume"], "500")

    def test_current_price_uses_hhdfc55010000_tr_id(self):
        """get_current_price()는 HHDFC55010000 TR ID를 사용해야 합니다."""
        api = self._api(demo=True)
        fake = _FakeResponse()
        fake.json = lambda: {"rt_cd": "0", "output": {}}
        with (
            patch.object(kis_futures_api, "_kis_futures_throttle"),
            patch.object(kis_futures_api.HTTP, "get", return_value=fake) as http_get,
        ):
            api.get_current_price("MNQM25")

        headers = http_get.call_args.kwargs["headers"]
        self.assertEqual(headers["tr_id"], "HHDFC55010000")


if __name__ == "__main__":
    unittest.main()
