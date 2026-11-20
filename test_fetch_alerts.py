import datetime as dt
import unittest
from unittest.mock import patch

import fetch_alerts


class FetchAlertsTest(unittest.TestCase):
    def test_strip_html_and_extract_lines(self):
        title = "DESVIO L102, 103 e 231: a partir 18/Novembro &#8211; Marco dos Pereiros"
        summary = "<p>A partir de amanhã, as <b>Linhas 6, 6F e Linha Azul</b> sofrem desvio. [&hellip;]</p>"
        clean_title = fetch_alerts.strip_html(title)
        clean_summary = fetch_alerts.strip_html(summary)
        self.assertEqual(clean_title, "DESVIO L102, 103 e 231: a partir 18/Novembro – Marco dos Pereiros")
        self.assertNotIn("<", clean_summary)
        lines = fetch_alerts.extract_affected_lines(clean_title, clean_summary)
        self.assertEqual(lines, ["Linha Azul", "102", "103", "231", "6", "6F"])

    def test_validate_official_url_rejects_untrusted_domains_and_http(self):
        self.assertIsNotNone(fetch_alerts.validate_official_url("https://www.smtuc.pt/novidades/teste/"))
        self.assertIsNotNone(fetch_alerts.validate_official_url("https://sit-regiaodecoimbra.pt/avisos/desvio/"))
        self.assertIsNone(fetch_alerts.validate_official_url("http://www.smtuc.pt/novidades/teste/"))
        self.assertIsNone(fetch_alerts.validate_official_url("https://evil.example.com/smtuc.pt"))
        self.assertIsNone(fetch_alerts.validate_official_url("javascript:alert(1)"))

    def test_parse_wp_posts_uses_modified_timestamp_and_classifies(self):
        now = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)
        posts = [
            {
                "id": 2732,
                "date_gmt": "2026-11-17T15:50:12",
                "modified_gmt": "2026-11-17T15:50:12",
                "link": "https://sit-regiaodecoimbra.pt/avisos/desvio/2026/desvio-l102/",
                "title": {"rendered": "DESVIO L102 e 103"},
                "excerpt": {"rendered": "<p>Corte de via em Coimbra.</p>"},
            },
            {
                "id": 1887,
                "date_gmt": "2026-01-06T11:20:58",
                "modified_gmt": "2026-11-11T11:59:50",
                "link": "https://sit-regiaodecoimbra.pt/avisos/2026/tocha/",
                "title": {"rendered": "Alteração Paragem: Tocha"},
                "excerpt": {"rendered": "<p>Paragem alterada.</p>"},
            },
            {
                "id": 9999,
                "date_gmt": "2025-01-01T10:00:00",
                "link": "https://sit-regiaodecoimbra.pt/avisos/antigo/",
                "title": {"rendered": "Aviso muito antigo"},
                "excerpt": {"rendered": "<p>Antigo</p>"},
            },
        ]
        alerts = fetch_alerts.parse_wp_posts("SIT", posts, now, max_age_days=45)
        self.assertEqual([a["id"] for a in alerts], ["sit-2732", "sit-1887"])
        self.assertEqual(alerts[0]["category"], "DISRUPTION")
        self.assertEqual(alerts[0]["affectedLines"], ["102", "103"])
        self.assertEqual(alerts[1]["publishedAt"], "2026-11-11T11:59:50Z")
        self.assertEqual(alerts[1]["category"], "DISRUPTION")

    def test_build_feed_preserves_operator_from_fallback_on_failure(self):
        now = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)
        smtuc_post = [
            {
                "id": 1,
                "date_gmt": "2026-11-19T10:00:00",
                "link": "https://www.smtuc.pt/avisos/1/",
                "title": {"rendered": "Aviso SMTUC"},
                "excerpt": {"rendered": "<p>Texto</p>"},
            }
        ]
        fallback_sit = {
            "SIT": [
                {
                    "id": "sit-2732",
                    "operator": "SIT",
                    "category": "DISRUPTION",
                    "title": "DESVIO L102, 103 e 231",
                    "summary": "Marco dos Pereiros",
                    "url": "https://sit-regiaodecoimbra.pt/avisos/desvio-l102/",
                    "publishedAt": "2026-11-17T15:50:12Z",
                    "affectedLines": ["102", "103", "231"],
                    "active": True,
                }
            ]
        }

        def fake_fetch(url, **_kwargs):
            if "sit-regiaodecoimbra" in url:
                raise TimeoutError("SIT timeout")
            return smtuc_post

        with patch.object(fetch_alerts, "fetch_json", side_effect=fake_fetch), patch.object(
            fetch_alerts, "load_fallback_alerts_by_operator", return_value=fallback_sit
        ):
            feed = fetch_alerts.build_feed(now=now, fallback_url="https://example.invalid/avisos.json")

        self.assertEqual([a["id"] for a in feed["alerts"]], ["smtuc-1", "sit-2732"])

    def test_prefer_avisos_path_filters_noticias_unless_empty(self):
        now = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)
        mixed = [
            {
                "id": 2732,
                "date_gmt": "2026-11-17T15:50:12",
                "link": "https://sit-regiaodecoimbra.pt/avisos/desvio/2026/desvio-l102/",
                "title": {"rendered": "DESVIO L102"},
                "excerpt": {"rendered": "<p>Desvio</p>"},
            },
            {
                "id": 2319,
                "date_gmt": "2026-11-10T10:00:00",
                "link": "https://sit-regiaodecoimbra.pt/noticias/2026/linhas-rapidas/",
                "title": {"rendered": "Linhas Rápidas SIT"},
                "excerpt": {"rendered": "<p>Campanha</p>"},
            },
        ]
        filtered = fetch_alerts.parse_wp_posts("SIT", mixed, now, max_age_days=45, prefer_avisos_path=True)
        self.assertEqual([a["id"] for a in filtered], ["sit-2732"])

        only_noticias = [mixed[1]]
        fallback = fetch_alerts.parse_wp_posts("SIT", only_noticias, now, max_age_days=45, prefer_avisos_path=True)
        self.assertEqual([a["id"] for a in fallback], ["sit-2319"])


if __name__ == "__main__":
    unittest.main()
