import unittest

from dbf_cv.render import render_document_metadata, render_header, render_sections


class RenderTest(unittest.TestCase):
    def test_render_document_metadata(self):
        profile = {
            "name": "Drummond B. Fielding",
            "document_titles": {
                "full": "Curriculum Vitae",
                "publications": "Publication List",
                "summary_only": "Curriculum Vitae",
            },
            "publication_section_titles": {
                "full": "Publications",
                "publications": "Publications",
                "summary_only": "Publications Summary",
            },
            "urls": {
                "ads_search": "https://example.com/ads",
                "orcid": "https://example.com/orcid",
                "arxiv_search": "https://example.com/arxiv",
                "google_scholar": "https://example.com/scholar",
            },
        }
        rendered = render_document_metadata(profile)
        self.assertIn(r"\def\cvname{Drummond B. Fielding}", rendered)
        self.assertIn(r"\def\FullDocumentTitle{Curriculum Vitae}", rendered)

    def test_render_header(self):
        profile = {
            "name": "Drummond B. Fielding",
            "header": {
                "title_line": "Assistant Professor",
                "address_lines": ["Department of Physics", "726 Broadway"],
                "links": [
                    {"label": "email@example.com", "icon": "faEnvelope", "url": "mailto:test@example.com"},
                    {"label": "ORCID", "icon": "faExternalLink", "url_key": "orcid"},
                ],
            },
            "urls": {"orcid": "https://example.com/orcid"},
        }
        rendered = render_header(profile)
        self.assertIn(r"\headersection{\cvname}{\cvdoctitle}", rendered)
        self.assertIn(r"\href{https://example.com/orcid}{\faExternalLink~~ORCID}", rendered)

    def test_render_sections(self):
        sections = {
            "sections": [
                {"title": "Education", "type": "itemize", "items": ["PhD"]},
                {
                    "title": "Advising",
                    "type": "generated_itemize",
                    "new_page_before": True,
                    "input": "build/generated/advising.tex",
                },
            ]
        }
        rendered = render_sections(sections)
        self.assertIn(r"\section*{Education}", rendered)
        self.assertIn(r"\newpage", rendered)
        self.assertIn(r"\input{build/generated/advising.tex}", rendered)


if __name__ == "__main__":
    unittest.main()
