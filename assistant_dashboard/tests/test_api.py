from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from assistant_dashboard.services import education_api
from assistant_dashboard.services.education_api import EducationAPIError, fetch_all_records


def response(status=200, payload=None):
    r = mock.Mock(status_code=status)
    r.json.return_value = payload or {}
    return r


@mock.patch("assistant_dashboard.services.education_api.time.sleep", lambda s: None)
class FetchAllRecordsTests(SimpleTestCase):
    def test_follows_pagination_until_total_count(self):
        session = mock.Mock()
        session.get.side_effect = [
            response(payload={"total_count": 5, "results": [{"i": 0}, {"i": 1}]}),
            response(payload={"total_count": 5, "results": [{"i": 2}, {"i": 3}]}),
            response(payload={"total_count": 5, "results": [{"i": 4}]}),
        ]
        records = fetch_all_records("ds", session=session, page_size=2)
        self.assertEqual([r["i"] for r in records], [0, 1, 2, 3, 4])
        offsets = [c.kwargs["params"]["offset"] for c in session.get.call_args_list]
        self.assertEqual(offsets, [0, 2, 4])
        self.assertTrue(all(c.kwargs["timeout"] for c in session.get.call_args_list))

    def test_stops_on_empty_page(self):
        session = mock.Mock()
        session.get.return_value = response(payload={"total_count": 10, "results": []})
        self.assertEqual(fetch_all_records("ds", session=session), [])

    def test_server_error_is_retried_then_raises(self):
        session = mock.Mock()
        session.get.return_value = response(503)
        with self.assertRaises(EducationAPIError):
            fetch_all_records("ds", session=session)
        self.assertEqual(session.get.call_count, education_api.MAX_ATTEMPTS)

    def test_client_error_is_not_retried(self):
        session = mock.Mock()
        session.get.return_value = response(404)
        with self.assertRaises(EducationAPIError):
            fetch_all_records("ds", session=session)
        self.assertEqual(session.get.call_count, 1)

    def test_timeout_raises_api_error(self):
        session = mock.Mock()
        session.get.side_effect = requests.Timeout("trop long")
        with self.assertRaises(EducationAPIError):
            fetch_all_records("ds", session=session)

    @override_settings(EDUCATION_API_KEY="secret")
    def test_api_key_header_when_configured(self):
        session = mock.Mock()
        session.get.return_value = response(payload={"total_count": 0, "results": []})
        fetch_all_records("ds", session=session)
        self.assertEqual(session.get.call_args.kwargs["headers"]["Authorization"], "Apikey secret")

    @override_settings(EDUCATION_API_KEY="")
    def test_no_auth_header_without_key(self):
        session = mock.Mock()
        session.get.return_value = response(payload={"total_count": 0, "results": []})
        fetch_all_records("ds", session=session)
        self.assertNotIn("Authorization", session.get.call_args.kwargs["headers"])
