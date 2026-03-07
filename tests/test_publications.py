import unittest

from dbf_cv.publications import build_advisee_data, compute_h_index


class PublicationsTest(unittest.TestCase):
    def test_compute_h_index(self):
        self.assertEqual(compute_h_index([12, 8, 5, 4, 3]), 4)
        self.assertEqual(compute_h_index([100, 4, 3, 1]), 3)

    def test_build_advisee_data(self):
        manifest = {
            "categories": {
                "graduate": {"symbol": "\\ddagger", "legend": "graduate student-led"},
                "postdoc": {"symbol": "\\dagger", "legend": "postdoc-led"},
            },
            "advisees": [
                {
                    "name": "Example Student",
                    "category": "graduate",
                    "role": "Graduate Student",
                    "affiliation": "NYU",
                    "led_papers": ["2024Example....1A"],
                },
                {
                    "name": "Example Postdoc",
                    "category": "postdoc",
                    "show_in_advising": False,
                    "led_papers": ["2024Example....2B"],
                },
            ],
        }
        categories, visible, led = build_advisee_data(manifest)
        self.assertEqual(categories["graduate"]["symbol"], "\\ddagger")
        self.assertEqual(len(visible), 1)
        self.assertEqual(led["2024Example....1A"]["category"], "graduate")
        self.assertEqual(led["2024Example....2B"]["category"], "postdoc")


if __name__ == "__main__":
    unittest.main()
