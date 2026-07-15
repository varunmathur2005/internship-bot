import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from internship_bot import Client, Job, build_email, load_config, location_allowed, matches, speedyapply_jobs


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

    def test_load_config_falls_back_to_example(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "config.example.yaml").write_text("email:\n  subject_prefix: Test\n")
            original_cwd = Path.cwd()
            try:
                os.chdir(temp_path)
                config = load_config("config.yaml")
            finally:
                os.chdir(original_cwd)
        self.assertEqual(config["email"]["subject_prefix"], "Test")

    def test_load_config_missing_explicit_file_raises(self):
        with TemporaryDirectory() as temp_dir:
            original_cwd = Path.cwd()
            try:
                os.chdir(temp_dir)
                with self.assertRaises(FileNotFoundError):
                    load_config("missing.yaml")
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
