"""Which of check.yml's jobs a pull request runs (.github/scripts/changes.py): documentation alone runs none but the
changelog check, the install templates alone only the templates job, tests alone only the tests, and anything else,
or any change to CI itself, everything that can be affected."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".github", "scripts"))
import changes

ALL = {"app": True, "image": True, "templates": True}


def run(*paths, **kw):
    return changes.classify(list(paths), **kw)


class Changes(unittest.TestCase):
    def test_documentation_alone_runs_nothing(self):
        self.assertEqual(run("README.md", "docs/screenshots/overview.png", "docs/database.md", "CHANGELOG.md"),
                         {"app": False, "image": False, "templates": False})

    def test_templates_alone(self):
        self.assertEqual(run("deploy/truenas/sanguity/templates/docker-compose.yaml", "deploy/check.py"),
                         {"app": False, "image": False, "templates": True})

    def test_tests_and_tools_skip_the_image(self):
        self.assertEqual(run("tests/test_api.py", "tools/screenshots.py"), {"app": True, "image": False, "templates": False})
        self.assertTrue(run("tests/docker_smoke.py")["image"], "the image jobs run it")

    def test_app_code_runs_tests_and_image(self):
        for p in ("server/db.py", "www/js/app.js", "www/css/tokens.css", "www/login.html",   # styles and pages: test_css_tokens.py (#252)
                  "watch/receiver.py", "requirements.txt", "nginx.conf", "build.py"):
            self.assertEqual(run(p, "README.md"), {"app": True, "image": True, "templates": False}, p)

    def test_compose_and_docker_run_everything(self):
        for p in ("compose.release.yaml", "docker/app.Dockerfile"):
            self.assertEqual(run(p), ALL, p)

    def test_ci_itself_and_pushes_run_everything(self):
        self.assertEqual(run(".github/workflows/check.yml"), ALL)
        self.assertEqual(run(".github/scripts/changes.py", "README.md"), ALL)
        self.assertEqual(run("README.md", everything=True), ALL)
        self.assertEqual(run(), ALL, "no list of changes: run everything")

    def test_something_new_counts_as_code(self):
        self.assertEqual(run("brand-new-folder/thing.py"), {"app": True, "image": True, "templates": False})


if __name__ == "__main__":
    unittest.main(verbosity=2)
