import datetime as dt
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_alerts  # noqa: E402


class FetchAlertsTests(unittest.TestCase):
    def test_extracts_sit_and_smtuc_lines_and_strips_html(self):
        now = dt.datetime(2026, 11, 18, 17, 0, tzinfo=dt.timezone.utc)
        posts_sit = [
            {
                "id": 2732,
                "date_gmt": "2026-11-17T15:50:12",
                "link": "https://sit-regiaodecoimbra.pt/avisos/desvio/2026/desvio-l102-103-e-231/",
                "title": {"rendered": "DESVIO L102, 103 e 231: a partir 18/Novembro &#8211; Coimbra"},
                "excerpt": {"rendered": "<p>Devido a obras, <strong>desvio</strong> nas linhas.</p>"},
            }
        ]
        posts_smtuc = [
            {
                "id": 21390,
                "date_gmt": "2026-11-12T10:19:22",
                "link": "https://www.smtuc.pt/eventos/linha-azul-com-percurso-alternativo/",
                "title": {"rendered": "Linha Azul com percurso alternativo esta 5ª feira"},
                "excerpt": {"rendered": "<p>Alteração temporária na Linha 21T e Linha Azul. [&hellip;]</p>"},
            },
            {
                "id": 21409,
                "date_gmt": "2026-11-16T11:26:37",
                "link": "https://www.smtuc.pt/novidades/stal-convoca-greve/",
                "title": {"rendered": "STAL convoca greve para esta 6ª feira"},
                "excerpt": {"rendered": "<p>Possíveis perturbações no serviço.</p>"},
            },
        ]

        sit_alerts = fetch_alerts.parse_wp_posts("SIT", posts_sit, now)
        self.assertEqual(len(sit_alerts), 1)
        self.assertEqual(sit_alerts[0]["category"], "DISRUPTION")
        self.assertEqual(sit_alerts[0]["affectedLines"], ["102", "103", "231"])
        self.assertEqual(sit_alerts[0]["summary"], "Devido a obras, desvio nas linhas.")
        self.assertIn("–", sit_alerts[0]["title"])

        smtuc_alerts = fetch_alerts.parse_wp_posts("SMTUC", posts_smtuc, now)
        self.assertEqual(len(smtuc_alerts), 2)
        self.assertEqual(smtuc_alerts[0]["category"], "DISRUPTION")
        self.assertEqual(smtuc_alerts[0]["affectedLines"], ["Linha Azul", "21T"])
        self.assertEqual(smtuc_alerts[1]["category"], "STRIKE")

    def test_rejects_untrusted_or_non_https_urls(self):
        now = dt.datetime(2026, 11, 18, 17, 0, tzinfo=dt.timezone.utc)
        bad_posts = [
            {
                "id": 1,
                "date_gmt": "2026-11-18T10:00:00",
                "link": "http://www.smtuc.pt/insecure/",
                "title": {"rendered": "HTTP link"},
                "excerpt": {"rendered": ""},
            },
            {
                "id": 2,
                "date_gmt": "2026-11-18T10:00:00",
                "link": "https://evil.example.com/phish",
                "title": {"rendered": "Untrusted domain"},
                "excerpt": {"rendered": ""},
            },
            {
                "id": 3,
                "date_gmt": "2026-11-18T10:00:00",
                "link": "javascript:alert(1)",
                "title": {"rendered": "XSS link"},
                "excerpt": {"rendered": ""},
            },
        ]
        self.assertEqual(fetch_alerts.parse_wp_posts("SMTUC", bad_posts, now), [])

    def test_filters_old_posts(self):
        now = dt.datetime(2026, 11, 18, 17, 0, tzinfo=dt.timezone.utc)
        old_post = [
            {
                "id": 99,
                "date_gmt": "2026-08-01T10:00:00",
                "link": "https://www.smtuc.pt/avisos/antigo/",
                "title": {"rendered": "Aviso antigo"},
                "excerpt": {"rendered": "Texto antigo"},
            }
        ]
        self.assertEqual(fetch_alerts.parse_wp_posts("SMTUC", old_post, now, max_age_days=45), [])


if __name__ == "__main__":
    unittest.main()
