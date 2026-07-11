import unittest

from internship_bot import Client, Job, build_email, location_allowed, matches, speedyapply_jobs


class BotTests(unittest.TestCase):
    def setUp(self):
        self.filters = {
            "seasons": ["summer"], "years": [2027], "require_target_year": True,
            "countries": ["United States", "Canada", "Remote"],
            "include_keywords": ["software engineer", "swe"], "exclude_keywords": ["senior", "new grad"],
            "require_internship_word": True,
        }

    def test_matching_role(self):
        job = Job("Acme", "Software Engineer Intern - Summer 2027", "Toronto, Canada", "https://x.test/1", "Test")
        self.assertTrue(matches(job, self.filters))

    def test_rejects_wrong_year(self):
        job = Job("Acme", "Software Engineer Intern - Summer 2026", "Toronto, Canada", "https://x.test/1", "Test")
        self.assertFalse(matches(job, self.filters))

    def test_rejects_undated_role(self):
        job = Job("Acme", "Software Engineer Intern", "Toronto, Canada", "https://x.test/1", "Test")
        self.assertFalse(matches(job, self.filters))

    def test_rejects_non_internship(self):
        job = Job("Acme", "Senior Software Engineer", "Remote", "https://x.test/1", "Test")
        self.assertFalse(matches(job, self.filters))

    def test_location_aliases(self):
        self.assertTrue(location_allowed("Redmond, WA", ["united states"]))
        self.assertTrue(location_allowed("Waterloo, Ontario", ["canada"]))
        self.assertFalse(location_allowed("London, UK", ["canada", "united states"]))

    def test_email_escapes_html(self):
        _, _, body = build_email([Job("A&B", "SWE <Intern>", "Remote", "https://x.test?a=1&b=2", "Test")], [], "Bot")
        self.assertIn("A&amp;B", body)
        self.assertIn("SWE &lt;Intern&gt;", body)

    def test_speedyapply_parser(self):
        class FakeClient(Client):
            def get(self, url, **kwargs):
                class Response:
                    text = '| <a href="https://acme.test"><strong>Acme</strong></a> | Software Engineer Intern - Summer 2027 | Toronto, Canada | <a href="https://apply.test/1"><img alt="Apply"/></a> | 1d |'
                return Response()

        jobs = speedyapply_jobs(FakeClient(), {"urls": ["https://list.test"]})
        self.assertEqual(jobs, [Job("Acme", "Software Engineer Intern - Summer 2027", "Toronto, Canada", "https://apply.test/1", "SpeedyApply")])


if __name__ == "__main__":
    unittest.main()
